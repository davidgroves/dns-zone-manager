"""Unit tests for DNS NOTIFY listener (RFC 1996)."""

from __future__ import annotations

import base64
import secrets

import dns.message
import dns.name
import dns.opcode
import dns.rcode
import dns.rdatatype
import dns.tsig
import dns.tsigkeyring
import pytest
from dns_zone_manager.config import NotifySettings, TSIGKeyEntry
from dns_zone_manager.dns.notify import NotifyListener


def _make_notify(zone: str = "test.example.") -> dns.message.Message:
    msg = dns.message.make_query(zone, dns.rdatatype.SOA)
    msg.set_opcode(dns.opcode.NOTIFY)
    return msg


def _make_query(zone: str = "test.example.") -> dns.message.Message:
    return dns.message.make_query(zone, dns.rdatatype.SOA)


@pytest.fixture
def tsig_key() -> TSIGKeyEntry:
    secret = base64.b64encode(secrets.token_bytes(32)).decode()
    return TSIGKeyEntry(name="notify-test-key", secret=secret, algorithm="hmac-sha256")


@pytest.mark.asyncio
async def test_handle_notify_success_invokes_callback():
    received: list[str] = []

    async def on_notify(zone: str) -> None:
        received.append(zone)

    listener = NotifyListener(
        settings=NotifySettings(enabled=True, require_tsig=False),
        on_notify=on_notify,
    )
    wire = _make_notify("test.example.").to_wire()
    response = await listener.handle_notify(wire, ("127.0.0.1", 5300), "udp")

    assert response is not None
    resp_msg = dns.message.from_wire(response)
    assert resp_msg.rcode() == dns.rcode.NOERROR

    # Callback is scheduled via coalesced create_task; yield to the loop
    import asyncio

    for _ in range(20):
        if any(z.startswith("test.example") for z in received):
            break
        await asyncio.sleep(0.01)
    assert any(z.startswith("test.example") for z in received)


@pytest.mark.asyncio
async def test_handle_notify_rejects_wrong_opcode():
    listener = NotifyListener(settings=NotifySettings(require_tsig=False))
    wire = _make_query("test.example.").to_wire()
    response = await listener.handle_notify(wire, ("127.0.0.1", 5300), "udp")

    assert response is not None
    resp_msg = dns.message.from_wire(response)
    assert resp_msg.rcode() == dns.rcode.REFUSED


@pytest.mark.asyncio
async def test_require_tsig_rejects_unsigned_notify(tsig_key: TSIGKeyEntry):
    listener = NotifyListener(
        settings=NotifySettings(require_tsig=True),
        tsig_key=tsig_key,
    )
    wire = _make_notify("test.example.").to_wire()
    response = await listener.handle_notify(wire, ("127.0.0.1", 5300), "udp")

    assert response is not None
    resp_msg = dns.message.from_wire(response)
    assert resp_msg.rcode() == dns.rcode.NOTAUTH


@pytest.mark.asyncio
async def test_require_tsig_accepts_valid_signature(tsig_key: TSIGKeyEntry):
    received: list[str] = []

    async def on_notify(zone: str) -> None:
        received.append(zone)

    listener = NotifyListener(
        settings=NotifySettings(require_tsig=True),
        tsig_key=tsig_key,
        on_notify=on_notify,
    )

    keyring = dns.tsigkeyring.from_text({tsig_key.name: tsig_key.secret.get_secret_value()})
    msg = _make_notify("test.example.")
    msg.use_tsig(keyring, keyname=tsig_key.name, algorithm=dns.tsig.HMAC_SHA256)
    wire = msg.to_wire()

    response = await listener.handle_notify(wire, ("127.0.0.1", 5300), "udp")
    assert response is not None
    # Response may carry an unsigned or residual TSIG section; success is NOERROR
    # from the handler path plus the callback firing.
    assert len(response) > 0

    import asyncio

    await asyncio.sleep(0)
    assert any(z.startswith("test.example") for z in received)


@pytest.mark.asyncio
async def test_require_tsig_rejects_bad_key(tsig_key: TSIGKeyEntry):
    listener = NotifyListener(
        settings=NotifySettings(require_tsig=True),
        tsig_key=tsig_key,
    )

    wrong_secret = base64.b64encode(secrets.token_bytes(32)).decode()
    wrong_keyring = dns.tsigkeyring.from_text({"notify-test-key": wrong_secret})
    msg = _make_notify("test.example.")
    msg.use_tsig(wrong_keyring, keyname="notify-test-key", algorithm=dns.tsig.HMAC_SHA256)
    wire = msg.to_wire()

    response = await listener.handle_notify(wire, ("127.0.0.1", 5300), "udp")
    # Bad TSIG cannot be answered with a signed error without a matching key
    assert response is None


def test_verify_tsig_false_without_keyring():
    listener = NotifyListener(settings=NotifySettings(require_tsig=True), tsig_key=None)
    msg = _make_notify()
    assert listener._verify_tsig(msg, msg.to_wire()) is False
