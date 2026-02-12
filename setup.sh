#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
#  TechTap — One-Command Setup for Linux / macOS
#  Installs Python 3, pip, ADB (platform-tools), clones the repo,
#  installs dependencies, and launches TechTap.
#
#  Usage:
#    curl -sSL https://raw.githubusercontent.com/CharlesNaig/TechTap/main/setup.sh | bash
# ─────────────────────────────────────────────────────────────────────
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

banner() {
    echo -e "${CYAN}"
    echo '  _______________  _  _____  _   ___'
    echo ' |_   _| __| __| || ||_   _|/ \ | _ \'
    echo '   | | | _|| _|| __ |  | | / _ \|  _/'
    echo '   |_| |___|___|_||_|  |_|/_/ \_\_|'
    echo ''
    echo -e "  Smart Identity via Tap — Linux/macOS Setup${NC}"
    echo ''
}

info()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
fail()    { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step()    { echo -e "\n${CYAN}── $1 ──${NC}"; }

banner

# ── Detect OS & package manager ──────────────────────────────────────
step "Detecting system"

OS="$(uname -s)"
case "$OS" in
    Linux*)  PLATFORM="linux" ;;
    Darwin*) PLATFORM="macos" ;;
    *)       fail "Unsupported OS: $OS. Use setup.ps1 for Windows." ;;
esac
info "Platform: $PLATFORM ($(uname -m))"

# Detect package manager
PKG_MGR=""
if command -v apt-get &>/dev/null; then
    PKG_MGR="apt"
elif command -v dnf &>/dev/null; then
    PKG_MGR="dnf"
elif command -v pacman &>/dev/null; then
    PKG_MGR="pacman"
elif command -v zypper &>/dev/null; then
    PKG_MGR="zypper"
elif command -v brew &>/dev/null; then
    PKG_MGR="brew"
fi
info "Package manager: ${PKG_MGR:-none detected}"

# ── Install Python 3 ────────────────────────────────────────────────
step "Checking Python 3"

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -n "$PYTHON" ]; then
    info "Python found: $($PYTHON --version)"
else
    warn "Python 3.10+ not found. Installing..."
    case "$PKG_MGR" in
        apt)
            sudo apt-get update -qq
            sudo apt-get install -y python3 python3-pip python3-venv
            ;;
        dnf)
            sudo dnf install -y python3 python3-pip
            ;;
        pacman)
            sudo pacman -Sy --noconfirm python python-pip
            ;;
        zypper)
            sudo zypper install -y python3 python3-pip
            ;;
        brew)
            brew install python@3
            ;;
        *)
            fail "Cannot auto-install Python. Please install Python 3.10+ manually:\n  https://www.python.org/downloads/"
            ;;
    esac
    # Re-detect
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    done
    [ -z "$PYTHON" ] && fail "Python installation failed."
    info "Python installed: $($PYTHON --version)"
fi

# ── Install pip ──────────────────────────────────────────────────────
step "Checking pip"

if $PYTHON -m pip --version &>/dev/null; then
    info "pip found: $($PYTHON -m pip --version 2>&1 | head -1)"
else
    warn "pip not found. Installing..."
    case "$PKG_MGR" in
        apt)    sudo apt-get install -y python3-pip ;;
        dnf)    sudo dnf install -y python3-pip ;;
        pacman) sudo pacman -Sy --noconfirm python-pip ;;
        zypper) sudo zypper install -y python3-pip ;;
        brew)   : ;; # brew python includes pip
        *)
            curl -sSL https://bootstrap.pypa.io/get-pip.py | $PYTHON
            ;;
    esac
    $PYTHON -m pip --version &>/dev/null || fail "pip installation failed."
    info "pip installed."
fi

# ── Install git ──────────────────────────────────────────────────────
step "Checking git"

if command -v git &>/dev/null; then
    info "git found: $(git --version)"
else
    warn "git not found. Installing..."
    case "$PKG_MGR" in
        apt)    sudo apt-get install -y git ;;
        dnf)    sudo dnf install -y git ;;
        pacman) sudo pacman -Sy --noconfirm git ;;
        zypper) sudo zypper install -y git ;;
        brew)   brew install git ;;
        *)      fail "Cannot auto-install git. Please install git manually." ;;
    esac
    info "git installed."
