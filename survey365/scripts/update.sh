#!/usr/bin/env bash
# Survey365 Update Script
# Pulls latest code from git and restarts the service.
# Run: bash ~/rtk-surveying/survey365/scripts/update.sh
#
# What this does:
#   1. Safely checks origin/main for updates
#   2. Refuses to update when tracked files are dirty
#   3. Installs updated Python dependencies when needed
#   4. Restarts or starts the survey365 service when an update is applied

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

AUTO_MODE=false
for arg in "$@"; do
    case "$arg" in
        --auto) AUTO_MODE=true ;;
        *)
            die "Unknown argument: $arg"
            ;;
    esac
done

# ── Locate repository root ─────────────────────────────────────────────
# The script lives at survey365/scripts/update.sh, so repo root is two
# directories up from the script location.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SURVEY365_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$SURVEY365_DIR/.." && pwd)"
VENV_DIR="$SURVEY365_DIR/venv"

# Verify we are in a git repo
if [[ ! -d "$REPO_DIR/.git" ]]; then
    die "Not a git repository: $REPO_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
    die "Virtual environment not found at $VENV_DIR. Run install.sh first."
fi

info "Repository: $REPO_DIR"
info "Survey365:  $SURVEY365_DIR"

CURRENT_BRANCH=$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
    if [[ "$AUTO_MODE" == "true" ]]; then
        warn "Auto-update skipped: repository is on branch '$CURRENT_BRANCH', not 'main'"
        exit 0
    fi
    die "Refusing to update from branch '$CURRENT_BRANCH' (expected 'main')"
fi

# ── Record current state ───────────────────────────────────────────────
PREV_COMMIT=$(git -C "$REPO_DIR" rev-parse HEAD)
PREV_SHORT=$(git -C "$REPO_DIR" rev-parse --short HEAD)
info "Current commit: $PREV_SHORT"

# ── Refuse to run when tracked files are dirty ─────────────────────────
DIRTY_STATUS="$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)"
if [[ -n "$DIRTY_STATUS" ]]; then
    warn "Tracked files are dirty; refusing to update:"
    printf '%s\n' "$DIRTY_STATUS"
    if [[ "$AUTO_MODE" == "true" ]]; then
        exit 0
    fi
    die "Clean the repository before updating"
fi

# ── Check remote availability ──────────────────────────────────────────
REMOTE_COMMIT="$(git -C "$REPO_DIR" ls-remote --exit-code --heads origin main 2>/dev/null | awk 'NR==1 {print $1}')"
if [[ -z "$REMOTE_COMMIT" ]]; then
    if [[ "$AUTO_MODE" == "true" ]]; then
        warn "Auto-update skipped: origin/main is not reachable"
        exit 0
    fi
    die "origin/main is not reachable"
fi

if [[ "$PREV_COMMIT" == "$REMOTE_COMMIT" ]]; then
    ok "Already up to date ($PREV_SHORT)"
    exit 0
fi

# ── Fetch and fast-forward ─────────────────────────────────────────────
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

# ── Check if requirements changed ──────────────────────────────────────
REQUIREMENTS_FILE="$SURVEY365_DIR/requirements.txt"

if [[ "$PREV_COMMIT" != "$NEW_COMMIT" ]]; then
    # Check if requirements.txt was modified between old and new commit
    if git -C "$REPO_DIR" diff --name-only "$PREV_COMMIT" "$NEW_COMMIT" -- survey365/requirements.txt | grep -q .; then
        info "requirements.txt changed -- reinstalling Python packages..."
        "$VENV_DIR/bin/pip" install --quiet --upgrade pip
        "$VENV_DIR/bin/pip" install --quiet -r "$REQUIREMENTS_FILE"
        ok "Python packages updated"
    else
        ok "requirements.txt unchanged -- skipping pip install"
    fi
else
    ok "No code changes -- skipping pip install"
fi

# ── Check if systemd/nginx files changed ───────────────────────────────
if [[ "$PREV_COMMIT" != "$NEW_COMMIT" ]]; then
    if git -C "$REPO_DIR" diff --name-only "$PREV_COMMIT" "$NEW_COMMIT" -- survey365/systemd/ | grep -q .; then
        warn "systemd service files changed -- re-run install.sh to deploy them:"
        warn "  sudo bash $SURVEY365_DIR/install.sh"
    fi

    if git -C "$REPO_DIR" diff --name-only "$PREV_COMMIT" "$NEW_COMMIT" -- survey365/nginx/ | grep -q .; then
        warn "nginx config changed -- re-run install.sh to deploy it:"
        warn "  sudo bash $SURVEY365_DIR/install.sh"
    fi
fi

# ── Restart or start survey365 service ─────────────────────────────────
if sudo systemctl is-active --quiet survey365; then
    info "Restarting survey365 service..."
    if sudo systemctl restart survey365; then
        ok "survey365 restarted"
    else
        die "Failed to restart survey365"
    fi
else
    info "Starting survey365 service..."
    if sudo systemctl start survey365; then
        ok "survey365 started"
    else
        die "Failed to start survey365"
    fi
fi

# Wait briefly and check status
sleep 2

if sudo systemctl is-active --quiet survey365; then
    ok "survey365 is running"
else
    err "survey365 failed to start after restart"
    echo ""
    info "Recent logs:"
    journalctl -u survey365 --no-pager -n 20
    exit 1
fi

# ── Print version info ──────────────────────────────────────────────────
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
