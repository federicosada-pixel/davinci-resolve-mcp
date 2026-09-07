#!/usr/bin/env python3
"""Scan Ableton Live Sets (.als) for missing samples, presets, Max devices and
plug-ins, match every missing reference against the file index built by
index_files.py, and write:

    data/report.json          machine-readable results
    data/REPORT.md            human-readable restoration plan
    data/restore_plan.sh      copy commands (dry-run by default; `--apply` to run)

Matching rule: exact file name (case-insensitive), then prefer a copy whose size
equals the size Live recorded, then the copy whose folder path is most similar to
the original, then a copy on the same volume, then a copy outside Backup folders.
A reference with no exact-name hit anywhere in the index is reported as
PERMANENTLY MISSING. The .als files themselves are never modified.

Usage:
    python3 scan_sets.py                     # user sets first, then every set found
    python3 scan_sets.py --user-only         # only your own projects
    python3 scan_sets.py --set "/path/to/My Set.als"
    python3 scan_sets.py --root ~/Music/Ableton/projects
"""
import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DEFAULT_DB = DATA / "file-index.sqlite"
HOME = Path.home()
USER_LIBRARY = HOME / "Music" / "Ableton" / "User Library"
FACTORY_PACKS = HOME / "Music" / "Ableton" / "Factory Packs"
CORE_LIBRARIES = [Path(p) for p in (
    "/Applications/Ableton Live 12 Beta.app/Contents/App-Resources/Core Library",
    "/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Core Library",
)]

# Folders that hold the user's own work (reported first and in full detail).
USER_WORK_ROOTS = [
    HOME / "Music" / "Ableton" / "MY LIVE SETS ABLETON",
    HOME / "Music" / "Ableton" / "projects",
    HOME / "Music" / "Ableton" / "FEDE TEST ERIK",
    HOME / "Music" / "Ableton" / "Live Recordings",
    HOME / "Music" / "Ableton" / "User Library" / "Templates",
    HOME / "Documents",
    HOME / "Desktop",
]
DEFAULT_SET_ROOTS = [
    HOME / "Music" / "Ableton",
    HOME / "Documents",
    HOME / "Desktop",
    HOME / "Downloads",
    Path("/Volumes/LaCie 4/03 PROJECTS & LIBRARY"),
]

AUDIO_EXT = {".wav", ".aif", ".aiff", ".aifc", ".flac", ".mp3", ".ogg", ".m4a",
             ".mp4", ".caf", ".w64", ".rex", ".rx2", ".sd2", ".opus", ".wma", ".mov"}
PRESET_LIKE = {".adg", ".adv", ".alc", ".agr", ".ask", ".amxd", ".fxp", ".fxb",
               ".vstpreset", ".aupreset", ".mid", ".midi", ".als"}
INTERESTING = AUDIO_EXT | PRESET_LIKE


