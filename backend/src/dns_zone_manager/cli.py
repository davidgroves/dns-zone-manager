"""DNS API CLI - Command-line interface for DNS record management.

This module provides a CLI to interact with the DNS API server via HTTP.
Install with: uv pip install dns-zone-manager[cli]

Configuration via environment variables:
    DNS_API_URL: Server URL (e.g., http://localhost:8000)
    DNS_API_KEY: API key for authentication
"""

# Check for required dependencies before importing
try:
    import click
    import httpx
except ImportError:
    import sys

    print(
        "Error: Missing CLI dependencies.\nInstall with: uv pip install dns-zone-manager[cli]",
        file=sys.stderr,
    )
    sys.exit(1)

import json
import os
import sys
from urllib.parse import quote

from dns_zone_manager import __version__


def get_client() -> httpx.Client:
    """Create an HTTP client with authentication headers."""
    api_url = os.environ.get("DNS_API_URL", "http://localhost:8000")
    api_key = os.environ.get("DNS_API_KEY", "")

    if not api_key:
        click.secho("Error: DNS_API_KEY environment variable not set", fg="red", err=True)
        sys.exit(1)

    return httpx.Client(
        base_url=api_url,
        headers={"X-API-Key": api_key},
        timeout=30.0,
    )


def format_success(message: str) -> None:
    """Print a success message."""
    click.secho(f"✓ {message}", fg="green")


def format_error(message: str) -> None:
    """Print an error message."""
    click.secho(f"✗ {message}", fg="red", err=True)


def handle_response(
    response: httpx.Response,
    success_message: str,
    output_json: bool = False,
) -> None:
    """Handle API response with appropriate output formatting."""
    if output_json:
        try:
            data = response.json()
            click.echo(json.dumps(data, indent=2))
        except json.JSONDecodeError:
            click.echo(response.text)
        return

    if response.is_success:
        format_success(success_message)
        try:
            data = response.json()
            if "rrset" in data and data["rrset"]:
                rrset = data["rrset"]
                click.echo(f"  Name: {rrset['name']}")
                click.echo(f"  Type: {rrset['type']}")
                click.echo(f"  TTL:  {rrset['ttl']}")
                click.echo(f"  Data: {', '.join(rrset['records'])}")
        except (json.JSONDecodeError, KeyError):
            pass
    else:
        try:
            error_data = response.json()
            detail = error_data.get("detail", error_data.get("message", response.text))
        except json.JSONDecodeError:
            detail = response.text
        format_error(f"API error ({response.status_code}): {detail}")
        sys.exit(1)


@click.group()
@click.version_option(version=__version__, prog_name="dns-cli")
@click.option(
    "--url",
    envvar="DNS_API_URL",
    default="http://localhost:8000",
    help="DNS API server URL",
)
@click.option(
    "--api-key",
    envvar="DNS_API_KEY",
    help="API key for authentication",
)
@click.option("--json", "output_json", is_flag=True, help="Output raw JSON response")
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.pass_context
def cli(
    ctx: click.Context, url: str, api_key: str | None, output_json: bool, verbose: bool
) -> None:
    """DNS API CLI - Manage DNS records via the DNS API server.

    Configure using environment variables:

        export DNS_API_URL=http://dns-api.example.com

        export DNS_API_KEY=your-api-key

    Or pass --url and --api-key options to each command.
    """
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["api_key"] = api_key
    ctx.obj["output_json"] = output_json
    ctx.obj["verbose"] = verbose


def get_client_from_context(ctx: click.Context) -> httpx.Client:
    """Create HTTP client from context."""
    url = ctx.obj["url"]
    api_key = ctx.obj["api_key"]

    if not api_key:
        format_error("API key required. Set DNS_API_KEY or use --api-key")
        sys.exit(1)

    return httpx.Client(
        base_url=url,
        headers={"X-API-Key": api_key},
        timeout=30.0,
    )


