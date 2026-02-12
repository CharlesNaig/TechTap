/*
 * ══════════════════════════════════════════════════════════════
 *  TechTap — Arduino NFC Writer Firmware
 *  Memory-optimized for Arduino Uno (2KB SRAM)
 *
 *  For PN532 NFC Module (I2C) + NTAG213/215/216 cards
 *
 *  Protocol (Serial @ 115200 baud):
 *    PC → Arduino:  COMMAND|DATA\n
 *    Arduino → PC:  RESPONSE|DATA\n
 *
 *  Supported commands:
 *    PING           → PONG
 *    WRITE_RAW|HEX  → TAP_CARD → VERIFY_OK|UID  or  WRITE_FAIL|reason
 *    ERASE          → TAP_CARD → ERASE_OK|UID
 *    READ           → TAP_CARD → DATA|HEX
 *    LOCK           → TAP_CARD → LOCK_OK|UID
 *    INFO           → TAP_CARD → TAG_INFO|uid:XX,type:NTAG215,size:504,locked:0
 *
 *  RAM strategy:
 *    - Single cmdBuffer (no separate data/read buffers)
 *    - Hex parsed inline during page writes (4 bytes at a time)
 *    - Reads streamed directly to Serial (no buffering)
 *    - UID stored as char[] instead of String
 *
 *  Hardware:  PN532 via I2C (SDA=A4, SCL=A5 on Uno)
 * ══════════════════════════════════════════════════════════════
 */

#include <Wire.h>
#include <Adafruit_PN532.h>

// ── Pin Configuration (I2C) ───────────────────────────────────
#define PN532_IRQ   2
#define PN532_RST   3

Adafruit_PN532 nfc(PN532_IRQ, PN532_RST);  // I2C mode

// ── If using SPI instead, uncomment below and comment I2C above ──
// #define PN532_SS    10
// Adafruit_PN532 nfc(PN532_SS);  // Hardware SPI

// ── Constants ─────────────────────────────────────────────────
#define SERIAL_BAUD     115200
#define CMD_TIMEOUT     30000    // 30s wait for card tap
#define NTAG_USER_START 4        // User memory starts at page 4

// cmdBuffer: 350 bytes supports NTAG213 fully (144 bytes = 288 hex chars)
// and most practical NTAG215 payloads (URLs, vCards < 165 bytes).
// For full NTAG215/216, use Arduino Mega with a larger buffer.
#define CMD_BUF_SIZE    350

// ── Tag Info Struct (must precede auto-generated prototypes) ──
struct TagInfo {
    char type[8];           // "NTAG213" / "NTAG215" / "NTAG216"
    uint16_t userBytes;
    uint16_t lastUserPage;
};

// ── Globals (~370 bytes total → leaves ~1680 for stack) ──────
char cmdBuffer[CMD_BUF_SIZE];
uint8_t uid[7];
uint8_t uidLen;
char uidStr[15];            // Pre-allocated UID hex string

// ── Setup ─────────────────────────────────────────────────────
void setup() {
    Serial.begin(SERIAL_BAUD);
    while (!Serial) delay(10);

    nfc.begin();

    uint32_t versiondata = nfc.getFirmwareVersion();
    if (!versiondata) {
        Serial.println(F("ERROR|PN532 not found. Check wiring."));
        while (1) delay(100);
    }

    nfc.SAMConfig();
    nfc.setPassiveActivationRetries(0xFF);

    Serial.println(F("READY|TechTap Firmware v1.0"));
}

// ── Main Loop ─────────────────────────────────────────────────
void loop() {
    if (Serial.available()) {
        int len = Serial.readBytesUntil('\n', cmdBuffer, CMD_BUF_SIZE - 1);
        cmdBuffer[len] = '\0';

        while (len > 0 && (cmdBuffer[len - 1] == '\r' || cmdBuffer[len - 1] == '\n')) {
            cmdBuffer[--len] = '\0';
        }

        processCommand(cmdBuffer);
    }
}

// ── Command Router ────────────────────────────────────────────
void processCommand(char* cmd) {
    char* data = strchr(cmd, '|');
    if (data) {
        *data = '\0';
        data++;
    }

    if (strcmp(cmd, "PING") == 0) {
        Serial.println(F("PONG"));
    }
    else if (strcmp(cmd, "WRITE_RAW") == 0) {
        handleWriteRaw(data);
    }
    else if (strcmp(cmd, "ERASE") == 0) {
        handleErase();
    }
    else if (strcmp(cmd, "READ") == 0) {
        handleRead();
    }
    else if (strcmp(cmd, "LOCK") == 0) {
        handleLock();
    }
    else if (strcmp(cmd, "INFO") == 0) {
        handleInfo();
    }
    else {
        Serial.print(F("ERROR|Unknown command: "));
        Serial.println(cmd);
    }
}

// ── Hex Helpers (no large buffers) ────────────────────────────
uint8_t hexNibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return 0xFF;
}

