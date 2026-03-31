#!/usr/bin/env bash
# Survey365 installer for Raspberry Pi
# Run: sudo bash install.sh
# Re-run safe: all operations are idempotent
#
# What this does:
#   1. Installs system dependencies (python3-venv, spatialite, nginx)
#   2. Adds user to dialout group for serial port access
#   3. Creates Python virtual environment and installs pip packages
#   4. Initializes the SQLite+SpatiaLite database
#   5. Deploys udev rule for F9P serial port
#   6. Generates SSL certificate (if needed)
#   7. Deploys nginx reverse proxy (port 80 -> Survey365)
#   8. Deploys systemd services (survey365 + boot/update helpers)
#   9. Adds sudoers rules for service management
#   10. Enables and starts everything

set -euo pipefail

# ── Color output ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()   { err "$*"; exit 1; }

service_state() {
    local state
    state=$(systemctl is-active "$1" 2>/dev/null || true)
    if [[ -n "$state" ]]; then
        echo "$state"
    else
        echo "unknown"
    fi
}

unit_enabled() {
    local state
    state=$(systemctl is-enabled "$1" 2>/dev/null || true)
    if [[ -n "$state" ]]; then
        echo "$state"
    else
        echo "unknown"
    fi
}

# ── Root check ──────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    die "This script must be run as root. Use: sudo bash install.sh"
fi

# ── Detect target user ──────────────────────────────────────────────────
# Accept --user=<name> flag
TARGET_USER=""
for arg in "$@"; do
    case "$arg" in
        --user=*) TARGET_USER="${arg#--user=}" ;;
    esac
done

if [[ -z "$TARGET_USER" ]]; then
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
        TARGET_USER="$SUDO_USER"
    else
        TARGET_USER=$(stat -c '%U' "$0" 2>/dev/null || stat -f '%Su' "$0" 2>/dev/null)
    fi
fi

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    die "Cannot determine target user. Run with: sudo bash install.sh --user=<username>"
fi

if ! id "$TARGET_USER" &>/dev/null; then
    die "User '$TARGET_USER' does not exist"
fi

TARGET_HOME=$(eval echo "~$TARGET_USER")
info "Target user: $TARGET_USER (home: $TARGET_HOME)"

# ── Path constants ──────────────────────────────────────────────────────
REPO_DIR="$TARGET_HOME/rtk-surveying"
SURVEY365_DIR="$REPO_DIR/survey365"
VENV_DIR="$SURVEY365_DIR/venv"
DATA_DIR="$SURVEY365_DIR/data"
DB_PATH="$DATA_DIR/survey365.db"

if [[ ! -d "$SURVEY365_DIR" ]]; then
    die "Survey365 directory not found at $SURVEY365_DIR. Clone the repo first."
fi

if [[ ! -f "$SURVEY365_DIR/requirements.txt" ]]; then
    die "requirements.txt not found at $SURVEY365_DIR/requirements.txt"
fi

# ── Step 1: System dependencies ─────────────────────────────────────────
info "Installing system dependencies..."

apt-get update -qq

DEPS=(
    python3-venv
    python3-dev
    libsqlite3-mod-spatialite
    nginx
    python3-serial
)

NEEDED=()
for pkg in "${DEPS[@]}"; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        NEEDED+=("$pkg")
    fi
done

