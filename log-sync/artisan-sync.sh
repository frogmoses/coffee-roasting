#!/usr/bin/env bash
# Sync .alog roast logs to a remote machine via rsync.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/artisan-sync.conf"

LOG_DIR="$HOME/.local/share/artisan-sync"
LOG_FILE="$LOG_DIR/sync.log"
mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" >> "$LOG_FILE"; }

# Ensure the remote directory exists
ssh -o ConnectTimeout=10 "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p '${REMOTE_PATH}'"

# Sync .alog and .png files
rsync -avz \
  --include='*.alog' \
  --include='*.png' \
  --exclude='*' \
  "${LOCAL_PATH}/" \
  "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/"

log "Sync completed — $(find "$LOCAL_PATH" -maxdepth 1 \( -name '*.alog' -o -name '*.png' \) | wc -l) local files"