void buildUidString() {
    static const char hexChars[] = "0123456789ABCDEF";
    for (uint8_t i = 0; i < uidLen; i++) {
        uidStr[i * 2]     = hexChars[uid[i] >> 4];
        uidStr[i * 2 + 1] = hexChars[uid[i] & 0x0F];
    }
    uidStr[uidLen * 2] = '\0';
}

// Print a single byte as 2 hex chars to Serial
void printHexByte(uint8_t b) {
    if (b < 0x10) Serial.print('0');
    Serial.print(b, HEX);
}

// Decode one page (4 bytes) from hex string at *ptr, advance ptr.
// Pads with 0 if fewer than 4 bytes remain.
// Returns number of data bytes decoded (1-4), or 0 on error.
uint8_t decodePageFromHex(const char* &ptr, uint8_t* page, uint16_t bytesLeft) {
    uint8_t count = (bytesLeft < 4) ? bytesLeft : 4;
    page[0] = page[1] = page[2] = page[3] = 0;
    for (uint8_t i = 0; i < count; i++) {
        if (!ptr[0] || !ptr[1]) return 0;
        uint8_t hi = hexNibble(*ptr++);
        uint8_t lo = hexNibble(*ptr++);
        if (hi > 0x0F || lo > 0x0F) return 0;
        page[i] = (hi << 4) | lo;
    }
    return count;
}

// ── Wait for Card ─────────────────────────────────────────────
bool waitForCard() {
    Serial.println(F("TAP_CARD"));

    unsigned long start = millis();
    while (millis() - start < CMD_TIMEOUT) {
        if (nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLen, 1000)) {
            buildUidString();
            return true;
        }
    }

    Serial.println(F("ERROR|Timeout waiting for card"));
    return false;
}

// ── Detect NTAG Type ──────────────────────────────────────────
TagInfo detectTagType() {
    TagInfo info;
    strcpy(info.type, "UNKNOWN");
    info.userBytes = 0;
    info.lastUserPage = 0;

    uint8_t page3[4];
    if (nfc.ntag2xx_ReadPage(3, page3)) {
        uint16_t totalSize = (uint16_t)page3[2] * 8;

        if (totalSize <= 192) {          // 144 + 48
            strcpy(info.type, "NTAG213");
            info.userBytes = 144;
            info.lastUserPage = 39;
        } else if (totalSize <= 552) {   // 504 + 48
            strcpy(info.type, "NTAG215");
            info.userBytes = 504;
            info.lastUserPage = 129;
        } else {
            strcpy(info.type, "NTAG216");
            info.userBytes = 888;
            info.lastUserPage = 225;
        }
    }

    return info;
}

// ── Check Lock Status ─────────────────────────────────────────
bool isTagLocked() {
    uint8_t page2[4];
    if (nfc.ntag2xx_ReadPage(2, page2)) {
        return (page2[2] != 0x00 || page2[3] != 0x00);
    }
    return false;
}

// ── WRITE RAW NDEF (zero-copy: hex parsed inline) ─────────────
void handleWriteRaw(const char* hexData) {
    if (!hexData || !hexData[0] || !hexData[1]) {
        Serial.println(F("ERROR|No data provided"));
        return;
    }

    uint16_t hexLen = strlen(hexData);
    if (hexLen & 1) {
        Serial.println(F("ERROR|Odd hex length"));
        return;
    }
    uint16_t dataLen = hexLen / 2;

    if (!waitForCard()) return;

    TagInfo tag = detectTagType();
    if (dataLen > tag.userBytes) {
        Serial.print(F("ERROR|Data too large: "));
        Serial.print(dataLen);
        Serial.print(F(" > "));
        Serial.println(tag.userBytes);
        return;
    }

    // ── Duplicate detection ──
    uint8_t firstPage[4];
    if (nfc.ntag2xx_ReadPage(NTAG_USER_START, firstPage)) {
        if (firstPage[0] == 0x03 && firstPage[1] != 0x00) {
            Serial.print(F("DUPLICATE|"));
            Serial.println(uidStr);

            unsigned long ws = millis();
            while (millis() - ws < 10000) {
                if (Serial.available()) {
                    char confirm[20];
                    int cLen = Serial.readBytesUntil('\n', confirm, sizeof(confirm) - 1);
                    confirm[cLen] = '\0';
                    if (strcmp(confirm, "CONFIRM_OVERWRITE") == 0) break;
                    if (strcmp(confirm, "CANCEL") == 0) {
                        Serial.println(F("ERROR|Write cancelled"));
                        return;
                    }
                }
            }
        }
    }

    // ── Write pages directly from hex string ──
    Serial.println(F("READY_TO_WRITE"));

    const char* ptr = hexData;
    uint8_t pageNum = NTAG_USER_START;
    uint16_t bytesWritten = 0;

    while (bytesWritten < dataLen) {
        uint8_t page[4];
        uint8_t decoded = decodePageFromHex(ptr, page, dataLen - bytesWritten);
        if (decoded == 0) {
            Serial.println(F("WRITE_FAIL|Bad hex data"));
            return;
        }

        if (!nfc.ntag2xx_WritePage(pageNum, page)) {
            Serial.print(F("WRITE_FAIL|Page "));
            Serial.println(pageNum);
            return;
        }

        pageNum++;
        bytesWritten += 4;
    }

    // Clean terminator page
    if (pageNum <= tag.lastUserPage) {
        uint8_t z[4] = {0, 0, 0, 0};
        nfc.ntag2xx_WritePage(pageNum, z);
    }

    Serial.println(F("WRITE_COMPLETE"));

    // ── Verify: re-parse hex from cmdBuffer and compare ──
    ptr = hexData;  // Reset — hexData still points into cmdBuffer
    pageNum = NTAG_USER_START;
    bytesWritten = 0;
    bool verified = true;

    while (bytesWritten < dataLen) {
        uint8_t expected[4];
        uint8_t decoded = decodePageFromHex(ptr, expected, dataLen - bytesWritten);
        if (decoded == 0) { verified = false; break; }

        uint8_t readBack[4];
        if (!nfc.ntag2xx_ReadPage(pageNum, readBack)) { verified = false; break; }
        if (memcmp(expected, readBack, decoded) != 0)  { verified = false; break; }

        pageNum++;
        bytesWritten += 4;
    }

    Serial.print(verified ? F("VERIFY_OK|") : F("WRITE_OK|"));
    Serial.println(uidStr);
}

