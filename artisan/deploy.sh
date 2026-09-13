#!/usr/bin/env bash
# Sync Artisan's settings file between this repo and the roaster.
#
#   artisan/deploy.sh pull   — copy the roaster's live file into artisan/Artisan.conf
#   artisan/deploy.sh diff   — show how the roaster's live file differs from the repo copy
#   artisan/deploy.sh push   — install the repo copy on the roaster (Artisan must be closed)
#   artisan/deploy.sh local  — install the repo copy into THIS machine's Artisan as a
#                              development copy (paths rewritten, no roaster needed)
#
# Requires DEPLOY_SSH_HOST (an ~/.ssh/config alias such as "roaster" or
# user@host), like ear/deploy.sh. ARTISAN_CONF_REMOTE overrides the remote
# path (default ~/.config/artisan-scope/Artisan.conf).
#
# Artisan writes its settings on exit and reads them at start, so a push
# while Artisan is running is silently overwritten; push refuses in that
# case. Every push keeps a timestamped backup beside the remote file.
#
# `local` keeps the roaster's device (Hottop, id 53) so the dialogs match,
# but points autosave at ARTISAN_DEV_ROASTS (default ~/coffee-roasts-dev, so
# simulator roasts never land in roast-logs/), drops the roaster's recent-file
# paths, and prefixes batches "dev#". Run Artisan here with a profile loaded
# and Tools -> Simulator checked: Artisan then replays that profile as live
# data and skips the Hottop serial connection entirely.

set -euo pipefail

MODE="${1:-}"
if [[ "$MODE" != "local" ]]; then
    REMOTE="${DEPLOY_SSH_HOST:?Set DEPLOY_SSH_HOST to your roaster SSH alias or user@host}"
fi
REMOTE_CONF="${ARTISAN_CONF_REMOTE:-.config/artisan-scope/Artisan.conf}"
LOCAL_ARTISAN_CONF="${ARTISAN_CONF_LOCAL:-$HOME/.config/artisan-scope/Artisan.conf}"
DEV_ROASTS="${ARTISAN_DEV_ROASTS:-$HOME/coffee-roasts-dev}"
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

# The launcher /usr/bin/artisan execs .../share/artisan/artisan; matching
# that path finds the real process and nothing else (not the log-sync
# watcher, not this script). The [a] keeps the pattern from matching the
# shell that carries it (pgrep excludes itself, not an enclosing bash -c).
ARTISAN_PROC='share/artisan/[a]rtisan'

artisan_running() {
    ssh "$REMOTE" "pgrep -fa '$ARTISAN_PROC' || true"
}

artisan_running_here() {
    pgrep -fa "$ARTISAN_PROC" || true
}

case "$MODE" in
    local)
        [[ -f "$LOCAL_CONF" ]] || { red "No $LOCAL_CONF; run pull first"; exit 1; }
        running="$(artisan_running_here)"
        if [[ -n "$running" ]]; then
            red "Artisan is running on this machine; close it first (it overwrites settings on exit):"
            echo "$running" >&2
            exit 1
        fi
        tmp="$(mktemp)"
        cp "$LOCAL_CONF" "$tmp"
        mkdir -p "$DEV_ROASTS"
        # Rewrite only what is machine-specific; everything else stays the roaster's
        "$PYTHON" "$CONF_TOOL" -f "$tmp" set General autosavepath "$DEV_ROASTS" >/dev/null
        "$PYTHON" "$CONF_TOOL" -f "$tmp" set General autosavealsopath "$DEV_ROASTS" >/dev/null
        "$PYTHON" "$CONF_TOOL" -f "$tmp" set General profilepath "$(cd "$HERE/.." && pwd)/roast-logs/" >/dev/null
        "$PYTHON" "$CONF_TOOL" -f "$tmp" delete General lastLoadedProfile >/dev/null
        "$PYTHON" "$CONF_TOOL" -f "$tmp" delete General recentFileList >/dev/null
        "$PYTHON" "$CONF_TOOL" -f "$tmp" delete General recentSettingList >/dev/null
        "$PYTHON" "$CONF_TOOL" -f "$tmp" set Batch batchprefix "dev#" >/dev/null
        "$PYTHON" "$CONF_TOOL" -f "$tmp" check >/dev/null || { red "artisan_conf.py check failed on the local copy"; rm -f "$tmp"; exit 1; }
        mkdir -p "$(dirname "$LOCAL_ARTISAN_CONF")"
        if [[ -f "$LOCAL_ARTISAN_CONF" ]]; then
            stamp="$(date +%Y%m%d-%H%M%S)"
            cp "$LOCAL_ARTISAN_CONF" "$LOCAL_ARTISAN_CONF.bak-$stamp"
            echo "Backed up the existing local file as .bak-$stamp"
        fi
        cp "$tmp" "$LOCAL_ARTISAN_CONF"; rm -f "$tmp"
        green "Installed artisan/Artisan.conf -> $LOCAL_ARTISAN_CONF (dev copy; roasts save to $DEV_ROASTS)"
        yellow "Start Artisan, load a profile from roast-logs/, and check Tools -> Simulator before ON."
        ;;
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
        echo "usage: $0 pull|diff|push|local" >&2
        exit 2
        ;;
esac
