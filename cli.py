"""
TechTap — CLI Application
Interactive terminal interface for NFC tag programming.
"""

import sys
import time
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

from techtap.ndef_encoder import (
    encode_url, encode_text, encode_vcard, encode_phone,
    encode_email, encode_social, encode_wifi,
    get_social_platforms, get_ndef_payload_info, hex_to_bytes
)
from techtap.rfid_reader import RFIDReader, list_serial_ports
from techtap.database import TagDatabase
from techtap.utils import (
    get_banner, get_version, load_config, save_config,
    validate_url, validate_email, validate_phone, sanitize_phone,
    check_tag_capacity, format_uid, format_bytes_size,
    format_timestamp, logger, NTAG_SPECS
)

# Phone NFC reader (lazy import — only loaded when needed)
def _get_phone_reader_class():
    """Import PhoneNFCReader on demand."""
    try:
        from techtap.phone_nfc import PhoneNFCReader
        return PhoneNFCReader
    except ImportError as e:
        logger.error(f"Phone NFC module not available: {e}")
        return None


# ── Console Setup ──────────────────────────────────────────────────────────

console = Console()


# ── Display Helpers ────────────────────────────────────────────────────────

def show_banner():
    """Display TechTap banner."""
    banner_text = Text(get_banner(), style="bold cyan")
    console.print(banner_text)
    console.print(f"  {get_version()}  |  Smart Identity via Tap\n",
                  style="dim white")


def show_menu():
    """Display main menu."""
    table = Table(
        title="[bold cyan]TECHTAP CLI[/bold cyan]",
        box=box.ROUNDED,
        border_style="cyan",
        title_style="bold white",
        show_header=False,
        padding=(0, 2),
    )
    table.add_column("Option", style="bold yellow", width=4)
    table.add_column("Action", style="white")

    menu_items = [
        ("1", "Write Website URL"),
        ("2", "Write Contact (vCard)"),
        ("3", "Write Phone Number"),
        ("4", "Write Email Address"),
        ("5", "Write Social Media Link"),
        ("6", "Write Custom Text"),
        ("7", "Write WiFi Credentials"),
        ("8", "Erase Tag"),
        ("9", "Lock Tag"),
        ("10", "Read Tag"),
        ("11", "Tag Info"),
        ("12", "Bulk Write Mode"),
        ("13", "Write History"),
        ("14", "Settings"),
        ("0", "Exit"),
    ]

    for opt, action in menu_items:
        table.add_row(f"[{opt}]", action)

    console.print(table)
    console.print()


def show_success(msg: str):
    console.print(f"\n[bold green]✓ SUCCESS:[/bold green] {msg}\n")


def show_error(msg: str):
    console.print(f"\n[bold red]✗ ERROR:[/bold red] {msg}\n")


def show_warning(msg: str):
    console.print(f"\n[bold yellow]⚠ WARNING:[/bold yellow] {msg}\n")


def show_info(msg: str):
    console.print(f"[dim cyan]ℹ {msg}[/dim cyan]")


def show_tap_prompt():
    """Called when Arduino is waiting for card tap."""
    console.print(
        "\n[bold blink yellow]📱 TAP YOUR NFC CARD NOW...[/bold blink yellow]\n"
    )


def show_data_preview(record_type: str, data: bytes, summary: str = ""):
    """Show a preview of data before writing."""
    panel_content = (
        f"[white]Type:[/white]  [cyan]{record_type}[/cyan]\n"
        f"[white]Size:[/white]  [cyan]{len(data)} bytes[/cyan]\n"
    )
    if summary:
        panel_content += f"[white]Data:[/white]  [green]{summary}[/green]\n"

    # Capacity check for common NTAG types
    for tag_type in ["NTAG213", "NTAG215", "NTAG216"]:
        cap = check_tag_capacity(tag_type, len(data))
        status = "[green]✓[/green]" if cap["fits"] else "[red]✗[/red]"
        panel_content += (
            f"  {status} {tag_type}: {cap['usage_percent']}% "
            f"({cap['remaining']} bytes free)\n"
        )

    console.print(Panel(
        panel_content,
        title="[bold]Data Preview[/bold]",
        border_style="blue",
        box=box.ROUNDED
    ))


