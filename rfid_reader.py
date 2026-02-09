"""
TechTap — RFID Reader Serial Communication Handler
Manages serial connection to Arduino + PN532/MFRC522 module.
Handles auto-detection, reconnection, and command protocol.
"""

import time
import threading
from typing import Optional, Callable

import serial
import serial.tools.list_ports

from techtap.utils import load_config, logger
from techtap.ndef_encoder import bytes_to_hex


# ── Protocol Constants ─────────────────────────────────────────────────────

CMD_WRITE_URL = "WRITE_URL"
CMD_WRITE_TEXT = "WRITE_TEXT"
CMD_WRITE_VCARD = "WRITE_VCARD"
CMD_WRITE_PHONE = "WRITE_PHONE"
CMD_WRITE_EMAIL = "WRITE_EMAIL"
CMD_WRITE_WIFI = "WRITE_WIFI"
CMD_WRITE_RAW = "WRITE_RAW"
CMD_ERASE = "ERASE"
CMD_READ = "READ"
CMD_LOCK = "LOCK"
CMD_INFO = "INFO"
CMD_PING = "PING"

RESP_OK = "OK"
RESP_WRITE_OK = "WRITE_OK"
RESP_ERASE_OK = "ERASE_OK"
RESP_READ_OK = "READ_OK"
RESP_LOCK_OK = "LOCK_OK"
RESP_TAP_CARD = "TAP_CARD"
RESP_ERROR = "ERROR"
RESP_PONG = "PONG"
RESP_READY = "READY"
RESP_VERIFY_OK = "VERIFY_OK"
RESP_TAG_INFO = "TAG_INFO"
RESP_NO_TAG = "NO_TAG"
RESP_WRITE_FAIL = "WRITE_FAIL"
RESP_DATA = "DATA"
RESP_DUPLICATE = "DUPLICATE"


# ── Arduino Auto-Detection ─────────────────────────────────────────────────

ARDUINO_VID_PID = [
    (0x2341, None),   # Arduino official
    (0x1A86, 0x7523), # CH340 (Arduino clones)
    (0x10C4, 0xEA60), # CP2102 (NodeMCU, etc.)
    (0x0403, 0x6001), # FTDI FT232
    (0x2A03, None),   # Arduino.org
    (0x1B4F, None),   # SparkFun
    (0x239A, None),   # Adafruit
]


def find_arduino_port() -> Optional[str]:
    """Auto-detect Arduino COM port by VID/PID."""
    ports = serial.tools.list_ports.comports()

    for port in ports:
        if port.vid is not None:
            for vid, pid in ARDUINO_VID_PID:
                if port.vid == vid and (pid is None or port.pid == pid):
                    logger.info(
                        f"Arduino detected: {port.device} "
                        f"({port.description})"
                    )
                    return port.device

    # Fallback: look for common descriptions
    for port in ports:
        desc = (port.description or "").lower()
        if any(kw in desc for kw in ["arduino", "ch340", "cp210", "ftdi", "usb serial"]):
            logger.info(f"Arduino detected (by desc): {port.device}")
            return port.device

    return None


def list_serial_ports() -> list[dict]:
    """List all available serial ports with details."""
    ports = serial.tools.list_ports.comports()
    return [
        {
            "port": p.device,
            "description": p.description,
            "vid": f"0x{p.vid:04X}" if p.vid else None,
            "pid": f"0x{p.pid:04X}" if p.pid else None,
            "manufacturer": p.manufacturer,
        }
        for p in ports
    ]


# ── RFIDReader Class ───────────────────────────────────────────────────────

