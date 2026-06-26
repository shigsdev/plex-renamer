#!/usr/bin/env python3
"""
dedupe.py — Find the same episode duplicated across drives.

The TV library is accidentally duplicated across physical drives (P/Q/R/N). Once
filenames use canonical S##E## (run the renamer first), a duplicate episode is
simply the same ``(library, show, season, episode)`` appearing on more than one
drive. This module finds those groups read-only and attaches per-copy metadata
(drive, size, resolution) plus a *suggested* keeper — the user still approves the
actual keep choice per group (see BACKLOG.md). Nothing here deletes anything.

Pure standard library. Importable (a UI consumes ``find_duplicates``) and
runnable (``python dedupe.py "P:/TV Shows" "Q:/TV Shows" ...``) for a summary.
"""

from __future__ import annotations

import os
import re
import shutil
import string
import subprocess
import sys
from dataclasses import asdict, dataclass

import scanner

# Resolution tokens -> a sortable rank. Unknown sorts lowest.
_RES_PATTERNS = [
    (re.compile(r"\b(?:2160p|4k|uhd)\b", re.I), 2160),
    (re.compile(r"\b1080[pi]\b", re.I), 1080),
    (re.compile(r"\b720p\b", re.I), 720),
    (re.compile(r"\b(?:480p|576p|sd)\b", re.I), 480),
]

_CANON = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")


def _resolution(name: str) -> int:
    for pat, rank in _RES_PATTERNS:
        if pat.search(name):
            return rank
    return 0


# ffprobe is optional: when the filename carries no resolution token we fall back
# to reading the real video height — but only if ffprobe is installed. Located
# once at import; None means "not available, stay name-based".
_FFPROBE = shutil.which("ffprobe")