def show_tag_summary(info: dict):
    """Display formatted tag info."""
    table = Table(
        title="[bold cyan]── TAG INFO ──[/bold cyan]",
        box=box.SIMPLE_HEAVY,
        border_style="cyan",
        show_header=False,
        padding=(0, 1),
    )
    table.add_column("Field", style="bold white", width=12)
    table.add_column("Value", style="green")

    for key, value in info.items():
        display_key = key.replace("_", " ").title()
        table.add_row(display_key, str(value))

    console.print(table)


# ── Write Operations ───────────────────────────────────────────────────────

def do_write(reader: RFIDReader, db: TagDatabase,
             ndef_data: bytes, record_type: str, content_summary: str):
    """Central write handler with preview, confirmation, and logging."""

    show_data_preview(record_type, ndef_data, content_summary)

    if not Confirm.ask("[yellow]Proceed with write?[/yellow]", default=True):
        show_info("Write cancelled.")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Sending data to Arduino...", total=None)

        result = reader.write_ndef(
            ndef_data,
            record_type=record_type,
            on_tap_prompt=show_tap_prompt
        )

    if result.get("success"):
        uid = result.get("uid", "Unknown")
        verified = "✓ Verified" if result.get("verified") else ""
        show_success(
            f"Written to tag [bold]{uid}[/bold] "
            f"({result.get('attempts', 1)} attempt(s)) {verified}"
        )
        db.log_write(
            uid=uid, operation="WRITE", record_type=record_type,
            content_summary=content_summary, data_size=len(ndef_data),
            success=True
        )
    else:
        error = result.get("error", "Unknown error")
        show_error(error)
        db.log_write(
            uid=result.get("uid", ""), operation="WRITE",
            record_type=record_type, content_summary=content_summary,
            data_size=len(ndef_data), success=False, error_message=error
        )

        if result.get("duplicate"):
            if Confirm.ask("[yellow]Tag has existing data. Overwrite?[/yellow]"):
                # Erase first, then rewrite
                erase_result = reader.erase_tag(on_tap_prompt=show_tap_prompt)
                if erase_result.get("success"):
                    show_info("Tag erased. Rewriting...")
                    do_write(reader, db, ndef_data, record_type, content_summary)


# ── Menu Handlers ──────────────────────────────────────────────────────────

def handle_write_url(reader: RFIDReader, db: TagDatabase):
    """Write a website URL."""
    console.print("\n[bold cyan]── Write Website URL ──[/bold cyan]\n")

    url = Prompt.ask("[white]Enter URL[/white]",
                     default="https://")

    if not validate_url(url):
        show_error("Invalid URL format. Must start with http:// or https://")
        return

    ndef_data = encode_url(url)
    do_write(reader, db, ndef_data, "URL", url)


def handle_write_vcard(reader: RFIDReader, db: TagDatabase):
    """Write a vCard contact."""
    console.print("\n[bold cyan]── Write Contact Card ──[/bold cyan]\n")

    name = Prompt.ask("[white]Full Name[/white]")
    if not name.strip():
        show_error("Name is required.")
        return

    phone = Prompt.ask("[white]Phone (optional)[/white]", default="")
    email = Prompt.ask("[white]Email (optional)[/white]", default="")
    org = Prompt.ask("[white]Organization (optional)[/white]", default="")
    title = Prompt.ask("[white]Job Title (optional)[/white]", default="")
    url = Prompt.ask("[white]Website (optional)[/white]", default="")

    if email and not validate_email(email):
        show_warning("Email format looks invalid, but proceeding anyway.")

    if phone and not validate_phone(phone):
        show_warning("Phone format looks invalid, but proceeding anyway.")

    ndef_data = encode_vcard(
        name=name, phone=sanitize_phone(phone) if phone else "",
        email=email, org=org, title=title, url=url
    )
    summary = f"{name}"
    if phone:
        summary += f" | {phone}"
    if email:
        summary += f" | {email}"

    do_write(reader, db, ndef_data, "vCard", summary)


