#!/usr/bin/env bash
# Stamp the current git short hash into HTML files to bust browser caches.
# Replaces __CACHE_VERSION__ with the 8-char commit hash.
# Safe to run repeatedly — always uses the template placeholder.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
UI_DIR="$REPO_ROOT/survey365/ui"
HASH=$(git -C "$REPO_ROOT" rev-parse --short=8 HEAD 2>/dev/null || echo "dev")

# First restore the placeholder (in case a previous stamp left a hash)
find "$UI_DIR" -name '*.html' -exec sed -i "s/?v=[a-f0-9]\{8\}/?v=__CACHE_VERSION__/g" {} +

# Now stamp the current hash
find "$UI_DIR" -name '*.html' -exec sed -i "s/__CACHE_VERSION__/$HASH/g" {} +

echo "Stamped cache version: $HASH"
