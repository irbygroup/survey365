#!/usr/bin/env bash
# Survey365 Update Script
# Check mode:  bash ~/survey365/scripts/update.sh --auto
# Apply mode:  bash ~/survey365/scripts/update.sh
# Full maintenance: bash ~/survey365/scripts/update.sh --os-upgrade
#
# What this does:
#   1. Checks origin/main for updates without mutating the repository in --auto mode
#   2. Applies app updates only when run without --auto
#   3. Optionally upgrades Debian packages
#   4. Re-runs scripts/setup-pi.sh so deployment logic stays single-sourced
#   5. Reboots back into the Pi's normal operating mode after a successful apply

set -euo pipefail
PATH="/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

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

AUTO_MODE=false
OS_UPGRADE=false
for arg in "$@"; do
    case "$arg" in
        --auto) AUTO_MODE=true ;;
        --os-upgrade) OS_UPGRADE=true ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

# ── Locate repository root ─────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || (cd "$SCRIPT_DIR/.." && pwd))"
SURVEY365_DIR="$REPO_DIR"
VENV_DIR="$SURVEY365_DIR/venv"
ROOT_WAS_RO=false
SURVEY365_WAS_ACTIVE=false
REBOOT_REQUESTED=false

cleanup() {
    local rc=$?

    if [[ "$rc" -ne 0 && "$AUTO_MODE" != "true" && "$SURVEY365_WAS_ACTIVE" == "true" ]]; then
        warn "Attempting to restart survey365 after failed update..."
        sudo systemctl start survey365 >/dev/null 2>&1 || true
    fi

    if [[ "$ROOT_WAS_RO" == "true" && "$REBOOT_REQUESTED" != "true" ]]; then
        info "Restoring read-only root filesystem..."
        if [[ -x /usr/local/bin/survey365-maint-ro ]]; then
            sudo /usr/local/bin/survey365-maint-ro >/dev/null 2>&1 || true
        elif [[ -x /usr/local/bin/survey365-root-ro ]]; then
            sudo /usr/local/bin/survey365-root-ro >/dev/null 2>&1 || true
        else
            sudo sync >/dev/null 2>&1 || true
            if mountpoint -q /boot/firmware; then
                sudo mount -o remount,ro /boot/firmware >/dev/null 2>&1 || true
            fi
            sudo mount -o remount,ro / >/dev/null 2>&1 || true
        fi
    fi

    exit "$rc"
}
trap cleanup EXIT

ensure_root_writable() {
    local opts
    opts=$(findmnt -no OPTIONS / 2>/dev/null || true)
    if [[ "$opts" =~ (^|,)ro(,|$) ]]; then
        ROOT_WAS_RO=true
        info "Remounting / read-write for update..."
        if [[ -x /usr/local/bin/survey365-maint-rw ]]; then
            sudo /usr/local/bin/survey365-maint-rw
        elif [[ -x /usr/local/bin/survey365-root-rw ]]; then
            sudo /usr/local/bin/survey365-root-rw
        else
            sudo mount -o remount,rw /
            if mountpoint -q /boot/firmware; then
                sudo mount -o remount,rw /boot/firmware || true
            fi
        fi
    fi
}

if [[ ! -d "$REPO_DIR/.git" ]]; then
    die "Not a git repository: $REPO_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
    die "Virtual environment not found at $VENV_DIR. Run scripts/setup-pi.sh first."
fi

info "Repository: $REPO_DIR"
info "Survey365:  $SURVEY365_DIR"

REMOTE_COMMIT="$(git -C "$REPO_DIR" ls-remote --exit-code --heads origin main 2>/dev/null | awk 'NR==1 {print $1}')"
if [[ -z "$REMOTE_COMMIT" ]]; then
    if [[ "$AUTO_MODE" == "true" ]]; then
        warn "Update check skipped: origin/main is not reachable"
        exit 0
    fi
    die "origin/main is not reachable"
fi

CURRENT_BRANCH=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
    if [[ "$AUTO_MODE" == "true" ]]; then
        warn "Update check skipped: repository is on branch '$CURRENT_BRANCH', not 'main'"
        exit 0
    fi
    die "Refusing to update from branch '$CURRENT_BRANCH' (expected 'main')"
fi

PREV_COMMIT=$(git -C "$REPO_DIR" rev-parse HEAD)
PREV_SHORT=$(git -C "$REPO_DIR" rev-parse --short HEAD)
info "Current commit: $PREV_SHORT"

