#!/bin/bash
# Rebuild the file index, then rescan Live Sets and regenerate the reports.
# Used by the 12-hourly launchd job and safe to run by hand.
#   bash run_recovery.sh            # full: index + scan every set
#   bash run_recovery.sh --user-only
#
# Fail-fast: any sub-command's non-zero exit stops the run, writes a FAILED
# marker to the log, and surfaces a non-zero status to launchd so the failure
# is visible in `launchctl print` rather than silently buried in run.log.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data
LOG=data/run.log
STATUS=0
STAGE="start"
trap 'echo "===== $(date "+%Y-%m-%d %H:%M:%S") FAILED in stage: $STAGE" >> "$LOG"; exit 1' ERR
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') start ($*)"
  STAGE="index_files"
  /usr/bin/python3 index_files.py
  STAGE="scan_sets"
  /usr/bin/python3 scan_sets.py "$@"
  STAGE="archive_report"
  cp -f data/REPORT.md "data/REPORT-$(date +%Y-%m-%d).md"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') done"
} >> "$LOG" 2>&1
# Cleanup is best-effort and must not mask a successful run above.
{ ls -t data/REPORT-*.md 2>/dev/null | tail -n +15 | xargs -I{} rm -f {}; } || true
tail -n 25 "$LOG"
exit "$STATUS"