def norm_plugin(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# ----------------------------------------------------------------------------- parsing
def read_set_xml(path: Path) -> bytes:
    with open(path, "rb") as fh:
        head = fh.read(2)
    if head == b"\x1f\x8b":
        with gzip.open(path, "rb") as fh:
            return fh.read()
    return path.read_bytes()


def project_root_of(set_path: Path) -> Path:
    for parent in [set_path.parent] + list(set_path.parents)[1:6]:
        if (parent / "Ableton Project Info").is_dir() or parent.name.endswith(" Project"):
            return parent
    return set_path.parent


def val(el, tag, default=""):
    child = el.find(tag)
    if child is None:
        return default
    return child.get("Value", default)


def parse_set(set_path: Path):
    """Return (file_refs, plugin_refs, error)."""
    try:
        root = ET.fromstring(read_set_xml(set_path))
    except Exception as exc:  # corrupt / not a Live Set / permission
        return [], [], f"{type(exc).__name__}: {exc}"
    seen = set()
    file_refs = []
    for fr in root.iter("FileRef"):
        p = val(fr, "Path")
        rel = val(fr, "RelativePath")
        if not p and not rel:
            continue
        name = os.path.basename(p or rel)
        ext = os.path.splitext(name)[1].lower()
        if ext not in INTERESTING:
            continue
        key = (p, rel)
        if key in seen:
            continue
        seen.add(key)
        try:
            size = int(val(fr, "OriginalFileSize", "0") or 0)
        except ValueError:
            size = 0
        file_refs.append({
            "name": name, "ext": ext, "path": p, "relative_path": rel,
            "relative_path_type": val(fr, "RelativePathType", ""),
            "pack": val(fr, "LivePackName", ""), "orig_size": size,
            "kind": "sample" if ext in AUDIO_EXT else "preset",
        })

    plugin_refs, seen_pl = [], set()
    for tag, fmt in (("Vst3PluginInfo", "VST3"), ("VstPluginInfo", "VST2"), ("AuPluginInfo", "AU")):
        for pi in root.iter(tag):
            name = val(pi, "Name") or val(pi, "PlugName")
            if not name:
                continue
            path = val(pi, "Path")
            manufacturer = val(pi, "Manufacturer")
            key = (fmt, name)
            if key in seen_pl:
                continue
            seen_pl.add(key)
            plugin_refs.append({"name": name, "format": fmt, "path": path,
                                "manufacturer": manufacturer})
    return file_refs, plugin_refs, None


# ----------------------------------------------------------------------------- resolving
def swap_home(p: str) -> str:
    m = re.match(r"^/Users/([^/]+)/(.*)$", p)
    if m and m.group(1) != HOME.name:
        return str(HOME / m.group(2))
    return p


def candidate_paths(ref, set_dir: Path, project_root: Path):
    """Deterministic places Live itself would look, before any name search."""
    out = []
    p, rel, t = ref["path"], ref["relative_path"], ref["relative_path_type"]
    if p:
        out.append(p)
        out.append(swap_home(p))
    if rel:
        out.append(os.path.normpath(set_dir / rel))
        out.append(os.path.normpath(project_root / rel))
        out.append(os.path.normpath(USER_LIBRARY / rel))
        if ref["pack"]:
            out.append(os.path.normpath(FACTORY_PACKS / ref["pack"] / rel))
        for core in CORE_LIBRARIES:
            out.append(os.path.normpath(core / rel))
        out.append(os.path.normpath(project_root / "Samples" / "Imported" / ref["name"]))
    dedup = []
    for c in out:
        if c not in dedup:
            dedup.append(c)
    return dedup


class Index:
    def __init__(self, db: Path):
        if not db.exists():
            sys.exit(f"file index not found: {db}\nrun: python3 index_files.py")
        self.conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        self.cache = {}
        self.built_at = self.conn.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()[0]
        self.plugins = defaultdict(list)          # norm name -> [(format, path)]
        for name, ext, path in self.conn.execute("SELECT name, ext, path FROM files WHERE kind='plugin'"):
            stem = os.path.splitext(name)[0]
            fmt = {".vst3": "VST3", ".vst": "VST2", ".component": "AU", ".clap": "CLAP"}.get(ext, ext)
            self.plugins[norm_plugin(stem)].append((fmt, path))

    def by_name(self, name: str):
        key = name.lower()
        if key not in self.cache:
            self.cache[key] = self.conn.execute(
                "SELECT path, size, volume, in_backup FROM files WHERE name_lower=? LIMIT 200",
                (key,)).fetchall()
        return self.cache[key]

    def installers_for(self, plugin_name: str):
        n = norm_plugin(plugin_name)
        if len(n) < 4:
            return []
        rows = self.conn.execute(
            "SELECT name, path FROM files WHERE kind='installer' AND name_lower LIKE ? LIMIT 20",
            (f"%{plugin_name.lower()[:12]}%",)).fetchall()
        return [p for _, p in rows if n in norm_plugin(os.path.splitext(_)[0])] or [p for _, p in rows]


def rank(cands, ref):
    """Score exact-name hits. Higher is better."""
    orig = ref["path"] or ref["relative_path"]
    orig_parts = [x.lower() for x in orig.split("/") if x][:-1]
    orig_vol = orig.split("/")[2] if orig.startswith("/Volumes/") else "Macintosh HD"
    scored = []
    for path, size, volume, in_backup in cands:
        s = 0
        if ref["orig_size"] and size == ref["orig_size"]:
            s += 100
        parts = [x.lower() for x in path.split("/") if x][:-1]
        n = 0
        while n < min(len(parts), len(orig_parts)) and parts[-1 - n] == orig_parts[-1 - n]:
            n += 1
        s += 10 * n
        if volume == orig_vol:
            s += 5
        if not in_backup:
            s += 3
        if "/Music/Ableton/" in path:
            s += 2
        if ".alp/" in path or "/.Trash" in path:
            s -= 50
        scored.append((s, path, size))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def plan_target(ref, set_dir: Path, project_root: Path):
    """Where a recovered copy should be placed so Live finds it without a re-link.
    Prefer the exact original location; fall back to the project's Samples/Imported."""
    p, rel, t = ref["path"], ref["relative_path"], ref["relative_path_type"]
    options = []
    if p:
        options.append(p)
        options.append(swap_home(p))
    if rel:
        if t == "5":
            options.append(os.path.normpath(USER_LIBRARY / rel))
        options.append(os.path.normpath(set_dir / rel))
    for target in options:
        parent = os.path.dirname(target)
        # writable only if it sits under an existing top-level container
        comps = target.split("/")
        anchor = "/".join(comps[:4]) if target.startswith("/Volumes/") else "/".join(comps[:3])
        if os.path.isdir(parent) or os.path.isdir(anchor):
            return target, "copy_to_original"
    sub = "Samples/Imported" if ref["kind"] == "sample" else "Presets/Recovered"
    return str(project_root / sub / ref["name"]), "stage_in_project"


# ----------------------------------------------------------------------------- main scan
def is_user_work(p: Path) -> bool:
    s = str(p)
    return any(s.startswith(str(r) + "/") for r in USER_WORK_ROOTS)


def find_sets(roots, user_only):
    sets = []
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("Backup", ".Trash", "node_modules")
                           and not d.endswith(".app")]
            for f in filenames:
                if f.lower().endswith(".als") and not f.startswith("._"):
                    sets.append(Path(dirpath) / f)
    sets = sorted(set(sets))
    if user_only:
        sets = [s for s in sets if is_user_work(s)]
    sets.sort(key=lambda s: (0 if is_user_work(s) else 1, str(s)))
    return sets


