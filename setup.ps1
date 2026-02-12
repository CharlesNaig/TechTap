# ─────────────────────────────────────────────────────────────────────
#  TechTap — One-Command Setup for Windows (PowerShell)
#  Installs Python 3, pip, ADB (platform-tools), clones the repo,
#  installs dependencies, and launches TechTap.
#
#  Usage:
#    irm https://raw.githubusercontent.com/CharlesNaig/TechTap/main/setup.ps1 | iex
# ─────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

function Write-Banner {
    Write-Host ""
    Write-Host "  _______________  _  _____  _   ___"       -ForegroundColor Cyan
    Write-Host " |_   _| __| __| || ||_   _|/ \ | _ \"     -ForegroundColor Cyan
    Write-Host "   | | | _|| _|| __ |  | | / _ \|  _/"     -ForegroundColor Cyan
    Write-Host "   |_| |___|___|_||_|  |_|/_/ \_\_|"       -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Smart Identity via Tap - Windows Setup"    -ForegroundColor Cyan
    Write-Host ""
}

function Write-Step  { param($msg) Write-Host "`n-- $msg --" -ForegroundColor Cyan }
function Write-OK    { param($msg) Write-Host "[OK] $msg"    -ForegroundColor Green }
function Write-Warn  { param($msg) Write-Host "[!]  $msg"    -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "[X]  $msg"    -ForegroundColor Red; exit 1 }

Write-Banner

# ── Check if running as admin (not required but helpful) ─────────────
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# ── Detect winget ────────────────────────────────────────────────────
$hasWinget = $null -ne (Get-Command winget -ErrorAction SilentlyContinue)

# ── Install Python 3 ────────────────────────────────────────────────
Write-Step "Checking Python 3"

$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    $p = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($p) {
        $ver = & $cmd --version 2>&1 | Select-String -Pattern "(\d+)\.(\d+)" | ForEach-Object { $_.Matches[0] }
        if ($ver) {
            $major = [int]$ver.Groups[1].Value
            $minor = [int]$ver.Groups[2].Value
            if ($major -ge 3 -and $minor -ge 10) {
                $python = $cmd
                break
            }
        }
    }
}

if ($python) {
    $pyVer = & $python --version 2>&1
    Write-OK "Python found: $pyVer"
} else {
    Write-Warn "Python 3.10+ not found. Installing..."

    if ($hasWinget) {
        winget install Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
    } else {
        Write-Warn "winget not available. Downloading Python installer..."
        $pyUrl = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
        $pyInstaller = "$env:TEMP\python-installer.exe"
        Invoke-WebRequest -Uri $pyUrl -OutFile $pyInstaller -UseBasicParsing
        Write-Warn "Running Python installer (this may take a minute)..."
        Start-Process -Wait -FilePath $pyInstaller -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1"
        Remove-Item $pyInstaller -ErrorAction SilentlyContinue
    }

    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")

    foreach ($cmd in @("python", "python3", "py")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            $python = $cmd
            break
        }
    }

    if (-not $python) { Write-Fail "Python installation failed. Install manually: https://www.python.org/downloads/" }
    Write-OK "Python installed: $(& $python --version 2>&1)"
}

# ── Check pip ────────────────────────────────────────────────────────
Write-Step "Checking pip"

$pipOK = $false
try {
    $null = & $python -m pip --version 2>&1
    $pipOK = $true
} catch {}

if ($pipOK) {
    Write-OK "pip found"
} else {
    Write-Warn "Installing pip..."
    & $python -m ensurepip --upgrade 2>&1 | Out-Null
    Write-OK "pip installed"
}

# ── Check git ────────────────────────────────────────────────────────
Write-Step "Checking git"

if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-OK "git found: $(git --version)"
} else {
    Write-Warn "git not found. Installing..."
    if ($hasWinget) {
        winget install Git.Git --accept-package-agreements --accept-source-agreements --silent
    } else {
        $gitUrl = "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.2/Git-2.47.1.2-64-bit.exe"
        $gitInstaller = "$env:TEMP\git-installer.exe"
        Invoke-WebRequest -Uri $gitUrl -OutFile $gitInstaller -UseBasicParsing
        Start-Process -Wait -FilePath $gitInstaller -ArgumentList "/VERYSILENT", "/NORESTART"
        Remove-Item $gitInstaller -ErrorAction SilentlyContinue
    }
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Fail "git installation failed. Install manually: https://git-scm.com"
    }
    Write-OK "git installed"
}

