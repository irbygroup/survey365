#!/usr/bin/env bash
# Survey365 Update Script
# Pulls latest code from git and restarts the service.
# Run: bash ~/rtk-surveying/survey365/scripts/update.sh
#
# What this does:
#   1. Pulls latest code from origin/main
#   2. Checks if requirements.txt changed and reinstalls if needed
#   3. Restarts the survey365 service
#   4. Prints the new version info

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

# ── Record current state ───────────────────────────────────────────────
PREV_COMMIT=$(git -C "$REPO_DIR" rev-parse HEAD)
PREV_SHORT=$(git -C "$REPO_DIR" rev-parse --short HEAD)
info "Current commit: $PREV_SHORT"

# ── Check for local changes ────────────────────────────────────────────
if [[ -n "$(git -C "$REPO_DIR" status --porcelain)" ]]; then
    warn "Working directory has uncommitted changes:"
    git -C "$REPO_DIR" status --short
    warn "Proceeding with pull (changes will be preserved if no conflicts)..."
fi

# ── Pull latest code ───────────────────────────────────────────────────
info "Pulling latest code from origin/main..."

if ! git -C "$REPO_DIR" pull origin main; then
    die "git pull failed. Resolve conflicts and try again."
fi

NEW_COMMIT=$(git -C "$REPO_DIR" rev-parse HEAD)
NEW_SHORT=$(git -C "$REPO_DIR" rev-parse --short HEAD)

if [[ "$PREV_COMMIT" == "$NEW_COMMIT" ]]; then
    ok "Already up to date ($NEW_SHORT)"
else
    info "Updated: $PREV_SHORT -> $NEW_SHORT"
    echo ""
    info "Changes:"
    git -C "$REPO_DIR" log --oneline "${PREV_COMMIT}..${NEW_COMMIT}"
    echo ""
fi

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

# ── Check if systemd units changed ─────────────────────────────────────
if [[ "$PREV_COMMIT" != "$NEW_COMMIT" ]]; then
    if git -C "$REPO_DIR" diff --name-only "$PREV_COMMIT" "$NEW_COMMIT" -- survey365/systemd/ | grep -q .; then
        warn "systemd service files changed -- re-run install.sh to update them:"
        warn "  sudo bash $SURVEY365_DIR/install.sh"
    fi

    if git -C "$REPO_DIR" diff --name-only "$PREV_COMMIT" "$NEW_COMMIT" -- survey365/nginx/ | grep -q .; then
        warn "nginx config changed -- re-run install.sh to update it:"
        warn "  sudo bash $SURVEY365_DIR/install.sh"
    fi
fi

# ── Restart survey365 service ───────────────────────────────────────────
info "Restarting survey365 service..."

if sudo systemctl restart survey365; then
    ok "survey365 restarted"
else
    die "Failed to restart survey365"
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
