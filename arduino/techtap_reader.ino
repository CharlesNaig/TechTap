/*
 * ══════════════════════════════════════════════════════════════
 *  TechTap — Arduino NFC Writer Firmware
 *  For PN532 NFC Module (I2C) + NTAG213/215/216 cards
 *
 *  Protocol (Serial @ 115200 baud):
 *    PC → Arduino:  COMMAND|DATA\n
 *    Arduino → PC:  RESPONSE|DATA\n
 *
 *  Supported commands:
 *    PING           → PONG
 *    WRITE_RAW|HEX  → TAP_CARD → WRITE_OK|UID  or  WRITE_FAIL|reason
 *    ERASE          → TAP_CARD → ERASE_OK|UID
 *    READ           → TAP_CARD → DATA|HEX
 *    LOCK           → TAP_CARD → LOCK_OK|UID
 *    INFO           → TAP_CARD → TAG_INFO|uid:XX,type:NTAG215,size:504,locked:0
 *
 *  Hardware:  PN532 via I2C (SDA=A4, SCL=A5 on Uno)
 *             or SPI (SS=10, SCK=13, MOSI=11, MISO=12)
 * ══════════════════════════════════════════════════════════════
 */

#include <Wire.h>
#include <Adafruit_PN532.h>

// ── Pin Configuration (I2C) ───────────────────────────────────
// For I2C: connect SDA→A4, SCL→A5 (Uno/Nano) or SDA→20, SCL→21 (Mega)
// IRQ and RST pins (optional, use -1 if not connected)
#define PN532_IRQ   2
#define PN532_RST   3

Adafruit_PN532 nfc(PN532_IRQ, PN532_RST);  // I2C mode

// ── If using SPI instead, uncomment below and comment I2C above ──
// #define PN532_SS    10
// Adafruit_PN532 nfc(PN532_SS);  // Hardware SPI

// ── Constants ─────────────────────────────────────────────────
#define SERIAL_BAUD    115200
#define MAX_DATA_SIZE  888       // NTAG216 max user bytes
#define CMD_TIMEOUT    30000     // 30s wait for card tap
#define READ_TIMEOUT   5000     // 5s read timeout

// NTAG page size = 4 bytes, user memory starts at page 4
#define NTAG_PAGE_SIZE      4
#define NTAG_USER_START     4

// NTAG type detection by capacity config pages
#define NTAG213_PAGES  45    // User: page 4-39  = 144 bytes
#define NTAG215_PAGES  135   // User: page 4-129 = 504 bytes
#define NTAG216_PAGES  231   // User: page 4-225 = 888 bytes

// ── Globals ───────────────────────────────────────────────────
char cmdBuffer[2048];
uint8_t dataBuffer[MAX_DATA_SIZE + 16];
uint16_t dataLen = 0;
uint8_t uid[7];
uint8_t uidLen;

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

    // Configure for NTAG reading/writing
    nfc.SAMConfig();
    nfc.setPassiveActivationRetries(0xFF);  // Retry indefinitely

    Serial.println(F("READY|TechTap Firmware v1.0"));
}

// ── Main Loop ─────────────────────────────────────────────────
void loop() {
    if (Serial.available()) {
        int len = Serial.readBytesUntil('\n', cmdBuffer, sizeof(cmdBuffer) - 1);
        cmdBuffer[len] = '\0';

        // Trim CR/LF
        while (len > 0 && (cmdBuffer[len - 1] == '\r' || cmdBuffer[len - 1] == '\n')) {
            cmdBuffer[--len] = '\0';
        }

        processCommand(cmdBuffer);
    }
}

// ── Command Router ────────────────────────────────────────────
void processCommand(char* cmd) {
    // Split command and data at '|'
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

// ── Hex String ↔ Bytes ────────────────────────────────────────
uint16_t hexToBytes(const char* hex, uint8_t* out, uint16_t maxLen) {
    uint16_t len = 0;
    while (*hex && *(hex + 1) && len < maxLen) {
        uint8_t hi = hexCharToNibble(*hex++);
        uint8_t lo = hexCharToNibble(*hex++);
        if (hi > 0x0F || lo > 0x0F) break;
        out[len++] = (hi << 4) | lo;
    }
    return len;
}

uint8_t hexCharToNibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return 0xFF;
}

void bytesToHex(const uint8_t* data, uint16_t len, char* out) {
    const char hex[] = "0123456789ABCDEF";
    for (uint16_t i = 0; i < len; i++) {
        out[i * 2]     = hex[(data[i] >> 4) & 0x0F];
        out[i * 2 + 1] = hex[data[i] & 0x0F];
    }
    out[len * 2] = '\0';
}

String uidToString() {
    String s = "";
    for (uint8_t i = 0; i < uidLen; i++) {
        if (uid[i] < 0x10) s += "0";
        s += String(uid[i], HEX);
    }
    s.toUpperCase();
    return s;
}