def scan(sets, index: Index, verbose=False):
    results = []
    t0 = time.time()
    for i, set_path in enumerate(sets, 1):
        if verbose or i % 200 == 0:
            print(f"  [{i}/{len(sets)}] {set_path.name}", flush=True)
        set_dir = set_path.parent
        project_root = project_root_of(set_path)
        file_refs, plugin_refs, err = parse_set(set_path)
        entry = {"set": str(set_path), "project_root": str(project_root),
                 "user_work": is_user_work(set_path), "error": err,
                 "refs_total": len(file_refs), "ok": 0, "missing": [],
                 "plugins_total": len(plugin_refs), "plugins_missing": []}
        for ref in file_refs:
            found = None
            for c in candidate_paths(ref, set_dir, project_root):
                if os.path.exists(c):
                    found = c
                    break
            if found:
                entry["ok"] += 1
                continue
            hits = rank(index.by_name(ref["name"]), ref)
            target, action = plan_target(ref, set_dir, project_root)
            item = {
                "name": ref["name"], "kind": ref["kind"], "original_path": ref["path"],
                "relative_path": ref["relative_path"], "pack": ref["pack"],
                "orig_size": ref["orig_size"],
                "status": "found_elsewhere" if hits else "permanently_missing",
                "best_match": hits[0][1] if hits else None,
                "best_score": hits[0][0] if hits else None,
                "size_match": bool(hits and ref["orig_size"] and hits[0][2] == ref["orig_size"]),
                "alternates": [h[1] for h in hits[1:4]],
                "hit_count": len(hits),
                "target": target if hits else None,
                "action": action if hits else None,
            }
            entry["missing"].append(item)
        for pl in plugin_refs:
            n = norm_plugin(pl["name"])
            installed = index.plugins.get(n, [])
            if pl["format"] == "VST2" and pl["path"] and os.path.exists(pl["path"]):
                continue
            same_fmt = [p for f, p in installed if f == pl["format"]]
            if same_fmt:
                continue
            entry["plugins_missing"].append({
                "name": pl["name"], "format": pl["format"], "manufacturer": pl["manufacturer"],
                "expected_path": pl["path"],
                "other_formats_installed": [f"{f}: {p}" for f, p in installed],
                "installers": index.installers_for(pl["name"]),
            })
        results.append(entry)
    print(f"scanned {len(sets)} sets in {time.time()-t0:.0f}s")
    return results