def handle_write_phone(reader: RFIDReader, db: TagDatabase):
    """Write a phone number."""
    console.print("\n[bold cyan]── Write Phone Number ──[/bold cyan]\n")

    phone = Prompt.ask("[white]Phone Number (with country code)[/white]")

    if not validate_phone(phone):
        show_error("Invalid phone number. Use format: +639123456789")
        return

    clean_phone = sanitize_phone(phone)
    ndef_data = encode_phone(clean_phone)
    do_write(reader, db, ndef_data, "Phone", clean_phone)


def handle_write_email(reader: RFIDReader, db: TagDatabase):
    """Write an email address."""
    console.print("\n[bold cyan]── Write Email Address ──[/bold cyan]\n")

    email = Prompt.ask("[white]Email Address[/white]")

    if not validate_email(email):
        show_error("Invalid email format.")
        return

    subject = Prompt.ask("[white]Subject (optional)[/white]", default="")
    body = Prompt.ask("[white]Body (optional)[/white]", default="")

    ndef_data = encode_email(email, subject=subject, body=body)
    do_write(reader, db, ndef_data, "Email", email)


def handle_write_social(reader: RFIDReader, db: TagDatabase):
    """Write a social media link."""
    console.print("\n[bold cyan]── Write Social Media ──[/bold cyan]\n")

    platforms = get_social_platforms()
    console.print("[dim]Supported platforms:[/dim]")
    # Display in columns
    cols = 4
    for i in range(0, len(platforms), cols):
        row = "  ".join(f"[cyan]{p:<14}[/cyan]" for p in platforms[i:i + cols])
        console.print(f"  {row}")
    console.print()

    platform = Prompt.ask("[white]Platform[/white]").strip().lower()
    username = Prompt.ask("[white]Username[/white]").strip()

    if not username:
        show_error("Username is required.")
        return

    try:
        ndef_data = encode_social(platform, username)
        from techtap.ndef_encoder import SOCIAL_PLATFORMS
        url = SOCIAL_PLATFORMS.get(platform, "").format(username.lstrip("@"))
        do_write(reader, db, ndef_data, "Social", f"{platform}: @{username} → {url}")
    except ValueError as e:
        show_error(str(e))


def handle_write_text(reader: RFIDReader, db: TagDatabase):
    """Write custom text."""
    console.print("\n[bold cyan]── Write Custom Text ──[/bold cyan]\n")

    text = Prompt.ask("[white]Enter text[/white]")
    if not text.strip():
        show_error("Text cannot be empty.")
        return

    ndef_data = encode_text(text)
    do_write(reader, db, ndef_data, "Text", text[:80])


def handle_write_wifi(reader: RFIDReader, db: TagDatabase):
    """Write WiFi credentials."""
    console.print("\n[bold cyan]── Write WiFi Credentials ──[/bold cyan]\n")

    ssid = Prompt.ask("[white]WiFi Network Name (SSID)[/white]")
    if not ssid.strip():
        show_error("SSID is required.")
        return

    auth = Prompt.ask(
        "[white]Authentication[/white]",
        choices=["WPA2", "WPA", "OPEN"],
        default="WPA2"
    )

    password = ""
    if auth != "OPEN":
        password = Prompt.ask("[white]Password[/white]")

    ndef_data = encode_wifi(ssid, password=password, auth_type=auth)
    do_write(reader, db, ndef_data, "WiFi", f"SSID: {ssid} ({auth})")


