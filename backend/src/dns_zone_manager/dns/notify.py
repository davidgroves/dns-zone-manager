"""DNS NOTIFY listener for zone change notifications (RFC 1996).

Listens on both UDP and TCP for NOTIFY messages from DNS servers,
with optional TSIG verification. Triggers zone refresh callbacks
when notifications are received.
"""

import asyncio
import logging
import struct
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import dns.exception
import dns.message
import dns.name
import dns.opcode
import dns.rcode
import dns.tsig
import dns.tsigkeyring

from dns_zone_manager.logging import log_internal_event
from dns_zone_manager.metrics import notifies_received_total, notifies_rejected_total

if TYPE_CHECKING:
    from dns_zone_manager.config import NotifySettings, TSIGKeyEntry

logger = logging.getLogger(__name__)

# Type alias for the zone refresh callback
NotifyCallback = Callable[[str], Awaitable[None]]


class NotifyListener:
    """Async listener for DNS NOTIFY messages over UDP and TCP.

    Handles RFC 1996 NOTIFY messages from DNS servers to trigger
    zone cache refreshes. Supports both UDP and TCP transports,
    and optional TSIG verification.
    """

    def __init__(
        self,
        settings: "NotifySettings",
        tsig_key: "TSIGKeyEntry | None" = None,
        on_notify: NotifyCallback | None = None,
    ):
        """Initialize the NOTIFY listener.

        Args:
            settings: NOTIFY listener configuration
            tsig_key: TSIG key for verification (optional)
            on_notify: Async callback to invoke when NOTIFY received
        """
        self.settings = settings
        self.tsig_key = tsig_key
        self.on_notify = on_notify

        # Set up TSIG keyring for verification if configured
        self.keyring: dict | None = None
        if tsig_key and tsig_key.secret:
            try:
                self.keyring = dns.tsigkeyring.from_text(
                    {tsig_key.name: tsig_key.secret.get_secret_value()}
                )
            except Exception as e:
                log_internal_event(
                    "notify_tsig_keyring_error",
                    logger,
                    level="WARNING",
                    error=str(e),
                )

        self._udp_transport: asyncio.DatagramTransport | None = None
        self._tcp_server: asyncio.Server | None = None
        self._running = False

    async def start(self) -> None:
        """Start the NOTIFY listeners on UDP and TCP."""
        if self._running:
            return

        self._running = True
        bind_addr = self.settings.bind_address

        # Start UDP listener
        loop = asyncio.get_running_loop()
        try:
            self._udp_transport, _ = await loop.create_datagram_endpoint(
                lambda: _NotifyUDPProtocol(self),
                local_addr=(bind_addr, self.settings.udp_port),
            )
            log_internal_event(
                "notify_udp_started",
                logger,
                bind_address=bind_addr,
                port=self.settings.udp_port,
            )
        except Exception as e:
            log_internal_event(
                "notify_udp_start_failed",
                logger,
                level="ERROR",
                error=str(e),
            )

        # Start TCP listener
        try:
            self._tcp_server = await asyncio.start_server(
                self._handle_tcp_connection,
                bind_addr,
                self.settings.tcp_port,
            )
            log_internal_event(
                "notify_tcp_started",
                logger,
                bind_address=bind_addr,
                port=self.settings.tcp_port,
            )
        except Exception as e:
            log_internal_event(
                "notify_tcp_start_failed",
                logger,
                level="ERROR",
                error=str(e),
            )

    async def stop(self) -> None:
        """Stop the NOTIFY listeners."""
        self._running = False

        if self._udp_transport:
            self._udp_transport.close()
            self._udp_transport = None
            log_internal_event("notify_udp_stopped", logger)

        if self._tcp_server:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
            self._tcp_server = None
            log_internal_event("notify_tcp_stopped", logger)

    async def handle_notify(
        self,
        data: bytes,
        addr: tuple[str, int],
        transport: str,
    ) -> bytes | None:
        """Process a DNS NOTIFY message and generate response.

        Args:
            data: Raw DNS message bytes
            addr: Source address (host, port)
            transport: Transport type ("udp" or "tcp")

        Returns:
            Response bytes to send back, or None if no response needed
        """
        try:
            # Parse the DNS message
            message = dns.message.from_wire(data)

            # Verify it's a NOTIFY message
            if message.opcode() != dns.opcode.NOTIFY:
                log_internal_event(
                    "notify_wrong_opcode",
                    logger,
                    level="DEBUG",
                    opcode=dns.opcode.to_text(message.opcode()),
                    source=f"{addr[0]}:{addr[1]}",
                )
                notifies_rejected_total.labels(transport=transport, reason="wrong_opcode").inc()
                return self._make_error_response(message, dns.rcode.REFUSED)

            # Extract zone name from question section
            if not message.question:
                log_internal_event(
                    "notify_no_question",
                    logger,
                    level="DEBUG",
                    source=f"{addr[0]}:{addr[1]}",
                )
                notifies_rejected_total.labels(transport=transport, reason="no_question").inc()
                return self._make_error_response(message, dns.rcode.FORMERR)

            zone_name = str(message.question[0].name)

            # Verify TSIG if required
            if self.settings.require_tsig:
                if not self._verify_tsig(message, data):
                    log_internal_event(
                        "notify_tsig_failed",
                        logger,
                        level="WARNING",
                        zone=zone_name,
                        source=f"{addr[0]}:{addr[1]}",
                        transport=transport,
                    )
                    notifies_rejected_total.labels(transport=transport, reason="tsig_failed").inc()
                    return self._make_error_response(message, dns.rcode.NOTAUTH)

            # Increment successful NOTIFY counter
            notifies_received_total.labels(transport=transport, zone=zone_name).inc()

            log_internal_event(
                "notify_received",
                logger,
                zone=zone_name,
                source=f"{addr[0]}:{addr[1]}",
                transport=transport,
                tsig_verified=message.had_tsig,
            )

            # Trigger the callback asynchronously
            if self.on_notify:
                asyncio.create_task(self._invoke_callback(zone_name))

            # Generate success response
            return self._make_notify_response(message)

        except dns.exception.DNSException as e:
            log_internal_event(
                "notify_parse_error",
                logger,
                level="WARNING",
                error=str(e),
                source=f"{addr[0]}:{addr[1]}",
            )
            notifies_rejected_total.labels(transport=transport, reason="parse_error").inc()
            return None
        except Exception as e:
            log_internal_event(
                "notify_handle_error",
                logger,
                level="ERROR",
                error=str(e),
                source=f"{addr[0]}:{addr[1]}",
            )
            return None

    async def _invoke_callback(self, zone_name: str) -> None:
        """Invoke the notify callback with error handling.

        Args:
            zone_name: Zone that received the NOTIFY
        """
        if not self.on_notify:
            return

        try:
            await self.on_notify(zone_name)
        except Exception as e:
            log_internal_event(
                "notify_callback_error",
                logger,
                level="ERROR",
                zone=zone_name,
                error=str(e),
            )

    def _verify_tsig(self, message: dns.message.Message, wire: bytes) -> bool:
        """Verify TSIG signature on a message.

        Args:
            message: Parsed DNS message
            wire: Original wire format data

        Returns:
            True if TSIG is valid or not required, False if invalid
        """
        if not message.had_tsig:
            # No TSIG present - fail if required
            return False

        if not self.keyring:
            # No keyring configured but TSIG present - can't verify
            return False

        try:
            # Re-parse with keyring for verification
            dns.message.from_wire(wire, keyring=self.keyring)
            return True
        except dns.tsig.PeerBadKey:
            log_internal_event(
                "notify_tsig_bad_key",
                logger,
                level="DEBUG",
            )
            return False
        except dns.tsig.PeerBadSignature:
            log_internal_event(
                "notify_tsig_bad_signature",
                logger,
                level="DEBUG",
            )
            return False
        except dns.tsig.PeerBadTime:
            log_internal_event(
                "notify_tsig_bad_time",
                logger,
                level="DEBUG",
            )
            return False
        except Exception:
            return False

    def _make_notify_response(self, query: dns.message.Message) -> bytes:
        """Create a NOTIFY response message.

        Args:
            query: Original NOTIFY query

        Returns:
            Wire format response bytes
        """
        response = dns.message.make_response(query)
        response.set_rcode(dns.rcode.NOERROR)
        return response.to_wire()

    def _make_error_response(
        self,
        query: dns.message.Message,
        rcode: dns.rcode.Rcode,
    ) -> bytes:
        """Create an error response message.

        Args:
            query: Original query
            rcode: Response code to set

        Returns:
            Wire format response bytes
        """
        response = dns.message.make_response(query)
        response.set_rcode(rcode)
        return response.to_wire()

    async def _handle_tcp_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a TCP connection for NOTIFY.

        DNS over TCP uses a 2-byte length prefix before each message.

        Args:
            reader: Stream reader
            writer: Stream writer
        """
        addr = writer.get_extra_info("peername")
        try:
            while True:
                # Read 2-byte length prefix
                length_data = await asyncio.wait_for(reader.read(2), timeout=30.0)
                if len(length_data) < 2:
                    break

                length = struct.unpack("!H", length_data)[0]
                if length == 0 or length > 65535:
                    break

                # Read message data
                data = await asyncio.wait_for(reader.read(length), timeout=30.0)
                if len(data) < length:
                    break

                # Process the message
                response = await self.handle_notify(data, addr, "tcp")
                if response:
                    # Send response with length prefix
                    writer.write(struct.pack("!H", len(response)))
                    writer.write(response)
                    await writer.drain()

                # Close after one message (NOTIFY is typically one-shot)
                break

        except TimeoutError:
            log_internal_event(
                "notify_tcp_timeout",
                logger,
                level="DEBUG",
                source=f"{addr[0]}:{addr[1]}" if addr else "unknown",
            )
        except Exception as e:
            log_internal_event(
                "notify_tcp_error",
                logger,
                level="WARNING",
                error=str(e),
                source=f"{addr[0]}:{addr[1]}" if addr else "unknown",
            )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class _NotifyUDPProtocol(asyncio.DatagramProtocol):
    """UDP protocol handler for NOTIFY messages."""

    def __init__(self, listener: NotifyListener):
        self.listener = listener
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.transports.BaseTransport) -> None:
        """Called when the UDP socket is ready."""
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle incoming UDP datagram.

        Args:
            data: Raw message bytes
            addr: Source address (host, port)
        """
        # Process asynchronously to not block the protocol
        asyncio.create_task(self._process_datagram(data, addr))

    async def _process_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        """Process a UDP NOTIFY datagram.

        Args:
            data: Raw message bytes
            addr: Source address
        """
        response = await self.listener.handle_notify(data, addr, "udp")
        if response and self.transport:
            self.transport.sendto(response, addr)

    def error_received(self, exc: Exception) -> None:
        """Handle UDP socket errors."""
        log_internal_event(
            "notify_udp_error",
            logger,
            level="WARNING",
            error=str(exc),
        )

    def connection_lost(self, exc: Exception | None) -> None:
        """Handle UDP socket closure."""
        if exc:
            log_internal_event(
                "notify_udp_connection_lost",
                logger,
                level="WARNING",
                error=str(exc),
            )
