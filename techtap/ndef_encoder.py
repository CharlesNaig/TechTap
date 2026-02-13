"""
TOMOTAP — NDEF Encoder Module
Encodes data into NDEF-compatible binary format for NTAG213/215/216 cards.
Phones (Android/iOS) can natively read these records.
"""

import struct
from typing import Optional


# ── NDEF URI Prefix Codes (NFC Forum) ──────────────────────────────────────
URI_PREFIXES = {
    "http://www.": 0x01,
    "https://www.": 0x02,
    "http://": 0x03,
    "https://": 0x04,
    "tel:": 0x05,
    "mailto:": 0x06,
    "ftp://anonymous:anonymous@": 0x07,
    "ftp://ftp.": 0x08,
    "ftps://": 0x09,
    "sftp://": 0x0A,
    "smb://": 0x0B,
    "nfs://": 0x0C,
    "ftp://": 0x0D,
    "dav://": 0x0E,
    "news:": 0x0F,
    "telnet://": 0x10,
    "imap:": 0x11,
    "rtsp://": 0x12,
    "urn:": 0x13,
    "pop:": 0x14,
    "sip:": 0x15,
    "sips:": 0x16,
    "tftp:": 0x17,
    "btspp://": 0x18,
    "btl2cap://": 0x19,
    "btgoep://": 0x1A,
    "tcpobex://": 0x1B,
    "irdaobex://": 0x1C,
    "file://": 0x1D,
    "urn:epc:id:": 0x1E,
    "urn:epc:tag:": 0x1F,
    "urn:epc:pat:": 0x20,
    "urn:epc:raw:": 0x21,
    "urn:epc:": 0x22,
    "urn:nfc:": 0x23,
}

# ── Social Media URL Templates ─────────────────────────────────────────────
SOCIAL_PLATFORMS = {
    "facebook": "https://facebook.com/{}",
    "instagram": "https://instagram.com/{}",
    "twitter": "https://twitter.com/{}",
    "x": "https://x.com/{}",
    "tiktok": "https://tiktok.com/@{}",
    "youtube": "https://youtube.com/@{}",
    "linkedin": "https://linkedin.com/in/{}",
    "github": "https://github.com/{}",
    "telegram": "https://t.me/{}",
    "snapchat": "https://snapchat.com/add/{}",
    "reddit": "https://reddit.com/u/{}",
    "pinterest": "https://pinterest.com/{}",
    "whatsapp": "https://wa.me/{}",
    "discord": "https://discord.gg/{}",
    "spotify": "https://open.spotify.com/user/{}",
    "twitch": "https://twitch.tv/{}",
    "threads": "https://threads.net/@{}",
}


# ── Low-Level NDEF Helpers ─────────────────────────────────────────────────

def _make_ndef_record(tnf: int, rec_type: bytes, payload: bytes,
                      is_first: bool = True, is_last: bool = True) -> bytes:
    """Build a single NDEF record with proper header flags."""
    flags = tnf & 0x07
    if is_first:
        flags |= 0x80  # MB — Message Begin
    if is_last:
        flags |= 0x40  # ME — Message End

    # Short Record if payload ≤ 255 bytes
    if len(payload) <= 255:
        flags |= 0x10  # SR
        header = struct.pack("BBB", flags, len(rec_type), len(payload))
    else:
        header = struct.pack(">BBI", flags, len(rec_type), len(payload))

    return header + rec_type + payload


def _wrap_tlv(ndef_message: bytes) -> bytes:
    """
    Wrap an NDEF message in TLV (Tag-Length-Value) for NTAG memory.
    0x03 = NDEF Message TLV, 0xFE = Terminator TLV.
    """
    length = len(ndef_message)
    if length < 0xFF:
        tlv = bytes([0x03, length]) + ndef_message + bytes([0xFE])
    else:
        tlv = bytes([0x03, 0xFF]) + struct.pack(">H", length) + ndef_message + bytes([0xFE])
    return tlv


def encode_empty_ndef() -> bytes:
    """
    Create an empty NDEF TLV container.
    This 'formats' a tag for NDEF use without writing any records.
    Bytes: 0x03 (NDEF TLV type) + 0x00 (zero length) + 0xFE (terminator).
    """
    return bytes([0x03, 0x00, 0xFE])


# ── Public Encoder Functions ───────────────────────────────────────────────