def handle_erase(reader: RFIDReader, db: TagDatabase):
    """Erase a tag."""
    console.print("\n[bold cyan]── Erase Tag ──[/bold cyan]\n")

    if not Confirm.ask("[yellow]This will erase ALL data on the tag. Continue?[/yellow]"):
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console, transient=True,
    ) as progress:
        progress.add_task("Preparing erase...", total=None)
        result = reader.erase_tag(on_tap_prompt=show_tap_prompt)

    if result.get("success"):
        uid = result.get("uid", "Unknown")
        show_success(f"Tag [bold]{uid}[/bold] erased successfully.")
        db.log_write(uid=uid, operation="ERASE", record_type="ERASE",
                     content_summary="Tag erased", success=True)
    else:
        show_error(result.get("error", "Erase failed"))


def handle_lock(reader: RFIDReader, db: TagDatabase):
    """Lock a tag."""
    console.print("\n[bold cyan]── Lock Tag ──[/bold cyan]\n")

    console.print(
        "[bold red]⚠ WARNING: Locking a tag is PERMANENT on most NTAG chips.[/bold red]\n"
        "[dim]The tag will become READ-ONLY. This cannot be undone.[/dim]\n"
    )

    if not Confirm.ask("[red]Are you absolutely sure?[/red]", default=False):
        return

    if not Confirm.ask("[red]Type 'yes' — this is irreversible. Confirm?[/red]",
                       default=False):
        return

    result = reader.lock_tag(on_tap_prompt=show_tap_prompt)

    if result.get("success"):
        uid = result.get("uid", "Unknown")
        show_success(f"Tag [bold]{uid}[/bold] locked permanently.")
        db.log_write(uid=uid, operation="LOCK", record_type="LOCK",
                     content_summary="Tag locked", success=True)
        db.set_tag_locked(uid, True)
    else:
        show_error(result.get("error", "Lock failed"))


def handle_read(reader, db: TagDatabase):
    """Read tag contents."""
    console.print("\n[bold cyan]── Read Tag ──[/bold cyan]\n")

    result = reader.read_tag(on_tap_prompt=show_tap_prompt)

    if result.get("success"):
        raw_data = result.get("data", "")
        raw_lines = result.get("raw", [])
        records = result.get("records", [])

        # Phone reader returns structured records directly
        if records:
            display = {}
            for i, rec in enumerate(records):
                rtype = rec.get("type", "unknown").upper()
                content = rec.get("content", "")
                label = f"Record {i+1}" if len(records) > 1 else "Type"
                display[label] = rtype
                display["Content"] = content[:200]
                if rec.get("size"):
                    display["Size"] = f"{rec['size']} bytes"
            show_tag_summary(display)
            return

        # Arduino reader: try to parse NDEF from hex data
        if raw_data:
            try:
                data_bytes = hex_to_bytes(raw_data)
                info = get_ndef_payload_info(data_bytes)
                if info:
                    show_tag_summary({
                        "Type": info.get("content_type", "Unknown"),
                        "Content": info.get("content", ""),
                        "Size": f"{len(data_bytes)} bytes",
                    })
                    return
            except (ValueError, Exception):
                pass

        # Fallback: display raw lines
        console.print(Panel(
            "\n".join(raw_lines) if raw_lines else raw_data or "No data",
            title="[bold]Tag Contents[/bold]",
            border_style="green",
            box=box.ROUNDED
        ))
    else:
        show_error(result.get("error", "Read failed"))


def handle_tag_info(reader: RFIDReader, db: TagDatabase):
    """Get detailed tag info."""
    console.print("\n[bold cyan]── Tag Info ──[/bold cyan]\n")

    result = reader.get_tag_info(on_tap_prompt=show_tap_prompt)

    if result.get("success"):
        info = result.get("info", {})
        uid = info.get("uid", "Unknown")

        display = {
            "UID": format_uid(bytes.fromhex(uid)) if len(uid) >= 8 else uid,
            "Type": info.get("type", "Unknown"),
            "Memory": f"{info.get('size', '?')} bytes",
            "Writable": "Yes" if info.get("locked", "1") == "0" else "No",
            "Locked": "Yes" if info.get("locked", "0") == "1" else "No",
        }

        # Check DB for write history
        db_tag = db.get_tag(uid)
        if db_tag:
            display["Writes"] = str(db_tag.get("write_count", 0))
            display["Last Written"] = db_tag.get("last_written", "Never")

        show_tag_summary(display)
    else:
        show_error(result.get("error", "Could not read tag info"))