fi

# ── Install ADB (platform-tools) ────────────────────────────────────
step "Checking ADB (Android Platform Tools)"

ADB_OK=false
if command -v adb &>/dev/null; then
    ADB_OK=true
    info "ADB found: $(adb version 2>&1 | head -1)"
fi

if [ "$ADB_OK" = false ]; then
    warn "ADB not found. Installing..."

    case "$PKG_MGR" in
        apt)
            sudo apt-get install -y adb
            ADB_OK=true
            ;;
        dnf)
            sudo dnf install -y android-tools
            ADB_OK=true
            ;;
        pacman)
            sudo pacman -Sy --noconfirm android-tools
            ADB_OK=true
            ;;
        brew)
            brew install android-platform-tools
            ADB_OK=true
            ;;
    esac

    # Fallback: download platform-tools directly from Google
    if [ "$ADB_OK" = false ]; then
        warn "Downloading platform-tools from Google..."
        INSTALL_DIR="$HOME/.local/share/techtap"
        mkdir -p "$INSTALL_DIR"

        if [ "$PLATFORM" = "macos" ]; then
            PT_URL="https://dl.google.com/android/repository/platform-tools-latest-darwin.zip"
        else
            PT_URL="https://dl.google.com/android/repository/platform-tools-latest-linux.zip"
        fi

        curl -sSL "$PT_URL" -o /tmp/platform-tools.zip
        unzip -qo /tmp/platform-tools.zip -d "$INSTALL_DIR"
        rm /tmp/platform-tools.zip

        # Add to PATH for this session and shell profile
        export PATH="$INSTALL_DIR/platform-tools:$PATH"
        SHELL_RC=""
        if [ -f "$HOME/.bashrc" ]; then SHELL_RC="$HOME/.bashrc"
        elif [ -f "$HOME/.zshrc" ]; then SHELL_RC="$HOME/.zshrc"
        fi
        if [ -n "$SHELL_RC" ]; then
            LINE="export PATH=\"$INSTALL_DIR/platform-tools:\$PATH\"  # TechTap ADB"
            grep -qF "TechTap ADB" "$SHELL_RC" 2>/dev/null || echo "$LINE" >> "$SHELL_RC"
            info "Added ADB to $SHELL_RC"
        fi
        ADB_OK=true
    fi

    if [ "$ADB_OK" = true ]; then
        info "ADB installed: $(adb version 2>&1 | head -1)"
    else
        warn "Could not install ADB automatically. Install manually:"
        warn "  https://developer.android.com/tools/releases/platform-tools"
    fi
fi

# ── Clone TechTap ────────────────────────────────────────────────────
step "Setting up TechTap"

INSTALL_TO="$HOME/TechTap"

if [ -d "$INSTALL_TO/.git" ]; then
    info "TechTap already cloned at $INSTALL_TO — pulling latest..."
    cd "$INSTALL_TO"
    git pull --ff-only origin main 2>/dev/null || git pull origin main
else
    info "Cloning TechTap..."
    git clone https://github.com/CharlesNaig/TechTap.git "$INSTALL_TO"
    cd "$INSTALL_TO"
fi

# ── Install Python dependencies ──────────────────────────────────────
step "Installing Python dependencies"

$PYTHON -m pip install --upgrade pip --quiet 2>/dev/null || true
$PYTHON -m pip install -r requirements.txt --quiet
info "All Python packages installed."

# ── Done ─────────────────────────────────────────────────────────────
step "Setup complete!"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  TechTap is ready!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  To start TechTap:"
echo -e "    ${CYAN}cd $INSTALL_TO && $PYTHON -m techtap${NC}"
echo ""
echo "  For Phone NFC mode, make sure to:"
echo "    1. Enable USB Debugging on your phone"
echo "    2. Connect phone via USB"
echo "    3. Set reader_mode to 'phone' in config.json"
echo ""

# Ask if user wants to launch now
read -rp "Launch TechTap now? [Y/n] " LAUNCH
case "$LAUNCH" in
    [nN]*) echo "Run later: cd $INSTALL_TO && $PYTHON -m techtap" ;;
    *)     $PYTHON -m techtap ;;
esac
