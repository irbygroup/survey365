#!/usr/bin/env bash
# Initial Pi setup for a Survey365 controller.
# Run once on a fresh Raspberry Pi OS / Debian trixie install.
#
# What this does:
#   1. Configures the fan (GPIO 14, 60 °C trigger)
#   2. Fixes locale (en_US.UTF-8)
#   3. Prompts for and sets the Pi hostname
#   4. Runs a full OS upgrade
#   5. Installs prerequisite packages (git, gh, minicom, python3-serial, curl)
#   6. Installs and configures Tailscale
#   7. Adds SSH authorized keys for workstations
#   8. Clones the Survey365 repository
#   9. Runs the Survey365 installer, including the pinned RTKLIB build
#  10. Applies any database-backed Wi-Fi profiles
#
# Usage:
#   sudo bash scripts/bootstrap-pi.sh \
#       --user=jaredirby \
#       --ts-authkey=tskey-auth-... \
#       --ts-hostname=rtkbase-pi \
#       --gh-token=ghp_...
#
# Safe to re-run: all steps are idempotent.

set -euo pipefail

# ── Color output ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()   { err "$*"; exit 1; }

prompt_hostname() {
    local default_host answer
    default_host=$(hostnamectl --static 2>/dev/null || hostname)
    if [[ -z "$TS_HOSTNAME" ]]; then
        if [[ -t 0 ]]; then
            read -r -p "Pi hostname [$default_host]: " answer
            TS_HOSTNAME="${answer:-$default_host}"
        else
            TS_HOSTNAME="$default_host"
            warn "No --ts-hostname provided; using current hostname '$TS_HOSTNAME'"
        fi
    fi

    TS_HOSTNAME=$(printf '%s' "$TS_HOSTNAME" | tr '[:upper:]' '[:lower:]')
    if [[ ! "$TS_HOSTNAME" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]]; then
        die "Invalid hostname '$TS_HOSTNAME'. Use lowercase letters, numbers, and hyphens only."
    fi
}

configure_hostname() {
    local current_host
    current_host=$(hostnamectl --static 2>/dev/null || hostname)

    info "Configuring hostname..."
    if [[ "$current_host" == "$TS_HOSTNAME" ]]; then
        ok "Hostname already set to $TS_HOSTNAME"
    else
        hostnamectl set-hostname "$TS_HOSTNAME"
        ok "Hostname set to $TS_HOSTNAME"
    fi

    if grep -q '^127\.0\.1\.1 ' /etc/hosts; then
        sed -i "s/^127\\.0\\.1\\.1 .*/127.0.1.1 $TS_HOSTNAME $TS_HOSTNAME/" /etc/hosts
    else
        printf '\n127.0.1.1 %s %s\n' "$TS_HOSTNAME" "$TS_HOSTNAME" >> /etc/hosts
    fi
}

# ── Root check ──────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    die "This script must be run as root. Use: sudo bash scripts/bootstrap-pi.sh ..."
fi

# ── Parse flags ─────────────────────────────────────────────────────────
TARGET_USER=""
TS_AUTHKEY=""
TS_HOSTNAME=""
GH_TOKEN=""

for arg in "$@"; do
    case "$arg" in
        --user=*)        TARGET_USER="${arg#--user=}" ;;
        --ts-authkey=*)  TS_AUTHKEY="${arg#--ts-authkey=}" ;;
        --ts-hostname=*) TS_HOSTNAME="${arg#--ts-hostname=}" ;;
        --gh-token=*)    GH_TOKEN="${arg#--gh-token=}" ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

# ── Detect target user ──────────────────────────────────────────────────
if [[ -z "$TARGET_USER" ]]; then
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
        TARGET_USER="$SUDO_USER"
    fi
fi

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    die "Cannot determine target user. Run with: --user=<username>"
fi

if ! id "$TARGET_USER" &>/dev/null; then
    die "User '$TARGET_USER' does not exist"
fi

TARGET_HOME=$(eval echo "~$TARGET_USER")
REPO_DIR="$TARGET_HOME/survey365"

info "Target user: $TARGET_USER (home: $TARGET_HOME)"
prompt_hostname

# ── Step 1: Fan configuration ───────────────────────────────────────────
info "Checking fan configuration..."

CONFIG_TXT="/boot/firmware/config.txt"
FAN_OVERLAY="dtoverlay=gpio-fan,gpiopin=14,temp=60000"

if [[ -f "$CONFIG_TXT" ]]; then
    if grep -q "gpio-fan" "$CONFIG_TXT"; then
        ok "Fan overlay already configured"
    else
        echo "" >> "$CONFIG_TXT"
        echo "$FAN_OVERLAY" >> "$CONFIG_TXT"
        ok "Fan overlay added to $CONFIG_TXT (GPIO 14, 60 °C)"
    fi
else
    warn "$CONFIG_TXT not found — skipping fan configuration"
fi

# ── Step 2: Fix locale ──────────────────────────────────────────────────
info "Configuring locale..."

if locale -a 2>/dev/null | grep -q "en_US.utf8"; then
    ok "en_US.UTF-8 already generated"
else
    sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
    locale-gen
    update-locale LANG=en_US.UTF-8
    ok "en_US.UTF-8 locale generated"
fi

# ── Step 3: Hostname ────────────────────────────────────────────────────
configure_hostname