@cli.command()
@click.argument("zone")
@click.argument("name")
@click.argument("ttl", type=int)
@click.argument("record_type", metavar="TYPE")
@click.argument("records", nargs=-1, required=True)
@click.pass_context
def add(
    ctx: click.Context,
    zone: str,
    name: str,
    ttl: int,
    record_type: str,
    records: tuple[str, ...],
) -> None:
    """Add a new DNS record.

    Examples:

        dns-cli add example.com www 3600 A 192.0.2.1

        dns-cli add example.com www 300 A 192.0.2.1 192.0.2.2

        dns-cli add example.com mail 3600 MX "10 mail.example.com."
    """
    client = get_client_from_context(ctx)
    output_json = ctx.obj["output_json"]

    payload = {
        "name": name,
        "type": record_type.upper(),
        "ttl": ttl,
        "records": list(records),
    }

    if ctx.obj["verbose"]:
        click.echo(f"POST /zones/{zone}/rrsets")
        click.echo(f"Payload: {json.dumps(payload, indent=2)}")

    response = client.post(f"/zones/{quote(zone, safe='')}/rrsets", json=payload)
    handle_response(
        response,
        f"Added {name} {record_type.upper()} to {zone}",
        output_json,
    )


@cli.command()
@click.argument("zone")
@click.argument("name")
@click.argument("record_type", metavar="TYPE")
@click.argument("records", nargs=-1)
@click.pass_context
def delete(
    ctx: click.Context,
    zone: str,
    name: str,
    record_type: str,
    records: tuple[str, ...],
) -> None:
    """Delete a DNS record or specific record values.

    If no RECORDS are specified, deletes the entire RRset.
    If RECORDS are specified, only those specific values are deleted.

    Examples:

        dns-cli delete example.com old-host A

        dns-cli delete example.com www A 192.0.2.1
    """
    client = get_client_from_context(ctx)
    output_json = ctx.obj["output_json"]

    payload: dict = {
        "name": name,
        "type": record_type.upper(),
    }

    if records:
        payload["records"] = list(records)

    if ctx.obj["verbose"]:
        click.echo(f"DELETE /zones/{zone}/rrsets")
        click.echo(f"Payload: {json.dumps(payload, indent=2)}")

    response = client.request(
        "DELETE",
        f"/zones/{quote(zone, safe='')}/rrsets",
        json=payload,
    )

    if records:
        handle_response(
            response,
            f"Deleted {', '.join(records)} from {name} {record_type.upper()} in {zone}",
            output_json,
        )
    else:
        handle_response(
            response,
            f"Deleted {name} {record_type.upper()} from {zone}",
            output_json,
        )


@cli.command()
@click.argument("zone")
@click.argument("name")
@click.argument("ttl", type=int)
@click.argument("record_type", metavar="TYPE")
@click.argument("records", nargs=-1, required=True)
@click.pass_context
def replace(
    ctx: click.Context,
    zone: str,
    name: str,
    ttl: int,
    record_type: str,
    records: tuple[str, ...],
) -> None:
    """Replace an existing DNS record with new values.

    All existing record values are replaced with the new values.

    Examples:

        dns-cli replace example.com www 3600 A 192.0.2.10

        dns-cli replace example.com www 600 A 192.0.2.10 192.0.2.11
    """
    client = get_client_from_context(ctx)
    output_json = ctx.obj["output_json"]

    payload = {
        "name": name,
        "type": record_type.upper(),
        "ttl": ttl,
        "records": list(records),
    }

    if ctx.obj["verbose"]:
        click.echo(f"PUT /zones/{zone}/rrsets")
        click.echo(f"Payload: {json.dumps(payload, indent=2)}")

    response = client.put(f"/zones/{quote(zone, safe='')}/rrsets", json=payload)
    handle_response(
        response,
        f"Replaced {name} {record_type.upper()} in {zone}",
        output_json,
    )