// ── ERASE TAG ─────────────────────────────────────────────────
void handleErase() {
    if (!waitForCard()) return;

    TagInfo tag = detectTagType();
    uint8_t z[4] = {0, 0, 0, 0};

    for (uint16_t page = NTAG_USER_START; page <= tag.lastUserPage; page++) {
        nfc.ntag2xx_WritePage(page, z);  // Skip failures (locked pages)
    }

    Serial.print(F("ERASE_OK|"));
    Serial.println(uidStr);
}

// ── READ TAG (streamed — no buffer) ───────────────────────────
void handleRead() {
    if (!waitForCard()) return;

    TagInfo tag = detectTagType();

    // Read first 2 pages (8 bytes) to parse TLV header
    uint8_t hdr[8];
    if (!nfc.ntag2xx_ReadPage(NTAG_USER_START, hdr) ||
        !nfc.ntag2xx_ReadPage(NTAG_USER_START + 1, hdr + 4)) {
        Serial.println(F("ERROR|Could not read tag"));
        return;
    }

    if (hdr[0] == 0x00) {
        Serial.println(F("DATA|EMPTY"));
        return;
    }

    // Determine total NDEF bytes to output
    uint16_t ndefLen;
    if (hdr[0] == 0x03) {
        if (hdr[1] == 0xFF) {
            ndefLen = ((uint16_t)hdr[2] << 8) | hdr[3];
            ndefLen += 5;  // 3-byte TLV header + len(2) + terminator
        } else {
            ndefLen = hdr[1] + 3;  // 1-byte TLV header + len(1) + terminator
        }
    } else {
        ndefLen = tag.userBytes;  // Not standard NDEF — dump everything
    }

    // ── Stream hex output page by page ──
    Serial.print(F("DATA|"));

    uint16_t bytesOut = 0;

    // Output the 8 header bytes we already have
    for (uint8_t i = 0; i < 8 && bytesOut < ndefLen; i++, bytesOut++) {
        printHexByte(hdr[i]);
    }

    // Continue from page 6 onward
    uint16_t page = NTAG_USER_START + 2;
    while (bytesOut < ndefLen && page <= tag.lastUserPage) {
        uint8_t pg[4];
        if (!nfc.ntag2xx_ReadPage(page, pg)) break;

        for (uint8_t i = 0; i < 4 && bytesOut < ndefLen; i++, bytesOut++) {
            printHexByte(pg[i]);
        }
        page++;
    }

    Serial.println();
}

// ── LOCK TAG ──────────────────────────────────────────────────
void handleLock() {
    if (!waitForCard()) return;

    uint8_t page2[4];
    if (!nfc.ntag2xx_ReadPage(2, page2)) {
        Serial.println(F("ERROR|Cannot read lock page"));
        return;
    }

    page2[2] = 0xFF;
    page2[3] = 0xFF;

    if (nfc.ntag2xx_WritePage(2, page2)) {
        TagInfo tag = detectTagType();
        uint8_t dynLock[4] = {0xFF, 0xFF, 0xFF, 0xFF};
        nfc.ntag2xx_WritePage(tag.lastUserPage + 1, dynLock);

        Serial.print(F("LOCK_OK|"));
        Serial.println(uidStr);
    } else {
        Serial.println(F("ERROR|Lock failed"));
    }
}

// ── TAG INFO ──────────────────────────────────────────────────
void handleInfo() {
    if (!waitForCard()) return;

    TagInfo tag = detectTagType();
    bool locked = isTagLocked();

    Serial.print(F("TAG_INFO|uid:"));
    Serial.print(uidStr);
    Serial.print(F(",type:"));
    Serial.print(tag.type);
    Serial.print(F(",size:"));
    Serial.print(tag.userBytes);
    Serial.print(F(",locked:"));
    Serial.println(locked ? '1' : '0');
}
