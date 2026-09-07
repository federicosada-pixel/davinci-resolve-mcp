#!/usr/bin/env python3
"""
File Analyzer for Duplicate Detection and Cleanup
==================================================

Interactive, on-demand utility. Pick a folder, external drive, or cloud
location (iCloud Drive, Dropbox, Google Drive, OneDrive), scan it, and get a
Markdown report that separates:

  1. Duplicate groups keyed by their *correct path* (the copy that should stay).
  2. Extras: redundant copies that are not linked to any application, media
     project, or sample library, and can be reviewed for deletion.

The tool NEVER deletes, moves, or modifies anything. It only reads metadata and
file contents (for hashing) and writes a report file.

Python 3.8+ standard library only. No third-party packages, no external tools.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import platform
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

__version__ = "1.0.0"

# --------------------------------------------------------------------------- #
# Constants and classification rules
# --------------------------------------------------------------------------- #

SF_DATALESS = 0x40000000  # macOS: file content not materialised locally (cloud)
PARTIAL_HASH_BYTES = 64 * 1024
READ_CHUNK = 1024 * 1024

HOME = os.path.expanduser("~")

# Directories never traversed and never reported: OS/cloud housekeeping areas.
ALWAYS_SKIP_DIR_NAMES = {
    ".Trash", ".Trashes", ".Spotlight-V100", ".fseventsd", ".TemporaryItems",
    ".DocumentRevisions-V100", ".PKInstallSandboxManager", ".vol",
    "System Volume Information", "$RECYCLE.BIN", "lost+found",
    ".dropbox.cache", ".dropbox", ".tmp.drivedownload", ".tmp.driveupload",
    ".file-revisions", "@eaDir",
}

# Developer / package internals: linked to tooling, skipped unless requested.
APP_INTERNAL_DIR_NAMES = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", "Pods", ".gradle", ".m2", ".cargo",
    "site-packages", "dist-packages", ".npm", ".yarn", ".pnpm-store",
    "DerivedData", "Caches", "Cache", "CachedData", "Code Cache", "GPUCache",
}

# Bundle-style directories owned by applications (macOS "packages").
APP_BUNDLE_EXTS = {
    ".app", ".framework", ".bundle", ".plugin", ".appex", ".kext", ".xpc",
    ".prefpane", ".qlgenerator", ".mdimporter", ".pkg", ".mpkg", ".driver",
    ".saver", ".wdgt", ".dSYM", ".xcodeproj", ".xcworkspace", ".playground",
    ".photoslibrary", ".musiclibrary", ".tvlibrary", ".aplibrary",
    ".imovielibrary", ".theater",
}

# Absolute path prefixes that belong to the OS or to installed applications.
APP_ROOT_PREFIXES = [
    "/Applications", "/System", "/Library", "/usr", "/bin", "/sbin", "/opt",
    "/private", "/cores", "/dev",
    os.path.join(HOME, "Applications"),
    os.path.join(HOME, "Library", "Application Support"),
    os.path.join(HOME, "Library", "Containers"),
    os.path.join(HOME, "Library", "Group Containers"),
    os.path.join(HOME, "Library", "Preferences"),
    os.path.join(HOME, "Library", "Developer"),
    os.path.join(HOME, "Library", "Mail"),
    os.path.join(HOME, "Library", "Messages"),
    os.path.join(HOME, "Library", "Photos"),
    os.path.join(HOME, "Library", "Safari"),
    os.path.join(HOME, "Library", "Calendars"),
    os.path.join(HOME, "Library", "Keychains"),
    os.path.join(HOME, "Library", "Accounts"),
    os.path.join(HOME, "Library", "Cookies"),
    os.path.join(HOME, "Library", "Autosave Information"),
    os.path.join(HOME, "Library", "Saved Application State"),
]
if platform.system() == "Windows":
    for _var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemRoot",
                 "LOCALAPPDATA", "APPDATA"):
        _v = os.environ.get(_var)
        if _v:
            APP_ROOT_PREFIXES.append(_v)

# Media-production roots: sample libraries, plug-ins, DAW/NLE user libraries.
PRODUCTION_ROOT_PREFIXES = [
    os.path.join(HOME, "Library", "Audio"),
    os.path.join(HOME, "Music", "Audio Music Apps"),
    os.path.join(HOME, "Music", "Logic"),
    os.path.join(HOME, "Music", "GarageBand"),
    os.path.join(HOME, "Music", "Ableton"),
    os.path.join(HOME, "Music", "Music", "Media"),
    os.path.join(HOME, "Music", "iTunes"),
    os.path.join(HOME, "Movies", "Motion Templates"),
    os.path.join(HOME, "Movies", "Motion Templates.localized"),
    os.path.join(HOME, "Documents", "Adobe"),
    os.path.join(HOME, "Documents", "Pro Tools"),
    os.path.join(HOME, "Documents", "Native Instruments"),
    os.path.join(HOME, "Documents", "Splice"),
    os.path.join(HOME, "Splice"),
    "/Library/Audio", "/Library/Application Support/Native Instruments",
    "/Library/Application Support/Avid", "/Library/Application Support/Blackmagic Design",
]

# Directory *names* (any depth) that indicate a media-production library.
PRODUCTION_DIR_NAMES = {
    "ableton", "user library", "factory packs", "live recordings", "packs",
    "sample libraries", "sample library", "samples", "sounds", "sound library",
    "sound libraries", "splice", "native instruments", "kontakt", "komplete",
    "loopmasters", "loopcloud", "audio music apps", "garageband",
    "pro tools", "avid", "waves", "plug-ins", "plugins", "vst", "vst3",
    "presets", "impulse responses", "irs", "expansions",
    "maschine", "serum presets", "omnisphere", "spectrasonics", "steam",
    "arturia", "u-he", "izotope", "soundtoys", "fabfilter", "ableton project info",
    "audio files", "session file backups", "bounced files", "bounces", "rendered files",
    "freeze files", "recorded", "recordings", "comps", "takes", "alternatives",
    "renders", "stems", "multitracks", "final cut pro", "final cut events",
    "final cut projects", "motion templates", "adobe premiere pro",
    "adobe after effects", "adobe audition", "davinci resolve", "resolve projects",
    "blackmagic design", "capturescratch", "media cache", "media cache files",
    "premiere pro auto-save", "proxies", "footage", "raw footage",
    "dailies", "camera originals", "luts", "project files", "ableton live",
    "reaper media", "fl studio", "image-line", "studio one", "bitwig studio",
    "propellerhead", "cubase", "nuendo", "steinberg", "vstplugins",
    # sample-library structure
    "loops", "one shots", "one-shots", "oneshots", "drumsets", "drum kits", "kits",
    "multisamples", "multi samples", "wavetables", "patches", "soundbanks",
    "sound banks", "banks", "instruments", "construction kits", "sample packs",
    "refills", "expansion packs", "midi files", "drum samples", "vocals",
    # plug-in and library vendors (installers, licences, presets, content)
    "mdrummer", "meldaproduction", "toontrack", "xln audio", "spitfire audio",
    "heavyocity", "output", "bfd drums", "ik multimedia", "roli", "air music technology",
    "imaginando", "beatskillz", "yum audio", "universal audio", "uad", "waves audio",
    "eventide", "plugin alliance", "slate digital", "softube", "sonnox", "valhalla",
    "cableguys", "xfer records", "xfer", "reveal sound", "spectrasonics", "gforce",
    "reverb machine", "geosynths", "boom library", "boom", "landr", "oxi instruments",
    "akai", "mpc", "mpc 3", "pioneer", "rekordbox", "serato", "traktor", "ni resources",
    "max 9", "max 8", "cycling '74", "ableton live projects", "live recordings",
    "bitwig studio", "bfd3", "superior drummer", "ezdrummer", "addictive drums",
}

# Project / session / instrument / preset file types. A directory that contains
# one of these anchors a production project; everything beneath it is linked.
PRODUCTION_PROJECT_EXTS = {
    # DAWs
    ".als", ".alp", ".logicx", ".logic", ".band", ".ptx", ".ptf", ".pts",
    ".ptxt", ".rpp", ".rpp-bak", ".flp", ".cpr", ".npr", ".song", ".bwproject",
    ".reason", ".rns", ".aup3", ".aup", ".sesx", ".dawproject", ".ardour",
    ".mmp", ".mmpz", ".mx4", ".mx5", ".tracktion", ".tracktionedit", ".ptxt",
    # NLE / motion / colour / 3D
    ".prproj", ".aep", ".aepx", ".drp", ".dra", ".fcpbundle", ".fcpxml", ".fcpxmld",
    ".veg", ".kdenlive", ".mlt", ".blend", ".motn", ".imovieproj", ".imovieproject",
    ".imovielibrary", ".c4d", ".hip", ".hiplc", ".ma", ".mb", ".nk", ".nknc",
    ".ppj", ".pproj", ".pek", ".cfa",
    # Instruments, racks, presets, sample formats
    ".nki", ".nkm", ".nkc", ".nkx", ".nkr", ".nicnt", ".nkb", ".exs", ".sfz",
    ".sf2", ".sf3", ".adg", ".adv", ".agr", ".amxd", ".fxp", ".fxb", ".vstpreset",
    ".aupreset", ".h2p", ".nmsv", ".ksd", ".serumpreset", ".spf", ".fst", ".fsp",
    ".rx2", ".rex", ".rcy", ".mgprj", ".mxgrp", ".mxinst", ".mxsnd", ".ens",
    ".ism", ".gig", ".dls", ".kmp", ".ksc", ".pgm", ".omnisphere", ".prt_omn",
    ".cube", ".3dl",
}

MEDIA_EXTS = {
    # audio
    ".wav", ".wave", ".aif", ".aiff", ".aifc", ".flac", ".mp3", ".m4a", ".ogg",
    ".oga", ".opus", ".aac", ".wma", ".caf", ".mid", ".midi", ".alac", ".ape",
    ".dsf", ".dff", ".w64", ".bwf", ".sd2", ".amr", ".mp2",
    # video
    ".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mxf", ".r3d", ".braw", ".webm",
    ".wmv", ".mpg", ".mpeg", ".mts", ".m2ts", ".ts", ".3gp", ".flv", ".dv",
    ".prores", ".ari", ".crm", ".mp4v",
    # stills / design
    ".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".tif", ".tiff", ".psd",
    ".psb", ".ai", ".eps", ".svg", ".raw", ".cr2", ".cr3", ".nef", ".arw",
    ".dng", ".orf", ".rw2", ".raf", ".pef", ".exr", ".hdr", ".bmp", ".webp",
    ".indd", ".idml", ".sketch", ".fig", ".afdesign", ".afphoto",
}

DOCUMENT_EXTS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".key", ".pages",
    ".numbers", ".txt", ".md", ".rtf", ".csv", ".tsv", ".odt", ".ods", ".odp",
    ".epub", ".mobi", ".gdoc", ".gsheet", ".gslides", ".gdraw", ".gform",
}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".sit", ".sitx"}
INSTALLER_EXTS = {".dmg", ".pkg", ".mpkg", ".exe", ".msi", ".iso", ".img", ".appimage", ".deb", ".rpm"}

# Camera/drone sidecars: they belong to the photo or video with the same stem in
# the same folder (DJI .SRT telemetry, .THM thumbnails, .LRF/.LRV proxies, .SCR,
# Lightroom .xmp, Apple Photos .aae). A sidecar only goes if its media goes.
SIDECAR_EXTS = {".srt", ".thm", ".lrf", ".lrv", ".scr", ".xmp", ".aae"}

# Broad user folders: a stray session file here must not protect every subfolder.
BROAD_DIR_NAMES = {"documents", "desktop", "downloads", "music", "movies", "pictures", "public", "shared", "home"}

# Staging areas: a copy here is a weaker candidate for "the correct path".
STAGING_DIR_NAMES = {
    "downloads", "download", "desktop", "tmp", "temp", "temporary", "trash",
    "recovered files", "recovered", "old", "backup", "backups", "bak", "archive",
    "archives", "to sort", "unsorted", "misc", "inbox", "scratch", "copies",
    "duplicates", "discarded", "junk", "_to_delete", "to_delete", "to delete",
    "99 archive", "new folder", "untitled folder", "old versions", "versions",
}
# Dated or suffixed variants: "Duplicates 2026-08-30", "discarded_2026-08-29_…",
# "Desktop _to_delete 2026-08-18", "Downloads Versions 2026-08-30", "Empty … Vault …"
STAGING_DIR_PREFIXES = (
    "duplicates", "discarded", "_to_delete", "to_delete", "to delete", "downloads versions",
    "desktop _to_delete", "empty ", "old ", "backup ", "archive ", "recovered ", "untitled folder",
    "new folder",
)


def is_staging_dir_name(name: str) -> bool:
    low = name.lower().strip()
    return low in STAGING_DIR_NAMES or low.startswith(STAGING_DIR_PREFIXES)

# Filename patterns that typically mark a copy rather than the original.
COPY_NAME_PATTERNS = [
    re.compile(r"\scopy(\s\d+)?$", re.IGNORECASE),          # "file copy 2"
    re.compile(r"\s-\scopy(\s\(\d+\))?$", re.IGNORECASE),   # "file - Copy (2)"
    re.compile(r"\s\(\d+\)$"),                              # "file (1)"
    re.compile(r"\s\d{1,2}$"),                              # "file 2"  (Finder)
    re.compile(r"[_-]copy(\d+)?$", re.IGNORECASE),          # "file_copy"
    re.compile(r"\(copy\)$", re.IGNORECASE),
    re.compile(r"^copy\sof\s", re.IGNORECASE),              # "Copy of file"
    re.compile(r"[_-](dup|duplicate|old|bak|backup)$", re.IGNORECASE),
    re.compile(r"\s\(conflicted copy.*\)$", re.IGNORECASE), # Dropbox conflicts
    re.compile(r"-\w+'s conflicted copy", re.IGNORECASE),
    re.compile(r"\s\(\w+'s\sconflicted\scopy.*\)", re.IGNORECASE),
]

# System litter / temporary files that are safe to remove when found outside
# protected areas (reported separately from content duplicates).
LITTER_EXACT = {".DS_Store", "Thumbs.db", "desktop.ini", "ehthumbs.db", "Icon\r", ".localized"}
LITTER_PREFIXES = ("._", "~$", ".~lock.")
LITTER_SUFFIXES = (
    ".crdownload", ".part", ".partial", ".download", ".tmp", ".temp",
    ".dropbox.attr", ".sb-", ".swp", ".swo",
)


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class FileRec:
    path: str
    name: str
    size: int
    mtime: float
    ctime: float
    birthtime: Optional[float]
    dev: int
    ino: int
    nlink: int
    dataless: bool
    protection: Optional[str] = None      # None | "app" | "production" | "manifest"
    protection_reason: str = ""
    kind: str = "other"
    sha256: Optional[str] = None
    hardlinks: List[str] = field(default_factory=list)
    score: int = 0
    score_notes: List[str] = field(default_factory=list)

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()

    @property
    def dir(self) -> str:
        return os.path.dirname(self.path)


@dataclass
class DupGroup:
    sha256: str
    size: int
    members: List[FileRec]
    keepers: List[FileRec] = field(default_factory=list)
    extras: List[FileRec] = field(default_factory=list)

    @property
    def canonical(self) -> FileRec:
        return self.keepers[0]

    @property
    def reclaimable(self) -> int:
        return self.size * len(self.extras)


@dataclass
class ScanStats:
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    dataless: int = 0
    symlinks: List[str] = field(default_factory=list)
    errors: List[Tuple[str, str]] = field(default_factory=list)
    excluded_dirs: List[Tuple[str, str]] = field(default_factory=list)
    hashed_files: int = 0
    hashed_bytes: int = 0
    empty_files: int = 0
    icloud_placeholders: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def human(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def iso(ts: Optional[float]) -> str:
    if ts is None:
        return "n/a"
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "n/a"


def parse_size(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)b?\s*", text, re.IGNORECASE)
    if not m:
        raise argparse.ArgumentTypeError(f"invalid size: {text!r}")
    mult = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


def md_code(s: str) -> str:
    """Render a path in inline code, escaping backticks."""
    return "`" + s.replace("`", "'") + "`"


def md_cell(s: str) -> str:
    """Inline code safe for a Markdown table cell."""
    return md_code(s).replace("|", "\\|")


def path_startswith(path: str, prefix: str) -> bool:
    prefix = prefix.rstrip(os.sep)
    return path == prefix or path.startswith(prefix + os.sep)


_MARKER_EXT_RE = re.compile(
    r"^(\.(?:" + "|".join(sorted((re.escape(e[1:]) for e in PRODUCTION_PROJECT_EXTS | APP_BUNDLE_EXTS), key=len, reverse=True))
    + r"))(?=$|[\s(\-_])",
    re.IGNORECASE,
)


def marker_ext(name: str) -> str:
    """Extension of a project/bundle name, tolerating cloud conflict suffixes.

    "Song.logicx" -> ".logicx"; "Song.logicx-Federico's MacBook Pro" -> ".logicx";
    "Edit.fcpbundle (1)" -> ".fcpbundle"; "notes.txt" -> ".txt".
    Only the final extension segment is examined, so dotted names such as
    "Artist.-.Title.(Original.Mix).aiff" cannot match on an inner token.
    """
    ext = os.path.splitext(name)[1].lower()
    if ext in PRODUCTION_PROJECT_EXTS or ext in APP_BUNDLE_EXTS:
        return ext
    m = _MARKER_EXT_RE.match(ext)
    return m.group(1).lower() if m else ext


def classify_kind(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in PRODUCTION_PROJECT_EXTS:
        return "project"
    if ext in MEDIA_EXTS:
        return "media"
    if ext in DOCUMENT_EXTS:
        return "document"
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in INSTALLER_EXTS:
        return "installer"
    return "other"


def is_litter(name: str) -> bool:
    if name in LITTER_EXACT:
        return True
    if name.startswith(LITTER_PREFIXES):
        return True
    low = name.lower()
    return any(low.endswith(s) for s in LITTER_SUFFIXES)


def looks_like_copy(name: str) -> Optional[str]:
    stem = os.path.splitext(name)[0]
    for pat in COPY_NAME_PATTERNS:
        if pat.search(stem):
            return pat.pattern
    return None


def sha256_of(path: str, limit: Optional[int] = None) -> str:
    h = hashlib.sha256()
    remaining = limit
    with open(path, "rb", buffering=0) as fh:
        while True:
            want = READ_CHUNK if remaining is None else min(READ_CHUNK, remaining)
            if want <= 0:
                break
            chunk = fh.read(want)
            if not chunk:
                break
            h.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Location discovery (the "prompt user to choose a folder or storage device")
# --------------------------------------------------------------------------- #

@dataclass
class Location:
    label: str
    path: str
    kind: str          # "external", "internal", "icloud", "dropbox", "gdrive", "onedrive", "home", "custom"
    note: str = ""


def _cloud_kind_from_name(name: str) -> Optional[Tuple[str, str]]:
    low = name.lower()
    if low.startswith("icloud"):
        return "icloud", "iCloud Drive"
    if low.startswith("dropbox"):
        return "dropbox", "Dropbox"
    if low.startswith("googledrive") or low.startswith("google drive"):
        return "gdrive", "Google Drive"
    if low.startswith("onedrive"):
        return "onedrive", "OneDrive"
    if low.startswith("box"):
        return "box", "Box"
    return None


def discover_locations() -> List[Location]:
    locs: List[Location] = []
    system = platform.system()

    # --- Removable / external volumes ------------------------------------- #
    try:
        root_dev = os.stat("/").st_dev if system != "Windows" else None
    except OSError:
        root_dev = None

    if system == "Darwin":
        vols = "/Volumes"
        if os.path.isdir(vols):
            for name in sorted(os.listdir(vols)):
                p = os.path.join(vols, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if not os.path.isdir(p):
                    continue
                if root_dev is not None and st.st_dev == root_dev:
                    locs.append(Location(name, p, "internal", "startup disk (large; consider a subfolder)"))
                else:
                    locs.append(Location(name, p, "external", "external / network volume"))
    elif system == "Linux":
        for base in (f"/media/{os.environ.get('USER', '')}", f"/run/media/{os.environ.get('USER', '')}", "/mnt", "/media"):
            if os.path.isdir(base):
                for name in sorted(os.listdir(base)):
                    p = os.path.join(base, name)
                    if os.path.isdir(p):
                        locs.append(Location(name, p, "external", "mounted volume"))
    elif system == "Windows":
        import string
        for letter in string.ascii_uppercase:
            p = f"{letter}:\\"
            if os.path.exists(p):
                kind = "internal" if letter == "C" else "external"
                locs.append(Location(f"Drive {letter}:", p, kind, "drive"))

    # --- Cloud providers ---------------------------------------------------- #
    seen_paths = set()

    def add(label: str, path: str, kind: str, note: str = "") -> None:
        rp = os.path.realpath(path)
        if os.path.isdir(path) and rp not in seen_paths:
            seen_paths.add(rp)
            locs.append(Location(label, path, kind, note))

    if system == "Darwin":
        cs = os.path.join(HOME, "Library", "CloudStorage")
        if os.path.isdir(cs):
            for name in sorted(os.listdir(cs)):
                ck = _cloud_kind_from_name(name)
                if ck:
                    add(ck[1], os.path.join(cs, name), ck[0], "File Provider sync folder (undownloaded files are skipped safely)")
        add("iCloud Drive", os.path.join(HOME, "Library", "Mobile Documents", "com~apple~CloudDocs"), "icloud",
            "classic iCloud Drive path")
    add("Dropbox", os.path.join(HOME, "Dropbox"), "dropbox")
    add("Google Drive", os.path.join(HOME, "Google Drive"), "gdrive")
    add("OneDrive", os.path.join(HOME, "OneDrive"), "onedrive")
    if system == "Windows":
        add("iCloud Drive", os.path.join(HOME, "iCloudDrive"), "icloud")
        for name in os.listdir(HOME):
            if name.lower().startswith("onedrive") and name != "OneDrive":
                add(name, os.path.join(HOME, name), "onedrive")
        for letter in "GHIJ":
            p = f"{letter}:\\My Drive"
            if os.path.isdir(p):
                add("Google Drive (stream)", p, "gdrive")

    locs.append(Location("Home folder", HOME, "home", "your user folder"))
    return locs


def location_kind_for_path(path: str, locs: Iterable[Location]) -> str:
    rp = os.path.realpath(path)
    best = ("custom", -1)
    for loc in locs:
        lrp = os.path.realpath(loc.path)
        if path_startswith(rp, lrp) and len(lrp) > best[1]:
            kind = loc.kind
            if kind == "home" and rp != lrp:
                kind = "custom"          # a plain folder somewhere under the home directory
            best = (kind, len(lrp))
    return best[0]


def prompt_for_location() -> str:
    locs = discover_locations()
    print("\nFile Analyzer — choose a folder or storage device to scan\n")
    for i, loc in enumerate(locs, 1):
        shown = loc.path.replace(HOME, "~", 1)
        tag = {"external": "external drive", "internal": "internal disk", "icloud": "iCloud",
               "dropbox": "Dropbox", "gdrive": "Google Drive", "onedrive": "OneDrive",
               "box": "Box", "home": "home"}.get(loc.kind, loc.kind)
        note = f"  — {loc.note}" if loc.note else ""
        print(f"  {i:>2}) {loc.label:<28} {shown}  [{tag}]{note}")
    manual = len(locs) + 1
    print(f"  {manual:>2}) Enter a path manually\n")

    chosen: Optional[str] = None
    while chosen is None:
        try:
            raw = input(f"Choice [1-{manual}]: ").strip()
        except EOFError:
            print("\nNo selection made. Exiting.")
            sys.exit(1)
        if raw.isdigit() and 1 <= int(raw) <= manual:
            idx = int(raw)
            if idx == manual:
                try:
                    raw_path = input("Path to scan: ").strip()
                except EOFError:
                    sys.exit(1)
                raw_path = os.path.expanduser(raw_path.strip("'\""))
                if os.path.isdir(raw_path):
                    chosen = raw_path
                else:
                    print(f"  Not a directory: {raw_path}")
            else:
                chosen = locs[idx - 1].path
        elif raw:
            cand = os.path.expanduser(raw.strip("'\""))
            if os.path.isdir(cand):
                chosen = cand
            else:
                print("  Please enter a number from the list or a valid directory path.")

    try:
        sub = input("Optional: subfolder inside that location (press Enter to scan all of it): ").strip()
    except EOFError:
        sub = ""
    if sub:
        cand = os.path.join(chosen, sub.strip("'\"").lstrip("/\\"))
        if os.path.isdir(cand):
            chosen = cand
        else:
            print(f"  Subfolder not found, scanning the whole location instead: {cand}")
    print()
    return chosen


# --------------------------------------------------------------------------- #
# Scanner
# --------------------------------------------------------------------------- #

class Scanner:
    def __init__(self, root: str, *, include_app_internals: bool, min_size: int,
                 quiet: bool, exclude_names: Iterable[str] = ()):
        self.root = root
        self.include_app_internals = include_app_internals
        self.min_size = max(1, min_size)
        self.quiet = quiet
        self.exclude_names = set(exclude_names)
        self.stats = ScanStats()
        self.files: List[FileRec] = []
        self.litter: List[FileRec] = []
        self.anchor_dirs: set = set()          # dirs that contain a production project file
        self.sidecar_targets: set = set()      # files that have an Ableton .asd analysis sidecar
        self._last_progress = 0.0

    # -- classification of directories (memoised per directory) ------------- #
    def _dir_flags(self, dirpath: str, name: str) -> Tuple[Optional[str], str]:
        """Return (skip_reason | None, protection) for a directory *about to be entered*."""
        low = name.lower()
        ext = marker_ext(name)
        if name in ALWAYS_SKIP_DIR_NAMES or name in self.exclude_names:
            return "housekeeping / excluded", ""
        if name in APP_INTERNAL_DIR_NAMES or ext in APP_BUNDLE_EXTS:
            if ext in PRODUCTION_PROJECT_EXTS or ext in {".fcpbundle", ".logicx", ".band", ".imovielibrary"}:
                return None, "production"
            if not self.include_app_internals:
                return "app-linked internals (use --include-app-internals to scan)", ""
            return None, "app"
        if low in PRODUCTION_DIR_NAMES:
            return None, "production"
        if ext in PRODUCTION_PROJECT_EXTS:      # bundle-style projects (.logicx, .fcpbundle …)
            return None, "production"
        return None, ""

    def _root_protection(self, path: str) -> Tuple[Optional[str], str]:
        """Protection implied by an absolute path prefix (OS, app, or media-library roots)."""
        for pre in PRODUCTION_ROOT_PREFIXES:
            if path_startswith(path, pre):
                return "production", f"inside media library root {pre}"
        for pre in APP_ROOT_PREFIXES:
            if path_startswith(path, pre):
                return "app", f"inside application/system root {pre}"
        # Everything else under ~/Library is application state, except the two
        # user-facing cloud folders that live there.
        user_lib = os.path.join(HOME, "Library")
        if path_startswith(path, user_lib):
            for user_data in ("Mobile Documents", "CloudStorage"):
                if path_startswith(path, os.path.join(user_lib, user_data)):
                    return None, ""
            return "app", f"inside {user_lib}"
        return None, ""

    def _progress(self, force: bool = False) -> None:
        if self.quiet:
            return
        now = time.time()
        if force or now - self._last_progress > 0.5:
            self._last_progress = now
            sys.stderr.write(f"\r  scanning… {self.stats.files:,} files, {self.stats.dirs:,} folders, "
                             f"{human(self.stats.bytes)}   ")
            sys.stderr.flush()

    # -- walk ---------------------------------------------------------------- #
    def scan(self) -> None:
        root_prot, root_reason = self._root_protection(os.path.realpath(self.root))
        # stack items: (dirpath, inherited_protection, inherited_reason)
        stack: List[Tuple[str, Optional[str], str]] = [(self.root, root_prot, root_reason)]
        while stack:
            dirpath, prot, reason = stack.pop()
            self.stats.dirs += 1
            try:
                it = os.scandir(dirpath)
            except OSError as e:
                self.stats.errors.append((dirpath, str(e)))
                continue
            with it:
                entries = []
                try:
                    for entry in it:
                        entries.append(entry)
                except OSError as e:
                    self.stats.errors.append((dirpath, str(e)))
            names = {e.name for e in entries}
            if "Ableton Project Info" in names:       # Ableton Live project folder
                self.anchor_dirs.add(dirpath)
            # Source checkouts and installed toolchains (google-cloud-sdk, SDKs with
            # bin/ + lib/) are software, not user data: treat like node_modules.
            if dirpath != self.root and prot is None and not self.include_app_internals:
                why = None
                if ".git" in names:
                    why = "source-code repository (has .git; use --include-app-internals to scan)"
                elif "bin" in names and ("lib" in names or "platform" in names or "libexec" in names):
                    why = "installed toolchain/SDK (bin + lib; use --include-app-internals to scan)"
                if why:
                    self.stats.excluded_dirs.append((dirpath, why))
                    self.stats.dirs -= 1
                    continue
            elif dirpath != self.root and prot is None and self.include_app_internals:
                if ".git" in names or ("bin" in names and ("lib" in names or "platform" in names)):
                    prot, reason = "app", f"inside source repository or installed toolchain {dirpath}"
            for entry in entries:
                p = entry.path
                try:
                    if entry.is_symlink():
                        self.stats.symlinks.append(p)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        skip, dprot = self._dir_flags(p, entry.name)
                        if skip:
                            self.stats.excluded_dirs.append((p, skip))
                            continue
                        child_prot, child_reason = prot, reason
                        if dprot == "production" and prot != "production":
                            child_prot, child_reason = "production", f"inside media-production folder {p}"
                        elif dprot == "app" and prot is None:
                            child_prot, child_reason = "app", f"inside app-linked folder {p}"
                        elif prot is None:
                            child_prot, child_reason = self._root_protection(p)
                        stack.append((p, child_prot, child_reason))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError as e:
                    self.stats.errors.append((p, str(e)))
                    continue

                name = entry.name
                # iCloud legacy placeholder: ".name.ext.icloud"
                if name.startswith(".") and name.endswith(".icloud"):
                    self.stats.icloud_placeholders.append(p)
                    self.stats.dataless += 1
                    continue
                dataless = bool(getattr(st, "st_flags", 0) & SF_DATALESS)
                ext = marker_ext(name)
                if ext in PRODUCTION_PROJECT_EXTS:
                    self.anchor_dirs.add(dirpath)
                elif ext == ".asd":
                    # Ableton analysis sidecar "track.aiff.asd": protects only that track,
                    # not the whole folder (a DJ library often has one stray .asd).
                    self.sidecar_targets.add(os.path.join(dirpath, name[:-4]))

                rec = FileRec(
                    path=p, name=name, size=st.st_size, mtime=st.st_mtime, ctime=st.st_ctime,
                    birthtime=getattr(st, "st_birthtime", None), dev=st.st_dev, ino=st.st_ino,
                    nlink=getattr(st, "st_nlink", 1), dataless=dataless, protection=prot, protection_reason=reason,
                    kind=classify_kind(name),
                )
                self.stats.files += 1
                self.stats.bytes += st.st_size
                if dataless:
                    self.stats.dataless += 1
                if is_litter(name):
                    self.litter.append(rec)
                    continue
                if st.st_size == 0:
                    self.stats.empty_files += 1
                    continue
                if st.st_size < self.min_size:
                    continue
                self.files.append(rec)
                self._progress()
        self._progress(force=True)
        if not self.quiet:
            sys.stderr.write("\n")

    # -- post-scan: production anchors propagate to descendants ------------- #
    def apply_anchor_protection(self) -> None:
        for rec in self.files:
            if rec.protection != "production" and rec.path in self.sidecar_targets:
                rec.protection = "production"
                rec.protection_reason = "has an Ableton .asd analysis sidecar (this file was loaded in Live)"
        if not self.anchor_dirs:
            return
        cache: Dict[str, Optional[str]] = {}

        def is_broad(d: str) -> bool:
            return d == HOME or os.path.basename(d).lower() in BROAD_DIR_NAMES

        def anchor_for(d: str) -> Optional[str]:
            """Nearest anchor whose scope covers directory d.

            A project folder protects its whole subtree. A broad folder such as
            Documents or Downloads that happens to hold a session file protects
            only the files directly inside it.
            """
            if d in cache:
                return cache[d]
            found = None
            cur = d
            while path_startswith(cur, self.root):
                if cur in self.anchor_dirs and (cur == d or not is_broad(cur)):
                    found = cur
                    break
                parent = os.path.dirname(cur)
                if parent == cur:
                    break
                cur = parent
            cache[d] = found
            return found

        for rec in self.files + self.litter:
            if rec.protection == "production":
                continue
            # Sessions reference media, not documents or installers: only media and
            # project-type files inherit protection from an anchor.
            if rec.kind not in ("media", "project"):
                continue
            a = anchor_for(rec.dir)
            if a is not None:
                rec.protection = "production"
                rec.protection_reason = f"media inside project folder anchored by session/preset files in {a}"


# --------------------------------------------------------------------------- #
# Duplicate detection
# --------------------------------------------------------------------------- #

def find_duplicates(files: List[FileRec], stats: ScanStats, quiet: bool) -> Tuple[List[DupGroup], List[List[FileRec]]]:
    """Return (confirmed duplicate groups, unverifiable size+name matches involving dataless files)."""
    by_size: Dict[int, List[FileRec]] = defaultdict(list)
    for rec in files:
        by_size[rec.size].append(rec)

    groups: List[DupGroup] = []
    unverified: List[List[FileRec]] = []
    candidates = [recs for recs in by_size.values() if len(recs) > 1]
    total = sum(len(c) for c in candidates)
    done = 0
    last = 0.0

    for recs in candidates:
        # Collapse hard links (same inode on same device, link count > 1) into a
        # single record. The nlink guard avoids false merges on filesystems that
        # report unstable inode numbers (some SMB/FAT mounts).
        by_inode: Dict[Tuple[int, int], FileRec] = {}
        unique: List[FileRec] = []
        for rec in recs:
            if rec.nlink > 1 and rec.ino:
                key = (rec.dev, rec.ino)
                if key in by_inode:
                    by_inode[key].hardlinks.append(rec.path)
                    continue
                by_inode[key] = rec
            unique.append(rec)

        materialised = [r for r in unique if not r.dataless]
        dataless = [r for r in unique if r.dataless]

        # Dataless files can only be matched by name + size (never opened).
        if dataless:
            by_name: Dict[str, List[FileRec]] = defaultdict(list)
            for r in unique:
                by_name[r.name.lower()].append(r)
            for same in by_name.values():
                if len(same) > 1 and any(r.dataless for r in same):
                    unverified.append(same)

        if len(materialised) < 2:
            done += len(recs)
            continue

        # Stage 1: partial hash.
        by_partial: Dict[str, List[FileRec]] = defaultdict(list)
        for r in materialised:
            try:
                by_partial[sha256_of(r.path, PARTIAL_HASH_BYTES)].append(r)
            except OSError as e:
                stats.errors.append((r.path, str(e)))
        # Stage 2: full hash (the partial digest already is the full digest for small files).
        for partial_digest, part in by_partial.items():
            if len(part) < 2:
                continue
            by_full: Dict[str, List[FileRec]] = defaultdict(list)
            for r in part:
                try:
                    if r.size <= PARTIAL_HASH_BYTES:
                        r.sha256 = partial_digest
                    else:
                        r.sha256 = sha256_of(r.path)
                    stats.hashed_files += 1
                    stats.hashed_bytes += r.size
                    by_full[r.sha256].append(r)
                except OSError as e:
                    stats.errors.append((r.path, str(e)))
            for digest, same in by_full.items():
                if len(same) > 1:
                    groups.append(DupGroup(sha256=digest, size=same[0].size, members=same))
        done += len(recs)
        if not quiet and time.time() - last > 0.5:
            last = time.time()
            sys.stderr.write(f"\r  hashing… {done:,}/{total:,} candidate files, {len(groups):,} duplicate groups   ")
            sys.stderr.flush()
    if not quiet and total:
        sys.stderr.write(f"\r  hashing… {done:,}/{total:,} candidate files, {len(groups):,} duplicate groups   \n")
    return groups, unverified


# --------------------------------------------------------------------------- #
# Manifest integrity (optional)
# --------------------------------------------------------------------------- #

@dataclass
class ManifestResult:
    path: str
    ok: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    size_mismatch: List[Tuple[str, int, int]] = field(default_factory=list)
    hash_mismatch: List[Tuple[str, str, str]] = field(default_factory=list)
    invalid: List[str] = field(default_factory=list)


def load_manifest(root: str, manifest_path: Optional[str]) -> Optional[Tuple[str, Dict[str, dict]]]:
    cand = manifest_path or os.path.join(root, "manifest.json")
    if not os.path.isfile(cand):
        return None
    try:
        with open(cand, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    entries: Dict[str, dict] = {}
    items = data.get("files", data) if isinstance(data, dict) else data
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and it.get("path"):
                entries[it["path"]] = it
    elif isinstance(items, dict):
        for k, v in items.items():
            if isinstance(v, dict):
                entries[k] = dict(v, path=k)
            elif isinstance(v, str):
                entries[k] = {"path": k, "sha256": v}
    return cand, entries


def verify_manifest(root: str, manifest: Tuple[str, Dict[str, dict]], files_by_path: Dict[str, FileRec]) -> ManifestResult:
    mpath, entries = manifest
    res = ManifestResult(path=mpath)
    for rel, meta in entries.items():
        abs_path = os.path.normpath(os.path.join(root, rel))
        if not os.path.isfile(abs_path):
            res.missing.append(rel)
            continue
        rec = files_by_path.get(abs_path)
        try:
            size = os.stat(abs_path).st_size
        except OSError:
            res.missing.append(rel)
            continue
        exp_size = meta.get("size")
        if isinstance(exp_size, int) and exp_size != size:
            res.size_mismatch.append((rel, exp_size, size))
            continue
        exp_hash = (meta.get("sha256") or "").lower()
        if exp_hash:
            if rec is not None and rec.dataless:
                res.invalid.append(f"{rel} (not downloaded; cannot hash)")
                continue
            try:
                actual = rec.sha256 if (rec and rec.sha256) else sha256_of(abs_path)
            except OSError as e:
                res.invalid.append(f"{rel} ({e})")
                continue
            if rec is not None:
                rec.sha256 = actual
            if actual != exp_hash:
                res.hash_mismatch.append((rel, exp_hash, actual))
                continue
        res.ok.append(rel)
        if rec is not None:
            rec.protection = "manifest"
            rec.protection_reason = f"listed in manifest {os.path.basename(mpath)}"
    return res


# --------------------------------------------------------------------------- #
# Deciding the correct path (keeper) and the extras
# --------------------------------------------------------------------------- #

def path_depth(rec: FileRec, root: str) -> int:
    rel_dir = os.path.relpath(rec.dir, root) if path_startswith(rec.dir, root) else rec.dir
    return len([p for p in rel_dir.split(os.sep) if p and p != "."])


def score_candidate(rec: FileRec, root: str, prefer: Iterable[str] = (), demote: Iterable[str] = ()) -> None:
    """Penalty score: 0 means 'looks like the original in its proper place'."""
    score = 0
    notes: List[str] = []
    rel_dir = os.path.relpath(rec.dir, root) if path_startswith(rec.dir, root) else rec.dir
    parts = [p for p in rel_dir.split(os.sep) if p and p != "."]
    for pre in prefer:
        if path_startswith(rec.dir, pre):
            score -= 100
            notes.append(f"inside preferred folder {os.path.basename(pre) or pre}")
            break
    for pre in demote:
        if path_startswith(rec.dir, pre):
            score += 100
            notes.append(f"inside demoted folder {os.path.basename(pre) or pre}")
            break
    staging = [p for p in parts if is_staging_dir_name(p)]
    if staging:
        score += 50
        notes.append(f"sits in a staging folder ({staging[0]})")
    if looks_like_copy(rec.name):
        score += 30
        notes.append("filename looks like a copy")
    if rec.protection == "manifest":
        score -= 1000
        notes.append("listed in manifest")
    rec.score = score
    rec.score_notes = notes


def resolve_groups(groups: List[DupGroup], root: str, protected_as_canonical: bool,
                   prefer: Iterable[str] = (), demote: Iterable[str] = ()) -> None:
    prefer = [os.path.abspath(os.path.expanduser(p)) for p in prefer]
    demote = [os.path.abspath(os.path.expanduser(p)) for p in demote]
    for g in groups:
        for m in g.members:
            score_candidate(m, root, prefer, demote)
        protected = [m for m in g.members if m.protection]
        free = [m for m in g.members if not m.protection]
        # Ordering key: fewest penalties, then oldest copy (the original, to the
        # second — sub-second noise from batch copies is ignored), then
        # shallowest path, then shortest path.
        free.sort(key=lambda r: (r.score, int(r.mtime), path_depth(r, root), len(r.path), r.path))
        if free:
            k = free[0]
            for r in free[1:]:
                if not r.score_notes:
                    if int(r.mtime) > int(k.mtime):
                        r.score_notes.append("newer copy of the same content")
                    elif path_depth(r, root) > path_depth(k, root):
                        r.score_notes.append("same age, deeper path than the keeper")
                    else:
                        r.score_notes.append("same age, longer path than the keeper")

        manifest_members = [m for m in protected if m.protection == "manifest"]
        if not free:
            g.keepers = sorted(protected, key=lambda r: r.path)
            g.extras = []
            continue
        if manifest_members:
            # The manifest is the objective definition of the correct path.
            g.keepers = sorted(manifest_members, key=lambda r: r.path) + \
                sorted((m for m in protected if m.protection != "manifest"), key=lambda r: r.path)
            g.extras = free
            for r in free:
                r.score_notes.insert(0, "a manifest-listed copy exists")
            continue
        if protected and protected_as_canonical:
            g.keepers = sorted(protected, key=lambda r: r.path)
            g.extras = free
            continue
        if protected and len(free) == 1:
            # Only one user-accessible copy: keep it. Nothing to delete.
            g.keepers = [free[0]] + sorted(protected, key=lambda r: r.path)
            g.extras = []
            continue
        g.keepers = [free[0]] + sorted(protected, key=lambda r: r.path)
        g.extras = free[1:]


def apply_sidecar_rule(groups: List[DupGroup], files: List[FileRec]) -> int:
    """Keep a duplicate sidecar whenever the media file it belongs to stays.

    Runs after resolve_groups(). Returns the number of extras converted to keepers.
    """
    extras_set = {e.path for g in groups for e in g.extras}
    by_stem: Dict[Tuple[str, str], List[FileRec]] = defaultdict(list)
    for f in files:
        if f.kind == "media":
            by_stem[(f.dir, os.path.splitext(f.name)[0].lower())].append(f)
    moved = 0
    for g in groups:
        if not g.extras:
            continue
        still_extra: List[FileRec] = []
        for e in g.extras:
            if e.ext not in SIDECAR_EXTS:
                still_extra.append(e)
                continue
            stem = os.path.splitext(e.name)[0].lower()
            owners = [m for m in by_stem.get((e.dir, stem), []) if m.path not in extras_set]
            if owners:
                e.protection = "sidecar"
                e.protection_reason = f"belongs to {owners[0].name}, which stays in this folder"
                e.score_notes = []
                g.keepers.append(e)
                moved += 1
            else:
                still_extra.append(e)
        g.extras = still_extra
    return moved


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

KIND_LABEL = {"external": "External / network volume", "internal": "Internal disk", "icloud": "iCloud Drive",
              "dropbox": "Dropbox", "gdrive": "Google Drive", "onedrive": "OneDrive", "box": "Box",
              "home": "Home folder", "custom": "Local folder"}


def rec_line(rec: FileRec, *, label: str, show_hash: bool = False, extra: str = "") -> str:
    bits = [human(rec.size), f"modified {iso(rec.mtime)}"]
    if rec.birthtime:
        bits.append(f"created {iso(rec.birthtime)}")
    if show_hash and rec.sha256:
        bits.append(f"sha256 {rec.sha256[:16]}…")
    if rec.protection:
        bits.append(f"protected: {rec.protection} — {rec.protection_reason}")
    if extra:
        bits.append(extra)
    return f"**{label}** {md_code(rec.path)} — " + " — ".join(bits)


def write_report(out_path: str, *, root: str, real_root: str, loc_kind: str, stats: ScanStats,
                 groups: List[DupGroup], unverified: List[List[FileRec]], litter: List[FileRec],
                 manifest_res: Optional[ManifestResult], args: argparse.Namespace, started: float) -> dict:
    actionable = [g for g in groups if g.extras]
    protected_only = [g for g in groups if not g.extras]
    extras = [(g, e) for g in actionable for e in g.extras]
    reclaim = sum(g.reclaimable for g in actionable)
    free_litter = [l for l in litter if not l.protection]
    litter_bytes = sum(l.size for l in free_litter)
    dup_bytes_total = sum(g.size * (len(g.members) - 1) for g in groups)

    L: List[str] = []
    w = L.append
    w("# File Analyzer Report — Duplicate Detection and Cleanup")
    w("")
    w(f"- **Target:** {md_code(root)}" + (f" (resolves to {md_code(real_root)})" if real_root != root else ""))
    w(f"- **Location type:** {KIND_LABEL.get(loc_kind, loc_kind)}")
    w(f"- **Scanned at:** {iso(started)} — took {time.time() - started:.1f}s")
    w(f"- **Files scanned:** {stats.files:,} in {stats.dirs:,} folders ({human(stats.bytes)})")
    w(f"- **Hashed for verification:** {stats.hashed_files:,} files ({human(stats.hashed_bytes)}) — SHA-256, byte-for-byte identical")
    w(f"- **Duplicate groups found:** {len(groups):,} (redundant data across all copies: {human(dup_bytes_total)})")
    w(f"- **Extras that can be deleted:** {len(extras):,} files — **{human(reclaim)} reclaimable**")
    w(f"- **System litter / temp files:** {len(free_litter):,} files ({human(litter_bytes)})")
    skipped = []
    if stats.dataless:
        skipped.append(f"{stats.dataless:,} cloud files not downloaded (never opened)")
    if stats.symlinks:
        skipped.append(f"{len(stats.symlinks):,} symlinks (not followed)")
    if stats.excluded_dirs:
        skipped.append(f"{len(stats.excluded_dirs):,} app-linked or housekeeping folders")
    if stats.empty_files:
        skipped.append(f"{stats.empty_files:,} empty files")
    if stats.errors:
        skipped.append(f"{len(stats.errors):,} unreadable items")
    w(f"- **Skipped:** {'; '.join(skipped) if skipped else 'nothing'}")
    w("")
    w("> This report is read-only. Nothing was deleted, moved, or modified. Review every item before acting.")
    w("")

    # ---- Section 1 ---------------------------------------------------------- #
    w("## 1. Duplicates grouped by their correct path")
    w("")
    w("Each group is listed under the folder of the copy that should stay (the **KEEP** entry). "
      "The keeper is chosen by: manifest listing, then `--prefer`/`--demote` folders"
      + (f" (preferred: {', '.join(args.prefer)})" if args.prefer else "")
      + (f" (demoted: {', '.join(args.demote)})" if args.demote else "")
      + ", then location (organised folder beats Downloads/Desktop/temp), "
      "then filename (originals beat names like `file copy 2`), then age (oldest wins), then shortest path. "
      "Copies inside app or media-production locations are always kept.")
    w("")
    if not actionable:
        w("_No deletable duplicates found._")
        w("")
    else:
        by_dir: Dict[str, List[DupGroup]] = defaultdict(list)
        for g in actionable:
            by_dir[g.canonical.dir].append(g)
        for d in sorted(by_dir):
            gs = sorted(by_dir[d], key=lambda g: (-g.size, g.canonical.name))
            w(f"### {md_code(d)}")
            w("")
            w(f"_{len(gs)} group(s), {human(sum(g.reclaimable for g in gs))} reclaimable_")
            w("")
            for g in gs:
                keeper = g.canonical
                w("- " + rec_line(keeper, label="KEEP", show_hash=True))
                for hl in keeper.hardlinks:
                    w(f"  - **SAME FILE** {md_code(hl)} — hard link to the keeper (no extra space used)")
                for k in g.keepers[1:]:
                    w("  - " + rec_line(k, label="KEEP"))
                for e in g.extras:
                    why = "; ".join(e.score_notes) if e.score_notes else "redundant copy of the keeper"
                    w("  - " + rec_line(e, label="EXTRA", extra=f"why: {why}"))
                    for hl in e.hardlinks:
                        w(f"    - **SAME FILE** {md_code(hl)} — hard link to this extra")
            w("")

    # ---- Section 2 ---------------------------------------------------------- #
    w("## 2. Extras that can be deleted")
    w("")
    w("Redundant copies **not** linked to any application, DAW/NLE project, plug-in, or sample library. "
      "An identical, verified copy exists at the *duplicate of* path. Sorted largest first.")
    w("")
    if not extras:
        w("_None._")
        w("")
    else:
        media_extras = [(g, e) for g, e in extras if e.kind == "media"]
        if media_extras:
            w(f"> ⚠️ {len(media_extras)} of these are audio/video/image files that sit outside any detected project or library. "
              "They are not referenced by anything inside the scanned location, but a DAW or NLE project stored "
              "*elsewhere* could still reference them by absolute path. Double-check before removing.")
            w("")
        w("| # | Path | Size | Modified | Kind | Duplicate of |")
        w("|---|------|------|----------|------|--------------|")
        for i, (g, e) in enumerate(sorted(extras, key=lambda ge: (-ge[1].size, ge[1].path)), 1):
            kind = e.kind + (" ⚠️" if e.kind == "media" else "")
            w(f"| {i} | {md_cell(e.path)} | {human(e.size)} | {iso(e.mtime)} | {kind} | {md_cell(g.canonical.path)} |")
        w("")
        w(f"**Subtotal: {len(extras):,} files, {human(reclaim)}.**")
        w("")
        w("Plain-path list (for copy/paste review):")
        w("")
        w("```")
        for g, e in sorted(extras, key=lambda ge: ge[1].path):
            w(e.path)
        w("```")
        w("")

    w("### 2b. System litter and temporary files (low risk)")
    w("")
    w("Finder/Explorer metadata, AppleDouble `._*` sidecars, editor lock files and abandoned partial downloads found "
      "outside protected areas. Safe to remove; the OS recreates what it needs. `._*` files carry macOS extended "
      "attributes for the sibling file on non-Apple volumes — harmless to lose for media and documents.")
    w("")
    if not free_litter:
        w("_None._")
    else:
        for l in sorted(free_litter, key=lambda r: r.path):
            w(f"- {md_code(l.path)} — {human(l.size)} — modified {iso(l.mtime)}")
        w("")
        w(f"**Subtotal: {len(free_litter):,} files, {human(litter_bytes)}.**")
    w("")

    # ---- Section 3 ---------------------------------------------------------- #
    w("## 3. Duplicates kept on purpose (informational)")
    w("")
    w("Groups where every copy, or every copy but one, lives inside an application, plug-in, DAW/NLE project, "
      "or media library. Deleting these would risk breaking something, so nothing here is proposed for removal."
      + (" Run with `--protected-as-canonical` to treat the single loose copy as an extra." if not args.protected_as_canonical else ""))
    w("")
    if not protected_only:
        w("_None._")
    else:
        for g in sorted(protected_only, key=lambda g: (-g.size, g.canonical.path)):
            w("- " + rec_line(g.canonical, label="KEEP", show_hash=True))
            for k in g.keepers[1:]:
                w("  - " + rec_line(k, label="KEEP"))
    w("")

    # ---- Section 4 ---------------------------------------------------------- #
    w("## 4. Could not verify")
    w("")
    w("### 4a. Cloud files not downloaded (possible duplicates by name and size only)")
    w("")
    w("These files are placeholders whose content is still in the cloud. The analyzer never opens them (that would "
      "trigger a download), so they cannot be hash-verified. Download the folder and re-run to confirm.")
    w("")
    if not unverified and not stats.icloud_placeholders:
        w("_None._")
    else:
        for same in sorted(unverified, key=lambda s: (-s[0].size, s[0].name)):
            w(f"- {md_code(same[0].name)} — {human(same[0].size)} × {len(same)}")
            for r in same:
                w(f"  - {md_code(r.path)} — modified {iso(r.mtime)}" + (" — not downloaded" if r.dataless else " — local"))
        if stats.icloud_placeholders:
            w("")
            w(f"iCloud placeholders (`.icloud` stubs) not analysed: {len(stats.icloud_placeholders):,}")
            for p in stats.icloud_placeholders[:200]:
                w(f"- {md_code(p)}")
            if len(stats.icloud_placeholders) > 200:
                w(f"- … and {len(stats.icloud_placeholders) - 200:,} more")
    w("")
    w("### 4b. Symlinks (not followed)")
    w("")
    if not stats.symlinks:
        w("_None._")
    else:
        for p in stats.symlinks[:300]:
            try:
                target = os.readlink(p)
            except OSError:
                target = "?"
            w(f"- {md_code(p)} → {md_code(target)}")
        if len(stats.symlinks) > 300:
            w(f"- … and {len(stats.symlinks) - 300:,} more")
    w("")
    w("### 4c. Folders not scanned")
    w("")
    if not stats.excluded_dirs:
        w("_None._")
    else:
        for p, why in stats.excluded_dirs[:300]:
            w(f"- {md_code(p)} — {why}")
        if len(stats.excluded_dirs) > 300:
            w(f"- … and {len(stats.excluded_dirs) - 300:,} more")
    w("")
    w("### 4d. Read errors")
    w("")
    if not stats.errors:
        w("_None._")
    else:
        for p, err in stats.errors[:300]:
            w(f"- {md_code(p)} — {err}")
        if len(stats.errors) > 300:
            w(f"- … and {len(stats.errors) - 300:,} more")
    w("")

    # ---- Section 5 ---------------------------------------------------------- #
    if manifest_res is not None:
        mr = manifest_res
        w("## 5. Manifest integrity check")
        w("")
        w(f"Manifest: {md_code(mr.path)} — {len(mr.ok):,} verified, {len(mr.missing):,} missing (ENOENT), "
          f"{len(mr.size_mismatch):,} size mismatches, {len(mr.hash_mismatch):,} hash mismatches, {len(mr.invalid):,} unverifiable")
        w("")
        status = "✅ COMPLETE — zero missing keys and zero hash mismatches" if not (mr.missing or mr.size_mismatch or mr.hash_mismatch or mr.invalid) else "❌ INCOMPLETE"
        w(f"**Status:** {status}")
        w("")
        for title, rows in (("Missing", mr.missing), ("Unverifiable", mr.invalid)):
            if rows:
                w(f"**{title}:**")
                for r in rows:
                    w(f"- {md_code(r)}")
                w("")
        if mr.size_mismatch:
            w("**Size mismatches:**")
            for rel, exp, got in mr.size_mismatch:
                w(f"- {md_code(rel)} — expected {exp:,} B, found {got:,} B")
            w("")
        if mr.hash_mismatch:
            w("**Hash mismatches:**")
            for rel, exp, got in mr.hash_mismatch:
                w(f"- {md_code(rel)} — expected `{exp[:16]}…`, found `{got[:16]}…`")
            w("")

    # ---- Appendix ------------------------------------------------------------ #
    w("## Appendix — how files were classified")
    w("")
    w("- **Canonical path resolution:** the target was resolved with `realpath`; symlinks are recorded but never followed, "
      "so link loops and traversal outside the target are impossible.")
    w("- **Duplicate:** same byte size → same SHA-256 of the first 64 KB → same SHA-256 of the whole file. "
      "Hard links to the same inode count as one file, not as duplicates.")
    w("- **App-linked (never deletable):** inside `.app`/`.framework`/`.bundle`-style packages, `/Applications`, `/System`, "
      "`/Library`, `~/Library/Application Support`, `~/Library/Containers`, package caches such as `node_modules`, `.git`, "
      "virtualenvs. These folders are not traversed by default (`--include-app-internals` to scan them).")
    w("- **Media-production (never deletable):** audio, video and image files beneath a folder that contains a DAW/NLE "
      "session, instrument, rack or preset file (Ableton `.als`/`.adg`, Logic `.logicx`, Pro Tools `.ptx`, Reaper `.rpp`, "
      "FL `.flp`, Cubase `.cpr`, Kontakt `.nki`, Premiere `.prproj`, After Effects `.aep`, Resolve `.drp`, Final Cut "
      "`.fcpbundle`, …); a session file sitting in a broad folder such as Documents, Desktop or Downloads protects only the "
      "media next to it. Also everything beneath a library folder such as `Samples`, `User Library`, `Packs`, `Splice`, "
      "`Native Instruments`, `Plug-Ins`, `Presets`, `Impulse Responses`, `Footage`, `Media Cache`, or the OS audio/plug-in roots.")
    w("- **Extra:** a verified duplicate that is not app-linked or production-linked and is not the chosen keeper.")
    w("- **Sidecars:** `.srt`, `.thm`, `.lrf`, `.lrv`, `.scr`, `.xmp`, `.aae` files belong to the photo or video with the "
      "same name in the same folder. A duplicate sidecar is only an extra when that media file is also an extra or is absent; "
      "otherwise it is kept with its media (shown as `protected: sidecar`).")
    w(f"- **Ignored:** files under {parse_size_label(args.min_size)}, empty files, OS/cloud housekeeping folders.")
    w("")
    w(f"_Generated by file-analyzer v{__version__} — Python {platform.python_version()} on {platform.system()} {platform.release()}._")
    w("")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    return {
        "target": root, "resolved": real_root, "location_type": loc_kind, "scanned_at": iso(started),
        "files": stats.files, "dirs": stats.dirs, "bytes": stats.bytes,
        "duplicate_groups": len(groups), "extras": len(extras), "reclaimable_bytes": reclaim,
        "litter": len(free_litter), "litter_bytes": litter_bytes,
        "dataless_skipped": stats.dataless, "errors": len(stats.errors),
    }


def parse_size_label(n: int) -> str:
    return human(n) if n > 1 else "1 B"


def write_json(out_path: str, summary: dict, groups: List[DupGroup], litter: List[FileRec],
               unverified: List[List[FileRec]], manifest_res: Optional[ManifestResult]) -> None:
    def rec(r: FileRec) -> dict:
        return {
            "path": r.path, "size": r.size, "modified": iso(r.mtime), "created": iso(r.birthtime),
            "sha256": r.sha256, "kind": r.kind, "protection": r.protection,
            "protection_reason": r.protection_reason or None, "hardlinks": r.hardlinks,
            "notes": r.score_notes,
        }
    data = {
        "summary": summary,
        "groups": [
            {
                "sha256": g.sha256, "size": g.size, "canonical_path": g.canonical.path,
                "keepers": [rec(k) for k in g.keepers], "extras": [rec(e) for e in g.extras],
                "reclaimable_bytes": g.reclaimable,
            } for g in groups
        ],
        "litter": [rec(l) for l in litter if not l.protection],
        "unverified_cloud_matches": [[rec(r) for r in same] for same in unverified],
        "manifest": None if manifest_res is None else {
            "path": manifest_res.path, "ok": manifest_res.ok, "missing": manifest_res.missing,
            "size_mismatch": manifest_res.size_mismatch, "hash_mismatch": manifest_res.hash_mismatch,
            "invalid": manifest_res.invalid,
        },
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="analyze.py",
        description="Scan a folder, external drive, or cloud folder for duplicate files and report which "
                    "copies are extras that can be deleted. Read-only: nothing is ever deleted.",
    )
    p.add_argument("--path", "-p", help="folder or volume to scan (omit to choose interactively)")
    p.add_argument("--out", "-o", help="directory for the report (default: tools/file-analyzer/reports)")
    p.add_argument("--json", action="store_true", help="also write a JSON sidecar next to the Markdown report")
    p.add_argument("--min-size", type=parse_size, default=1, metavar="SIZE",
                   help="ignore files smaller than SIZE, e.g. 4K, 1M (default: 1 byte)")
    p.add_argument("--include-app-internals", action="store_true",
                   help="traverse .app bundles, node_modules, .git, caches, etc. (files there are still protected)")
    p.add_argument("--protected-as-canonical", action="store_true",
                   help="when a copy exists inside an app or media project, treat the single loose copy as an extra")
    p.add_argument("--manifest", help="manifest.json to verify (default: <target>/manifest.json if present)")
    p.add_argument("--prefer", action="append", default=[], metavar="DIR",
                   help="copies inside DIR win as the keeper (repeatable)")
    p.add_argument("--demote", action="append", default=[], metavar="DIR",
                   help="copies inside DIR lose as the keeper when another copy exists (repeatable)")
    p.add_argument("--exclude", action="append", default=[], metavar="NAME",
                   help="additional folder name to skip (repeatable)")
    p.add_argument("--quiet", "-q", action="store_true", help="no progress output")
    p.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--version", action="version", version=f"file-analyzer {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    root = os.path.expanduser(args.path) if args.path else prompt_for_location()
    root = os.path.abspath(root.rstrip(os.sep) or os.sep)
    if not os.path.isdir(root):
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    real_root = os.path.realpath(root)
    if not os.access(real_root, os.R_OK | os.X_OK):
        print(f"error: no read/execute permission on {real_root}", file=sys.stderr)
        return 2

    locs = discover_locations()
    loc_kind = location_kind_for_path(root, locs)
    if loc_kind == "internal" and real_root in ("/", os.path.realpath("/Volumes/Macintosh HD")):
        print("note: scanning the whole startup disk can take a very long time; a subfolder is usually more useful.",
              file=sys.stderr)

    if not args.yes and not args.path:
        try:
            ans = input(f"Scan {root} ({KIND_LABEL.get(loc_kind, loc_kind)})? [Y/n] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans not in ("", "y", "yes"):
            print("Cancelled.")
            return 1

    started = time.time()
    if not args.quiet:
        print(f"Scanning {root} …", file=sys.stderr)

    scanner = Scanner(root, include_app_internals=args.include_app_internals, min_size=args.min_size,
                      quiet=args.quiet, exclude_names=args.exclude)
    root_prot, root_reason = scanner._root_protection(real_root)
    if root_prot == "app":
        print(f"note: this location is {root_reason}. Everything inside counts as app-linked, "
              "so duplicates will be reported but no extras will be proposed for deletion.", file=sys.stderr)
    scanner.scan()
    scanner.apply_anchor_protection()

    groups, unverified = find_duplicates(scanner.files, scanner.stats, args.quiet)

    manifest_res = None
    manifest = load_manifest(root, args.manifest)
    if manifest:
        by_path = {r.path: r for r in scanner.files}
        manifest_res = verify_manifest(root, manifest, by_path)

    resolve_groups(groups, root, args.protected_as_canonical, prefer=args.prefer, demote=args.demote)
    apply_sidecar_rule(groups, scanner.files)

    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(out_dir, exist_ok=True)
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(root) or "root").strip("-") or "root"
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    md_path = os.path.join(out_dir, f"duplicate-report_{label}_{stamp}.md")

    summary = write_report(md_path, root=root, real_root=real_root, loc_kind=loc_kind, stats=scanner.stats,
                           groups=groups, unverified=unverified, litter=scanner.litter,
                           manifest_res=manifest_res, args=args, started=started)
    if args.json:
        json_path = md_path[:-3] + ".json"
        write_json(json_path, summary, groups, scanner.litter, unverified, manifest_res)

    extras = summary["extras"]
    print()
    print(f"Done. {summary['files']:,} files scanned, {summary['duplicate_groups']:,} duplicate groups, "
          f"{extras:,} extras ({human(summary['reclaimable_bytes'])} reclaimable), "
          f"{summary['litter']:,} litter files.")
    if summary["dataless_skipped"]:
        print(f"      {summary['dataless_skipped']:,} cloud files were not downloaded and were skipped (not opened).")
    print(f"Report: {md_path}")
    if args.json:
        print(f"JSON:   {md_path[:-3] + '.json'}")
    print("Nothing was deleted. Review the report before removing anything.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted. No report written.", file=sys.stderr)
        sys.exit(130)
