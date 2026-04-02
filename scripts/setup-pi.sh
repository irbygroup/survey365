#!/usr/bin/env bash
# Survey365 installer for Raspberry Pi
# Run: sudo bash scripts/setup-pi.sh
# Re-run safe: all operations are idempotent
#
# What this does:
#   1. Installs system dependencies (python3-venv, spatialite, nginx)
#   2. Adds user to dialout group for serial port access
#   3. Creates the Python virtual environment and installs pip packages
#   4. Initializes the SQLite+SpatiaLite database
#   5. Deploys udev, nginx, systemd, and sudoers configuration
#   6. Optionally configures resilient mode for read-only-root operation

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

LEGACY_STATION_CONF=""
GENERATED_LEGACY_CONF=""

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

mount_source() {
    findmnt -no SOURCE --target "$1" 2>/dev/null || true
}

mount_fstype() {
    findmnt -no FSTYPE --target "$1" 2>/dev/null || true
}

replace_managed_block() {
    local file="$1"
    local begin_marker="$2"
    local end_marker="$3"
    local block_file="$4"
    local tmp

    tmp=$(mktemp)
    if [[ -f "$file" ]]; then
        sed "/^${begin_marker}\$/,/^${end_marker}\$/d" "$file" > "$tmp"
    fi

    if [[ -s "$block_file" ]]; then
        {
            if [[ -s "$tmp" ]] && [[ "$(tail -c 1 "$tmp" 2>/dev/null || true)" != "" ]]; then
                echo ""
            fi
            cat "$block_file"
        } >> "$tmp"
    fi

    cp "$tmp" "$file"
    rm -f "$tmp" "$block_file"
}

ensure_helper_scripts() {
    cat > /usr/local/bin/survey365-root-rw <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
mount -o remount,rw /
EOF
    chmod 0755 /usr/local/bin/survey365-root-rw

    cat > /usr/local/bin/survey365-root-ro <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
sync
mount -o remount,ro /
EOF
    chmod 0755 /usr/local/bin/survey365-root-ro

    cat > /usr/local/bin/survey365-maint-rw <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
mount -o remount,rw /
if mountpoint -q /boot/firmware; then
    mount -o remount,rw /boot/firmware || true
fi
EOF
    chmod 0755 /usr/local/bin/survey365-maint-rw

    cat > /usr/local/bin/survey365-maint-ro <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
sync
if mountpoint -q /boot/firmware; then
    mount -o remount,ro /boot/firmware || true
fi
mount -o remount,ro /
EOF
    chmod 0755 /usr/local/bin/survey365-maint-ro
}

resolve_repo_dir() {
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    git -C "$script_dir" rev-parse --show-toplevel 2>/dev/null || (
        cd "$script_dir/.." && pwd
    )
}

resolve_legacy_station_conf() {
    local candidate
    local commit
    local tmp
    local fallback_tmp=""

    if [[ -n "$LEGACY_STATION_CONF" ]]; then
        [[ -f "$LEGACY_STATION_CONF" ]] || die "Legacy station.conf not found: $LEGACY_STATION_CONF"
        echo "$LEGACY_STATION_CONF"
        return 0
    fi

    candidate="$REPO_DIR/base-station/station.conf"
    if [[ -f "$candidate" ]]; then
        echo "$candidate"
        return 0
    fi

    [[ -d "$REPO_DIR/.git" ]] || return 1

    while read -r commit; do
        [[ -n "$commit" ]] || continue
        tmp=$(mktemp)
        chmod 0644 "$tmp"
        if ! git -C "$REPO_DIR" show "$commit:base-station/station.conf" > "$tmp" 2>/dev/null; then
            rm -f "$tmp"
            continue
        fi
        if grep -q '^ORIGINAL_IMEI=' "$tmp"; then
            GENERATED_LEGACY_CONF="$tmp"
            echo "$tmp"
            return 0
        fi
        if [[ -z "$fallback_tmp" ]] && grep -q '^WIFI_[0-9]\+=' "$tmp"; then
            fallback_tmp="$tmp"
        else
            rm -f "$tmp"
        fi
    done < <(git -C "$REPO_DIR" log --format=%H -- base-station/station.conf 2>/dev/null)

    if [[ -n "$fallback_tmp" ]]; then
        GENERATED_LEGACY_CONF="$fallback_tmp"
        echo "$fallback_tmp"
        return 0
    fi

    return 1
}

