#!/usr/bin/env bash
set -u

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/voice-input"
LOG_FILE="$STATE_DIR/voice_input.log"
LOCK_FILE="$STATE_DIR/voice_input.lock"

mkdir -p "$STATE_DIR"

if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    flock -n 9 || exit 0
fi

cd "$APP_DIR" || exit 1

printf '\n[%s] Starting voice input\n' "$(date -Is)" >> "$LOG_FILE"

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    printf '[%s] Missing virtualenv Python: %s\n' "$(date -Is)" "$APP_DIR/.venv/bin/python" >> "$LOG_FILE"
    exit 1
fi

exec "$APP_DIR/.venv/bin/python" "$APP_DIR/voice_input.py" >> "$LOG_FILE" 2>&1
