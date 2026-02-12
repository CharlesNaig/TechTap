"""
TechTap — Phone NFC Bridge
Uses your Android phone's NFC reader via ADB + WebSocket + Web NFC API.

How it works:
  1. ADB reverse-forwards ports from phone → PC over USB
  2. Python runs a WebSocket server + HTTP file server
  3. Chrome on your phone opens a local web page with Web NFC controls
  4. Commands flow:  CLI → WebSocket → Phone Chrome → Web NFC API → Tag

Requirements:
  - Android phone with NFC (e.g., Poco M5s)
  - USB cable connected to PC
  - USB Debugging enabled on phone (Developer Options)
  - ADB installed on PC (Android SDK Platform Tools)
  - Google Chrome on phone
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import http.server
import functools
from pathlib import Path
from typing import Optional, Callable

try:
    import websockets
    import websockets.server
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from techtap.utils import load_config, logger, APP_DIR, PROJECT_ROOT
from techtap.ndef_encoder import get_ndef_payload_info, bytes_to_hex


# ── Ports ──────────────────────────────────────────────────────────────────

WS_PORT = 8765
HTTP_PORT = 8766
STATIC_DIR = APP_DIR / "static"


# ── ADB Helpers ────────────────────────────────────────────────────────────

def check_adb_available() -> bool:
    """Check if ADB is available in PATH or common locations."""
    if shutil.which("adb"):
        return True

    is_windows = sys.platform == "win32"
    adb_name = "adb.exe" if is_windows else "adb"

    # Common install paths (including project-local platform-tools)
    common_paths = [str(PROJECT_ROOT / "platform-tools")]

    if is_windows:
        common_paths += [
            os.path.expanduser(r"~\AppData\Local\Android\Sdk\platform-tools"),
            r"C:\platform-tools",
            r"C:\Android\platform-tools",
            os.path.expanduser(r"~\scoop\apps\adb\current"),
        ]
    else:
        common_paths += [
            os.path.expanduser("~/Android/Sdk/platform-tools"),
            os.path.expanduser("~/android-sdk/platform-tools"),
            "/usr/lib/android-sdk/platform-tools",
            "/opt/android-sdk/platform-tools",
            "/usr/local/bin",
            "/snap/bin",
        ]

    for p in common_paths:
        adb_path = os.path.join(p, adb_name)
        if os.path.exists(adb_path):
            os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
            logger.info(f"Found ADB at {p}")
            return True
    return False


def get_adb_devices() -> list[str]:
    """Return list of connected ADB device serial numbers."""
    try:
        result = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.strip().split("\n")
        devices = []
        for line in lines[1:]:
            parts = line.strip().split("\t")
            if len(parts) == 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices
    except Exception:
        return []


def setup_adb_reverse(ws_port: int = WS_PORT,
                      http_port: int = HTTP_PORT) -> bool:
    """Set up ADB reverse port forwarding (phone → PC)."""
    try:
        for port in [ws_port, http_port]:
            result = subprocess.run(
                ["adb", "reverse", f"tcp:{port}", f"tcp:{port}"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                logger.error(f"ADB reverse failed for port {port}: {result.stderr}")
                return False

        logger.info(f"ADB reverse ports: {ws_port}, {http_port}")
        return True
    except Exception as e:
        logger.error(f"ADB reverse error: {e}")
        return False


def open_chrome_on_phone(url: str) -> bool:
    """Open a URL in Chrome on the connected Android phone."""
    try:
        result = subprocess.run(
            [
                "adb", "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", url,
                "-n", "com.android.chrome/com.google.android.apps.chrome.Main"
            ],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            logger.info(f"Opened {url} on phone Chrome.")
            return True

        # Fallback: try generic browser intent
        subprocess.run(
            ["adb", "shell", "am", "start", "-a",
             "android.intent.action.VIEW", "-d", url],
            capture_output=True, text=True, timeout=10
        )
        return True
    except Exception as e:
        logger.warning(f"Could not open browser on phone: {e}")
        return False


def cleanup_adb_reverse():
    """Remove all ADB reverse port forwards."""
    try:
        subprocess.run(
            ["adb", "reverse", "--remove-all"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


# ── Silent HTTP Handler ───────────────────────────────────────────────────

class QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that doesn't log to console."""

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format, *args):
        logger.debug(f"HTTP: {format % args}")


# ── PhoneNFCReader ─────────────────────────────────────────────────────────