def handle_bulk_write(reader: RFIDReader, db: TagDatabase):
    """Bulk write mode — same data to multiple tags."""
    console.print("\n[bold cyan]── Bulk Write Mode ──[/bold cyan]\n")
    console.print("[dim]Write the same data to multiple cards in sequence.[/dim]\n")

    record_type = Prompt.ask(
        "[white]Record type[/white]",
        choices=["url", "text", "vcard", "phone", "email", "social", "wifi"],
        default="url"
    )

    # Collect data based on type
    ndef_data = None
    content_summary = ""

    if record_type == "url":
        url = Prompt.ask("[white]URL[/white]", default="https://")
        if not validate_url(url):
            show_error("Invalid URL")
            return
        ndef_data = encode_url(url)
        content_summary = url

    elif record_type == "text":
        text = Prompt.ask("[white]Text[/white]")
        ndef_data = encode_text(text)
        content_summary = text[:80]

    elif record_type == "phone":
        phone = Prompt.ask("[white]Phone[/white]")
        if not validate_phone(phone):
            show_error("Invalid phone")
            return
        ndef_data = encode_phone(sanitize_phone(phone))
        content_summary = phone

    elif record_type == "email":
        email = Prompt.ask("[white]Email[/white]")
        if not validate_email(email):
            show_error("Invalid email")
            return
        ndef_data = encode_email(email)
        content_summary = email

    elif record_type == "social":
        platform = Prompt.ask("[white]Platform[/white]").lower()
        username = Prompt.ask("[white]Username[/white]")
        try:
            ndef_data = encode_social(platform, username)
            content_summary = f"{platform}/@{username}"
        except ValueError as e:
            show_error(str(e))
            return

    elif record_type == "vcard":
        name = Prompt.ask("[white]Name[/white]")
        phone = Prompt.ask("[white]Phone[/white]", default="")
        email = Prompt.ask("[white]Email[/white]", default="")
        ndef_data = encode_vcard(name, phone=phone, email=email)
        content_summary = name

    elif record_type == "wifi":
        ssid = Prompt.ask("[white]SSID[/white]")
        password = Prompt.ask("[white]Password[/white]", default="")
        ndef_data = encode_wifi(ssid, password=password)
        content_summary = f"WiFi: {ssid}"

    if ndef_data is None:
        return

    show_data_preview(record_type.upper(), ndef_data, content_summary)

    if not Confirm.ask("[yellow]Start bulk write session?[/yellow]", default=True):
        return

    session_id = db.start_bulk_session(record_type, content_summary)
    written = 0
    failed = 0

    console.print(
        "\n[bold green]Bulk mode active.[/bold green] "
        "[dim]Press Ctrl+C to stop.[/dim]\n"
    )

    try:
        while True:
            console.print(
                f"[cyan]Card #{written + failed + 1}[/cyan] — "
                f"[green]{written} written[/green] | "
                f"[red]{failed} failed[/red]"
            )

            result = reader.write_ndef(
                ndef_data,
                record_type=record_type.upper(),
                on_tap_prompt=show_tap_prompt
            )

            if result.get("success"):
                written += 1
                uid = result.get("uid", "?")
                show_success(f"Card #{written} done → {uid}")
                db.log_write(
                    uid=uid, operation="BULK_WRITE",
                    record_type=record_type.upper(),
                    content_summary=content_summary,
                    data_size=len(ndef_data), success=True
                )
                db.update_bulk_session(session_id, written=1)
            else:
                failed += 1
                show_error(f"Card failed: {result.get('error')}")
                db.update_bulk_session(session_id, failed=1)

            time.sleep(0.5)

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Bulk session ended.[/bold yellow]")

    summary = db.end_bulk_session(session_id)
    console.print(Panel(
        f"[green]Written: {written}[/green]\n"
        f"[red]Failed:  {failed}[/red]\n"
        f"[white]Total:   {written + failed}[/white]",
        title="[bold]Bulk Session Summary[/bold]",
        border_style="cyan",
        box=box.ROUNDED
    ))