# ── Step 4: OS upgrade ──────────────────────────────────────────────────
info "Running full OS upgrade..."
apt-get update -qq
apt-get full-upgrade -y -qq
ok "OS upgraded"

# ── Step 5: Install prerequisite packages ────────────────────────────────
info "Installing prerequisite packages..."

PREREQS=(git gh python3-serial minicom curl)
NEEDED=()
for pkg in "${PREREQS[@]}"; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        NEEDED+=("$pkg")
    fi
done

if [[ ${#NEEDED[@]} -gt 0 ]]; then
    apt-get install -y -qq "${NEEDED[@]}"
    ok "Installed: ${NEEDED[*]}"
else
    ok "All prerequisites already installed"
fi

# ── Step 6: Install and configure Tailscale ──────────────────────────────
info "Setting up Tailscale..."

if command -v tailscale &>/dev/null; then
    ok "Tailscale already installed"
else
    if [[ -z "$TS_AUTHKEY" ]]; then
        die "Tailscale not installed and no --ts-authkey provided"
    fi
    curl -fsSL https://tailscale.com/install.sh | sh
    ok "Tailscale installed"
fi

if tailscale status &>/dev/null; then
    ok "Tailscale already connected"
else
    if [[ -z "$TS_AUTHKEY" ]]; then
        die "Tailscale not connected and no --ts-authkey provided"
    fi

    TS_UP_ARGS=(--authkey="$TS_AUTHKEY")
    if [[ -n "$TS_HOSTNAME" ]]; then
        TS_UP_ARGS+=(--hostname="$TS_HOSTNAME")
    fi

    tailscale up "${TS_UP_ARGS[@]}"
    ok "Tailscale connected"
fi

tailscale set --ssh
systemctl enable tailscaled
ok "Tailscale SSH enabled, service set to start on boot"

# ── Step 7: SSH authorized keys ──────────────────────────────────────────
info "Configuring SSH keys..."

SSH_DIR="$TARGET_HOME/.ssh"
AUTH_KEYS="$SSH_DIR/authorized_keys"

KEYS=(
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIZDEKdM6Jo4kQGNZD/sLYsyHKF0M2vqurK4cFXF0TQs jaredirby@jareds-mac-mini"
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILPz5BQ22gQqgfoWrAXBDdTFkpDPFOuKAvUNtXDhGcPx jaredirby@jareds-mac-work"
)

sudo -u "$TARGET_USER" mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"
touch "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"
chown "$TARGET_USER:$TARGET_USER" "$SSH_DIR" "$AUTH_KEYS"

ADDED=0
for key in "${KEYS[@]}"; do
    if ! grep -qF "$key" "$AUTH_KEYS" 2>/dev/null; then
        echo "$key" >> "$AUTH_KEYS"
        ((ADDED++))
    fi
done

if [[ $ADDED -gt 0 ]]; then
    ok "Added $ADDED SSH key(s)"
else
    ok "SSH keys already present"
fi

# ── Step 8: Clone repository ─────────────────────────────────────────────
info "Setting up repository..."

if [[ -d "$REPO_DIR/.git" ]]; then
    ok "Repository already cloned at $REPO_DIR"
else
    if [[ -z "$GH_TOKEN" ]]; then
        die "Repository not found at $REPO_DIR and no --gh-token provided"
    fi

    sudo -u "$TARGET_USER" bash -c "
        echo '$GH_TOKEN' | gh auth login --with-token
        cd '$TARGET_HOME'
        gh repo clone irbygroup/survey365
    "
    ok "Repository cloned to $REPO_DIR"
fi

# ── Step 9: Survey365 install ─────────────────────────────────────────────
SURVEY365_INSTALL="$REPO_DIR/scripts/setup-pi.sh"

if [[ -f "$SURVEY365_INSTALL" ]]; then
    info "Running Survey365 installer..."
    bash "$SURVEY365_INSTALL" --user="$TARGET_USER"
    ok "Survey365 installed"
else
    die "Survey365 setup script not found at $SURVEY365_INSTALL"
fi

# ── Step 10: Apply WiFi profiles (if any) ────────────────────────────────
WIFI_SCRIPT="$REPO_DIR/scripts/setup-wifi.sh"
if [[ -f "$WIFI_SCRIPT" ]]; then
    info "Applying any configured WiFi profiles..."
    bash "$WIFI_SCRIPT"
    ok "WiFi apply step completed"
else
    warn "WiFi script not found at $WIFI_SCRIPT — skipping"
fi

# ── Done ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Initial Pi setup complete${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""

TS_IP=$(tailscale ip -4 2>/dev/null || echo "not available")
echo -e "  ${BLUE}Tailscale:${NC}"
echo -e "    IP:       $TS_IP"
if [[ -n "$TS_HOSTNAME" ]]; then
    echo -e "    Hostname: $TS_HOSTNAME"
fi

echo ""
echo -e "  ${BLUE}Next steps:${NC}"
echo "    1. SSH in via Tailscale: ssh $TARGET_USER@${TS_HOSTNAME:-$TS_IP}"
echo "    2. Open Survey365: https://${TS_HOSTNAME:-$TS_IP}"
echo "    3. For resilient mode, re-run scripts/setup-pi.sh with --resilient"
echo ""
