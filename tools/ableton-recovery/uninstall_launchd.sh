#!/bin/bash
# Unload and remove the Ableton recovery launchd agent, and optionally delete
# the generated reports, index, and logs. Safe to run even if nothing is
# currently installed — every step is best-effort.
#
#   bash uninstall_launchd.sh              # unload agent, keep data/logs
#   bash uninstall_launchd.sh --purge      # also delete data/ and the log dir
#
# The .als files themselves are never touched; this only removes artifacts
# produced by this tool.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="${ABLETON_RECOVERY_LOG_DIR:-$HOME/Library/Logs/ableton-recovery}"
PLIST="$AGENT_DIR/com.federicosada.ableton-recovery.plist"
DOMAIN="gui/$(id -u)"

PURGE=0
if [[ "${1:-}" == "--purge" ]]; then
  PURGE=1
fi

echo "unloading launchd agent (if present) ..."
launchctl bootout "$DOMAIN/com.federicosada.ableton-recovery" 2>/dev/null || true

if [[ -f "$PLIST" ]]; then
  echo "removing $PLIST"
  rm -f "$PLIST"
fi

if [[ "$PURGE" == "1" ]]; then
  echo "purging generated artifacts ..."
  rm -rf "$SCRIPT_DIR/data"
  # Only delete the log dir if it is the default under ~/Library/Logs — never
  # recursively delete a directory the user pointed us at via the env var.
  if [[ "$LOG_DIR" == "$HOME/Library/Logs/ableton-recovery" && -d "$LOG_DIR" ]]; then
    rm -rf "$LOG_DIR"
  else
    echo "custom ABLETON_RECOVERY_LOG_DIR=$LOG_DIR left in place; remove by hand if desired"
  fi
fi

echo "done."