// ── Wait for Card ─────────────────────────────────────────────
bool waitForCard(unsigned long timeout = CMD_TIMEOUT) {
    Serial.println(F("TAP_CARD"));

    unsigned long start = millis();
    while (millis() - start < timeout) {
        if (nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLen, 1000)) {
            return true;
        }
    }

    Serial.println(F("ERROR|Timeout waiting for card"));
    return false;
}

// ── Detect NTAG Type ──────────────────────────────────────────
struct TagInfo {
    String type;
    uint16_t userBytes;
    uint16_t lastUserPage;
};

TagInfo detectTagType() {
    TagInfo info;
    info.type = "UNKNOWN";
    info.userBytes = 0;
    info.lastUserPage = 0;

    // Read Capability Container (page 3)
    uint8_t page3[4];
    if (nfc.ntag2xx_ReadPage(3, page3)) {
        // CC byte 2 = total size / 8
        uint16_t totalSize = page3[2] * 8;

        if (totalSize <= 144 + 48) {
            info.type = "NTAG213";
            info.userBytes = 144;
            info.lastUserPage = 39;
        } else if (totalSize <= 504 + 48) {
            info.type = "NTAG215";
            info.userBytes = 504;
            info.lastUserPage = 129;
        } else {
            info.type = "NTAG216";
            info.userBytes = 888;
            info.lastUserPage = 225;
        }
    }

    return info;
}

// ── Check Lock Status ─────────────────────────────────────────
bool isTagLocked() {
    // Read lock bytes (pages 2)
    uint8_t page2[4];
    if (nfc.ntag2xx_ReadPage(2, page2)) {
        // Static lock bytes are byte 2 and 3 of page 2
        return (page2[2] != 0x00 || page2[3] != 0x00);
    }
    return false;  // Assume unlocked if can't read
}

// ── WRITE RAW NDEF ────────────────────────────────────────────
void handleWriteRaw(const char* hexData) {
    if (!hexData || strlen(hexData) < 2) {
        Serial.println(F("ERROR|No data provided"));
        return;
    }

    // Decode hex to bytes
    dataLen = hexToBytes(hexData, dataBuffer, MAX_DATA_SIZE);
    if (dataLen == 0) {
        Serial.println(F("ERROR|Invalid hex data"));
        return;
    }

    // Wait for card
    if (!waitForCard()) return;

    // Detect tag type and check capacity
    TagInfo tag = detectTagType();
    if (dataLen > tag.userBytes) {
        Serial.print(F("ERROR|Data too large: "));
        Serial.print(dataLen);
        Serial.print(F(" bytes > "));
        Serial.print(tag.userBytes);
        Serial.println(F(" capacity"));
        return;
    }

    // Check if already has data (duplicate detection)
    uint8_t firstPage[4];
    if (nfc.ntag2xx_ReadPage(NTAG_USER_START, firstPage)) {
        if (firstPage[0] == 0x03 && firstPage[1] != 0x00) {
            // Has NDEF TLV data already
            Serial.print(F("DUPLICATE|"));
            Serial.println(uidToString());
            // Continue anyway — Python side decides whether to proceed

            // Wait for OVERWRITE confirmation or next command
            unsigned long waitStart = millis();
            while (millis() - waitStart < 10000) {
                if (Serial.available()) {
                    char confirm[32];
                    int cLen = Serial.readBytesUntil('\n', confirm, sizeof(confirm) - 1);
                    confirm[cLen] = '\0';
                    if (strcmp(confirm, "CONFIRM_OVERWRITE") == 0) {
                        break;
                    } else if (strcmp(confirm, "CANCEL") == 0) {
                        Serial.println(F("ERROR|Write cancelled"));
                        return;
                    }
                }
            }
        }
    }

    // ── Write NDEF data page by page ──
    Serial.println(F("READY_TO_WRITE"));

    uint16_t offset = 0;
    uint8_t pageNum = NTAG_USER_START;
    bool writeSuccess = true;

    while (offset < dataLen) {
        uint8_t pageData[4] = {0, 0, 0, 0};
        uint16_t remaining = dataLen - offset;
        uint16_t toCopy = (remaining < 4) ? remaining : 4;

        memcpy(pageData, dataBuffer + offset, toCopy);

        if (!nfc.ntag2xx_WritePage(pageNum, pageData)) {
            Serial.print(F("WRITE_FAIL|Page "));
            Serial.print(pageNum);
            Serial.println(F(" write failed"));
            writeSuccess = false;
            break;
        }

        pageNum++;
        offset += 4;
    }

    if (!writeSuccess) return;

    // Pad remaining with zeros (clean termination)
    uint8_t zeroPage[4] = {0, 0, 0, 0};
    if (offset == dataLen && pageNum <= tag.lastUserPage) {
        // Write one more zero page after data for clean end
        nfc.ntag2xx_WritePage(pageNum, zeroPage);
    }

    Serial.println(F("WRITE_COMPLETE"));

    // ── Verify ──
    bool verified = true;
    offset = 0;
    pageNum = NTAG_USER_START;

    while (offset < dataLen) {
        uint8_t readBack[4];
        if (!nfc.ntag2xx_ReadPage(pageNum, readBack)) {
            verified = false;
            break;
        }

        uint16_t remaining = dataLen - offset;
        uint16_t toCheck = (remaining < 4) ? remaining : 4;

        if (memcmp(readBack, dataBuffer + offset, toCheck) != 0) {
            verified = false;
            break;
        }

        pageNum++;
        offset += 4;
    }

    if (verified) {
        Serial.print(F("VERIFY_OK|"));
        Serial.println(uidToString());
    } else {
        Serial.print(F("WRITE_OK|"));
        Serial.println(uidToString());
    }
}

