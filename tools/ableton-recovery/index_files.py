#!/usr/bin/env python3
"""Build a fast lookup index of every audio, preset, plug-in, MIDI and Live file
reachable from this Mac (home folders, shared content, plug-in folders, and all
mounted volumes). Standard library only.

Output: SQLite database (default tools/ableton-recovery/data/file-index.sqlite)

    files(name_lower, name, ext, kind, path, size, mtime, volume, in_backup)

Usage:
    python3 index_files.py                # default roots
    python3 index_files.py --root /Volumes/LaCie\ 4 --root ~/Music
    python3 index_files.py --stats        # print counts from the existing index
"""
import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "data" / "file-index.sqlite"
HOME = Path.home()

AUDIO_EXT = {".wav", ".aif", ".aiff", ".aifc", ".flac", ".mp3", ".ogg", ".m4a",
             ".mp4", ".caf", ".w64", ".rex", ".rx2", ".sd2", ".opus", ".wma",
             ".mov", ".m4v", ".avi"}
LIVE_EXT = {".als", ".alc", ".adg", ".adv", ".agr", ".amxd", ".ask", ".alp",
            ".asd", ".ams"}
MIDI_EXT = {".mid", ".midi"}
PRESET_EXT = {".fxp", ".fxb", ".vstpreset", ".aupreset", ".nksf", ".nki", ".nkm",
              ".nkc", ".ncw", ".nkx", ".sfz", ".sf2", ".serumpreset", ".spf",
              ".h2p", ".xpn", ".ksd", ".mxb", ".mxp", ".massive", ".msv",
              ".preset", ".pgm", ".prst", ".adg", ".adv", ".xps", ".mpr",
              ".kit", ".aupreset", ".vital", ".diva", ".repro-1", ".repro-5"}
PLUGIN_BUNDLE_EXT = {".vst", ".vst3", ".component", ".clap", ".aaxplugin"}
INSTALLER_EXT = {".pkg", ".dmg", ".zip", ".rar", ".7z"}
MAXPATCH_EXT = {".maxpat", ".maxhelp", ".amxd"}

ALL_FILE_EXT = AUDIO_EXT | LIVE_EXT | MIDI_EXT | PRESET_EXT | INSTALLER_EXT | MAXPATCH_EXT

SKIP_DIR_NAMES = {
    "node_modules", ".git", ".svn", "__pycache__", ".Trash", ".Trashes",
    ".Spotlight-V100", ".fseventsd", ".TemporaryItems", ".DocumentRevisions-V100",
    "System Volume Information", "Caches", "com.apple.bird", "CloudStorage",
    "Xcode", "DerivedData", "Containers", "Group Containers", "Mail",
    "Messages", "Photos Library.photoslibrary", "Photo Booth Library",
    "Safari", "Google", "Mobile Documents",
}

DEFAULT_ROOTS = [
    HOME / "Music",
    HOME / "Documents",
    HOME / "Desktop",
    HOME / "Downloads",
    HOME / "Movies",
    HOME / "Dropbox",
    HOME / "Library" / "Audio",
    HOME / "Library" / "Application Support",
    Path("/Users/Shared"),
    Path("/Library/Audio/Plug-Ins"),
    Path("/Library/Application Support"),
]


def mounted_volumes():
    vols = []
    try:
        for entry in os.scandir("/Volumes"):
            if entry.name in ("Macintosh HD",):
                continue
            if entry.is_dir(follow_symlinks=False) or entry.is_symlink():
                real = os.path.realpath(entry.path)
                if real == "/":
                    continue
                vols.append(Path(entry.path))
    except FileNotFoundError:
        pass
    return vols


def volume_of(path: str) -> str:
    if path.startswith("/Volumes/"):
        parts = path.split("/", 3)
        return parts[2] if len(parts) > 2 else "Volumes"
    return "Macintosh HD"


def classify(ext: str) -> str:
    if ext in PLUGIN_BUNDLE_EXT:
        return "plugin"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in LIVE_EXT:
        return "live"
    if ext in MIDI_EXT:
        return "midi"
    if ext in PRESET_EXT:
        return "preset"
    if ext in INSTALLER_EXT:
        return "installer"
    if ext in MAXPATCH_EXT:
        return "max"
    return "other"


