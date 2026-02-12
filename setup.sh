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

# ═══════════════════════════════════════════════════════════════════
#  PHASE 1: System packages (Python, pip, venv, git, ADB, unzip)
# ═══════════════════════════════════════════════════════════════════

step "Checking system dependencies"

# Build a list of what needs to be installed
# NOTE: We do NOT check/install pip system-wide (PEP 668 blocks it on
#       modern Debian/Ubuntu). The venv ships its own pip.
NEED_PYTHON=false
NEED_VENV=false
NEED_GIT=false
NEED_ADB=false
NEED_UNZIP=false

# ── Python 3 ─────────────────────────────────────────────────────────
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
[ -z "$PYTHON" ] && NEED_PYTHON=true

# ── venv ─────────────────────────────────────────────────────────────
if [ -n "$PYTHON" ]; then
    $PYTHON -m venv --help &>/dev/null 2>&1 || NEED_VENV=true
else
    NEED_VENV=true
fi

# ── git ──────────────────────────────────────────────────────────────
command -v git &>/dev/null || NEED_GIT=true

# ── unzip (needed for ADB fallback download) ─────────────────────────
command -v unzip &>/dev/null || NEED_UNZIP=true

# ── ADB ──────────────────────────────────────────────────────────────
command -v adb &>/dev/null || NEED_ADB=true

# ── Report what's found / missing ────────────────────────────────────
[ "$NEED_PYTHON" = false ] && info "Python:  $($PYTHON --version)" || warn "Python 3.10+: NOT FOUND"
[ "$NEED_VENV" = false ]   && info "venv:    found"                 || warn "venv:         NOT FOUND"
[ "$NEED_GIT" = false ]    && info "git:     $(git --version)"      || warn "git:          NOT FOUND"
[ "$NEED_UNZIP" = false ]  && info "unzip:   found"                 || warn "unzip:        NOT FOUND"
[ "$NEED_ADB" = false ]    && info "ADB:     found"                 || warn "ADB:          NOT FOUND"
info "pip:     will use venv's built-in pip (PEP 668 safe)"

# ── Install missing system packages ──────────────────────────────────
APT_PKGS="" DNF_PKGS="" PAC_PKGS="" ZYP_PKGS="" BREW_PKGS=""

if [ "$NEED_PYTHON" = true ]; then
    case "$PKG_MGR" in
        apt)    APT_PKGS+=" python3-full" ;; # python3-full includes venv + pip
        dnf)    DNF_PKGS+=" python3" ;;
        pacman) PAC_PKGS+=" python" ;;
        zypper) ZYP_PKGS+=" python3" ;;
        brew)   BREW_PKGS+=" python@3" ;;
        *)      fail "Cannot auto-install Python 3.10+. Install manually:\n  https://www.python.org/downloads/" ;;
    esac
fi

# pip is NOT installed system-wide — the venv provides its own pip.
# On Debian 12+ / Ubuntu 23.04+, system pip is blocked by PEP 668.

if [ "$NEED_VENV" = true ]; then
    case "$PKG_MGR" in
        apt)    APT_PKGS+=" python3-venv" ;;
        dnf)    DNF_PKGS+=" python3-libs" ;; # venv ships with python3 on Fedora
        pacman) : ;; # venv ships with python on Arch
        zypper) ZYP_PKGS+=" python3-venv" ;;
        brew)   : ;; # included with python
        *)      : ;;
    esac
fi

if [ "$NEED_GIT" = true ]; then
    case "$PKG_MGR" in
        apt)    APT_PKGS+=" git" ;;
        dnf)    DNF_PKGS+=" git" ;;
        pacman) PAC_PKGS+=" git" ;;
        zypper) ZYP_PKGS+=" git" ;;
        brew)   BREW_PKGS+=" git" ;;
        *)      fail "Cannot auto-install git. Install manually." ;;
    esac
fi

if [ "$NEED_UNZIP" = true ]; then
    case "$PKG_MGR" in
        apt)    APT_PKGS+=" unzip" ;;
        dnf)    DNF_PKGS+=" unzip" ;;
        pacman) PAC_PKGS+=" unzip" ;;
        zypper) ZYP_PKGS+=" unzip" ;;
        brew)   : ;; # macOS has unzip built-in
        *)      : ;;
    esac
fi

# Run a single install command per package manager
if [ -n "$APT_PKGS" ]; then
    step "Installing system packages via apt"
    sudo apt-get update -qq
    sudo apt-get install -y $APT_PKGS
fi
if [ -n "$DNF_PKGS" ]; then
    step "Installing system packages via dnf"
    sudo dnf install -y $DNF_PKGS
fi
if [ -n "$PAC_PKGS" ]; then
    step "Installing system packages via pacman"
    sudo pacman -Sy --noconfirm $PAC_PKGS
fi
if [ -n "$ZYP_PKGS" ]; then
    step "Installing system packages via zypper"
    sudo zypper install -y $ZYP_PKGS
