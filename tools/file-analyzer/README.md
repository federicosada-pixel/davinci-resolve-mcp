# File Analyzer — Duplicate Detection and Cleanup

On-demand, read-only utility that scans a folder, an external drive, or a cloud
folder (iCloud Drive, Dropbox, Google Drive, OneDrive) and produces a Markdown
report that separates:

1. **Duplicates grouped by their correct path** — every group of byte-identical
   files, listed under the folder of the copy that should stay.
2. **Extras that can be deleted** — redundant copies that are *not* linked to any
   application, DAW/NLE project, plug-in, or sample library.

It never deletes, moves, or modifies anything. It uses only the Python standard
library; no external tools or packages.

## Run it

```bash
npm run files:analyze
```

or directly:

```bash
python3 tools/file-analyzer/analyze.py
```

You get a menu of detected locations (external volumes, iCloud Drive, Dropbox,
Google Drive, OneDrive, home folder) plus a manual path option, then an optional
subfolder prompt, then a confirmation. Reports land in
`tools/file-analyzer/reports/` (git-ignored) as
`duplicate-report_<folder>_<timestamp>.md`.

> On this Mac the `python3` on `PATH` is a micromamba build that fails when run
> from some directories. If you see `ModuleNotFoundError: No module named
> 'encodings'`, use `/usr/bin/python3 tools/file-analyzer/analyze.py` instead.

### Non-interactive

```bash
python3 tools/file-analyzer/analyze.py --path "/Volumes/My Passport" --json
python3 tools/file-analyzer/analyze.py --path ~/Library/CloudStorage/Dropbox-Federicosada/Music --min-size 1M
```

| Flag | Effect |
|------|--------|
| `--path, -p DIR` | scan this folder instead of prompting |
| `--out, -o DIR` | where to write the report |
| `--json` | also write a JSON sidecar with every group, keeper, and extra |
| `--min-size SIZE` | ignore files smaller than e.g. `4K`, `1M` |
| `--include-app-internals` | traverse `.app` bundles, `node_modules`, `.git`, caches (their files stay protected) |
| `--protected-as-canonical` | if a copy lives inside an app or media project, treat the single loose copy as an extra |
| `--manifest FILE` | verify a `manifest.json` (defaults to `<target>/manifest.json` if present) |
| `--prefer DIR` | copies inside DIR win as the keeper (repeatable), e.g. a consolidated library |
| `--demote DIR` | copies inside DIR lose as the keeper when another copy exists (repeatable) |
| `--exclude NAME` | extra folder name to skip (repeatable) |
| `--quiet, -q` | no progress output |

## How it decides

**Duplicate** = same size → same SHA-256 of the first 64 KB → same SHA-256 of
the whole file. Hard links to the same inode are one file, not duplicates.
Symlinks are listed but never followed.

**Protected (never proposed for deletion)**

- *App-linked*: inside `.app` / `.framework` / `.bundle`-style packages,
  `/Applications`, `/System`, `/Library`, `~/Library/*` (except the iCloud and
  CloudStorage folders), developer caches such as `node_modules`, `.git`,
  virtualenvs, any folder that is a source checkout (contains `.git`) or an
  installed toolchain (contains `bin` plus `lib`, for example
  `google-cloud-sdk`). Not traversed by default.
- *Media production*: audio, video and image files beneath a folder that
  contains a session, instrument, rack, or preset file (Ableton `.als`/`.adg`, Logic `.logicx`,
  Pro Tools `.ptx`, Reaper `.rpp`, FL `.flp`, Cubase `.cpr`, Kontakt `.nki`,
  Premiere `.prproj`, After Effects `.aep`, Resolve `.drp`, Final Cut
  `.fcpbundle`, …), an Ableton `Ableton Project Info` folder, or a library
  folder such as `Samples`, `User Library`, `Packs`, `Splice`,
  `Native Instruments`, `Plug-Ins`, `Presets`, `Impulse Responses`, `Footage`,
  `Media Cache`, `~/Library/Audio`, `~/Music/Audio Music Apps`. A session
  file sitting in a broad folder (Documents, Desktop, Downloads, Music,
  Movies, Pictures) protects only the media directly next to it, never the
  whole folder tree. Project
  bundles renamed by cloud conflict handling (for example
  `Song.logicx-Federico's MacBook Pro`) are still recognised. A single
  audio file with an Ableton `.asd` analysis sidecar next to it is protected on
  its own (it was loaded in Live), without protecting its whole folder.
