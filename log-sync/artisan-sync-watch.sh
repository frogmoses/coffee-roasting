#!/usr/bin/env bash
# Watch ~/Documents for new/modified .alog files and trigger a sync.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/artisan-sync.conf"

LOG_DIR="$HOME/.local/share/artisan-sync"
LOG_FILE="$LOG_DIR/sync.log"
mkdir -p "$LOG_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" >> "$LOG_FILE"; }

DEBOUNCE=2
LAST_SYNC=0

log "Watcher started — monitoring ${LOCAL_PATH} for ${FILE_PATTERN}"

inotifywait -m -e close_write -e moved_to --format '%f' "$LOCAL_PATH" | while read -r FILENAME; do
  case "$FILENAME" in
    *.alog|*.png)
      NOW=$(date +%s)
      ELAPSED=$((NOW - LAST_SYNC))
      if [ "$ELAPSED" -ge "$DEBOUNCE" ]; then
        log "Detected: $FILENAME — syncing"
        "$SCRIPT_DIR/artisan-sync.sh" 2>&1 | while read -r line; do log "  $line"; done || log "Sync failed (exit $?)"
        LAST_SYNC=$(date +%s)
      else
        log "Detected: $FILENAME — debounced (${ELAPSED}s since last sync)"
      fi
      ;;
  esac
done