fi
if [ -n "$BREW_PKGS" ]; then
    step "Installing system packages via brew"
    brew install $BREW_PKGS
fi

# Re-detect Python after install
if [ -z "$PYTHON" ]; then
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    done
    [ -z "$PYTHON" ] && fail "Python installation failed."
fi
info "Using Python: $($PYTHON --version)"

# Ensure venv module works (pip comes from venv, not system)
if ! $PYTHON -m venv --help &>/dev/null 2>&1; then
    fail "Python venv module not available. Install python3-venv for your distro."
fi
info "venv module ready"

# ── Install ADB (platform-tools) ────────────────────────────────────
if [ "$NEED_ADB" = true ]; then
    step "Installing ADB (Android Platform Tools)"

    ADB_OK=false
    case "$PKG_MGR" in
        apt)
            sudo apt-get install -y adb && ADB_OK=true
            ;;
        dnf)
            sudo dnf install -y android-tools && ADB_OK=true
            ;;
        pacman)
            sudo pacman -Sy --noconfirm android-tools && ADB_OK=true
            ;;
        brew)
            brew install android-platform-tools && ADB_OK=true
            ;;
    esac

    # Fallback: download platform-tools directly from Google
    if [ "$ADB_OK" = false ]; then
        warn "Downloading platform-tools from Google..."
        ADB_DIR="$HOME/.local/share/techtap"
        mkdir -p "$ADB_DIR"

        if [ "$PLATFORM" = "macos" ]; then
            PT_URL="https://dl.google.com/android/repository/platform-tools-latest-darwin.zip"
        else
            PT_URL="https://dl.google.com/android/repository/platform-tools-latest-linux.zip"
        fi

        curl -sSL "$PT_URL" -o /tmp/platform-tools.zip
        unzip -qo /tmp/platform-tools.zip -d "$ADB_DIR"
        rm -f /tmp/platform-tools.zip

        export PATH="$ADB_DIR/platform-tools:$PATH"

        # Persist to shell profile
        SHELL_RC=""
        [ -f "$HOME/.bashrc" ] && SHELL_RC="$HOME/.bashrc"
        [ -f "$HOME/.zshrc" ]  && SHELL_RC="$HOME/.zshrc"
        if [ -n "$SHELL_RC" ]; then
            LINE="export PATH=\"$ADB_DIR/platform-tools:\$PATH\"  # TechTap ADB"
            grep -qF "TechTap ADB" "$SHELL_RC" 2>/dev/null || echo "$LINE" >> "$SHELL_RC"
            info "Added ADB to $SHELL_RC"
        fi
        ADB_OK=true
    fi

    if [ "$ADB_OK" = true ] && command -v adb &>/dev/null; then
        info "ADB installed: $(adb version 2>&1 | head -1)"
    else
        warn "Could not install ADB. Install manually:"
        warn "  https://developer.android.com/tools/releases/platform-tools"
    fi
else
    info "ADB: $(adb version 2>&1 | head -1)"
fi

# ═══════════════════════════════════════════════════════════════════
#  PHASE 2: Clone repository
# ═══════════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════════
#  PHASE 3: Create & activate virtual environment, install packages
# ═══════════════════════════════════════════════════════════════════

VENV_DIR="$INSTALL_TO/.venv"

step "Setting up Python virtual environment"

if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/activate" ]; then
    info "Virtual environment already exists at .venv/"
else
    info "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
    info "Virtual environment created at .venv/"
fi

# Activate venv
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
info "Activated venv ($(python --version), $(which python))"

# Upgrade pip inside venv
step "Installing Python packages in venv"
python -m pip install --upgrade pip --quiet 2>/dev/null || true
python -m pip install -r requirements.txt --quiet
info "All Python packages installed in .venv/"

# Show installed packages summary
echo ""
python -m pip list --format=columns 2>/dev/null | head -20
TOTAL=$(python -m pip list 2>/dev/null | tail -n +3 | wc -l)
info "$TOTAL packages installed in virtual environment"

# ═══════════════════════════════════════════════════════════════════
#  PHASE 4: Done
# ═══════════════════════════════════════════════════════════════════

step "Setup complete!"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  TechTap is ready!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  To start TechTap:"
echo -e "    ${CYAN}cd $INSTALL_TO && source .venv/bin/activate && python -m techtap${NC}"
echo ""
echo "  For Phone NFC mode, make sure to:"
echo "    1. Enable USB Debugging on your phone"
echo "    2. Connect phone via USB"
echo "    3. Set reader_mode to 'phone' in config.json"
echo ""

# Ask if user wants to launch now
read -rp "Launch TechTap now? [Y/n] " LAUNCH
case "$LAUNCH" in
    [nN]*) echo "Run later: cd $INSTALL_TO && source .venv/bin/activate && python -m techtap" ;;
    *)     python -m techtap ;;
esac
