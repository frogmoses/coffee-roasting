#!/usr/bin/env bash
# Sync Artisan's settings file between this repo and the roaster.
#
#   artisan/deploy.sh pull   — copy the roaster's live file into artisan/Artisan.conf
#   artisan/deploy.sh diff   — show how the roaster's live file differs from the repo copy
#   artisan/deploy.sh push   — install the repo copy on the roaster (Artisan must be closed)
#
# Requires DEPLOY_SSH_HOST (an ~/.ssh/config alias such as "roaster" or
# user@host), like ear/deploy.sh. ARTISAN_CONF_REMOTE overrides the remote
# path (default ~/.config/artisan-scope/Artisan.conf).
#
# Artisan writes its settings on exit and reads them at start, so a push
# while Artisan is running is silently overwritten; push refuses in that
# case. Every push keeps a timestamped backup beside the remote file.

set -euo pipefail

REMOTE="${DEPLOY_SSH_HOST:?Set DEPLOY_SSH_HOST to your roaster SSH alias or user@host}"
REMOTE_CONF="${ARTISAN_CONF_REMOTE:-.config/artisan-scope/Artisan.conf}"
HERE="$(cd "$(dirname "$0")" && pwd)"
LOCAL_CONF="$HERE/Artisan.conf"
CONF_TOOL="$HERE/../artisan_conf.py"
PYTHON="${PYTHON:-python3}"

green() { printf '\033[32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[33m%s\033[0m\n' "$1"; }
red() { printf '\033[31m%s\033[0m\n' "$1" >&2; }

# Anything in the file that looks like a credential must not be committed.
scrub_check() {
    if grep -inE 'plus_?(account|token|password)|password=|passwd=|token=|secret=|apikey=' "$1" >/dev/null; then
        red "Possible credential in $1:"
        grep -inE 'plus_?(account|token|password)|password=|passwd=|token=|secret=|apikey=' "$1" | cut -c1-80 >&2
        return 1
    fi
}

artisan_running() {
    # Artisan runs as /usr/bin/artisan; the log-sync watcher also has
    # "artisan" in its name, so filter it out
    ssh "$REMOTE" 'ps -eo pid=,cmd= | grep -i "[a]rtisan" | grep -v "artisan-sync" | grep -v "grep" || true'
}

case "${1:-}" in
    pull)
        tmp="$(mktemp)"
        scp -q "$REMOTE:$REMOTE_CONF" "$tmp"
        scrub_check "$tmp"
        cp "$tmp" "$LOCAL_CONF"; rm -f "$tmp"
        green "Pulled $REMOTE:$REMOTE_CONF -> artisan/Artisan.conf"
        if git -C "$HERE" diff --quiet -- Artisan.conf 2>/dev/null; then
            echo "No change against the committed copy."
        else
            git -C "$HERE" --no-pager diff --stat -- Artisan.conf || true
        fi
        ;;
    diff)
        tmp="$(mktemp)"
        scp -q "$REMOTE:$REMOTE_CONF" "$tmp"
        if diff -u "$LOCAL_CONF" "$tmp"; then
            green "Roaster and repo copies are identical."
        fi
        rm -f "$tmp"
        ;;
    push)
        [[ -f "$LOCAL_CONF" ]] || { red "No $LOCAL_CONF; run pull first"; exit 1; }
        scrub_check "$LOCAL_CONF"
        "$PYTHON" "$CONF_TOOL" -f "$LOCAL_CONF" check >/dev/null || { red "artisan_conf.py check failed; not pushing"; exit 1; }
        running="$(artisan_running)"
        if [[ -n "$running" ]]; then
            red "Artisan is running on $REMOTE; close it first (it overwrites settings on exit):"
            echo "$running" >&2
            exit 1
        fi
        stamp="$(date +%Y%m%d-%H%M%S)"
        ssh "$REMOTE" "cp '$REMOTE_CONF' '$REMOTE_CONF.bak-$stamp'"
        scp -q "$LOCAL_CONF" "$REMOTE:$REMOTE_CONF"
        green "Pushed artisan/Artisan.conf -> $REMOTE:$REMOTE_CONF (backup .bak-$stamp)"
        yellow "Start Artisan on the roaster to load it."
        ;;
    *)
        echo "usage: $0 pull|diff|push" >&2
        exit 2
        ;;
esac