@cli.command()
@click.option(
    "-f",
    "--file",
    "input_file",
    type=click.File("r"),
    help="Read nsupdate commands from file (default: stdin)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate input without executing",
)
@click.pass_context
def nsupdate(
    ctx: click.Context,
    input_file: click.utils.LazyFile | None,
    dry_run: bool,
) -> None:
    """Execute nsupdate-formatted commands.

    Reads nsupdate commands from a file or stdin and sends them to the API.
    Supports standard nsupdate syntax including zones, prerequisites, and updates.

    Examples:

        dns-cli nsupdate -f changes.txt

        echo "zone example.com
        update add test 300 A 192.0.2.50
        send" | dns-cli nsupdate

        cat << EOF | dns-cli nsupdate
        zone example.com
        prereq nxdomain newhost.example.com
        update add newhost 3600 A 192.0.2.100
        send
        EOF
    """
    client = get_client_from_context(ctx)
    output_json = ctx.obj["output_json"]

    # Read from file or stdin
    if input_file:
        nsupdate_text = input_file.read()
    else:
        if sys.stdin.isatty():
            click.echo("Enter nsupdate commands (Ctrl+D to finish):")
        nsupdate_text = sys.stdin.read()

    if not nsupdate_text.strip():
        format_error("No nsupdate commands provided")
        sys.exit(1)

    if ctx.obj["verbose"]:
        click.echo("POST /nsupdate")
        click.echo(f"Input:\n{nsupdate_text}")

    params = {"dry_run": "true"} if dry_run else {}
    response = client.post(
        "/nsupdate",
        content=nsupdate_text,
        headers={"Content-Type": "text/plain"},
        params=params,
    )

    if output_json:
        try:
            data = response.json()
            click.echo(json.dumps(data, indent=2))
        except json.JSONDecodeError:
            click.echo(response.text)
        return

    if response.is_success:
        try:
            data = response.json()
            total_success = data.get("total_success", 0)
            total_failed = data.get("total_failed", 0)

            if dry_run:
                format_success(f"Dry run: {len(data.get('transactions', []))} transaction(s) valid")
            elif total_failed == 0:
                format_success(f"{total_success} transaction(s) completed successfully")
            else:
                click.secho(
                    f"⚠ {total_success} succeeded, {total_failed} failed",
                    fg="yellow",
                )

            # Show transaction details
            for tx in data.get("transactions", []):
                status_icon = "✓" if tx["success"] else "✗"
                status_color = "green" if tx["success"] else "red"
                click.secho(
                    f"  {status_icon} {tx['zone']}: {tx['message']}",
                    fg=status_color,
                )

            if total_failed > 0:
                sys.exit(1)

        except (json.JSONDecodeError, KeyError):
            format_success("nsupdate commands executed")
    else:
        try:
            error_data = response.json()
            detail = error_data.get("detail", error_data.get("message", response.text))
        except json.JSONDecodeError:
            detail = response.text
        format_error(f"nsupdate failed: {detail}")
        sys.exit(1)


@cli.group(name="list")
def list_group() -> None:
    """List zones or records."""
    pass


@list_group.command("zones")
@click.pass_context
def list_zones(ctx: click.Context) -> None:
    """List all available zones.

    Example:

        dns-cli list zones
    """
    client = get_client_from_context(ctx)
    output_json = ctx.obj["output_json"]

    if ctx.obj["verbose"]:
        click.echo("GET /zones")

    response = client.get("/zones")

    if output_json:
        try:
            data = response.json()
            click.echo(json.dumps(data, indent=2))
        except json.JSONDecodeError:
            click.echo(response.text)
        return

    if response.is_success:
        try:
            data = response.json()
            zones = data.get("zones", [])
            if not zones:
                click.echo("No zones found")
                return

            click.echo(f"Found {len(zones)} zone(s):\n")
            for zone in zones:
                click.secho(f"  {zone['zone']}", fg="cyan")
                click.echo(f"    Serial: {zone['serial']}")
                click.echo(f"    RRsets: {zone['rrset_count']}")
        except (json.JSONDecodeError, KeyError) as e:
            format_error(f"Failed to parse response: {e}")
            sys.exit(1)
    else:
        try:
            error_data = response.json()
            detail = error_data.get("detail", response.text)
        except json.JSONDecodeError:
            detail = response.text
        format_error(f"Failed to list zones: {detail}")
        sys.exit(1)