def walk(root: Path, rows, seen_dirs, stats):
    """Iterative scandir walk. Records matching files and plug-in bundles
    (bundles are recorded as a single row and not descended into)."""
    stack = [str(root)]
    while stack:
        d = stack.pop()
        try:
            real = os.path.realpath(d)
            if real in seen_dirs:
                continue
            seen_dirs.add(real)
            it = os.scandir(d)
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            stats["dirs_skipped"] += 1
            continue
        with it:
            for entry in it:
                name = entry.name
                if name.startswith("._"):
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                ext = os.path.splitext(name)[1].lower()
                if is_dir:
                    if ext in PLUGIN_BUNDLE_EXT:
                        try:
                            st = entry.stat(follow_symlinks=False)
                            rows.append((name.lower(), name, ext, "plugin", entry.path,
                                         0, int(st.st_mtime), volume_of(entry.path),
                                         int("/Backup/" in entry.path)))
                            stats["plugins"] += 1
                        except OSError:
                            pass
                        continue  # do not descend into bundles
                    if name in SKIP_DIR_NAMES or name.endswith(".app") \
                            or name.endswith(".photoslibrary") or name.endswith(".framework"):
                        continue
                    stack.append(entry.path)
                    stats["dirs"] += 1
                    continue
                if ext in ALL_FILE_EXT:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    rows.append((name.lower(), name, ext, classify(ext), entry.path,
                                 st.st_size, int(st.st_mtime), volume_of(entry.path),
                                 int("/Backup/" in entry.path)))
                    stats["files"] += 1
                    if len(rows) >= 5000:
                        flush(rows)


_conn = None


def flush(rows):
    if not rows:
        return
    _conn.executemany(
        "INSERT INTO files(name_lower,name,ext,kind,path,size,mtime,volume,in_backup)"
        " VALUES(?,?,?,?,?,?,?,?,?)", rows)
    _conn.commit()
    rows.clear()


def build(db_path: Path, roots):
    global _conn
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building.sqlite")
    if tmp.exists():
        tmp.unlink()
    _conn = sqlite3.connect(str(tmp))
    _conn.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE files(
            name_lower TEXT, name TEXT, ext TEXT, kind TEXT, path TEXT,
            size INTEGER, mtime INTEGER, volume TEXT, in_backup INTEGER);
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE roots(root TEXT, existed INTEGER, files INTEGER, seconds REAL);
    """)
    rows, seen = [], set()
    total = {"files": 0, "plugins": 0, "dirs": 0, "dirs_skipped": 0}
    t0 = time.time()
    for root in roots:
        root = Path(os.path.expanduser(str(root)))
        exists = root.exists()
        t = time.time()
        before = total["files"] + total["plugins"]
        if exists:
            print(f"  scanning {root} ...", flush=True)
            walk(root, rows, seen, total)
            flush(rows)
        n = total["files"] + total["plugins"] - before
        _conn.execute("INSERT INTO roots VALUES(?,?,?,?)",
                      (str(root), int(exists), n, round(time.time() - t, 1)))
        print(f"    {'ok' if exists else 'NOT MOUNTED'}: {n} entries in {time.time()-t:.1f}s", flush=True)
    flush(rows)
    _conn.execute("CREATE INDEX idx_name ON files(name_lower)")
    _conn.execute("CREATE INDEX idx_kind ON files(kind)")
    _conn.execute("INSERT INTO meta VALUES('built_at',?)", (time.strftime("%Y-%m-%dT%H:%M:%S"),))
    _conn.execute("INSERT INTO meta VALUES('seconds',?)", (str(round(time.time() - t0, 1)),))
    for k, v in total.items():
        _conn.execute("INSERT INTO meta VALUES(?,?)", (k, str(v)))
    _conn.commit()
    _conn.close()
    os.replace(tmp, db_path)
    print(f"index written: {db_path}  ({total['files']} files, {total['plugins']} plug-in bundles, "
          f"{total['dirs']} dirs, {time.time()-t0:.0f}s)")


def stats(db_path: Path):
    if not db_path.exists():
        print("no index yet:", db_path)
        return 1
    c = sqlite3.connect(str(db_path))
    print("built_at:", c.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()[0])
    for kind, n in c.execute("SELECT kind, COUNT(*) FROM files GROUP BY kind ORDER BY 2 DESC"):
        print(f"  {kind:10s} {n:>9,}")
    print("roots:")
    for root, existed, n, sec in c.execute("SELECT * FROM roots"):
        print(f"  {'ok ' if existed else 'MISSING'} {n:>9,}  {sec:>7}s  {root}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--root", action="append", help="extra/override root (repeatable)")
    ap.add_argument("--no-defaults", action="store_true", help="only use --root roots")
    ap.add_argument("--no-volumes", action="store_true", help="skip /Volumes/*")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    db = Path(a.db)
    if a.stats:
        return stats(db)
    roots = [] if a.no_defaults else list(DEFAULT_ROOTS)
    if not a.no_volumes:
        roots += mounted_volumes()
    roots += [Path(r) for r in (a.root or [])]
    build(db, roots)
    return 0


if __name__ == "__main__":
    sys.exit(main())
