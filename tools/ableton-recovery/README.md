# Ableton Recovery

Finds files a Live Set says it needs but can no longer open — samples, presets,
racks, Max devices, plug-ins — by indexing every candidate file reachable from
this Mac and matching missing references by exact name, size, and path
similarity. The `.als` files themselves are never modified; a copy of each
recovered file is placed where Live expects to find it.

## Run it

The primary entry point is `run_recovery.sh`, which rebuilds the file index and
rescans every Live Set:

```bash
npm run ableton:recovery              # full pass (index + scan all sets)
npm run ableton:recovery -- --user-only
```

or directly:

```bash
bash tools/ableton-recovery/run_recovery.sh
```

For finer control:

| Command | Effect |
|---------|--------|
| `npm run ableton:index` | rebuild the SQLite file index only |
| `npm run ableton:scan` | rescan sets using the existing index |
| `npm run ableton:recovery:install` | install the 12-hourly launchd agent |
| `npm run ableton:recovery:uninstall` | remove the launchd agent (add `-- --purge` to also delete `data/` and the log dir) |

Reports land in `tools/ableton-recovery/data/` (git-ignored):

- `report.json` — machine-readable results
- `REPORT.md` — human-readable restoration plan
- `restore_plan.sh` — copy commands (dry-run by default; `--apply` to run)
- `REPORT-YYYY-MM-DD.md` — the most recent 14 dated snapshots

## Scheduled runs

`install_launchd.sh` installs a user LaunchAgent that runs every 12 hours. Logs
land in `~/Library/Logs/ableton-recovery/` by default; override with the
`ABLETON_RECOVERY_LOG_DIR` environment variable before running the installer.

If a run fails, the wrapper writes a dated `FAILED in stage: <stage>` marker to
`data/run.log` and exits non-zero so `launchctl print gui/$(id -u)/com.federicosada.ableton-recovery`
surfaces the fault.

## Cleanup

```bash
npm run ableton:recovery:uninstall             # unload agent, keep data/logs
npm run ableton:recovery:uninstall -- --purge  # also delete data/ and the log dir
```

The custom-location log directory (if `ABLETON_RECOVERY_LOG_DIR` was set) is
intentionally never recursively deleted — remove it by hand if desired.
