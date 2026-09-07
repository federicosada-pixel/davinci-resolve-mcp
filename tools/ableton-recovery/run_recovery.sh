#!/bin/bash
# Rebuild the file index, then rescan Live Sets and regenerate the reports.
# Used by the 12-hourly launchd job and safe to run by hand.
#   bash run_recovery.sh            # full: index + scan every set
#   bash run_recovery.sh --user-only
set -u
cd "$(dirname "$0")"
mkdir -p data
LOG=data/run.log
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') start ($*)"
  /usr/bin/python3 index_files.py
  /usr/bin/python3 scan_sets.py "$@"
  cp -f data/REPORT.md "data/REPORT-$(date +%Y-%m-%d).md"
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') done"
} >> "$LOG" 2>&1
# keep only the last 14 dated reports
ls -t data/REPORT-*.md 2>/dev/null | tail -n +15 | xargs -I{} rm -f {}
tail -n 25 "$LOG"