APP_UPDATE_NEEDED=true
if [[ "$PREV_COMMIT" == "$REMOTE_COMMIT" ]]; then
    APP_UPDATE_NEEDED=false
fi

if [[ "$APP_UPDATE_NEEDED" == "false" && "$OS_UPGRADE" == "false" ]]; then
    ok "Already up to date ($PREV_SHORT)"
    exit 0
fi

if [[ "$AUTO_MODE" == "true" ]]; then
    ok "Update available: $PREV_SHORT -> ${REMOTE_COMMIT:0:7}"
    exit 0
fi

DIRTY_STATUS="$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)"
if [[ -n "$DIRTY_STATUS" ]]; then
    warn "Tracked files are dirty; refusing to update:"
    printf '%s\n' "$DIRTY_STATUS"
    die "Clean the repository before updating"
fi

SURVEY365_WAS_ACTIVE=false
if sudo systemctl is-active --quiet survey365; then
    SURVEY365_WAS_ACTIVE=true
fi

ensure_root_writable

if [[ "$SURVEY365_WAS_ACTIVE" == "true" ]]; then
    info "Stopping survey365 before updating..."
    sudo systemctl stop survey365
fi

NEW_COMMIT="$PREV_COMMIT"
NEW_SHORT="$PREV_SHORT"
if [[ "$APP_UPDATE_NEEDED" == "true" ]]; then
    info "Fetching latest code from origin/main..."
    git -C "$REPO_DIR" fetch origin main
    git -C "$REPO_DIR" merge --ff-only FETCH_HEAD

    NEW_COMMIT=$(git -C "$REPO_DIR" rev-parse HEAD)
    NEW_SHORT=$(git -C "$REPO_DIR" rev-parse --short HEAD)

    info "Updated: $PREV_SHORT -> $NEW_SHORT"
    echo ""
    info "Changes:"
    git -C "$REPO_DIR" log --oneline "${PREV_COMMIT}..${NEW_COMMIT}"
    echo ""
else
    info "Application already at $PREV_SHORT -- continuing with maintenance tasks."
fi

REQUIREMENTS_FILE="$SURVEY365_DIR/requirements.txt"
if [[ "$APP_UPDATE_NEEDED" == "true" ]] && git -C "$REPO_DIR" diff --name-only "$PREV_COMMIT" "$NEW_COMMIT" -- requirements.txt | grep -q .; then
    info "requirements.txt changed -- reinstalling Python packages..."
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -r "$REQUIREMENTS_FILE"
    ok "Python packages updated"
else
    ok "requirements.txt unchanged -- skipping pip install"
fi

RUNNING_USER="$(whoami)"
DATA_DB_PATH=$(systemctl show -p Environment survey365.service 2>/dev/null | sed -n 's/.*SURVEY365_DB=\([^ ]*\).*/\1/p')
if [[ -z "$DATA_DB_PATH" ]]; then
    DATA_ROOT="$REPO_DIR/data"
else
    DATA_ROOT="$(dirname "$DATA_DB_PATH")"
fi
INSTALL_ARGS=(--user="$RUNNING_USER" --data-root="$DATA_ROOT")
if grep -q '^# BEGIN survey365-resilient$' /etc/fstab 2>/dev/null; then
    INSTALL_ARGS+=(--resilient)
fi

info "Re-running scripts/setup-pi.sh to deploy the updated system state..."
if [[ "$OS_UPGRADE" == "true" ]]; then
    info "Updating Debian packages..."
    sudo apt-get update
    sudo apt-get -y \
        -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold \
        full-upgrade
    ok "Debian packages upgraded"
fi

sudo "$REPO_DIR/scripts/setup-pi.sh" "${INSTALL_ARGS[@]}"

sleep 2

if sudo systemctl is-active --quiet survey365; then
    ok "survey365 is running"
else
    err "survey365 failed to start after update"
    echo ""
    info "Recent logs:"
    journalctl -u survey365 --no-pager -n 20
    exit 1
fi

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Survey365 updated successfully${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "  ${BLUE}Commit:${NC}  $NEW_SHORT"
echo -e "  ${BLUE}Date:${NC}    $(git -C "$REPO_DIR" log -1 --format='%ci' HEAD)"
echo -e "  ${BLUE}Message:${NC} $(git -C "$REPO_DIR" log -1 --format='%s' HEAD)"
echo -e "  ${BLUE}Status:${NC}  $(sudo systemctl is-active survey365)"
echo ""

warn "Rebooting to return the Pi to normal operating mode..."
REBOOT_REQUESTED=true
sudo sync
exec sudo systemctl reboot