import_legacy_station_conf() {
    local station_conf="$1"

    [[ -f "$station_conf" ]] || return 0

    info "Importing legacy station settings from $station_conf ..."
    sudo -u "$TARGET_USER" bash -c "
        cd '$SURVEY365_DIR' && \
        SURVEY365_DB='$DB_PATH' LEGACY_STATION_CONF='$station_conf' '$VENV_DIR/bin/python3' - <<'PY'
import os
import sqlite3
from pathlib import Path

station_conf = Path(os.environ['LEGACY_STATION_CONF'])
db_path = Path(os.environ['SURVEY365_DB'])

if not station_conf.exists():
    raise SystemExit(0)

config_map = {
    'ORIGINAL_IMEI': 'original_imei',
    'GENERATED_IMEI': 'generated_imei',
    'GENERATED_MODEL': 'generated_model',
    'GENERATED_DATE': 'generated_date',
    'IMEI_API_TOKEN': 'imei_api_token',
    'IMEI_MAX_RETRIES': 'imei_max_retries',
    'IMEI_MODELS': 'imei_models',
    'CHECK_LOST_DEVICE': 'check_lost_device',
    'CHECK_VERIZON': 'check_verizon',
    'CHECK_TMOBILE': 'check_tmobile',
    'CHECK_BLACKLIST': 'check_blacklist',
}

parsed_config = {}
wifi_rows = []
for raw_line in station_conf.read_text().splitlines():
    line = raw_line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    key, value = line.split('=', 1)
    key = key.strip()
    value = value.strip()
    if key.startswith('WIFI_'):
        parts = value.split('|', 3)
        if len(parts) != 4:
            continue
        ssid, password, priority, metric = parts
        try:
            wifi_rows.append((ssid.strip(), password, int(priority), int(metric)))
        except ValueError:
            continue
        continue
    mapped = config_map.get(key)
    if mapped is not None and value != '':
        parsed_config[mapped] = value

conn = sqlite3.connect(db_path)
try:
    cursor = conn.execute('SELECT COUNT(*) FROM wifi_networks')
    wifi_count = int(cursor.fetchone()[0] or 0)
    imported_wifi = 0
    if wifi_count == 0:
        for ssid, password, priority, metric in wifi_rows:
            before = conn.total_changes
            conn.execute(
                '''
                INSERT OR IGNORE INTO wifi_networks
                    (ssid, psk, priority, metric, created_at, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
                ''',
                (ssid, password, priority, metric),
            )
            imported_wifi += conn.total_changes - before

    imported_config = 0
    for key, value in parsed_config.items():
        cursor = conn.execute('SELECT value FROM config WHERE key = ?', (key,))
        row = cursor.fetchone()
        current = row[0] if row else None
        if current not in (None, ''):
            continue
        conn.execute(
            '''
            INSERT INTO config (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            ''',
            (key, value),
        )
        imported_config += 1

    conn.commit()
    print(f'imported_wifi={imported_wifi} imported_config={imported_config}')
finally:
    conn.close()
PY
    "
}

set_config_value() {
    local key="$1"
    local value="$2"
    sudo -u "$TARGET_USER" bash -c "
        cd '$SURVEY365_DIR' && \
        SURVEY365_DB='$DB_PATH' '$VENV_DIR/bin/python3' - <<'PY'
import asyncio
from app.db import set_config
asyncio.run(set_config(${key@Q}, ${value@Q}))
PY
    "
}

migrate_dir_contents() {
    local src="$1"
    local dst="$2"
    if [[ ! -d "$src" || "$src" == "$dst" ]]; then
        return 0
    fi

    mkdir -p "$dst"
    cp -an "$src/." "$dst/" 2>/dev/null || true
}

