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


def _drive_of(path: str) -> str:
    d = os.path.splitdrive(path)[0]
    return d.rstrip(":").upper() or "?"


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
    library: str             # "TV Shows" / "TV Shows Matteo"
    show: str
    season: int
    episode: int
    copies: list[Copy]
    suggested_keep: int      # index into copies (highest res, tie-break size)

    @property
    def drives(self) -> set[str]:
        return {c.drive for c in self.copies}

    @property
    def reclaimable_bytes(self) -> int:
        """Bytes freed if only the suggested keeper is kept."""
        return sum(c.size for i, c in enumerate(self.copies) if i != self.suggested_keep)

    def to_dict(self) -> dict:
        return {
            "library": self.library, "show": self.show,
            "season": self.season, "episode": self.episode,
            "se": f"S{self.season:02d}E{self.episode:02d}",
            "copies": [c.to_dict() for c in self.copies],
            "suggested_keep": self.suggested_keep,
            "drives": sorted(self.drives),
            "reclaimable_bytes": self.reclaimable_bytes,
        }


def _suggest_keep(copies: list[Copy]) -> int:
    """Index of the copy to pre-select: highest resolution, tie-break largest."""
    best = 0
    for i, c in enumerate(copies):
        b = copies[best]
        if (c.resolution, c.size) > (b.resolution, b.size):
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
    display: dict[tuple, tuple[str, str]] = {}   # key -> (library, show) for output
    for root in roots:
        if not os.path.isdir(root):
            continue
        library = os.path.basename(root.rstrip("/\\"))
        drive = _drive_of(root)
        for dirpath, _dirs, entries in _walk_scandir(root):
            parent = os.path.basename(dirpath)
            show = scanner._show_name(root, dirpath)   # robust to scan depth
            for fname, size in entries:
                stem, ext = os.path.splitext(fname)
                if ext.lower() not in scanner.VIDEO_EXTS:
                    continue
                se = episode_key(stem, parent, show)
                if se is None:
                    continue
                full = os.path.join(dirpath, fname)
                key = (library.lower(), show.lower(), se[0], se[1])
                groups.setdefault(key, []).append(
                    Copy(full, drive, fname, size, _resolution(fname)))
                display.setdefault(key, (library, show))

    out: list[DupGroup] = []
    for key, copies in groups.items():
        if len(copies) < 2:
            continue
        if cross_drive_only and len({c.drive for c in copies}) < 2:
            continue
        copies.sort(key=lambda c: (c.drive, c.filename))   # stable display order
        lib_disp, show_disp = display[key]
        out.append(DupGroup(
            library=lib_disp, show=show_disp, season=key[2], episode=key[3],
            copies=copies, suggested_keep=_suggest_keep(copies)))
    out.sort(key=lambda g: (g.show.lower(), g.season, g.episode))
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
        print(f"\n{g.show}  S{g.season:02d}E{g.episode:02d}  (drives {','.join(sorted(g.drives))})")
        for i, c in enumerate(g.copies):
            mark = "  <-- suggest KEEP" if i == g.suggested_keep else ""
            res = f"{c.resolution}p" if c.resolution else "?res"
            print(f"   [{c.drive}] {res:6} {_human(c.size):>8}  {c.filename}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_summary(sys.argv[1:] or ["."]))