- *Manifest*: files listed in a verified `manifest.json`.

**Keeper (the correct path)** within a group of unprotected copies: fewest
penalties (a copy in a staging folder, or with a name like `report copy 2`,
`report (1)`, `Copy of …`, `… conflicted copy`, is penalised), then oldest
modification time, then shallowest path, then shortest path. Everything else in
the group is an **extra**.

A folder counts as *staging* when its name matches exactly (case-insensitive)
one of the known names — Downloads, Desktop, temp, backup, archive, `discarded`,
`_to_delete`, `duplicates`, `99 archive` and similar — or starts with a known
prefix such as `Duplicates 2026-08-30` or `discarded_2026-08-29`. See
`STAGING_DIR_NAMES` and `STAGING_DIR_PREFIXES` in `analyze.py` for the full list.

**Sidecars** (`.srt`, `.thm`, `.lrf`, `.lrv`, `.scr`, `.xmp`, `.aae`) belong to
the photo or video with the same name in the same folder. A duplicate sidecar is
only listed as an extra when that media file is also an extra or is missing;
otherwise it stays with its media.

If a group has protected copies and only one loose copy, the loose copy is kept
too (you would otherwise lose your only accessible copy). Use
`--protected-as-canonical` to change that.

**System litter** (`.DS_Store`, `Thumbs.db`, `._*` sidecars, `~$` lock files,
`.crdownload` / `.part` partial downloads, `.tmp`) outside protected areas is
listed separately as low-risk cleanup.

## Acting on a report: move extras to the Trash

`trash_from_report.py` reads the JSON sidecar of a report and moves the extras
to the macOS Trash (recoverable with Put Back). It never deletes permanently,
and it does nothing unless `--execute` is given: the default is a dry run that
prints the plan.

```bash
python3 tools/file-analyzer/trash_from_report.py tools/file-analyzer/reports/<report>.json                    # dry run
python3 tools/file-analyzer/trash_from_report.py tools/file-analyzer/reports/<report>.json --execute          # asks for confirmation
python3 tools/file-analyzer/trash_from_report.py <report>.json --include-litter --only "/path/prefix" --execute
```

Before every move it re-checks that the extra and its keeper both still exist,
are not undownloaded cloud placeholders, have the recorded size, and hash to the
same SHA-256 right now. Anything that fails is skipped and listed. A
`<report>.trash-log.json` records every file moved, with its original path,
size, hash and keeper, so the operation can be audited or reversed.

Trash backend: PyObjC `NSFileManager.trashItemAtURL` when available, otherwise
Finder via `osascript` (keeps Put Back), otherwise a plain move into `~/.Trash`.

Note for synced folders: trashing a file inside Dropbox, OneDrive, Google Drive
or iCloud Drive also removes it from the cloud copy. Each service keeps its own
recycle bin (typically 30 days) in addition to the Mac Trash.

## Cloud folders

Files that are still only in the cloud (Dropbox, Google Drive, OneDrive under
`~/Library/CloudStorage`, iCloud `.icloud` stubs) are detected via the macOS
dataless flag and are **never opened**, so a scan does not trigger downloads.
They are matched by name and size only and listed under *Could not verify*.
Download a folder ("Make available offline") and re-run to hash-verify it.

## Manifest integrity

If the target contains `manifest.json`, or you pass `--manifest`, each entry is
checked for presence, size, and SHA-256. Supported shapes:

```json
{"files": [{"path": "bin/app", "size": 1234, "sha256": "…"}]}
{"bin/app": {"size": 1234, "sha256": "…"}, "config/app.json": "…sha256…"}
```

The report states whether the folder is complete (zero missing keys, zero hash
mismatches). Verified files are treated as canonical.

## Report layout

1. Duplicates grouped by their correct path (KEEP / EXTRA / SAME FILE entries
   with size, modified, created, SHA-256, protection reason, and why a copy was
   ranked as an extra)
2. Extras that can be deleted (table sorted by size, plus a plain path list)
   and 2b. system litter
3. Duplicates kept on purpose (protected copies involved)
4. Could not verify: undownloaded cloud files, symlinks, folders not scanned,
   read errors
5. Manifest integrity (when a manifest exists)
6. Appendix: the classification rules applied

Original paths and metadata are preserved verbatim in the report and in the
optional JSON sidecar.