// ── ERASE TAG ─────────────────────────────────────────────────
void handleErase() {
    if (!waitForCard()) return;

    TagInfo tag = detectTagType();
    uint8_t zeroPage[4] = {0, 0, 0, 0};
    bool success = true;

    // Write zeros to all user pages
    for (uint16_t page = NTAG_USER_START; page <= tag.lastUserPage; page++) {
        if (!nfc.ntag2xx_WritePage(page, zeroPage)) {
            // Some pages might be locked, skip and continue
            continue;
        }
    }

    Serial.print(F("ERASE_OK|"));
    Serial.println(uidToString());
}

// ── READ TAG ──────────────────────────────────────────────────
void handleRead() {
    if (!waitForCard()) return;

    TagInfo tag = detectTagType();

    // Read all user pages
    uint8_t readBuffer[MAX_DATA_SIZE];
    uint16_t totalRead = 0;
    uint16_t ndefLen = 0;
    bool foundNdef = false;

    for (uint16_t page = NTAG_USER_START; page <= tag.lastUserPage && totalRead < MAX_DATA_SIZE; page++) {
        uint8_t pageData[4];
        if (!nfc.ntag2xx_ReadPage(page, pageData)) {
            break;
        }

        memcpy(readBuffer + totalRead, pageData, 4);
        totalRead += 4;

        // Detect NDEF message length to know when to stop
        if (!foundNdef && totalRead >= 2) {
            if (readBuffer[0] == 0x03) {  // NDEF TLV
                if (readBuffer[1] == 0xFF && totalRead >= 4) {
                    ndefLen = (readBuffer[2] << 8) | readBuffer[3];
                    ndefLen += 5;  // TLV header + terminator
                    foundNdef = true;
                } else if (readBuffer[1] != 0xFF) {
                    ndefLen = readBuffer[1] + 3;  // TLV header + terminator
                    foundNdef = true;
                }
            } else if (readBuffer[0] == 0x00) {
                // Empty tag
                Serial.println(F("DATA|EMPTY"));
                return;
            }
        }

        // Stop if we've read enough
        if (foundNdef && totalRead >= ndefLen) {
            break;
        }
    }

    if (totalRead == 0) {
        Serial.println(F("ERROR|Could not read tag"));
        return;
    }

    // Trim to actual NDEF length
    uint16_t outputLen = foundNdef ? min(totalRead, ndefLen) : totalRead;

    // Send as hex
    Serial.print(F("DATA|"));
    for (uint16_t i = 0; i < outputLen; i++) {
        if (readBuffer[i] < 0x10) Serial.print('0');
        Serial.print(readBuffer[i], HEX);
    }
    Serial.println();
}

// ── LOCK TAG ──────────────────────────────────────────────────
void handleLock() {
    if (!waitForCard()) return;

    // NTAG static lock bits are on page 2, bytes 2-3
    // Dynamic lock bits on page after user memory
    // Setting lock bytes makes the tag READ-ONLY permanently

    uint8_t page2[4];
    if (!nfc.ntag2xx_ReadPage(2, page2)) {
        Serial.println(F("ERROR|Cannot read lock page"));
        return;
    }

    // Set static lock bits (lock pages 3+)
    page2[2] = 0xFF;  // Lock pages 4-9
    page2[3] = 0xFF;  // Lock pages 10-15

    if (nfc.ntag2xx_WritePage(2, page2)) {
        // Also set dynamic lock bits
        TagInfo tag = detectTagType();
        uint16_t dynLockPage = tag.lastUserPage + 1;

        uint8_t dynLock[4] = {0xFF, 0xFF, 0xFF, 0xFF};
        nfc.ntag2xx_WritePage(dynLockPage, dynLock);

        Serial.print(F("LOCK_OK|"));
        Serial.println(uidToString());
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
    Serial.print(uidToString());
    Serial.print(F(",type:"));
    Serial.print(tag.type);
    Serial.print(F(",size:"));
    Serial.print(tag.userBytes);
    Serial.print(F(",locked:"));
    Serial.println(locked ? "1" : "0");
}