def _resolution_from_file(path: str) -> int:
    """Real video height via ffprobe, bucketed to a standard label (1080, 2160,
    …). Returns 0 when ffprobe is unavailable or the probe fails. Used ONLY as a
    fallback for files whose name has no resolution token, and ONLY on files that
    are already in a duplicate group, so the cost is bounded."""
    if not _FFPROBE:
        return 0
    try:
        proc = subprocess.run(
            [_FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=height", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=20)
        height = int((proc.stdout or "").strip().splitlines()[0])
    except (ValueError, IndexError, OSError, subprocess.SubprocessError):
        return 0
    for thresh, label in ((1800, 2160), (1300, 1440), (900, 1080),
                          (650, 720), (520, 576), (380, 480)):
        if height >= thresh:
            return label
    return height if height > 0 else 0


def _fill_unknown_resolutions(copies: list[Copy]) -> None:
    """Try ffprobe on any copy whose resolution couldn't be read from its name."""
    for c in copies:
        if c.resolution == 0:
            c.resolution = _resolution_from_file(c.path)


def _drive_of(path: str) -> str:
    d = os.path.splitdrive(path)[0]
    return d.rstrip(":").upper() or "?"


def _drive_letters() -> list[str]:
    """Present drive letters. Uses the Windows logical-drive bitmask when
    available (fast, lists only mounted drives — no hang on unmapped letters);
    falls back to probing A:..Z: so it degrades gracefully off-Windows / in CI."""
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()   # type: ignore[attr-defined]
        return [chr(ord("A") + i) for i in range(26) if bitmask & (1 << i)]
    except (AttributeError, OSError, ValueError):
        return [d for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]


def _libraries_in(root: str) -> list[dict]:
    """Immediate sub-folders of ``root`` that look like a media library
    ("TV Shows", "TV Shows Matteo", "Movies", "Anime", ...). These — not bare
    drive roots — are the scan targets, because cross-drive matching keys on the
    library folder name (P:\\TV Shows must match R:\\TV Shows)."""
    libs = []
    try:
        with os.scandir(root) as it:
            for e in it:
                try:
                    if e.is_dir() and scanner._LIBRARY_FOLDER_RE.match(e.name):
                        libs.append({"name": e.name, "path": e.path})
                except OSError:
                    continue
    except OSError:
        return []
    libs.sort(key=lambda x: x["name"].lower())
    return libs


def list_drive_libraries() -> list[dict]:
    """Enumerate local drives and the media-library folders on each, for the
    UI's drive picker. Drives with no recognized library folder are still
    returned (with an empty ``libraries`` list) so the caller can decide whether
    to show them."""
    out = []
    for letter in _drive_letters():
        root = f"{letter}:\\"
        if not os.path.isdir(root):
            continue
        out.append({"drive": letter, "root": root, "libraries": _libraries_in(root)})
    return out


def episode_key(stem: str, parent: str, show: str) -> tuple[int, int] | None:
    """Return (season, episode) for a file, canonical or parseable, else None."""
    m = _CANON.search(stem)
    if m:
        return int(m.group(1)), int(m.group(2))
    r = scanner.parse(stem, parent, None, show)
    if r["kind"] is scanner.Kind.EPISODE:
        return r["season"], r["episode"]
    return None


@dataclass
class Copy:
    path: str
    drive: str
    filename: str
    size: int
    resolution: int          # 0 = unknown

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DupGroup:
    kind: str                # "episode" | "movie"
    library: str             # "TV Shows" / "TV Shows Matteo" / "Movies"
    show: str                # series name, or movie title for movies
    season: int | None       # None for movies
    episode: int | None      # None for movies
    year: int | None         # set for movies (None if unknown); None for episodes
    copies: list[Copy]
    suggested_keep: int      # index into copies (highest res, tie-break size)

    @property
    def drives(self) -> set[str]:
        return {c.drive for c in self.copies}

    @property
    def reclaimable_bytes(self) -> int:
        """Bytes freed if only the suggested keeper is kept."""
        return sum(c.size for i, c in enumerate(self.copies) if i != self.suggested_keep)

    @property
    def label(self) -> str:
        """Short identifier shown on the group: S##E## for TV, (year) for movies."""
        if self.kind == "movie":
            return f"({self.year})" if self.year else "movie"
        return f"S{self.season:02d}E{self.episode:02d}"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "library": self.library, "show": self.show,
            "season": self.season, "episode": self.episode, "year": self.year,
            "label": self.label,
            "se": self.label,                 # back-compat alias for the UI
            "copies": [c.to_dict() for c in self.copies],
            "suggested_keep": self.suggested_keep,
            "drives": sorted(self.drives),
            "reclaimable_bytes": self.reclaimable_bytes,
        }


def _suggest_keep(copies: list[Copy]) -> int:
    """Index of the copy to pre-select.

    Normally: highest resolution, tie-break largest file. BUT if any copy's
    resolution is unknown (no name token and ffprobe couldn't read it), the
    resolution signal is unreliable for the group, so rank by SIZE first
    (tie-break resolution) — keep the largest, the safer bet against discarding
    the better encode (e.g. a 3 GB file whose name omits "1080p" beating a 1 GB
    file that happens to spell it out)."""
    unknown = any(c.resolution == 0 for c in copies)

    def score(c: Copy) -> tuple[int, int]:
        return (c.size, c.resolution) if unknown else (c.resolution, c.size)

    best = 0
    for i, c in enumerate(copies):
        if score(c) > score(copies[best]):
            best = i
    return best


def _walk_scandir(root: str):
    """Like os.walk but yields (dirpath, subdirs, [(filename, size)]) using
    scandir so each file's size comes from the cached directory entry (one stat
    on Windows, not two) — much faster over external/network drives."""
    try:
        with os.scandir(root) as it:
            subdirs, files = [], []
            for e in it:
                try:
                    if e.is_dir():
                        if not scanner._EXCLUDE_DIR_RE.match(e.name):
                            subdirs.append(e.path)
                    elif e.is_file():
                        files.append((e.name, e.stat().st_size))
                except OSError:
                    continue
    except OSError:
        return
    yield root, subdirs, files
    for sub in subdirs:
        yield from _walk_scandir(sub)


def find_duplicates(roots: list[str], *, cross_drive_only: bool = True) -> list[DupGroup]:
    """Group video files by (library, show, S##E##) across all ``roots``.

    Each root is a library path like ``P:/TV Shows``. A group becomes a duplicate
    when it has >1 copy (``cross_drive_only`` restricts to copies on >=2 drives,
    the accidental-duplication case we care about).
    """
    groups: dict[tuple, list[Copy]] = {}
    # key -> (kind, library, show/title, season, episode, year) for display
    display: dict[tuple, tuple] = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        library = os.path.basename(root.rstrip("/\\"))
        drive = _drive_of(root)
        for dirpath, _dirs, entries in _walk_scandir(root):
            parent = os.path.basename(dirpath)
            is_movie = scanner.is_movie_path(dirpath)
            show = "" if is_movie else scanner._show_name(root, dirpath)
            for fname, size in entries:
                stem, ext = os.path.splitext(fname)
                if ext.lower() not in scanner.VIDEO_EXTS:
                    continue
                full = os.path.join(dirpath, fname)
                if is_movie:
                    # A trailer/featurette/etc. shares the movie's title+year, so
                    # without this guard it looks like a duplicate of the film.
                    if scanner._EXTRA_RE.search(stem):
                        continue
                    title, year, part = scanner.movie_identity(stem, parent)
                    if not title:
                        continue
                    key = ("movie", library.lower(), title.lower(), year, part)
                    display.setdefault(key, ("movie", library, title, None, None, year))
                else:
                    se = episode_key(stem, parent, show)
                    if se is None:
                        continue
                    key = ("episode", library.lower(), show.lower(), se[0], se[1])
                    display.setdefault(key, ("episode", library, show, se[0], se[1], None))
                groups.setdefault(key, []).append(
                    Copy(full, drive, fname, size, _resolution(fname)))

    out: list[DupGroup] = []
    for key, copies in groups.items():
        if len(copies) < 2:
            continue
        if cross_drive_only and len({c.drive for c in copies}) < 2:
            continue
        copies.sort(key=lambda c: (c.drive, c.filename))   # stable display order
        _fill_unknown_resolutions(copies)   # ffprobe fallback, dup-group members only
        kind, lib_disp, show_disp, season, episode, year = display[key]
        out.append(DupGroup(
            kind=kind, library=lib_disp, show=show_disp,
            season=season, episode=episode, year=year,
            copies=copies, suggested_keep=_suggest_keep(copies)))
    # episodes first (sorted by show/season/ep), then movies (by title/year)
    out.sort(key=lambda g: (g.kind, g.show.lower(), g.season or 0, g.episode or 0, g.year or 0))
    return out


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def _summary(roots: list[str]) -> int:
    groups = find_duplicates(roots)
    total_reclaim = sum(g.reclaimable_bytes for g in groups)
    print(f"Roots: {roots}")
    print(f"Cross-drive duplicate episodes: {len(groups)}")
    print(f"Reclaimable if keeping one copy each: {_human(total_reclaim)}")
    print("=" * 70)
    for g in groups[:30]:
        print(f"\n{g.show}  {g.label}  (drives {','.join(sorted(g.drives))})")
        for i, c in enumerate(g.copies):
            mark = "  <-- suggest KEEP" if i == g.suggested_keep else ""
            res = f"{c.resolution}p" if c.resolution else "?res"
            print(f"   [{c.drive}] {res:6} {_human(c.size):>8}  {c.filename}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_summary(sys.argv[1:] or ["."]))