# ── Install ADB (platform-tools) ────────────────────────────────────
Write-Step "Checking ADB (Android Platform Tools)"

$adbFound = $false

# Check PATH
if (Get-Command adb -ErrorAction SilentlyContinue) {
    $adbFound = $true
    Write-OK "ADB found in PATH: $(adb version 2>&1 | Select-Object -First 1)"
}

# Check common locations
if (-not $adbFound) {
    $commonPaths = @(
        "$env:LOCALAPPDATA\Android\Sdk\platform-tools",
        "C:\platform-tools",
        "C:\Android\platform-tools"
    )
    foreach ($p in $commonPaths) {
        if (Test-Path "$p\adb.exe") {
            $env:PATH = "$p;$env:PATH"
            $adbFound = $true
            Write-OK "ADB found at $p"
            break
        }
    }
}

if (-not $adbFound) {
    Write-Warn "ADB not found. Downloading platform-tools from Google..."

    $ptUrl   = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    $ptZip   = "$env:TEMP\platform-tools.zip"
    $ptDest  = "$env:LOCALAPPDATA\TechTap"

    New-Item -ItemType Directory -Path $ptDest -Force | Out-Null
    Invoke-WebRequest -Uri $ptUrl -OutFile $ptZip -UseBasicParsing
    Expand-Archive -Path $ptZip -DestinationPath $ptDest -Force
    Remove-Item $ptZip -ErrorAction SilentlyContinue

    $ptPath = "$ptDest\platform-tools"
    $env:PATH = "$ptPath;$env:PATH"

    # Persist to user PATH
    $userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    if ($userPath -notlike "*$ptPath*") {
        [System.Environment]::SetEnvironmentVariable("PATH", "$ptPath;$userPath", "User")
        Write-OK "Added ADB to user PATH (permanent)"
    }

    if (Test-Path "$ptPath\adb.exe") {
        Write-OK "ADB installed at $ptPath"
    } else {
        Write-Warn "ADB download may have failed. Install manually:"
        Write-Warn "  https://developer.android.com/tools/releases/platform-tools"
    }
}

# ── Clone TechTap ────────────────────────────────────────────────────
Write-Step "Setting up TechTap"

$installTo = "$env:USERPROFILE\Desktop\TechTap"

if (Test-Path "$installTo\.git") {
    Write-OK "TechTap already cloned at $installTo - pulling latest..."
    Push-Location $installTo
    git pull origin main 2>&1 | Out-Null
} else {
    Write-OK "Cloning TechTap..."
    git clone https://github.com/CharlesNaig/TechTap.git $installTo
    Push-Location $installTo
}

# ── Install Python dependencies ──────────────────────────────────────
Write-Step "Installing Python dependencies"

& $python -m pip install --upgrade pip --quiet 2>&1 | Out-Null
& $python -m pip install -r requirements.txt --quiet
Write-OK "All Python packages installed."

# ── Done ─────────────────────────────────────────────────────────────
Write-Step "Setup complete!"

Write-Host ""
Write-Host "  ======================================================" -ForegroundColor Green
Write-Host "    TechTap is ready!" -ForegroundColor Green
Write-Host "  ======================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  To start TechTap:"
Write-Host "    cd $installTo; $python -m techtap" -ForegroundColor Cyan
Write-Host ""
Write-Host "  For Phone NFC mode, make sure to:"
Write-Host "    1. Enable USB Debugging on your phone"
Write-Host "    2. Connect phone via USB"
Write-Host "    3. Set reader_mode to 'phone' in config.json"
Write-Host ""

$launch = Read-Host "Launch TechTap now? [Y/n]"
if ($launch -ne "n" -and $launch -ne "N") {
    & $python -m techtap
} else {
    Write-Host "Run later: cd $installTo; $python -m techtap"
}

Pop-Location
