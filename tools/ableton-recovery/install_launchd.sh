#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="${ABLETON_RECOVERY_LOG_DIR:-$HOME/Library/Logs/ableton-recovery}"
PLIST="$AGENT_DIR/com.federicosada.ableton-recovery.plist"
DOMAIN="gui/$(id -u)"

mkdir -p "$SCRIPT_DIR/data" "$AGENT_DIR" "$LOG_DIR"
/usr/bin/python3 - "$SCRIPT_DIR/com.federicosada.ableton-recovery.plist" "$PLIST" "$SCRIPT_DIR" "$LOG_DIR" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as source:
    plist = plistlib.load(source)

def expand(value):
    if isinstance(value, dict):
        return {key: expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item) for item in value]
    if isinstance(value, str):
        return value.replace("@SCRIPT_DIR@", sys.argv[3]).replace("@LOG_DIR@", sys.argv[4])
    return value

with open(sys.argv[2], "wb") as destination:
    plistlib.dump(expand(plist), destination)
PY

launchctl bootout "$DOMAIN/com.federicosada.ableton-recovery" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
