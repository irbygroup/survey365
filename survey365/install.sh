#!/usr/bin/env bash
# Survey365 installer for Raspberry Pi
# Run: sudo bash install.sh
# Re-run safe: all operations are idempotent
#
# What this does:
#   1. Installs system dependencies (python3-venv, spatialite, nginx)
#   2. Creates Python virtual environment and installs pip packages
#   3. Initializes the SQLite+SpatiaLite database
#   4. Moves RTKBase from port 80 to port 8000
#   5. Deploys nginx reverse proxy (port 80 -> Survey365 + RTKBase)
#   6. Deploys systemd services (survey365 + survey365-boot)
#   7. Adds sudoers rules for service management
#   8. Enables and starts everything

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

# ── Root check ──────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    die "This script must be run as root. Use: sudo bash install.sh"
fi

# ── Detect target user ──────────────────────────────────────────────────
# Accept --user=<name> flag (same pattern as RTKBase installer)
# Otherwise detect from SUDO_USER or fall back to the owner of this script
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
        # Fall back to the owner of the install script itself
        TARGET_USER=$(stat -c '%U' "$0" 2>/dev/null || stat -f '%Su' "$0" 2>/dev/null)
    fi
fi

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    die "Cannot determine target user. Run with: sudo bash install.sh --user=<username>"
fi

# Verify the user exists
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
RTKBASE_DIR="$TARGET_HOME/rtkbase"
RTKBASE_SETTINGS="$RTKBASE_DIR/settings.conf"

# Verify the survey365 directory exists (the repo should be cloned already)
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

# Install only packages that are not already installed
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

# ── Step 2: Python virtual environment ──────────────────────────────────
info "Setting up Python virtual environment..."

if [[ ! -d "$VENV_DIR" ]]; then
    sudo -u "$TARGET_USER" python3 -m venv "$VENV_DIR"
    ok "Virtual environment created at $VENV_DIR"
else
    ok "Virtual environment already exists"
fi

# Always update pip and install/upgrade requirements
info "Installing Python packages..."
sudo -u "$TARGET_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
sudo -u "$TARGET_USER" "$VENV_DIR/bin/pip" install --quiet -r "$SURVEY365_DIR/requirements.txt"
ok "Python packages installed"

# ── Step 3: Data directory and database ─────────────────────────────────
info "Initializing database..."

if [[ ! -d "$DATA_DIR" ]]; then
    sudo -u "$TARGET_USER" mkdir -p "$DATA_DIR"
    ok "Data directory created at $DATA_DIR"
fi

# Initialize the database if it does not exist or is empty
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

# ── Step 4: Move RTKBase to port 8000 ──────────────────────────────────
info "Configuring RTKBase port..."

if [[ -f "$RTKBASE_SETTINGS" ]]; then
    # Read the current web port from RTKBase settings
    CURRENT_PORT=$(grep -E '^\s*web_port\s*=' "$RTKBASE_SETTINGS" | head -1 | sed 's/.*=\s*//' | tr -d '[:space:]')

    if [[ "$CURRENT_PORT" != "8000" ]]; then
        info "Moving RTKBase web UI from port ${CURRENT_PORT:-80} to port 8000..."

        # Back up settings before modifying
        cp "$RTKBASE_SETTINGS" "$RTKBASE_SETTINGS.bak.$(date +%Y%m%d%H%M%S)"

        # Update or add web_port in the [general] section
        if grep -qE '^\s*web_port\s*=' "$RTKBASE_SETTINGS"; then
            sed -i 's/^\(\s*web_port\s*=\s*\).*/\18000/' "$RTKBASE_SETTINGS"
        else
            # Add web_port under [general] section if it exists, otherwise at top
            if grep -q '^\[general\]' "$RTKBASE_SETTINGS"; then
                sed -i '/^\[general\]/a web_port=8000' "$RTKBASE_SETTINGS"
            else
                # Prepend [general] section with web_port
                sed -i '1i\[general\]\nweb_port=8000\n' "$RTKBASE_SETTINGS"
            fi
        fi

        # Restart RTKBase web service if it is active
        if systemctl is-active --quiet rtkbase_web 2>/dev/null; then
            info "Restarting rtkbase_web on port 8000..."
            systemctl restart rtkbase_web
            ok "RTKBase web restarted on port 8000"
        else
            warn "rtkbase_web service not active, skipping restart"
        fi
    else
        ok "RTKBase already configured on port 8000"
    fi
else
    warn "RTKBase settings.conf not found at $RTKBASE_SETTINGS -- skipping port change"
fi

# ── Step 5: Generate SSL certificate (if not already present) ───────────
SSL_DIR="/etc/nginx/ssl"
SSL_CERT="$SSL_DIR/survey365.crt"
SSL_KEY="$SSL_DIR/survey365.key"

if [[ ! -f "$SSL_CERT" ]] || [[ ! -f "$SSL_KEY" ]]; then
    info "Generating self-signed SSL certificate (10 year, all interfaces)..."
    mkdir -p "$SSL_DIR"

    # Collect all IPs for SAN
    TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "")
    HOSTNAME_FQDN=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null || echo "")
    SAN="DNS:$(hostname),DNS:localhost"
    [[ -n "$HOSTNAME_FQDN" ]] && SAN="$SAN,DNS:$HOSTNAME_FQDN"
    [[ -n "$TAILSCALE_IP" ]] && SAN="$SAN,IP:$TAILSCALE_IP"
    SAN="$SAN,IP:127.0.0.1"
    # Add all non-loopback IPv4 addresses
    for ip in $(hostname -I 2>/dev/null); do
        [[ "$ip" == 127.* ]] && continue
        [[ "$ip" == *:* ]] && continue  # skip IPv6
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