class PhoneNFCReader:
    """
    NFC reader using your Android phone via ADB + Web NFC API.

    Drop-in replacement for RFIDReader — same public interface so the
    CLI works without changes.

    Protocol over WebSocket (JSON):
      PC → Phone:  {"cmd": "write|read|erase|lock|info", ...params}
      Phone → PC:  {"event": "write_ok|read_ok|erase_ok|...", ...data}
    """

    def __init__(self):
        if not HAS_WEBSOCKETS:
            raise ImportError(
                "Package 'websockets' is required for phone NFC mode.\n"
                "Install it:  pip install websockets"
            )

        config = load_config()
        self.max_retries = config.get("max_retries", 3)
        self.verify_after_write = config.get("verify_after_write", True)

        # Connection state
        self._ws_client = None
        self._connected = False
        self._phone_ready = False
        self._nfc_supported = False

        # Threading / async coordination
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server_thread: Optional[threading.Thread] = None
        self._http_thread: Optional[threading.Thread] = None
        self._ws_server = None
        self._httpd = None

        # Response handling
        self._pending_response: Optional[dict] = None
        self._response_event = threading.Event()

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected and self._phone_ready

    @property
    def port(self) -> str:
        return "Phone NFC (USB/WebSocket)"

    @port.setter
    def port(self, value):
        pass  # Compatibility with CLI settings code

    # ── WebSocket Server ───────────────────────────────────────────────

    async def _ws_handler(self, websocket):
        """Handle a WebSocket connection from the phone."""
        logger.info("Phone connected via WebSocket.")
        self._ws_client = websocket
        self._phone_ready = False

        try:
            async for raw_msg in websocket:
                try:
                    msg = json.loads(raw_msg)
                    event = msg.get("event", "")
                    logger.debug(f"Phone → PC: {event}")

                    if event == "connected":
                        self._nfc_supported = msg.get("nfc_supported", False)
                        self._phone_ready = True
                        self._response_event.set()
                        logger.info(
                            f"Phone bridge ready. "
                            f"NFC: {'supported' if self._nfc_supported else 'NOT supported'}"
                        )

                    elif event == "nfc_not_supported":
                        self._nfc_supported = False
                        self._phone_ready = True
                        self._response_event.set()
                        logger.error("Web NFC is NOT supported on this phone/browser.")

                    else:
                        # Command response
                        self._pending_response = msg
                        self._response_event.set()

                except json.JSONDecodeError:
                    logger.warning(f"Bad JSON from phone: {raw_msg[:100]}")

        except Exception as e:
            logger.warning(f"Phone WebSocket closed: {e}")
        finally:
            self._ws_client = None
            self._phone_ready = False
            logger.info("Phone disconnected.")

    async def _run_ws_server(self):
        """Start the async WebSocket server."""
        self._ws_server = await websockets.server.serve(
            self._ws_handler, "0.0.0.0", WS_PORT
        )
        logger.info(f"WebSocket server on port {WS_PORT}")
        await self._ws_server.wait_closed()

    def _ws_thread_target(self):
        """Background thread: run the asyncio event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._run_ws_server())

    # ── HTTP File Server ───────────────────────────────────────────────

    def _http_thread_target(self):
        """Background thread: serve static files."""
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
        handler = functools.partial(QuietHTTPHandler, directory=str(STATIC_DIR))
        self._httpd = http.server.HTTPServer(("0.0.0.0", HTTP_PORT), handler)
        logger.info(f"HTTP server on port {HTTP_PORT}")
        self._httpd.serve_forever()

    # ── Connect / Disconnect ───────────────────────────────────────────

    def connect(self) -> bool:
        """
        Set up the phone NFC bridge:
          1. Verify ADB is available
          2. Verify phone is connected via USB
          3. Set up ADB reverse port forwarding
          4. Start WebSocket + HTTP servers
          5. Open Chrome on phone with NFC page
          6. Wait for phone to connect back
        """
        if self._connected and self._phone_ready:
            return True

        # Step 1: ADB
        if not check_adb_available():
            logger.error(
                "ADB not found. Install Android SDK Platform Tools:\n"
                "  https://developer.android.com/tools/releases/platform-tools"
            )
            return False

        # Step 2: Device
        devices = get_adb_devices()
        if not devices:
            logger.error(
                "No ADB device found.\n"
                "  1. Enable Developer Options on your Poco M5s\n"
                "  2. Enable USB Debugging\n"
                "  3. Connect phone via USB\n"
                "  4. Accept 'Allow USB debugging' prompt on phone"
            )
            return False

        logger.info(f"ADB device: {devices[0]}")

        # Step 3: ADB reverse
        if not setup_adb_reverse():
            return False

        # Step 4: Start servers
        if self._server_thread is None or not self._server_thread.is_alive():
            self._server_thread = threading.Thread(
                target=self._ws_thread_target, daemon=True
            )
            self._server_thread.start()

        if self._http_thread is None or not self._http_thread.is_alive():
            self._http_thread = threading.Thread(
                target=self._http_thread_target, daemon=True
            )
            self._http_thread.start()

        time.sleep(1.0)  # Let servers start
        self._connected = True

        # Step 5: Open Chrome
        nfc_url = f"http://localhost:{HTTP_PORT}/phone_nfc.html"
        open_chrome_on_phone(nfc_url)

        # Step 6: Wait for phone to connect
        logger.info("Waiting for phone to connect...")
        self._response_event.clear()
        if self._response_event.wait(timeout=30):
            if self._nfc_supported:
                logger.info("Phone NFC bridge is ready!")
                return True
            else:
                logger.error(
                    "Web NFC not supported. "
                    "Make sure you're using Google Chrome on Android."
                )
                return True  # Servers running, but NFC won't work
        else:
            logger.warning(
                "Phone didn't connect within 30s.\n"
                f"  Open this URL manually in Chrome on your phone:\n"
                f"  {nfc_url}"
            )
            return True  # Servers running, phone might connect later

    def disconnect(self) -> None:
        """Shut down servers and clean up ADB."""
        try:
            if self._ws_server:
                self._ws_server.close()
            if self._httpd:
                self._httpd.shutdown()
            cleanup_adb_reverse()
        except Exception:
            pass

        self._connected = False
        self._phone_ready = False
        self._ws_client = None
        logger.info("Phone NFC bridge disconnected.")

    def reconnect(self) -> bool:
        """Disconnect and reconnect."""
        self.disconnect()
        time.sleep(1.0)
        return self.connect()

    # ── Command Transport ──────────────────────────────────────────────

    def _send_command(self, cmd: dict, timeout: float = 30.0) -> Optional[dict]:
        """
        Send a JSON command to the phone, wait for response.
        Returns the response dict or None on timeout.
        """
        if not self._ws_client or not self._loop:
            return None

        try:
            self._response_event.clear()
            self._pending_response = None

            # Send from main thread → asyncio loop
            future = asyncio.run_coroutine_threadsafe(
                self._ws_client.send(json.dumps(cmd)),
                self._loop
            )
            future.result(timeout=5)  # Wait for send to complete

            # Wait for phone's response
            if self._response_event.wait(timeout=timeout):
                return self._pending_response
            else:
                logger.warning(f"Timeout waiting for phone response ({cmd.get('cmd')})")
                return None

        except Exception as e:
            logger.error(f"Send command error: {e}")
            return None

    # ── NDEF Data Translation ──────────────────────────────────────────

    def _ndef_to_web_nfc(self, ndef_data: bytes, record_type: str) -> dict:
        """
        Convert TechTap's TLV-wrapped NDEF bytes into a command dict
        that the phone's Web NFC API can understand.
        """
        info = get_ndef_payload_info(ndef_data)

        if not info:
            return {"cmd": "write", "type": "raw", "hex_data": bytes_to_hex(ndef_data)}

        content_type = info.get("content_type", "Unknown")
        rec_type_raw = info.get("type", "")

        # URL record
        if content_type == "URL":
            return {
                "cmd": "write",
                "type": "url",
                "url": info["content"]
            }

        # Text record
        if content_type == "Text":
            return {
                "cmd": "write",
                "type": "text",
                "text": info["content"],
                "lang": info.get("language", "en")
            }

        # vCard (MIME)
        if content_type == "vCard":
            return {
                "cmd": "write",
                "type": "mime",
                "mime_type": "text/vcard",
                "text_data": info["content"]
            }

        # WiFi WSC (MIME with binary payload)
        if content_type == "WiFi":
            return {
                "cmd": "write",
                "type": "mime",
                "mime_type": "application/vnd.wfa.wsc",
                "hex_data": bytes_to_hex(info.get("raw_payload", b""))
            }

        # Unknown MIME type — try to detect from NDEF record type
        if info.get("tnf") == 2 and rec_type_raw:
            raw_payload = info.get("raw_payload", b"")
            # Try text decode, fallback to hex
            try:
                text_data = raw_payload.decode("utf-8")
                return {
                    "cmd": "write",
                    "type": "mime",
                    "mime_type": rec_type_raw,
                    "text_data": text_data
                }
            except UnicodeDecodeError:
                return {
                    "cmd": "write",
                    "type": "mime",
                    "mime_type": rec_type_raw,
                    "hex_data": bytes_to_hex(raw_payload)
                }

        # Fallback: raw hex
        return {"cmd": "write", "type": "raw", "hex_data": bytes_to_hex(ndef_data)}

    # ── Public Commands ────────────────────────────────────────────────
    # Same interface as RFIDReader so CLI works without changes.

    def write_ndef(self, ndef_data: bytes, record_type: str = "RAW",
                   on_tap_prompt: Optional[Callable] = None) -> dict:
        """Write NDEF data to tag via phone NFC."""
        with self._lock:
            if not self.connected:
                return {"success": False, "error": "Phone not connected"}

            if not self._nfc_supported:
                return {"success": False, "error": "Web NFC not supported on phone"}

            # Convert NDEF TLV → Web NFC command
            cmd = self._ndef_to_web_nfc(ndef_data, record_type)

            if on_tap_prompt:
                on_tap_prompt()

            for attempt in range(1, self.max_retries + 1):
                logger.info(
                    f"Phone write attempt {attempt}/{self.max_retries}: "
                    f"{record_type} ({len(ndef_data)} bytes)"
                )

                response = self._send_command(cmd, timeout=60.0)

                if response is None:
                    logger.warning("No response from phone.")
                    continue

                event = response.get("event", "")

                if event == "write_ok":
                    uid = response.get("uid", "Unknown")
                    return {
                        "success": True,
                        "uid": uid,
                        "attempts": attempt,
                        "verified": response.get("verified", False)
                    }

                if event == "error":
                    error = response.get("error", "Unknown error")
                    logger.warning(f"Phone write error: {error}")
                    if attempt < self.max_retries:
                        time.sleep(0.5)
                        continue
                    return {"success": False, "error": error}

            return {
                "success": False,
                "error": f"Failed after {self.max_retries} attempts"
            }

    def erase_tag(self, on_tap_prompt: Optional[Callable] = None) -> dict:
        """Erase tag via phone (writes empty NDEF message)."""
        with self._lock:
            if not self.connected:
                return {"success": False, "error": "Phone not connected"}

            if on_tap_prompt:
                on_tap_prompt()

            response = self._send_command({"cmd": "erase"}, timeout=60.0)

            if response and response.get("event") == "erase_ok":
                return {"success": True, "uid": response.get("uid", "")}

            error = "No response from phone"
            if response:
                error = response.get("error", "Erase failed")
            return {"success": False, "error": error}

    def read_tag(self, on_tap_prompt: Optional[Callable] = None) -> dict:
        """Read tag via phone NFC scan."""
        with self._lock:
            if not self.connected:
                return {"success": False, "error": "Phone not connected"}

            if on_tap_prompt:
                on_tap_prompt()

            response = self._send_command({"cmd": "read"}, timeout=60.0)

            if response and response.get("event") == "read_ok":
                # Build display-friendly raw lines from records
                records = response.get("records", [])
                raw_lines = []
                for rec in records:
                    rtype = rec.get("type", "unknown")
                    content = rec.get("content", "")
                    raw_lines.append(f"{rtype.upper()}: {content}")

                return {
                    "success": True,
                    "data": "",  # No raw hex from Web NFC
                    "records": records,
                    "raw": raw_lines
                }

            error = "No response from phone"
            if response:
                error = response.get("error", "Read failed")
            return {"success": False, "error": error}

    def lock_tag(self, on_tap_prompt: Optional[Callable] = None) -> dict:
        """Lock tag via phone (NDEFReader.makeReadOnly)."""
        with self._lock:
            if not self.connected:
                return {"success": False, "error": "Phone not connected"}

            if on_tap_prompt:
                on_tap_prompt()

            response = self._send_command({"cmd": "lock"}, timeout=60.0)

            if response and response.get("event") == "lock_ok":
                return {"success": True, "uid": response.get("uid", "")}

            error = "No response from phone"
            if response:
                error = response.get("error", "Lock failed")
            return {"success": False, "error": error}

    def get_tag_info(self, on_tap_prompt: Optional[Callable] = None) -> dict:
        """Get tag info via phone NFC scan."""
        with self._lock:
            if not self.connected:
                return {"success": False, "error": "Phone not connected"}

            if on_tap_prompt:
                on_tap_prompt()

            response = self._send_command({"cmd": "info"}, timeout=60.0)

            if response and response.get("event") == "tag_info":
                return {"success": True, "info": response.get("info", {})}

            error = "No response from phone"
            if response:
                error = response.get("error", "Info failed")
            return {"success": False, "error": error}

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