def handle_history(reader: RFIDReader, db: TagDatabase):
    """Show write history."""
    console.print("\n[bold cyan]── Write History ──[/bold cyan]\n")

    history = db.get_write_history(limit=20)
    if not history:
        show_info("No write history yet.")
        return

    table = Table(
        box=box.SIMPLE,
        border_style="dim",
        title="Recent Operations",
        title_style="bold white"
    )
    table.add_column("Time", style="dim", width=19)
    table.add_column("UID", style="cyan", width=12)
    table.add_column("Op", style="yellow", width=10)
    table.add_column("Type", style="white", width=8)
    table.add_column("Content", style="green", max_width=30)
    table.add_column("Status", width=4)

    for entry in history:
        status = "[green]✓[/green]" if entry.get("success") else "[red]✗[/red]"
        table.add_row(
            entry.get("timestamp", "")[:19],
            entry.get("uid", "")[:12],
            entry.get("operation", ""),
            entry.get("record_type", ""),
            (entry.get("content_summary", "") or "")[:30],
            status
        )

    console.print(table)

    # Stats
    stats = db.get_stats()
    console.print(
        f"\n[dim]Tags: {stats['total_tags']} | "
        f"Writes: {stats['total_writes']} | "
        f"Success Rate: {stats['success_rate']}[/dim]"
    )


def handle_settings(reader, db: TagDatabase):
    """Settings menu."""
    console.print("\n[bold cyan]── Settings ──[/bold cyan]\n")

    config = load_config()
    reader_mode = config.get("reader_mode", "arduino")

    table = Table(box=box.SIMPLE, show_header=False, border_style="dim")
    table.add_column("Setting", style="white", width=20)
    table.add_column("Value", style="cyan")

    table.add_row("Reader Mode", reader_mode.upper())
    table.add_row("Serial Port", config["serial"]["port"])
    table.add_row("Baud Rate", str(config["serial"]["baudrate"]))
    table.add_row("Module", config["module"])
    table.add_row("Verify After Write", str(config["verify_after_write"]))
    table.add_row("Max Retries", str(config["max_retries"]))
    table.add_row("Log Writes", str(config["log_writes"]))

    console.print(table)

    if reader_mode == "arduino":
        # List available ports
        console.print("\n[bold]Available Serial Ports:[/bold]")
        ports = list_serial_ports()
        if ports:
            for p in ports:
                console.print(
                    f"  [cyan]{p['port']}[/cyan] — {p['description']}"
                )
        else:
            console.print("  [dim]No serial ports detected.[/dim]")
    else:
        console.print("\n[dim]Using phone NFC via USB/ADB.[/dim]")

    console.print()

    # Switch reader mode
    if Confirm.ask(
        f"[yellow]Switch reader mode? (currently: {reader_mode})[/yellow]",
        default=False
    ):
        new_mode = Prompt.ask(
            "[white]Reader mode[/white]",
            choices=["arduino", "phone"],
            default="phone" if reader_mode == "arduino" else "arduino"
        )
        config["reader_mode"] = new_mode
        save_config(config)
        show_success(
            f"Reader mode set to {new_mode.upper()}.\n"
            "  Restart TechTap to use the new reader."
        )

    if reader_mode == "arduino":
        if Confirm.ask("[yellow]Change serial port?[/yellow]", default=False):
            port = Prompt.ask(
                "[white]Port[/white]",
                default=config["serial"]["port"]
            )
            config["serial"]["port"] = port
            save_config(config)
            show_success(f"Port set to {port}. Reconnecting...")
            reader.port = port
            reader.reconnect()

    if Confirm.ask("[yellow]Change other settings?[/yellow]", default=False):
        config["verify_after_write"] = Confirm.ask(
            "Verify after write?",
            default=config["verify_after_write"]
        )
        config["max_retries"] = int(Prompt.ask(
            "Max retries", default=str(config["max_retries"])
        ))
        save_config(config)
        show_success("Settings saved.")