# ── Step 6: Deploy nginx configuration ──────────────────────────────────
info "Deploying nginx configuration..."

NGINX_SRC="$SURVEY365_DIR/nginx/survey365.conf"
NGINX_AVAIL="/etc/nginx/sites-available/survey365"
NGINX_ENABLED="/etc/nginx/sites-enabled/survey365"

if [[ ! -f "$NGINX_SRC" ]]; then
    die "Nginx config not found at $NGINX_SRC"
fi

# Copy config to sites-available
cp "$NGINX_SRC" "$NGINX_AVAIL"

# Remove default site if it exists (it conflicts on port 80)
if [[ -L "/etc/nginx/sites-enabled/default" ]]; then
    rm "/etc/nginx/sites-enabled/default"
    info "Removed default nginx site"
fi

# Create symlink in sites-enabled (idempotent: remove old link first)
if [[ -L "$NGINX_ENABLED" ]]; then
    rm "$NGINX_ENABLED"
fi
ln -s "$NGINX_AVAIL" "$NGINX_ENABLED"

# Test nginx configuration
if nginx -t 2>&1; then
    ok "Nginx configuration valid"
else
    die "Nginx configuration test failed. Check $NGINX_AVAIL"
fi

# ── Step 6: Deploy systemd services ────────────────────────────────────
info "Deploying systemd services..."

deploy_service() {
    local src="$1"
    local name="$2"

    if [[ ! -f "$src" ]]; then
        die "Service file not found: $src"
    fi

    # Replace {user} and {home} placeholders with actual values
    sed \
        -e "s|{user}|$TARGET_USER|g" \
        -e "s|{home}|$TARGET_HOME|g" \
        "$src" > "/etc/systemd/system/$name"

    ok "Deployed $name"
}

deploy_service "$SURVEY365_DIR/systemd/survey365.service" "survey365.service"
deploy_service "$SURVEY365_DIR/systemd/survey365-boot.service" "survey365-boot.service"

systemctl daemon-reload
ok "systemd daemon reloaded"

# ── Step 7: Sudoers rules for service management ───────────────────────
info "Configuring sudoers for service management..."

SUDOERS_FILE="/etc/sudoers.d/survey365"
SUDOERS_CONTENT="# Survey365: allow $TARGET_USER to manage GNSS and base services without password
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart str2str_*
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop str2str_*
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl start str2str_*
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active str2str_*
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart rtkbase_web
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop rtkbase_web
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl start rtkbase_web
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active rtkbase_web
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl start survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active survey365
"

echo "$SUDOERS_CONTENT" > "$SUDOERS_FILE"
chmod 0440 "$SUDOERS_FILE"

# Validate sudoers syntax
if visudo -cf "$SUDOERS_FILE" &>/dev/null; then
    ok "Sudoers rules installed"
else
    rm -f "$SUDOERS_FILE"
    die "Invalid sudoers syntax -- removed $SUDOERS_FILE"
fi

# ── Step 8: Stamp cache version ────────────────────────────────────────
info "Stamping cache version into HTML..."
bash "$SURVEY365_DIR/scripts/stamp-version.sh"

# ── Step 9: Enable and start services ───────────────────────────────────
info "Enabling and starting services..."

# Enable survey365 services
systemctl enable survey365.service
systemctl enable survey365-boot.service

# Start (or restart) survey365
if systemctl is-active --quiet survey365 2>/dev/null; then
    systemctl restart survey365
    ok "survey365 restarted"
else
    systemctl start survey365
    ok "survey365 started"
fi

# Reload nginx to pick up the new config
systemctl enable nginx
systemctl reload nginx
ok "nginx reloaded"

# ── Step 9: Print access information ────────────────────────────────────
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Survey365 installation complete${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""

# Gather IP addresses
TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "not available")
LAN_IPS=$(ip -4 addr show scope global | grep -oP 'inet \K[\d.]+' 2>/dev/null || hostname -I 2>/dev/null || echo "not available")

echo -e "  ${BLUE}Survey365:${NC}"
if [[ "$TAILSCALE_IP" != "not available" ]]; then
    echo -e "    Tailscale: ${GREEN}http://$TAILSCALE_IP/${NC}"
fi
for ip in $LAN_IPS; do
    # Skip tailscale IPs (100.x.x.x)
    if [[ ! "$ip" =~ ^100\. ]]; then
        echo -e "    LAN:       ${GREEN}http://$ip/${NC}"
    fi
done

echo ""
echo -e "  ${BLUE}RTKBase (proxied):${NC}"
if [[ "$TAILSCALE_IP" != "not available" ]]; then
    echo -e "    Tailscale: ${GREEN}http://$TAILSCALE_IP/rtkbase/${NC}"
fi
for ip in $LAN_IPS; do
    if [[ ! "$ip" =~ ^100\. ]]; then
        echo -e "    LAN:       ${GREEN}http://$ip/rtkbase/${NC}"
    fi
done

echo ""
echo -e "  ${BLUE}Services:${NC}"
echo -e "    survey365:       $(systemctl is-active survey365 2>/dev/null || echo 'unknown')"
echo -e "    survey365-boot:  $(systemctl is-active survey365-boot 2>/dev/null || echo 'unknown')"
echo -e "    rtkbase_web:     $(systemctl is-active rtkbase_web 2>/dev/null || echo 'unknown')"
echo -e "    nginx:           $(systemctl is-active nginx 2>/dev/null || echo 'unknown')"
echo ""
echo -e "  ${BLUE}Logs:${NC}"
echo "    journalctl -u survey365 -f"
echo "    journalctl -u survey365-boot"
echo ""