class RFIDReader:
    """
    Serial communication handler for Arduino NFC module.

    Protocol:
        PC → Arduino:  COMMAND|HEX_DATA\\n
        Arduino → PC:   RESPONSE|DATA\\n

    Supports handshake: READY_TO_WRITE → WRITE_COMPLETE → VERIFY_OK
    """

    def __init__(self, port: Optional[str] = None,
                 baudrate: int = 115200,
                 timeout: float = 5.0,
                 write_timeout: float = 5.0):

        config = load_config()
        serial_cfg = config.get("serial", {})

        self.port = port or serial_cfg.get("port", "auto")
        self.baudrate = baudrate or serial_cfg.get("baudrate", 115200)
        self.timeout = timeout or serial_cfg.get("timeout", 5)
        self.write_timeout = write_timeout or serial_cfg.get("write_timeout", 5)
        self.max_retries = config.get("max_retries", 3)
        self.verify_after_write = config.get("verify_after_write", True)

        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._serial is not None and self._serial.is_open

    def connect(self) -> bool:
        """
        Establish serial connection. Auto-detects port if set to 'auto'.
        Returns True on success.
        """
        with self._lock:
            if self.connected:
                return True

            # Auto-detect port
            port = self.port
            if port == "auto":
                port = find_arduino_port()
                if not port:
                    logger.error("No Arduino detected. Check USB connection.")
                    return False

            try:
                self._serial = serial.Serial(
                    port=port,
                    baudrate=self.baudrate,
                    timeout=self.timeout,
                    write_timeout=self.write_timeout
                )

                # Wait for Arduino reset after serial open
                time.sleep(2.0)

                # Flush buffers
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()

                # Ping test
                if self._ping():
                    self._connected = True
                    self.port = port
                    logger.info(f"Connected to {port} @ {self.baudrate} baud")
                    return True
                else:
                    logger.warning(f"Connected to {port} but PING failed. Device may not be running TechTap firmware.")
                    # Still mark as connected — firmware might respond differently
                    self._connected = True
                    self.port = port
                    return True

            except serial.SerialException as e:
                logger.error(f"Serial connection failed: {e}")
                self._serial = None
                self._connected = False
                return False

    def disconnect(self) -> None:
        """Close serial connection."""
        with self._lock:
            if self._serial and self._serial.is_open:
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None
            self._connected = False
            logger.info("Disconnected from Arduino.")

    def reconnect(self) -> bool:
        """Disconnect and reconnect."""
        self.disconnect()
        time.sleep(1.0)
        return self.connect()

    def _ping(self) -> bool:
        """Send PING, expect PONG."""
        try:
            self._serial.write(f"{CMD_PING}\n".encode())
            self._serial.flush()
            response = self._read_line(timeout=3.0)
            return response is not None and RESP_PONG in response
        except Exception:
            return False

    def _send(self, command: str) -> None:
        """Send a command string to Arduino."""
        if not self._serial or not self._serial.is_open:
            raise ConnectionError("Not connected to Arduino.")
        self._serial.write(f"{command}\n".encode())
        self._serial.flush()

    def _read_line(self, timeout: Optional[float] = None) -> Optional[str]:
        """Read a single line from Arduino with optional custom timeout."""
        if not self._serial:
            return None

        old_timeout = self._serial.timeout
        if timeout is not None:
            self._serial.timeout = timeout

        try:
            raw = self._serial.readline()
            if raw:
                return raw.decode("utf-8", errors="replace").strip()
            return None
        except serial.SerialException as e:
            logger.error(f"Read error: {e}")
            return None
        finally:
            if timeout is not None:
                self._serial.timeout = old_timeout

    def _wait_for_response(self, expected: list[str],
                           timeout: float = 30.0) -> dict:
        """
        Wait for one of the expected responses.
        Returns dict with 'status', 'data', and 'raw'.
        """
        start = time.time()
        lines = []

        while time.time() - start < timeout:
            line = self._read_line(timeout=1.0)
            if line is None:
                continue

            lines.append(line)
            logger.debug(f"← {line}")

            parts = line.split("|", 1)
            status = parts[0].strip()
            data = parts[1].strip() if len(parts) > 1 else ""

            if status in expected:
                return {"status": status, "data": data, "raw": lines}

            if status == RESP_ERROR:
                return {"status": RESP_ERROR, "data": data, "raw": lines}

        return {"status": "TIMEOUT", "data": "", "raw": lines}

    # ── Public Commands ────────────────────────────────────────────────

    def write_ndef(self, ndef_data: bytes, record_type: str = "RAW",
                   on_tap_prompt: Optional[Callable] = None) -> dict:
        """
        Write NDEF data to a tag.

        Process:
        1. Send WRITE_RAW|HEX_DATA
        2. Arduino responds TAP_CARD (waiting for tag)
        3. User taps card
        4. Arduino writes and responds WRITE_OK or WRITE_FAIL
        5. If verify enabled: VERIFY_OK

        Returns result dict.
        """
        with self._lock:
            if not self.connected:
                return {"success": False, "error": "Not connected"}

            hex_data = bytes_to_hex(ndef_data)
            command = f"{CMD_WRITE_RAW}|{hex_data}"

            for attempt in range(1, self.max_retries + 1):
                try:
                    logger.info(f"Write attempt {attempt}/{self.max_retries}: "
                                f"{record_type} ({len(ndef_data)} bytes)")

                    self._send(command)

                    # Wait for TAP_CARD prompt
                    resp = self._wait_for_response(
                        [RESP_TAP_CARD, RESP_ERROR], timeout=5.0
                    )

                    if resp["status"] == RESP_TAP_CARD:
                        if on_tap_prompt:
                            on_tap_prompt()

                        # Wait for write result (user needs to tap)
                        result = self._wait_for_response(
                            [RESP_WRITE_OK, RESP_WRITE_FAIL, RESP_ERROR,
                             RESP_VERIFY_OK, RESP_DUPLICATE],
                            timeout=30.0
                        )

                        if result["status"] == RESP_WRITE_OK:
                            uid = result.get("data", "")
                            return {
                                "success": True,
                                "uid": uid,
                                "attempts": attempt,
                                "verified": False
                            }

                        if result["status"] == RESP_VERIFY_OK:
                            uid = result.get("data", "")
                            return {
                                "success": True,
                                "uid": uid,
                                "attempts": attempt,
                                "verified": True
                            }

                        if result["status"] == RESP_DUPLICATE:
                            return {
                                "success": False,
                                "error": "Tag already contains data",
                                "duplicate": True,
                                "uid": result.get("data", "")
                            }

                        if result["status"] == RESP_WRITE_FAIL:
                            logger.warning(f"Write failed: {result.get('data')}")
                            continue

                    elif resp["status"] == RESP_ERROR:
                        return {"success": False, "error": resp.get("data", "Unknown error")}

                    elif resp["status"] == "TIMEOUT":
                        logger.warning("Timeout waiting for TAP_CARD")
                        continue

                except serial.SerialException as e:
                    logger.error(f"Serial error during write: {e}")
                    self.reconnect()
                    continue

            return {"success": False, "error": f"Failed after {self.max_retries} attempts"}

    def erase_tag(self, on_tap_prompt: Optional[Callable] = None) -> dict:
        """Erase/reset a tag."""
        with self._lock:
            if not self.connected:
                return {"success": False, "error": "Not connected"}

            try:
                self._send(CMD_ERASE)

                resp = self._wait_for_response(
                    [RESP_TAP_CARD, RESP_ERROR], timeout=5.0
                )

                if resp["status"] == RESP_TAP_CARD:
                    if on_tap_prompt:
                        on_tap_prompt()

                    result = self._wait_for_response(
                        [RESP_ERASE_OK, RESP_ERROR], timeout=30.0
                    )

                    if result["status"] == RESP_ERASE_OK:
                        return {"success": True, "uid": result.get("data", "")}

                return {"success": False, "error": resp.get("data", "Erase failed")}

            except serial.SerialException as e:
                logger.error(f"Serial error during erase: {e}")
                return {"success": False, "error": str(e)}

    def read_tag(self, on_tap_prompt: Optional[Callable] = None) -> dict:
        """Read tag contents."""
        with self._lock:
            if not self.connected:
                return {"success": False, "error": "Not connected"}

            try:
                self._send(CMD_READ)

                resp = self._wait_for_response(
                    [RESP_TAP_CARD, RESP_ERROR], timeout=5.0
                )

                if resp["status"] == RESP_TAP_CARD:
                    if on_tap_prompt:
                        on_tap_prompt()

                    result = self._wait_for_response(
                        [RESP_READ_OK, RESP_TAG_INFO, RESP_DATA,
                         RESP_NO_TAG, RESP_ERROR],
                        timeout=30.0
                    )

                    if result["status"] in (RESP_READ_OK, RESP_DATA, RESP_TAG_INFO):
                        return {
                            "success": True,
                            "data": result.get("data", ""),
                            "raw": result.get("raw", [])
                        }

                    if result["status"] == RESP_NO_TAG:
                        return {"success": False, "error": "No tag detected"}

                return {"success": False, "error": resp.get("data", "Read failed")}

            except serial.SerialException as e:
                logger.error(f"Serial error during read: {e}")
                return {"success": False, "error": str(e)}

    def lock_tag(self, on_tap_prompt: Optional[Callable] = None) -> dict:
        """Lock tag to prevent further writes."""
        with self._lock:
            if not self.connected:
                return {"success": False, "error": "Not connected"}

            try:
                self._send(CMD_LOCK)

                resp = self._wait_for_response(
                    [RESP_TAP_CARD, RESP_ERROR], timeout=5.0
                )

                if resp["status"] == RESP_TAP_CARD:
                    if on_tap_prompt:
                        on_tap_prompt()

                    result = self._wait_for_response(
                        [RESP_LOCK_OK, RESP_ERROR], timeout=30.0
                    )

                    if result["status"] == RESP_LOCK_OK:
                        return {"success": True, "uid": result.get("data", "")}

                return {"success": False, "error": resp.get("data", "Lock failed")}

            except serial.SerialException as e:
                logger.error(f"Serial error during lock: {e}")
                return {"success": False, "error": str(e)}

    def get_tag_info(self, on_tap_prompt: Optional[Callable] = None) -> dict:
        """Get tag info (UID, type, capacity, lock status)."""
        with self._lock:
            if not self.connected:
                return {"success": False, "error": "Not connected"}

            try:
                self._send(CMD_INFO)

                resp = self._wait_for_response(
                    [RESP_TAP_CARD, RESP_ERROR], timeout=5.0
                )

                if resp["status"] == RESP_TAP_CARD:
                    if on_tap_prompt:
                        on_tap_prompt()

                    result = self._wait_for_response(
                        [RESP_TAG_INFO, RESP_NO_TAG, RESP_ERROR],
                        timeout=30.0
                    )

                    if result["status"] == RESP_TAG_INFO:
                        # Expected format: TAG_INFO|uid:AABBCCDD,type:NTAG215,size:504,locked:0
                        info = {}
                        for pair in result.get("data", "").split(","):
                            if ":" in pair:
                                k, v = pair.split(":", 1)
                                info[k.strip()] = v.strip()
                        return {"success": True, "info": info}

                return {"success": False, "error": resp.get("data", "Info failed")}

            except serial.SerialException as e:
                logger.error(f"Serial error during info: {e}")
                return {"success": False, "error": str(e)}

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