# ----------------------------------------------------------------------------- outputs
def write_outputs(results, index, out_dir: Path, scope):
    out_dir.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%d %H:%M")
    n_sets = len(results)
    with_issues = [r for r in results if r["missing"] or r["plugins_missing"] or r["error"]]
    all_missing = [m for r in results for m in r["missing"]]
    recoverable = [m for m in all_missing if m["status"] == "found_elsewhere"]
    perm = sorted({m["name"] for m in all_missing if m["status"] == "permanently_missing"})
    plugins_missing = defaultdict(set)
    plugin_meta = {}
    for r in results:
        for p in r["plugins_missing"]:
            key = (p["name"], p["format"])
            plugins_missing[key].add(r["set"])
            plugin_meta[key] = p

    # --- JSON
    payload = {"generated": now, "index_built_at": index.built_at, "scope": scope,
               "summary": {"sets_scanned": n_sets, "sets_with_issues": len(with_issues),
                           "missing_refs": len(all_missing), "recoverable": len(recoverable),
                           "permanently_missing_unique_names": len(perm),
                           "plugins_missing": len(plugins_missing)},
               "permanently_missing": perm,
               "plugins_missing": [{"name": k[0], "format": k[1], "sets": sorted(v),
                                    **{kk: vv for kk, vv in plugin_meta[k].items() if kk not in ("name", "format")}}
                                   for k, v in sorted(plugins_missing.items(), key=lambda kv: -len(kv[1]))],
               "sets": results}
    (out_dir / "report.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False))

    # --- restore script (dedup by target)
    lines = ["#!/bin/bash",
             f"# Ableton recovery restore plan, generated {now}",
             "# Dry run by default. Run with --apply to copy. Never overwrites an existing file.",
             "# Each line: source (found copy)  ->  target (where the Live Set expects it).",
             'APPLY=0; [ "$1" = "--apply" ] && APPLY=1',
             "n=0; done_=0; skipped=0",
             "restore() {",
             '  local src="$1" dst="$2"; n=$((n+1))',
             '  if [ -e "$dst" ]; then skipped=$((skipped+1)); return; fi',
             '  if [ ! -e "$src" ]; then echo "SOURCE GONE: $src"; skipped=$((skipped+1)); return; fi',
             '  if [ "$APPLY" = 1 ]; then mkdir -p "$(dirname "$dst")" && cp -p "$src" "$dst" && done_=$((done_+1)) && echo "copied -> $dst"',
             '  else echo "[dry-run] $src"; echo "        -> $dst"; fi',
             "}", ""]
    seen_targets = set()
    for r in results:
        block = []
        for m in r["missing"]:
            if m["status"] != "found_elsewhere" or m["target"] in seen_targets:
                continue
            seen_targets.add(m["target"])
            flag = "" if m["size_match"] or not m["orig_size"] else "   # size differs from original, verify"
            block.append(f'restore {sh(m["best_match"])} {sh(m["target"])}{flag}')
        if block:
            lines.append(f'# === {r["set"]}')
            lines += block + [""]
    lines += ['echo "targets: $n  copied: $done_  skipped(existing/gone): $skipped"',
              '[ "$APPLY" = 1 ] || echo "dry run only. Re-run with --apply to copy."']
    script = out_dir / "restore_plan.sh"
    script.write_text("\n".join(lines) + "\n")
    script.chmod(0o755)

    # --- Markdown
    md = [f"# Ableton missing-file recovery report", "",
          f"Generated {now}. File index built {index.built_at}. Scope: {scope}.", "",
          "| Metric | Count |", "|---|---:|",
          f"| Live Sets scanned | {n_sets} |",
          f"| Sets with missing files or plug-ins | {len(with_issues)} |",
          f"| Missing file references | {len(all_missing)} |",
          f"| Recoverable (exact-name copy exists on a mounted drive) | {len(recoverable)} |",
          f"| Unique file names found nowhere (permanently missing) | {len(perm)} |",
          f"| Plug-ins referenced but not installed | {len(plugins_missing)} |", "",
          "## How to restore", "",
          "1. Review `restore_plan.sh` (dry run): `bash restore_plan.sh`",
          "2. Apply: `bash restore_plan.sh --apply` (copies only, never overwrites, Live Sets untouched).",
          "3. Open the Set in Live. If anything still shows orange, File > Manage Files > Locate Missing Files, "
          "enable *Search Project* and *Search Folder* = `~/Music/Ableton`, click Go.",
          "4. Save the Set, then File > Collect All and Save if you want it self-contained.", ""]

    def set_section(r, full):
        title = os.path.relpath(r["set"], HOME) if r["set"].startswith(str(HOME)) else r["set"]
        md.append(f"### {title}")
        if r["error"]:
            md.append(f"- Could not parse: `{r['error']}`")
            md.append("")
            return
        md.append(f"- references: {r['refs_total']}, ok: {r['ok']}, missing: {len(r['missing'])}, "
                  f"plug-ins missing: {len(r['plugins_missing'])}")
        if r["plugins_missing"]:
            md.append("- plug-ins: " + ", ".join(f"{p['name']} ({p['format']})" for p in r["plugins_missing"]))
        if full and r["missing"]:
            md.append("")
            md.append("| File | Status | Found copy | Restore to |")
            md.append("|---|---|---|---|")
            for m in r["missing"]:
                st = "recoverable" + (" (size ok)" if m["size_match"] else "") if m["status"] == "found_elsewhere" else "PERMANENTLY MISSING"
                md.append(f"| `{m['name']}` | {st} | `{m['best_match'] or ''}` | `{m['target'] or ''}` |")
        elif r["missing"]:
            names = sorted({m["name"] for m in r["missing"] if m["status"] == "permanently_missing"})
            if names:
                md.append(f"- permanently missing: {', '.join(f'`{n}`' for n in names[:12])}"
                          + (f" (+{len(names)-12} more)" if len(names) > 12 else ""))
        md.append("")

    user = [r for r in with_issues if r["user_work"]]
    other = [r for r in with_issues if not r["user_work"]]
    md.append(f"## Your own projects with issues ({len(user)})")
    md.append("")
    for r in user:
        set_section(r, full=True)
    if not user:
        md.append("None. All references in your own projects resolve.")
        md.append("")

    md.append("## Plug-ins referenced but not installed")
    md.append("")
    if plugins_missing:
        md.append("| Plug-in | Format | Sets | Other formats installed | Installer found |")
        md.append("|---|---|---:|---|---|")
        for k, sets_ in sorted(plugins_missing.items(), key=lambda kv: -len(kv[1])):
            p = plugin_meta[k]
            md.append(f"| {k[0]} | {k[1]} | {len(sets_)} | {'; '.join(p['other_formats_installed'][:2])} | "
                      f"{'; '.join(f'`{i}`' for i in p['installers'][:2])} |")
    else:
        md.append("None.")
    md.append("")
    md.append("After installing a plug-in: Live > Settings > Plug-Ins > Rescan.")
    md.append("")

    md.append(f"## Permanently missing file names ({len(perm)})")
    md.append("")
    md.append("No file with this exact name exists on any indexed drive. Reconnect the drive that held them, "
              "or re-download the pack, then re-run the scan.")
    md.append("")
    for n in perm[:400]:
        md.append(f"- `{n}`")
    if len(perm) > 400:
        md.append(f"- ... {len(perm)-400} more in report.json")
    md.append("")

    md.append(f"## Pack / course / template sets with issues ({len(other)})")
    md.append("")
    for r in other:
        set_section(r, full=False)
    (out_dir / "REPORT.md").write_text("\n".join(md) + "\n")
    return payload["summary"]


def sh(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--out", default=str(DATA))
    ap.add_argument("--set", action="append", help="scan only this .als (repeatable)")
    ap.add_argument("--root", action="append", help="folder(s) to search for .als (repeatable)")
    ap.add_argument("--user-only", action="store_true", help="only your own project folders")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    index = Index(Path(a.db))
    if a.set:
        sets = [Path(s).expanduser() for s in a.set]
        scope = "explicit sets"
    else:
        roots = [Path(r).expanduser() for r in a.root] if a.root else DEFAULT_SET_ROOTS
        sets = find_sets(roots, a.user_only)
        scope = ("user projects only" if a.user_only else "all sets") + " under " + ", ".join(map(str, roots))
    if a.limit:
        sets = sets[:a.limit]
    print(f"{len(sets)} Live Sets to scan (index built {index.built_at})")
    results = scan(sets, index, a.verbose)
    summary = write_outputs(results, index, Path(a.out), scope)
    print(json.dumps(summary, indent=1))
    print(f"reports: {Path(a.out)/'REPORT.md'}, {Path(a.out)/'restore_plan.sh'}")


if __name__ == "__main__":
    main()
