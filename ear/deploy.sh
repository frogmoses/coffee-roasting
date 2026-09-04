#!/usr/bin/env bash
# Deploy the ear (microphone first-crack detector) to the roaster machine.
#
#   ./deploy.sh         — rsync .py files only (~1s)
#   ./deploy.sh --full  — rsync all files, create the venv, install deps
#
# Requires DEPLOY_SSH_HOST (an ~/.ssh/config alias such as "roaster" or
# user@host). The roaster needs libportaudio2 (apt) for sounddevice.

set -euo pipefail

REMOTE="${DEPLOY_SSH_HOST:?Set DEPLOY_SSH_HOST to your roaster SSH alias or user@host}"
REMOTE_DIR="${DEPLOY_REMOTE_DIR:-~/CodeProjects/ear}"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

green() { printf '\033[32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[33m%s\033[0m\n' "$1"; }

if [[ "${1:-}" == "--full" ]]; then
    yellow "Full deploy: syncing project files..."
    ssh "$REMOTE" "mkdir -p $REMOTE_DIR/captures"
    rsync -avz \
        --exclude='__pycache__/' --exclude='.venv/' --exclude='captures/' \
        --exclude='*.pyc' --exclude='ear.conf' \
        "$LOCAL_DIR/" "$REMOTE:$REMOTE_DIR/"

    yellow "Creating venv and installing dependencies..."
    ssh "$REMOTE" "source ~/.local/bin/env 2>/dev/null; cd $REMOTE_DIR && uv venv --python 3.12 -q && uv pip install -q numpy sounddevice 'websockets>=16'"
    yellow "Reminder: libportaudio2 must be installed (sudo apt install libportaudio2);"
    yellow "settings live in $REMOTE_DIR/ear.conf (template ear.conf.example), loaded by ~/.local/bin/run_ear."
    green "Full deploy complete."
else
    yellow "Quick deploy: syncing .py files..."
    rsync -avz "$LOCAL_DIR/"*.py "$REMOTE:$REMOTE_DIR/"
    green "Quick deploy complete."
fi