if [[ ${#NEEDED[@]} -gt 0 ]]; then
    info "Installing: ${NEEDED[*]}"
    apt-get install -y -qq "${NEEDED[@]}"
    ok "System dependencies installed"
else
    ok "All system dependencies already installed"
fi

# ── Step 2: Add user to dialout group for serial port access ────────────
info "Ensuring $TARGET_USER is in dialout group..."
if id -nG "$TARGET_USER" | grep -qw dialout; then
    ok "$TARGET_USER already in dialout group"
else
    usermod -aG dialout "$TARGET_USER"
    ok "Added $TARGET_USER to dialout group (re-login required)"
fi

# ── Step 3: Python virtual environment ──────────────────────────────────
info "Setting up Python virtual environment..."

if [[ ! -d "$VENV_DIR" ]]; then
    sudo -u "$TARGET_USER" python3 -m venv "$VENV_DIR"
    ok "Virtual environment created at $VENV_DIR"
else
    ok "Virtual environment already exists"
fi

info "Installing Python packages..."
sudo -u "$TARGET_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u "$TARGET_USER" "$VENV_DIR/bin/pip" install --quiet -r "$SURVEY365_DIR/requirements.txt"
ok "Python packages installed"

# ── Step 4: Data directory and database ─────────────────────────────────
info "Initializing database..."

if [[ ! -d "$DATA_DIR" ]]; then
    sudo -u "$TARGET_USER" mkdir -p "$DATA_DIR"
    ok "Data directory created at $DATA_DIR"
fi

if [[ ! -f "$DB_PATH" || ! -s "$DB_PATH" ]]; then
    sudo -u "$TARGET_USER" bash -c "
        cd '$SURVEY365_DIR' && \
        SURVEY365_DB='$DB_PATH' '$VENV_DIR/bin/python3' -c \
        'from app.db import init_db; import asyncio; asyncio.run(init_db())'
    "
    ok "Database initialized at $DB_PATH"
else
    ok "Database already exists at $DB_PATH"
fi

# ── Step 5: Deploy udev rule for F9P ───────────────────────────────────
info "Deploying udev rule for GNSS receiver..."

UDEV_RULE="/etc/udev/rules.d/99-survey365-gnss.rules"
UDEV_CONTENT='# Survey365: u-blox ZED-F9P serial symlink
SUBSYSTEM=="tty", ATTRS{idVendor}=="1546", ATTRS{idProduct}=="01a9", SYMLINK+="ttyGNSS", GROUP="dialout", MODE="0660"'

echo "$UDEV_CONTENT" > "$UDEV_RULE"
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true
ok "udev rule deployed: /dev/ttyGNSS -> F9P"

# ── Step 6: Generate SSL certificate (if not already present) ───────────
SSL_DIR="/etc/nginx/ssl"
SSL_CERT="$SSL_DIR/survey365.crt"
SSL_KEY="$SSL_DIR/survey365.key"

if [[ ! -f "$SSL_CERT" ]] || [[ ! -f "$SSL_KEY" ]]; then
    info "Generating self-signed SSL certificate (10 year, all interfaces)..."
    mkdir -p "$SSL_DIR"

    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "")
    HOSTNAME_FQDN=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null || echo "")
    SAN="DNS:$(hostname),DNS:localhost"
    [[ -n "$HOSTNAME_FQDN" ]] && SAN="$SAN,DNS:$HOSTNAME_FQDN"
    [[ -n "$TAILSCALE_IP" ]] && SAN="$SAN,IP:$TAILSCALE_IP"
    SAN="$SAN,IP:127.0.0.1"
    for ip in $(hostname -I 2>/dev/null); do
        [[ "$ip" == 127.* ]] && continue
        [[ "$ip" == *:* ]] && continue
        SAN="$SAN,IP:$ip"
    done

    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "$SSL_KEY" \
        -out "$SSL_CERT" \
        -subj "/CN=$(hostname)" \
        -addext "subjectAltName=$SAN" \
        2>/dev/null
    ok "SSL certificate generated (SAN: $SAN)"
else
    ok "SSL certificate already exists"
fi

# ── Step 7: Deploy nginx configuration ──────────────────────────────────
info "Deploying nginx configuration..."

NGINX_SRC="$SURVEY365_DIR/nginx/survey365.conf"
NGINX_AVAIL="/etc/nginx/sites-available/survey365"
NGINX_ENABLED="/etc/nginx/sites-enabled/survey365"

if [[ ! -f "$NGINX_SRC" ]]; then
    die "Nginx config not found at $NGINX_SRC"
fi

cp "$NGINX_SRC" "$NGINX_AVAIL"

if [[ -L "/etc/nginx/sites-enabled/default" ]]; then
    rm "/etc/nginx/sites-enabled/default"
    info "Removed default nginx site"
fi

if [[ -L "$NGINX_ENABLED" ]]; then
    rm "$NGINX_ENABLED"
fi
ln -s "$NGINX_AVAIL" "$NGINX_ENABLED"

if nginx -t 2>&1; then
    ok "Nginx configuration valid"
else
    die "Nginx configuration test failed. Check $NGINX_AVAIL"
fi

# ── Step 8: Deploy systemd services ────────────────────────────────────
info "Deploying systemd services..."

deploy_service() {
    local src="$1"
    local name="$2"

    if [[ ! -f "$src" ]]; then
        die "Service file not found: $src"
    fi

    sed \
        -e "s|{user}|$TARGET_USER|g" \
        -e "s|{home}|$TARGET_HOME|g" \
        "$src" > "/etc/systemd/system/$name"

    ok "Deployed $name"
}

deploy_service "$SURVEY365_DIR/systemd/survey365.service" "survey365.service"
deploy_service "$SURVEY365_DIR/systemd/survey365-boot.service" "survey365-boot.service"
deploy_service "$SURVEY365_DIR/systemd/survey365-update.service" "survey365-update.service"
deploy_service "$SURVEY365_DIR/systemd/survey365-update.timer" "survey365-update.timer"

systemctl daemon-reload
ok "systemd daemon reloaded"

# ── Step 9: Sudoers rules for service management ───────────────────────
info "Configuring sudoers for service management..."

SUDOERS_FILE="/etc/sudoers.d/survey365"
SUDOERS_CONTENT="# Survey365: allow $TARGET_USER to manage services without password
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl start survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl start survey365-update.service
"

echo "$SUDOERS_CONTENT" > "$SUDOERS_FILE"
chmod 0440 "$SUDOERS_FILE"

if visudo -cf "$SUDOERS_FILE" &>/dev/null; then
    ok "Sudoers rules installed"
else
    rm -f "$SUDOERS_FILE"
    die "Invalid sudoers syntax -- removed $SUDOERS_FILE"
fi

# ── Step 10: Enable and start services ─────────────────────────────────
info "Enabling and starting services..."

systemctl enable survey365.service
systemctl enable survey365-boot.service
systemctl enable --now survey365-update.timer

if systemctl is-active --quiet survey365 2>/dev/null; then
    systemctl restart survey365
    ok "survey365 restarted"
else
    systemctl start survey365
    ok "survey365 started"
fi

systemctl enable nginx
systemctl reload nginx
ok "nginx reloaded"

# ── Print access information ────────────────────────────────────────────
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Survey365 installation complete${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""

TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "not available")
LAN_IPS=$(ip -4 addr show scope global | grep -oP 'inet \K[\d.]+' 2>/dev/null || hostname -I 2>/dev/null || echo "not available")

echo -e "  ${BLUE}Survey365:${NC}"
if [[ "$TAILSCALE_IP" != "not available" ]]; then
    echo -e "    Tailscale: ${GREEN}http://$TAILSCALE_IP/${NC}"
fi
for ip in $LAN_IPS; do
    if [[ ! "$ip" =~ ^100\. ]]; then
        echo -e "    LAN:       ${GREEN}http://$ip/${NC}"
    fi
done

echo ""
echo -e "  ${BLUE}Services:${NC}"
echo -e "    survey365:          $(service_state survey365)"
echo -e "    survey365-boot:     $(service_state survey365-boot)"
echo -e "    survey365-update:   $(service_state survey365-update)"
echo -e "    survey365-update.timer: $(service_state survey365-update.timer) ($(unit_enabled survey365-update.timer))"
echo -e "    nginx:              $(service_state nginx)"
echo ""
echo -e "  ${BLUE}Logs:${NC}"
echo "    journalctl -u survey365 -f"
echo "    journalctl -u survey365-boot"
echo ""