@list_group.command("records")
@click.argument("zone")
@click.option("--type", "record_type", help="Filter by record type")
@click.option("--name", help="Filter by record name")
@click.pass_context
def list_records(
    ctx: click.Context,
    zone: str,
    record_type: str | None,
    name: str | None,
) -> None:
    """List records in a zone.

    Examples:

        dns-cli list records example.com

        dns-cli list records example.com --type A

        dns-cli list records example.com --name www
    """
    client = get_client_from_context(ctx)
    output_json = ctx.obj["output_json"]

    params = {}
    if record_type:
        params["type"] = record_type.upper()
    if name:
        params["name"] = name

    if ctx.obj["verbose"]:
        click.echo(f"GET /zones/{zone}/rrsets")

    response = client.get(f"/zones/{quote(zone, safe='')}/rrsets", params=params)

    if output_json:
        try:
            data = response.json()
            click.echo(json.dumps(data, indent=2))
        except json.JSONDecodeError:
            click.echo(response.text)
        return

    if response.is_success:
        try:
            records = response.json()
            if not records:
                click.echo(f"No records found in {zone}")
                return

            click.echo(f"Found {len(records)} record(s) in {zone}:\n")

            # Group by name for nicer display
            for record in records:
                type_colors = {
                    "A": "green",
                    "AAAA": "blue",
                    "CNAME": "yellow",
                    "MX": "magenta",
                    "TXT": "cyan",
                    "NS": "white",
                    "SOA": "white",
                }
                type_color = type_colors.get(record["type"], "white")

                click.echo(f"  {record['name']}")
                click.secho(f"    Type: {record['type']}", fg=type_color)
                click.echo(f"    TTL:  {record['ttl']}")
                for rdata in record["records"]:
                    click.echo(f"    Data: {rdata}")
                click.echo()

        except (json.JSONDecodeError, KeyError) as e:
            format_error(f"Failed to parse response: {e}")
            sys.exit(1)
    else:
        try:
            error_data = response.json()
            detail = error_data.get("detail", response.text)
        except json.JSONDecodeError:
            detail = response.text
        format_error(f"Failed to list records: {detail}")
        sys.exit(1)


@cli.command()
@click.argument("zone")
@click.argument("name")
@click.argument("record_type", metavar="TYPE")
@click.pass_context
def get(
    ctx: click.Context,
    zone: str,
    name: str,
    record_type: str,
) -> None:
    """Get a specific DNS record.

    Example:

        dns-cli get example.com www A
    """
    client = get_client_from_context(ctx)
    output_json = ctx.obj["output_json"]

    if ctx.obj["verbose"]:
        click.echo(f"GET /zones/{zone}/rrsets/{name}/{record_type.upper()}")

    response = client.get(
        f"/zones/{quote(zone, safe='')}/rrsets/{quote(name, safe='')}/{record_type.upper()}"
    )

    if output_json:
        try:
            data = response.json()
            click.echo(json.dumps(data, indent=2))
        except json.JSONDecodeError:
            click.echo(response.text)
        return

    if response.is_success:
        try:
            record = response.json()
            click.echo(f"  Name: {record['name']}")
            click.echo(f"  Type: {record['type']}")
            click.echo(f"  TTL:  {record['ttl']}")
            for rdata in record["records"]:
                click.echo(f"  Data: {rdata}")
        except (json.JSONDecodeError, KeyError) as e:
            format_error(f"Failed to parse response: {e}")
            sys.exit(1)
    else:
        try:
            error_data = response.json()
            detail = error_data.get("detail", response.text)
        except json.JSONDecodeError:
            detail = response.text
        format_error(f"Record not found: {detail}")
        sys.exit(1)


@cli.command()
@click.argument("zone")
@click.option(
    "-o",
    "--output-file",
    type=click.Path(),
    help="Write zone file to this path (default: stdout)",
)
@click.pass_context
def export(
    ctx: click.Context,
    zone: str,
    output_file: str | None,
) -> None:
    """Export a zone as a BIND master format zone file.

    Writes the zone file to stdout by default, or to a file if --output-file is specified.

    Examples:

        dns-cli export example.com

        dns-cli export example.com -o example.com.zone

        dns-cli export example.com > backup.zone
    """
    client = get_client_from_context(ctx)

    if ctx.obj["verbose"]:
        click.echo(f"GET /zones/{zone}/export", err=True)

    response = client.get(f"/zones/{quote(zone, safe='')}/export")

    if response.is_success:
        zone_content = response.text

        if output_file:
            # Write to file
            try:
                with open(output_file, "w") as f:
                    f.write(zone_content)
                format_success(f"Zone exported to {output_file}")
            except OSError as e:
                format_error(f"Failed to write file: {e}")
                sys.exit(1)
        else:
            # Write to stdout
            click.echo(zone_content, nl=False)
    else:
        try:
            error_data = response.json()
            detail = error_data.get("detail", response.text)
        except json.JSONDecodeError:
            detail = response.text
        format_error(f"Export failed: {detail}")
        sys.exit(1)


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