preflight_resilient_mode() {
    local root_source data_source data_uuid data_label

    [[ "$DATA_ROOT" = /* ]] || die "--data-root must be an absolute path"

    mkdir -p "$DATA_ROOT"
    root_source=$(mount_source /)
    data_source=$(mount_source "$DATA_ROOT")

    if [[ -z "$data_source" || "$data_source" == "$root_source" ]]; then
        die "Resilient mode requires $DATA_ROOT to be a separate mounted filesystem (for example USB SSD or offline-created data partition)"
    fi

    DATA_FSTYPE=$(mount_fstype "$DATA_ROOT")
    if [[ -z "$DATA_FSTYPE" ]]; then
        die "Unable to determine filesystem type for $DATA_ROOT"
    fi

    if [[ -z "${DATA_DEVICE:-}" ]]; then
        if [[ "$data_source" == /dev/* ]]; then
            data_uuid=$(blkid -s UUID -o value "$data_source" 2>/dev/null || true)
            data_label=$(blkid -s LABEL -o value "$data_source" 2>/dev/null || true)
            if [[ -n "$data_uuid" ]]; then
                DATA_DEVICE="UUID=$data_uuid"
            elif [[ -n "$data_label" ]]; then
                DATA_DEVICE="LABEL=$data_label"
            else
                die "Unable to derive a persistent mount identifier for $data_source. Re-run with --data-device=UUID=... or LABEL=..."
            fi
        else
            die "Unable to derive a persistent mount identifier from '$data_source'. Re-run with --data-device=UUID=... or LABEL=..."
        fi
    fi

    if [[ "$DATA_FSTYPE" == "ext4" ]]; then
        DATA_MOUNT_OPTS="defaults,noatime,commit=5"
    else
        DATA_MOUNT_OPTS="defaults,noatime"
    fi
}

configure_cmdline_for_resilient_mode() {
    local cmdline_file="/boot/firmware/cmdline.txt"
    local cmdline

    [[ -f "$cmdline_file" ]] || die "Missing $cmdline_file"
    cmdline=$(tr '\n' ' ' < "$cmdline_file" | xargs)
    cmdline=$(printf '%s' "$cmdline" | sed -E 's/(^| )rw( |$)/ /g')

    if [[ ! "$cmdline" =~ (^|[[:space:]])ro($|[[:space:]]) ]]; then
        cmdline="$cmdline ro"
    fi
    if [[ ! "$cmdline" =~ (^|[[:space:]])fsck\.repair= ]]; then
        cmdline="$cmdline fsck.repair=yes"
    fi

    printf '%s\n' "$(echo "$cmdline" | xargs)" > "$cmdline_file"
}

configure_fstab_for_resilient_mode() {
    local fstab="/etc/fstab"
    local tmp block

    tmp=$(mktemp)
    awk '
        BEGIN { OFS="\t" }
        /^[[:space:]]*#/ || NF < 4 { print; next }
        {
            if ($2 == "/" || $2 == "/boot/firmware") {
                n = split($4, opts, ",")
                out = ""
                have_ro = 0
                for (i = 1; i <= n; i++) {
                    if (opts[i] == "rw" || opts[i] == "") {
                        continue
                    }
                    if (opts[i] == "ro") {
                        have_ro = 1
                    }
                    out = out ? out "," opts[i] : opts[i]
                }
                if (!have_ro) {
                    out = out ? out ",ro" : "ro"
                }
                $4 = out
            }
            print
        }
    ' "$fstab" > "$tmp"
    cp "$tmp" "$fstab"
    rm -f "$tmp"

    block=$(mktemp)
    cat > "$block" <<EOF
# BEGIN survey365-resilient
$DATA_DEVICE	$DATA_ROOT	$DATA_FSTYPE	$DATA_MOUNT_OPTS	0	2
tmpfs	/tmp	tmpfs	defaults,noatime,nosuid,nodev,size=64m	0	0
tmpfs	/var/tmp	tmpfs	defaults,noatime,nosuid,nodev,size=32m	0	0
tmpfs	/var/log	tmpfs	defaults,noatime,nosuid,nodev,size=48m	0	0
tmpfs	/var/lib/nginx	tmpfs	defaults,noatime,nosuid,nodev,size=8m,mode=0755	0	0
tmpfs	/var/lib/sudo	tmpfs	defaults,noatime,nosuid,nodev,size=1m,mode=0700	0	0
tmpfs	/var/lib/chrony	tmpfs	defaults,noatime,nosuid,nodev,size=1m	0	0
$TAILSCALE_DATA_DIR	/var/lib/tailscale	none	bind	0	0
# END survey365-resilient
EOF
    replace_managed_block "$fstab" "# BEGIN survey365-resilient" "# END survey365-resilient" "$block"
}

configure_runtime_links_for_resilient_mode() {
    local systemd_dir="/var/lib/systemd"
    local random_seed_target="$SYSTEMD_DATA_DIR/random-seed"

    mkdir -p "$SYSTEMD_DATA_DIR"

    if [[ -f /var/lib/systemd/random-seed && ! -e "$random_seed_target" ]]; then
        cp -a /var/lib/systemd/random-seed "$random_seed_target"
    elif [[ ! -e "$random_seed_target" ]]; then
        dd if=/dev/urandom of="$random_seed_target" bs=32 count=1 status=none
    fi
    chmod 600 "$random_seed_target"

    mkdir -p "$systemd_dir"
    rm -f /var/lib/systemd/random-seed
    ln -s "$random_seed_target" /var/lib/systemd/random-seed

    if [[ ! -L /etc/resolv.conf || "$(readlink -f /etc/resolv.conf 2>/dev/null || true)" != "/run/NetworkManager/resolv.conf" ]]; then
        if [[ -e /etc/resolv.conf && ! -L /etc/resolv.conf ]]; then
            cp -a /etc/resolv.conf /etc/resolv.conf.survey365-backup
            rm -f /etc/resolv.conf
        fi
        ln -sfn /run/NetworkManager/resolv.conf /etc/resolv.conf
    fi
}

configure_resilient_os_settings() {
    local journald_dropin="/etc/systemd/journald.conf.d/survey365-volatile.conf"
    local tmpfiles_conf="/etc/tmpfiles.d/survey365-resilient.conf"

    info "Configuring resilient read-only-root settings..."
    preflight_resilient_mode

    mkdir -p /etc/systemd/journald.conf.d
    cat > "$journald_dropin" <<'EOF'
[Journal]
Storage=volatile
EOF

    mkdir -p /etc/tmpfiles.d
    cat > "$tmpfiles_conf" <<'EOF'
d /var/log/nginx 0755 root adm -
d /var/lib/nginx 0755 root root -
d /var/lib/nginx/body 0700 root root -
d /var/lib/nginx/proxy 0700 root root -
d /var/lib/nginx/fastcgi 0700 root root -
d /var/lib/nginx/uwsgi 0700 root root -
d /var/lib/nginx/scgi 0700 root root -
EOF

    ensure_helper_scripts

    mkdir -p /var/lib/tailscale /var/lib/chrony /var/log/nginx /var/lib/nginx/body /var/lib/nginx/proxy /var/lib/nginx/fastcgi /var/lib/nginx/uwsgi /var/lib/nginx/scgi
    migrate_dir_contents /var/lib/tailscale "$TAILSCALE_DATA_DIR"
    configure_runtime_links_for_resilient_mode
    systemd-tmpfiles --create "$tmpfiles_conf" 2>/dev/null || true

    configure_cmdline_for_resilient_mode
    configure_fstab_for_resilient_mode

    systemctl disable --now apt-daily.timer apt-daily-upgrade.timer man-db.timer 2>/dev/null || true
    ok "Resilient-mode boot settings written (reboot required)"
    REBOOT_REQUIRED=true
}

# ── Root check ──────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    die "This script must be run as root. Use: sudo bash scripts/setup-pi.sh"
fi

# ── Parse flags ─────────────────────────────────────────────────────────
TARGET_USER=""
RESILIENT_MODE=false
DATA_ROOT=""
DATA_DEVICE=""
LEGACY_STATION_CONF=""

for arg in "$@"; do
    case "$arg" in
        --user=*) TARGET_USER="${arg#--user=}" ;;
        --data-root=*) DATA_ROOT="${arg#--data-root=}" ;;
        --data-device=*) DATA_DEVICE="${arg#--data-device=}" ;;
        --legacy-station-conf=*) LEGACY_STATION_CONF="${arg#--legacy-station-conf=}" ;;
        --resilient) RESILIENT_MODE=true ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

# ── Detect target user ──────────────────────────────────────────────────
if [[ -z "$TARGET_USER" ]]; then
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
        TARGET_USER="$SUDO_USER"
    else
        TARGET_USER=$(stat -c '%U' "$0" 2>/dev/null || stat -f '%Su' "$0" 2>/dev/null)
    fi
fi

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    die "Cannot determine target user. Run with: sudo bash scripts/setup-pi.sh --user=<username>"
fi

if ! id "$TARGET_USER" &>/dev/null; then
    die "User '$TARGET_USER' does not exist"
fi

TARGET_HOME=$(eval echo "~$TARGET_USER")
info "Target user: $TARGET_USER (home: $TARGET_HOME)"

# ── Path constants ──────────────────────────────────────────────────────
REPO_DIR="$(resolve_repo_dir)"
SURVEY365_DIR="$REPO_DIR"
VENV_DIR="$SURVEY365_DIR/venv"
LEGACY_DATA_DIR="$SURVEY365_DIR/data"

if [[ ! -d "$SURVEY365_DIR" ]]; then
    die "Survey365 directory not found at $SURVEY365_DIR. Clone the repo first."
fi

if [[ ! -f "$SURVEY365_DIR/requirements.txt" ]]; then
    die "requirements.txt not found at $SURVEY365_DIR/requirements.txt"
fi

if [[ -z "$DATA_ROOT" ]]; then
    if [[ "$RESILIENT_MODE" == "true" ]]; then
        DATA_ROOT="/srv/survey365"
    else
        DATA_ROOT="$LEGACY_DATA_DIR"
    fi
fi

DB_PATH="$DATA_ROOT/survey365.db"
LOG_DIR="$DATA_ROOT/logs"
RINEX_DIR="$DATA_ROOT/rinex"
TAILSCALE_DATA_DIR="$DATA_ROOT/tailscale"
SYSTEMD_DATA_DIR="$DATA_ROOT/systemd"
REBOOT_REQUIRED=false
DATA_FSTYPE=""
DATA_MOUNT_OPTS=""

info "Data root: $DATA_ROOT"
if [[ "$RESILIENT_MODE" == "true" ]]; then
    info "Mode: resilient"
    if [[ -n "$DATA_DEVICE" ]]; then
        info "Data device: $DATA_DEVICE"
    fi
else
    info "Mode: standard"
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

# ── Step 2: Add user to dialout group for serial port access ───────────
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
info "Preparing persistent data..."

if [[ "$RESILIENT_MODE" == "true" ]]; then
    preflight_resilient_mode
fi

mkdir -p "$DATA_ROOT"
mkdir -p "$RINEX_DIR" "$LOG_DIR" "$TAILSCALE_DATA_DIR"

if [[ "$DATA_ROOT" != "$LEGACY_DATA_DIR" ]]; then
    migrate_dir_contents "$LEGACY_DATA_DIR" "$DATA_ROOT"
fi

chown -R "$TARGET_USER:$TARGET_USER" "$DATA_ROOT"
ok "Data directories ready at $DATA_ROOT"

sudo -u "$TARGET_USER" bash -c "
    cd '$SURVEY365_DIR' && \
    SURVEY365_DB='$DB_PATH' '$VENV_DIR/bin/python3' -c \
    'from app.db import init_db; import asyncio; asyncio.run(init_db())'
"
ok "Database initialized/migrated at $DB_PATH"

LEGACY_CONF_PATH="$(resolve_legacy_station_conf || true)"
if [[ -n "$LEGACY_CONF_PATH" ]]; then
    import_legacy_station_conf "$LEGACY_CONF_PATH"
    ok "Legacy station settings imported"
fi

if [[ "$DATA_ROOT" != "$LEGACY_DATA_DIR" ]]; then
    set_config_value "rinex_data_dir" "$RINEX_DIR"
    ok "RINEX data directory set to $RINEX_DIR"
fi

# ── Step 5: Deploy udev rule for F9P ────────────────────────────────────
info "Deploying udev rule for GNSS receiver..."

UDEV_RULE="/etc/udev/rules.d/99-survey365-gnss.rules"
UDEV_CONTENT='# Survey365: u-blox ZED-F9P serial symlink
SUBSYSTEM=="tty", ATTRS{idVendor}=="1546", ATTRS{idProduct}=="01a9", SYMLINK+="ttyGNSS", GROUP="dialout", MODE="0660"'

echo "$UDEV_CONTENT" > "$UDEV_RULE"
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true
ok "udev rule deployed: /dev/ttyGNSS -> F9P"

# ── Step 6: Generate SSL certificate (if not already present) ──────────
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

# ── Step 8: Deploy systemd services ─────────────────────────────────────
info "Deploying systemd services..."

ensure_helper_scripts

deploy_service() {
    local src="$1"
    local name="$2"

    if [[ ! -f "$src" ]]; then
        die "Service file not found: $src"
    fi

    sed \
        -e "s|{user}|$TARGET_USER|g" \
        -e "s|{home}|$TARGET_HOME|g" \
        -e "s|{repo_dir}|$REPO_DIR|g" \
        -e "s|{data_dir}|$DATA_ROOT|g" \
        "$src" > "/etc/systemd/system/$name"

    ok "Deployed $name"
}

deploy_service "$SURVEY365_DIR/systemd/survey365.service" "survey365.service"
deploy_service "$SURVEY365_DIR/systemd/survey365-boot.service" "survey365-boot.service"
deploy_service "$SURVEY365_DIR/systemd/survey365-update.service" "survey365-update.service"
deploy_service "$SURVEY365_DIR/systemd/survey365-update-check.service" "survey365-update-check.service"
deploy_service "$SURVEY365_DIR/systemd/survey365-update-check.timer" "survey365-update-check.timer"

systemctl disable --now survey365-update.timer 2>/dev/null || true
rm -f /etc/systemd/system/survey365-update.timer
ok "Removed legacy survey365-update.timer"

if [[ "$RESILIENT_MODE" == "true" ]]; then
    configure_resilient_os_settings
fi

systemctl daemon-reload
ok "systemd daemon reloaded"

# ── Step 9: Sudoers rules for service management ────────────────────────
info "Configuring sudoers for service management..."

SUDOERS_FILE="/etc/sudoers.d/survey365"
SUDOERS_CONTENT="# Survey365: allow $TARGET_USER to manage services without password
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl start survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl start survey365-update.service
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl reboot
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl daemon-reload
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get *
$TARGET_USER ALL=(ALL) NOPASSWD: $REPO_DIR/scripts/setup-pi.sh
$TARGET_USER ALL=(ALL) NOPASSWD: $REPO_DIR/scripts/setup-pi.sh *
$TARGET_USER ALL=(ALL) NOPASSWD: $REPO_DIR/scripts/setup-wifi.sh
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/local/bin/survey365-root-rw
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/local/bin/survey365-root-ro
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/local/bin/survey365-maint-rw
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/local/bin/survey365-maint-ro
# Auto-deploy: update.sh writes systemd units and nginx config
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/rm -f /etc/systemd/system/survey365-update.timer
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/systemd/system/survey365*
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/cp $REPO_DIR/nginx/survey365.conf /etc/nginx/sites-available/survey365
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/sbin/nginx -t
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/sbin/nginx -s reload
"

echo "$SUDOERS_CONTENT" > "$SUDOERS_FILE"
chmod 0440 "$SUDOERS_FILE"

if visudo -cf "$SUDOERS_FILE" &>/dev/null; then
    ok "Sudoers rules installed"
else
    rm -f "$SUDOERS_FILE"
    die "Invalid sudoers syntax -- removed $SUDOERS_FILE"
fi

# ── Step 10: Enable and start services ──────────────────────────────────
info "Enabling and starting services..."

systemctl enable survey365.service
systemctl enable survey365-boot.service
systemctl enable --now survey365-update-check.timer

if systemctl is-active --quiet survey365 2>/dev/null; then
    systemctl restart survey365
    ok "survey365 restarted"
else
    systemctl start survey365
    ok "survey365 started"
fi

systemctl enable nginx
if systemctl is-active --quiet nginx 2>/dev/null; then
    systemctl reload nginx
    ok "nginx reloaded"
else
    systemctl start nginx
    ok "nginx started"
fi

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
echo -e "  ${BLUE}Config:${NC}"
echo -e "    data root:          $DATA_ROOT"
echo -e "    resilient mode:     $RESILIENT_MODE"
echo ""
echo -e "  ${BLUE}Services:${NC}"
echo -e "    survey365:               $(service_state survey365)"
echo -e "    survey365-boot:          $(service_state survey365-boot)"
echo -e "    survey365-update:        $(service_state survey365-update)"
echo -e "    survey365-update-check:  $(service_state survey365-update-check)"
echo -e "    survey365-update-check.timer: $(service_state survey365-update-check.timer) ($(unit_enabled survey365-update-check.timer))"
echo -e "    nginx:                   $(service_state nginx)"
echo ""
echo -e "  ${BLUE}Logs:${NC}"
echo "    journalctl -u survey365 -f"
echo "    journalctl -u survey365-boot"
echo "    journalctl -u survey365-update -f"
echo ""

if [[ "$REBOOT_REQUIRED" == "true" ]]; then
    warn "Reboot required before resilient-mode filesystem settings take effect"
fi

if [[ -n "$GENERATED_LEGACY_CONF" ]]; then
    rm -f "$GENERATED_LEGACY_CONF"
fi
