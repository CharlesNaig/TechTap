"""
TechTap — Utility Functions
Helpers for validation, formatting, logging, and common operations.
"""

import re
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Paths ──────────────────────────────────────────────────────────────────

APP_DIR = Path(__file__).parent
PROJECT_ROOT = APP_DIR.parent
LOG_DIR = PROJECT_ROOT / "logs"
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_PATH = PROJECT_ROOT / "config.json"


# ── Logging Setup ──────────────────────────────────────────────────────────

def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure and return the TechTap logger."""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"techtap_{datetime.now():%Y%m%d}.log"

    logger = logging.getLogger("techtap")
    logger.setLevel(level)

    if not logger.handlers:
        # File handler
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(fh)

        # Console handler (minimal)
        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(ch)

    return logger


logger = setup_logging()


# ── Config Management ──────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "serial": {
        "port": "auto",
        "baudrate": 115200,
        "timeout": 5,
        "write_timeout": 5
    },
    "reader_mode": "arduino",
    "module": "PN532",
    "bulk_mode": False,
    "log_writes": True,
    "verify_after_write": True,
    "max_retries": 3,
    "theme": "dark"
}


def load_config() -> dict:
    """Load config from config.json or create defaults."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            # Merge with defaults (user overrides)
            merged = {**DEFAULT_CONFIG, **user_cfg}
            if "serial" in user_cfg:
                merged["serial"] = {**DEFAULT_CONFIG["serial"], **user_cfg["serial"]}
            return merged
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Config load error: {e}. Using defaults.")
    return DEFAULT_CONFIG.copy()


def save_config(config: dict) -> None:
    """Save config to config.json."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        logger.info("Config saved.")
    except OSError as e:
        logger.error(f"Config save error: {e}")


# ── Validation Helpers ─────────────────────────────────────────────────────

def validate_url(url: str) -> bool:
    """Validate URL format."""
    pattern = re.compile(
        r'^https?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE
    )
    return bool(pattern.match(url.strip()))


def validate_email(email: str) -> bool:
    """Validate email format."""
    pattern = re.compile(
        r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    )
    return bool(pattern.match(email.strip()))


def validate_phone(phone: str) -> bool:
    """Validate phone number (allows +, digits, spaces, dashes)."""
    cleaned = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    pattern = re.compile(r'^\+?\d{7,15}$')
    return bool(pattern.match(cleaned))


def sanitize_phone(phone: str) -> str:
    """Clean phone number to digits + optional leading +."""
    cleaned = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if cleaned.startswith("+"):
        return "+" + re.sub(r'\D', '', cleaned[1:])
    return re.sub(r'\D', '', cleaned)


# ── Formatting Helpers ─────────────────────────────────────────────────────

def format_uid(uid_bytes: bytes) -> str:
    """Format UID bytes into readable hex string like 'A3 FF 22 19'."""
    return " ".join(f"{b:02X}" for b in uid_bytes)


def format_bytes_size(size: int) -> str:
    """Human-readable byte size."""
    if size < 1024:
        return f"{size} bytes"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def format_timestamp(dt: Optional[datetime] = None) -> str:
    """ISO-style timestamp string."""
    dt = dt or datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ── NTAG Capacity Info ─────────────────────────────────────────────────────

NTAG_SPECS = {
    "NTAG213": {"user_bytes": 144, "pages": 45, "total_memory": 180},
    "NTAG215": {"user_bytes": 504, "pages": 135, "total_memory": 540},
    "NTAG216": {"user_bytes": 888, "pages": 231, "total_memory": 924},
}


def check_tag_capacity(tag_type: str, data_size: int) -> dict:
    """Check if data fits on the tag. Returns info dict."""
    spec = NTAG_SPECS.get(tag_type.upper(), NTAG_SPECS["NTAG215"])
    fits = data_size <= spec["user_bytes"]
    return {
        "tag_type": tag_type.upper(),
        "capacity": spec["user_bytes"],
        "data_size": data_size,
        "fits": fits,
        "remaining": max(0, spec["user_bytes"] - data_size),
        "usage_percent": min(100, round(data_size / spec["user_bytes"] * 100, 1))
    }


# ── Banner ─────────────────────────────────────────────────────────────────

BANNER = r"""
  _______________  _  _____  _   ___
 |_   _| __| __| || ||_   _|/ \ | _ \
   | | | _|| _|| __ |  | | / _ \|  _/
   |_| |___|___|_||_|  |_|/_/ \_\_|

  Smart Identity via Tap
  ─────────────────────────────────
"""

VERSION = "1.0.0"


def get_banner() -> str:
    """Return styled banner text."""
    return BANNER


def get_version() -> str:
    """Return version string."""
    return f"TechTap v{VERSION}"