def encode_url(url: str) -> bytes:
    """
    Encode a URL into NDEF URI record wrapped in TLV.
    Automatically applies URI prefix compression.
    """
    url = url.strip()
    prefix_code = 0x00  # No prefix
    uri_field = url

    # Try to match longest prefix first
    for prefix, code in sorted(URI_PREFIXES.items(), key=lambda x: -len(x[0])):
        if url.lower().startswith(prefix):
            prefix_code = code
            uri_field = url[len(prefix):]
            break

    payload = bytes([prefix_code]) + uri_field.encode("utf-8")
    record = _make_ndef_record(tnf=0x01, rec_type=b"U", payload=payload)
    return _wrap_tlv(record)


def encode_text(text: str, lang: str = "en") -> bytes:
    """Encode plain text into an NDEF Text record."""
    text = text.strip()
    lang_bytes = lang.encode("ascii")
    status_byte = len(lang_bytes) & 0x3F  # UTF-8, length of lang code
    payload = bytes([status_byte]) + lang_bytes + text.encode("utf-8")
    record = _make_ndef_record(tnf=0x01, rec_type=b"T", payload=payload)
    return _wrap_tlv(record)


def encode_vcard(name: str, phone: str = "", email: str = "",
                 org: str = "", title: str = "", url: str = "",
                 address: str = "", note: str = "") -> bytes:
    """
    Encode contact info into an NDEF MIME record (vCard 3.0).
    Phones will offer to save as a contact.
    """
    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{name.strip()}",
    ]

    # Split name into parts for N field
    parts = name.strip().split(maxsplit=1)
    if len(parts) == 2:
        lines.append(f"N:{parts[1]};{parts[0]};;;")
    else:
        lines.append(f"N:{parts[0]};;;;")

    if phone:
        lines.append(f"TEL;TYPE=CELL:{phone.strip()}")
    if email:
        lines.append(f"EMAIL:{email.strip()}")
    if org:
        lines.append(f"ORG:{org.strip()}")
    if title:
        lines.append(f"TITLE:{title.strip()}")
    if url:
        lines.append(f"URL:{url.strip()}")
    if address:
        lines.append(f"ADR;TYPE=HOME:;;{address.strip()};;;;")
    if note:
        lines.append(f"NOTE:{note.strip()}")

    lines.append("END:VCARD")
    vcard_str = "\r\n".join(lines)

    mime_type = b"text/vcard"
    payload = vcard_str.encode("utf-8")
    record = _make_ndef_record(tnf=0x02, rec_type=mime_type, payload=payload)
    return _wrap_tlv(record)