# ── Main Application Loop ─────────────────────────────────────────────────

def connect_reader():
    """Initialize and connect to RFID reader (Arduino or Phone)."""
    config = load_config()
    reader_mode = config.get("reader_mode", "arduino")

    if reader_mode == "phone":
        # Phone NFC mode
        PhoneNFCReader = _get_phone_reader_class()
        if PhoneNFCReader is None:
            show_error(
                "Phone NFC module not available.\n"
                "  Install websockets:  pip install websockets\n"
                "  Falling back to Arduino mode."
            )
            reader_mode = "arduino"
        else:
            console.print(
                "\n[bold cyan]📱 Phone NFC Mode[/bold cyan]\n"
                "[dim]Using your Android phone as the NFC reader via USB.[/dim]\n"
            )
            reader = PhoneNFCReader()

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console, transient=True,
            ) as progress:
                progress.add_task(
                    "Setting up phone NFC bridge (ADB → WebSocket → Chrome)...",
                    total=None
                )
                connected = reader.connect()

            if connected and reader.connected:
                show_success(
                    "Phone NFC bridge active!\n"
                    "  Make sure you tapped 'Enable NFC Scanner' on your phone."
                )
            elif connected:
                show_warning(
                    "Servers started but phone hasn't connected yet.\n"
                    "  On your phone: open Chrome and go to:\n"
                    "  http://localhost:8766/phone_nfc.html\n"
                    "  Then tap 'Enable NFC Scanner'."
                )
            else:
                show_error(
                    "Could not set up phone NFC bridge.\n"
                    "  Check:\n"
                    "  1. ADB is installed (Android SDK Platform Tools)\n"
                    "  2. USB Debugging is enabled on your phone\n"
                    "  3. Phone is connected via USB\n"
                    "  4. You accepted 'Allow USB debugging' on phone"
                )
            return reader

    # Arduino mode (default)
    reader = RFIDReader()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console, transient=True,
    ) as progress:
        progress.add_task("Connecting to Arduino...", total=None)
        connected = reader.connect()

    if connected:
        show_success(f"Connected to {reader.port}")
    else:
        show_warning(
            "Could not connect to Arduino.\n"
            "  Make sure your Arduino is plugged in and running TechTap firmware.\n"
            "  You can change the port in Settings (option 14).\n"
            "  Running in offline mode — encoding features still work."
        )

    return reader


def main():
    """Main entry point."""
    try:
        show_banner()
        reader = connect_reader()
        db = TagDatabase()

        HANDLERS = {
            "1": handle_write_url,
            "2": handle_write_vcard,
            "3": handle_write_phone,
            "4": handle_write_email,
            "5": handle_write_social,
            "6": handle_write_text,
            "7": handle_write_wifi,
            "8": handle_erase,
            "9": handle_lock,
            "10": handle_read,
            "11": handle_tag_info,
            "12": handle_bulk_write,
            "13": handle_history,
            "14": handle_settings,
        }

        while True:
            try:
                show_menu()
                choice = Prompt.ask(
                    "[bold white]Select option[/bold white]",
                    choices=[str(i) for i in range(15)],
                    show_choices=False
                )

                if choice == "0":
                    console.print("\n[bold cyan]Goodbye! Keep tapping. 🤙[/bold cyan]\n")
                    break

                handler = HANDLERS.get(choice)
                if handler:
                    handler(reader, db)
                else:
                    show_error("Invalid option.")

            except KeyboardInterrupt:
                console.print("\n[dim]Use option 0 to exit properly.[/dim]")
                continue

    except Exception as e:
        logger.exception("Fatal error")
        console.print(f"\n[bold red]Fatal error: {e}[/bold red]")
        sys.exit(1)

    finally:
        try:
            reader.disconnect()
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
