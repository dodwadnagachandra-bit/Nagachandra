#!/usr/bin/env bash
# tools/setup-dev-env.sh — Ubuntu 24.04 Developer Environment Setup for EMS
#
# Takes a fresh Ubuntu 24.04 machine from zero to a fully working EMS dev
# environment. Each step is idempotent — safe to re-run after partial failure.
#
# Usage:
#   1. Clone the repo:  git clone git@github.com:ReVx-Energy/EMS.git && cd EMS
#   2. Run this script: bash tools/setup-dev-env.sh
#
# Must run as regular user (not root). Uses sudo for system packages.
# Requires git and SSH access to GitHub before running (steps 2-4 handle
# configuration but the repo must already be cloned).

set -euo pipefail

# ------------------------------------------------------------------ Helpers
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    GREEN='' RED='' YELLOW='' BLUE='' BOLD='' NC=''
fi

info()  { printf "${BLUE}[INFO]${NC}  %s\n" "$*"; }
ok()    { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
err()   { printf "${RED}[FAIL]${NC}  %s\n" "$*"; }
step()  { printf "\n${BOLD}[STEP %s/14]${NC} %s\n" "$1" "$2"; printf '%*s\n' 50 '' | tr ' ' '-'; }
ask()   { printf "${YELLOW}[?]${NC} %s" "$*"; }

TOTAL_STEPS=14
INSTALLED=()

# ------------------------------------------------------------------ Guards
if [ "$(id -u)" -eq 0 ]; then
    err "Do not run this script as root. Run as your normal user — it will sudo when needed."
    exit 1
fi

if ! grep -qi 'ubuntu' /etc/os-release 2>/dev/null; then
    warn "This script is designed for Ubuntu 24.04. Proceeding anyway..."
fi

echo ""
printf "${BOLD}EMS Developer Environment Setup${NC}\n"
echo "This script will install all tools needed for EMS development."
echo "It will prompt for sudo password and interactive input as needed."
echo ""

# ================================================================== STEP 1
step 1 "System update"

sudo apt-get update -y
sudo apt-get upgrade -y
ok "System updated"
INSTALLED+=("system packages (updated)")

# ================================================================== STEP 2
step 2 "Git setup"

if ! command -v git &>/dev/null; then
    sudo apt-get install -y git
    INSTALLED+=("git")
else
    ok "git already installed ($(git --version))"
fi

# Configure user identity if not set
if [ -z "$(git config --global user.name 2>/dev/null || true)" ]; then
    ask "Git user name: "
    read -r GIT_NAME
    git config --global user.name "$GIT_NAME"
fi

if [ -z "$(git config --global user.email 2>/dev/null || true)" ]; then
    ask "Git email: "
    read -r GIT_EMAIL
    git config --global user.email "$GIT_EMAIL"
fi

git config --global init.defaultBranch master
git config --global pull.rebase true
ok "Git configured: $(git config --global user.name) <$(git config --global user.email)>"

# ================================================================== STEP 3
step 3 "SSH key for GitHub"

if [ ! -f "$HOME/.ssh/id_ed25519" ]; then
    EMAIL=$(git config --global user.email)
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -t ed25519 -C "$EMAIL" -f "$HOME/.ssh/id_ed25519" -N ""
    INSTALLED+=("SSH key (ed25519)")
    echo ""
    info "Your public key:"
    echo ""
    cat "$HOME/.ssh/id_ed25519.pub"
    echo ""
    info "Add this key to GitHub: https://github.com/settings/ssh/new"
    ask "Press Enter after adding the key to GitHub..."
    read -r
    echo ""
    info "Testing SSH connection to GitHub..."
    if ssh -T git@github.com 2>&1 | grep -qi "success\|authenticated"; then
        ok "SSH connection to GitHub verified"
    else
        warn "Could not verify GitHub SSH access — you may need to add the key. Continuing..."
    fi
else
    ok "SSH key already exists (~/.ssh/id_ed25519)"
fi

# ================================================================== STEP 4
step 4 "Verify EMS repository"

# This is a private repo — must be cloned before running this script.
# Find the repo root by walking up from the script's location or cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/../Makefile" ] && grep -q "EMS monorepo" "$SCRIPT_DIR/../Makefile" 2>/dev/null; then
    REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
elif [ -f "Makefile" ] && grep -q "EMS monorepo" Makefile 2>/dev/null; then
    REPO_DIR="$(pwd)"
else
    err "Could not find EMS repo. This is a private repo — clone it first:"
    echo ""
    echo "  git clone git@github.com:ReVx-Energy/EMS.git"
    echo "  cd EMS"
    echo "  bash tools/setup-dev-env.sh"
    echo ""
    exit 1
fi

cd "$REPO_DIR"
ok "EMS repo at $REPO_DIR (branch: $(git branch --show-current))"
info "Working directory: $(pwd)"

# ================================================================== STEP 5
step 5 "System packages (build tools, cross-compiler, CAN utils)"

PACKAGES=(
    build-essential
    cmake
    ninja-build
    gcc-aarch64-linux-gnu
    g++-aarch64-linux-gnu
    clang-format
    libgpiod-dev
    libzmq3-dev
    can-utils
    socat
    curl
    jq
    wget
    gnupg
)

sudo apt-get install -y "${PACKAGES[@]}"
ok "System packages installed"
INSTALLED+=("cmake, ninja, gcc, cross-compiler, can-utils, socat, etc.")

# ================================================================== STEP 6
step 6 "uv (Python package manager) + Python 3.12"

if ! command -v uv &>/dev/null; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    INSTALLED+=("uv")
fi

# Source uv into current session
if [ -f "$HOME/.local/bin/env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.local/bin/env"
elif [ -d "$HOME/.local/bin" ]; then
    export PATH="$HOME/.local/bin:$PATH"
fi

if command -v uv &>/dev/null; then
    ok "uv $(uv --version)"
    info "Installing Python 3.12..."
    uv python install 3.12
    ok "Python 3.12 installed"
else
    err "uv installation failed — check PATH and retry"
    exit 1
fi

# ================================================================== STEP 7
step 7 "bun (JavaScript runtime for HMI)"

if ! command -v bun &>/dev/null; then
    info "Installing bun..."
    curl -fsSL https://bun.sh/install | bash
    INSTALLED+=("bun")
fi

# Source bun into current session
if [ -d "$HOME/.bun/bin" ]; then
    export PATH="$HOME/.bun/bin:$PATH"
fi

if command -v bun &>/dev/null; then
    ok "bun $(bun --version)"
else
    err "bun installation failed — check PATH and retry"
    exit 1
fi

# ================================================================== STEP 8
step 8 "Node.js 22 LTS (for Claude Code CLI)"

if command -v node &>/dev/null; then
    NODE_MAJOR=$(node --version | cut -d. -f1 | tr -d 'v')
    if [ "$NODE_MAJOR" -ge 22 ]; then
        ok "Node.js $(node --version) already installed"
    else
        info "Node.js $(node --version) found but v22+ needed. Upgrading..."
        curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
        sudo apt-get install -y nodejs
        INSTALLED+=("Node.js 22 LTS (upgraded)")
    fi
else
    info "Installing Node.js 22 LTS from NodeSource..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y nodejs
    INSTALLED+=("Node.js 22 LTS")
fi

ok "Node.js $(node --version), npm $(npm --version)"

# ================================================================== STEP 9
step 9 "VS Code"

if ! command -v code &>/dev/null; then
    info "Installing VS Code..."
    wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /tmp/ms-vscode.gpg
    sudo install -D -o root -g root -m 644 /tmp/ms-vscode.gpg /etc/apt/keyrings/microsoft.gpg
    echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/code stable main" | \
        sudo tee /etc/apt/sources.list.d/vscode.list > /dev/null
    rm -f /tmp/ms-vscode.gpg
    sudo apt-get update -y
    sudo apt-get install -y code
    INSTALLED+=("VS Code")
    ok "VS Code installed"
else
    ok "VS Code already installed ($(code --version | head -1))"
fi

# ================================================================== STEP 10
step 10 "VS Code extensions"

if command -v code &>/dev/null && [ -f .vscode/extensions.json ]; then
    info "Installing recommended extensions..."
    # Parse extensions from JSON (jq is installed in step 5)
    EXTENSIONS=$(jq -r '.recommendations[]' .vscode/extensions.json)
    EXT_COUNT=0
    EXT_TOTAL=$(echo "$EXTENSIONS" | wc -l)
    while IFS= read -r ext; do
        EXT_COUNT=$((EXT_COUNT + 1))
        printf "  (%d/%d) %s ... " "$EXT_COUNT" "$EXT_TOTAL" "$ext"
        if code --install-extension "$ext" --force >/dev/null 2>&1; then
            printf "${GREEN}done${NC}\n"
        else
            printf "${YELLOW}skipped${NC}\n"
        fi
    done <<< "$EXTENSIONS"
    INSTALLED+=("VS Code extensions ($EXT_TOTAL)")
    ok "VS Code extensions installed"
else
    warn "VS Code not available or extensions.json not found — skipping extensions"
fi

# ================================================================== STEP 11
step 11 "Claude Code CLI"

if ! command -v claude &>/dev/null; then
    info "Installing Claude Code..."
    sudo npm install -g @anthropic-ai/claude-code
    INSTALLED+=("Claude Code CLI")
    ok "Claude Code installed"
else
    ok "Claude Code already installed"
fi

# ================================================================== STEP 12
step 12 "EMS dev group + passwordless sudo + /run/ems"

# Create 'ems' group if it doesn't exist
if ! getent group ems &>/dev/null; then
    sudo groupadd ems
    INSTALLED+=("ems group")
    ok "Created 'ems' group"
else
    ok "'ems' group already exists"
fi

# Add current user to 'ems' group
if ! id -nG "$USER" | grep -qw ems; then
    sudo usermod -aG ems "$USER"
    INSTALLED+=("$USER added to ems group")
    ok "Added $USER to 'ems' group"
    warn "Group membership takes effect on next login (or run: newgrp ems)"
else
    ok "$USER is already in 'ems' group"
fi

# Install sudoers drop-in for passwordless vcan/ip-link commands
if [ ! -f /etc/sudoers.d/ems-dev ]; then
    sudo cp deploy/sudoers/ems-dev.sudo /etc/sudoers.d/ems-dev
    sudo chmod 0440 /etc/sudoers.d/ems-dev
    INSTALLED+=("sudoers: passwordless vcan/ip-link for ems group")
    ok "Installed /etc/sudoers.d/ems-dev (passwordless vcan setup)"
else
    ok "Sudoers drop-in already installed"
fi

# Install tmpfiles config for /run/ems (persists across reboots)
if [ ! -f /etc/tmpfiles.d/ems-dev.conf ]; then
    sudo cp deploy/tmpfiles/ems-dev.conf /etc/tmpfiles.d/ems-dev.conf
    sudo systemd-tmpfiles --create /etc/tmpfiles.d/ems-dev.conf
    INSTALLED+=("tmpfiles: /run/ems auto-created on boot")
    ok "Installed /etc/tmpfiles.d/ems-dev.conf (/run/ems created on boot)"
else
    ok "tmpfiles config already installed"
fi

# Create /run/ems now if it doesn't exist
if [ ! -d /run/ems ]; then
    sudo mkdir -p /run/ems
    sudo chgrp ems /run/ems
    sudo chmod 0775 /run/ems
    ok "/run/ems created"
else
    ok "/run/ems already exists"
fi

# ================================================================== STEP 12b
step 12 "Kernel modules (vcan) + persistence"

# Load modules now
sudo modprobe vcan can can_raw || warn "Could not load CAN kernel modules"
sudo ip link add dev vcan0 type vcan 2>/dev/null || true
sudo ip link set up vcan0 2>/dev/null || true

if ip link show vcan0 &>/dev/null; then
    ok "vcan0 interface is up"
else
    warn "vcan0 could not be created — CAN simulators will not work"
fi

# Persist module loading across reboots
if [ ! -f /etc/modules-load.d/ems-can.conf ]; then
    printf "vcan\ncan\ncan_raw\n" | sudo tee /etc/modules-load.d/ems-can.conf > /dev/null
    ok "CAN modules will load on boot (/etc/modules-load.d/ems-can.conf)"
else
    ok "CAN module persistence already configured"
fi

# Auto-create vcan0 via systemd-networkd
if [ ! -f /etc/systemd/network/80-vcan0.netdev ]; then
    cat <<'NETDEV' | sudo tee /etc/systemd/network/80-vcan0.netdev > /dev/null
[NetDev]
Name=vcan0
Kind=vcan
NETDEV
    cat <<'NETWORK' | sudo tee /etc/systemd/network/80-vcan0.network > /dev/null
[Match]
Name=vcan0
NETWORK
    sudo systemctl enable --now systemd-networkd
    ok "vcan0 will auto-create on boot (systemd-networkd)"
    INSTALLED+=("vcan0 persistence (systemd-networkd)")
else
    ok "vcan0 systemd-networkd config already exists"
fi

# ================================================================== STEP 13
step 13 "Project setup (CLAUDE.md, uv sync, bun install, verify)"

# Copy shared CLAUDE.md to project root (gitignored, so each dev gets it locally)
if [ -f architecture/CLAUDE.md ]; then
    cp architecture/CLAUDE.md CLAUDE.md
    ok "Copied architecture/CLAUDE.md → CLAUDE.md (shared team conventions)"
else
    warn "architecture/CLAUDE.md not found — skipping CLAUDE.md setup"
fi

info "Running make setup..."
make setup
ok "Project dependencies installed and verified"

# ================================================================== STEP 14
step 14 "Build check"

info "Running make build..."
if make build; then
    ok "C build succeeded"
else
    err "Build failed — check compiler output above"
    warn "You can retry with: make build"
fi

# ================================================================== Summary
echo ""
printf '%*s\n' 60 '' | tr ' ' '='
printf "${BOLD} EMS Dev Environment Setup Complete${NC}\n"
printf '%*s\n' 60 '' | tr ' ' '='
echo ""

if [ ${#INSTALLED[@]} -gt 0 ]; then
    info "Newly installed:"
    for item in "${INSTALLED[@]}"; do
        printf "  ${GREEN}+${NC} %s\n" "$item"
    done
    echo ""
fi

printf "${BOLD}Quick-start commands:${NC}\n"
echo ""
echo "  make build        Build all C targets"
echo "  make tui          Launch TUI process manager (starts everything)"
echo "  make test         Run all tests (ctest + pytest)"
echo "  make sim-all      Start all simulators"
echo "  make dev-hmi      Start HMI dev server"
echo "  make lint         Check code formatting"
echo "  make help         Show all available targets"
echo ""
printf "${BOLD}Next steps:${NC}\n"
echo ""
echo "  1. Open the project:  code ."
echo "  2. Build C targets:   make build"
echo "  3. Launch the TUI:    make tui   (press 'a' to start all)"
echo ""

# Source profile reminder if we installed new tools
if [ ${#INSTALLED[@]} -gt 0 ]; then
    warn "Some tools were just installed. If commands are not found, run:"
    echo "    source ~/.bashrc"
    echo ""
fi