def encode_phone(phone: str) -> bytes:
    """Encode a phone number as an NDEF URI record (tel: scheme)."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    url = f"tel:{phone}"
    payload = bytes([0x05]) + phone.encode("utf-8")  # 0x05 = tel:
    record = _make_ndef_record(tnf=0x01, rec_type=b"U", payload=payload)
    return _wrap_tlv(record)


def encode_email(email: str, subject: str = "", body: str = "") -> bytes:
    """Encode an email address as an NDEF URI record (mailto: scheme)."""
    email = email.strip()
    mailto = email
    params = []
    if subject:
        params.append(f"subject={subject}")
    if body:
        params.append(f"body={body}")
    if params:
        mailto += "?" + "&".join(params)

    payload = bytes([0x06]) + mailto.encode("utf-8")  # 0x06 = mailto:
    record = _make_ndef_record(tnf=0x01, rec_type=b"U", payload=payload)
    return _wrap_tlv(record)


def encode_social(platform: str, username: str) -> bytes:
    """
    Encode a social media link by resolving platform + username to a URL.
    Returns NDEF URI record.
    """
    platform = platform.strip().lower()
    username = username.strip().lstrip("@")

    if platform not in SOCIAL_PLATFORMS:
        raise ValueError(
            f"Unknown platform '{platform}'. "
            f"Supported: {', '.join(sorted(SOCIAL_PLATFORMS.keys()))}"
        )

    url = SOCIAL_PLATFORMS[platform].format(username)
    return encode_url(url)


def encode_wifi(ssid: str, password: str = "",
                auth_type: str = "WPA2", hidden: bool = False) -> bytes:
    """
    Encode WiFi credentials as an NDEF MIME record.
    Android 5+ will offer to connect automatically.
    Uses the application/vnd.wfa.wsc MIME type (Wi-Fi Simple Config).
    """
    # Wi-Fi Simple Configuration TLV attributes
    credential = b""

    # Network Index (required, always 1)
    credential += struct.pack(">HH", 0x1026, 1) + b"\x01"

    # SSID
    ssid_bytes = ssid.encode("utf-8")
    credential += struct.pack(">HH", 0x1045, len(ssid_bytes)) + ssid_bytes

    # Authentication Type
    auth_map = {"OPEN": 0x0001, "WPA": 0x0002, "WPA2": 0x0020, "WPA2-EAP": 0x0020}
    auth_val = auth_map.get(auth_type.upper(), 0x0020)
    credential += struct.pack(">HHH", 0x1003, 2, auth_val)

    # Encryption Type
    enc_val = 0x0004 if auth_type.upper() != "OPEN" else 0x0001  # AES or None
    credential += struct.pack(">HHH", 0x100F, 2, enc_val)

    # Network Key (password)
    if password:
        pw_bytes = password.encode("utf-8")
        credential += struct.pack(">HH", 0x1027, len(pw_bytes)) + pw_bytes

    # MAC Address (use broadcast FF:FF:FF:FF:FF:FF)
    credential += struct.pack(">HH", 0x1020, 6) + b"\xFF" * 6

    # Wrap credential in Credential attribute
    payload = struct.pack(">HH", 0x100E, len(credential)) + credential

    mime_type = b"application/vnd.wfa.wsc"
    record = _make_ndef_record(tnf=0x02, rec_type=mime_type, payload=payload)
    return _wrap_tlv(record)


def get_social_platforms() -> list[str]:
    """Return sorted list of supported social platforms."""
    return sorted(SOCIAL_PLATFORMS.keys())


def bytes_to_hex(data: bytes) -> str:
    """Convert bytes to hex string for serial transmission."""
    return data.hex().upper()


def hex_to_bytes(hex_str: str) -> bytes:
    """Convert hex string back to bytes."""
    return bytes.fromhex(hex_str)


def get_ndef_payload_info(data: bytes) -> Optional[dict]:
    """
    Parse raw NDEF TLV data and return a summary dict.
    Useful for reading tag contents.
    """
    try:
        if len(data) < 5:
            return None

        # Skip TLV header
        idx = 0
        if data[idx] == 0x03:
            idx += 1
            if data[idx] == 0xFF:
                msg_len = struct.unpack(">H", data[idx + 1:idx + 3])[0]
                idx += 3
            else:
                msg_len = data[idx]
                idx += 1
        else:
            return None

        # Parse NDEF record header
        header = data[idx]
        tnf = header & 0x07
        sr = bool(header & 0x10)
        idx += 1

        type_len = data[idx]
        idx += 1

        if sr:
            payload_len = data[idx]
            idx += 1
        else:
            payload_len = struct.unpack(">I", data[idx:idx + 4])[0]
            idx += 4

        rec_type = data[idx:idx + type_len].decode("ascii", errors="replace")
        idx += type_len

        payload = data[idx:idx + payload_len]

        # Interpret
        info = {"type": rec_type, "tnf": tnf, "raw_payload": payload}

        if rec_type == "U" and tnf == 0x01:
            prefix_code = payload[0]
            uri_suffix = payload[1:].decode("utf-8", errors="replace")
            # Reverse lookup prefix
            prefix = ""
            for p, c in URI_PREFIXES.items():
                if c == prefix_code:
                    prefix = p
                    break
            info["content_type"] = "URL"
            info["content"] = prefix + uri_suffix

        elif rec_type == "T" and tnf == 0x01:
            lang_len = payload[0] & 0x3F
            lang = payload[1:1 + lang_len].decode("ascii")
            text = payload[1 + lang_len:].decode("utf-8", errors="replace")
            info["content_type"] = "Text"
            info["content"] = text
            info["language"] = lang

        elif "vcard" in rec_type.lower():
            info["content_type"] = "vCard"
            info["content"] = payload.decode("utf-8", errors="replace")

        elif "wfa.wsc" in rec_type.lower():
            info["content_type"] = "WiFi"
            info["content"] = "[WiFi Configuration Data]"

        else:
            info["content_type"] = "Unknown"
            info["content"] = payload.hex()

        return info

    except (IndexError, struct.error):
        return None
