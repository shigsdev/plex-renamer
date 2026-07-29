#!/usr/bin/env python3
"""
music.py — Find duplicate songs in a music / iTunes library by TAGS.

Filenames lie (``06 Beautiful.mp3`` vs ``06 Beautiful 1.mp3``, ``.m4a`` vs
``.mp3``, case differences), so duplicates are matched on the audio TAGS read
with mutagen: ``(artist, title)`` normalized, then confirmed by DURATION
proximity. Duration is the safety net — it keeps "Part 1"/"Part 2", live vs
studio, and radio vs extended edits correctly separate even when the titles
normalize the same.

Matching is WHOLE-LIBRARY: the same recording is grouped even when it lives in
different album/compilation folders (that's the point — one song copied onto
several albums is still a duplicate). The keeper suggestion prefers the AAC/m4a
copy (iTunes-native), then higher bitrate, then larger file.

Read-only. The web UI consumes ``build_groups``; removal reuses ``quarantine``
(same reversible move + undo as the video deduper). Pure aside from tag reads.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass

import mutagen

import scanner  # reuse the NAS recycle/system-folder exclusion

AUDIO_EXTS = {
    ".mp3", ".m4a", ".m4p", ".m4b", ".aac", ".alac",
    ".flac", ".wav", ".aif", ".aiff", ".wma", ".ogg",
}

# AAC/Apple family — the iTunes-native container, preferred as the keeper.
_AAC_EXTS = {".m4a", ".m4p", ".aac", ".alac"}

# Copies whose durations are within this many seconds are the same recording.
_DUR_TOLERANCE = 3.0

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    """Conservative match key: lowercase, non-alphanumeric -> space, collapse.

    Deliberately does NOT strip parentheticals like "(Radio Edit)" / "(Live)" —
    those distinguish versions, and under-matching (missing a dup) is safe while
    over-matching (deleting the wrong song) is not."""
    return _NORM_RE.sub(" ", (s or "").lower()).strip()


def _first_tag(mf, *keys) -> str:
    """First non-empty value among ``keys`` from a mutagen easy-tags mapping."""
    for key in keys:
        try:
            v = mf.get(key)
        except Exception:
            v = None
        if isinstance(v, list):
            v = v[0] if v else None
        if v:
            return str(v).strip()
    return ""


@dataclass
class SongCopy:
    path: str
    filename: str
    folder: str          # album folder (relative to scan root), for display
    fmt: str             # "mp3", "m4a", ...
    bitrate: int         # kbps, 0 = unknown
    size: int
    duration: float      # seconds

    def to_dict(self) -> dict:
        d = asdict(self)
        mm, ss = divmod(int(self.duration), 60)
        d["length"] = f"{mm}:{ss:02d}"
        return d


@dataclass
class Record:
    artist_key: str
    title_key: str
    artist: str          # display
    title: str           # display
    copy: SongCopy


@dataclass
class SongGroup:
    artist: str
    title: str
    duration: float
    copies: list[SongCopy]
    suggested_keep: int

    @property
    def reclaimable_bytes(self) -> int:
        return sum(c.size for i, c in enumerate(self.copies) if i != self.suggested_keep)

    def to_dict(self) -> dict:
        mm, ss = divmod(int(self.duration), 60)
        return {
            "artist": self.artist, "title": self.title,
            "duration": self.duration, "length": f"{mm}:{ss:02d}",
            "copies": [c.to_dict() for c in self.copies],
            "suggested_keep": self.suggested_keep,
            "reclaimable_bytes": self.reclaimable_bytes,
        }


def _folder(root: str, path: str) -> str:
    rel = os.path.relpath(os.path.dirname(path), root)
    return "" if rel == "." else rel


def iter_audio_files(root: str) -> list[str]:
    """All audio files under ``root``, skipping NAS recycle/snapshot/system dirs."""
    out: list[str] = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if not scanner._EXCLUDE_DIR_RE.match(d)]
        for f in files:
            if os.path.splitext(f)[1].lower() in AUDIO_EXTS:
                out.append(os.path.join(dirpath, f))
    return out


def read_record(path: str, root: str) -> Record | None:
    """Read one file's tags into a Record. Falls back to the folder name for a
    missing artist and the filename stem for a missing title so tagless files
    can still match a sibling. Returns None if the file can't be stat'd."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    ext = os.path.splitext(path)[1].lower()
    fname = os.path.basename(path)
    folder = _folder(root, path)
    try:
        mf = mutagen.File(path, easy=True)
    except Exception:
        mf = None

    if mf is None:
        artist, title, dur, br = "", os.path.splitext(fname)[0], 0.0, 0
    else:
        artist = _first_tag(mf, "artist", "albumartist", "album_artist")
        title = _first_tag(mf, "title")
        dur = float(getattr(mf.info, "length", 0) or 0)
        br = int(getattr(mf.info, "bitrate", 0) or 0) // 1000

    if not artist:
        artist = folder.split(os.sep)[0] if folder else ""
    if not title:
        title = os.path.splitext(fname)[0]

    copy = SongCopy(path, fname, folder, ext.lstrip("."), br, size, dur)
    return Record(_norm(artist), _norm(title), artist, title, copy)


def _cluster_by_duration(copies: list[SongCopy]) -> list[list[SongCopy]]:
    """Split copies (already sharing an artist+title key) into clusters whose
    durations are within tolerance. Copies with an unknown (0) duration can't be
    safely matched, so each becomes its own singleton cluster (dropped later)."""
    known = sorted((c for c in copies if c.duration > 0), key=lambda c: c.duration)
    clusters: list[list[SongCopy]] = []
    cur: list[SongCopy] = []
    for c in known:
        if cur and c.duration - cur[-1].duration > _DUR_TOLERANCE:
            clusters.append(cur)
            cur = []
        cur.append(c)
    if cur:
        clusters.append(cur)
    return clusters


def _suggest_keep(copies: list[SongCopy]) -> int:
    """Prefer the AAC/m4a copy (iTunes-native), then higher bitrate, then larger
    file — per the user's keeper choice."""
    def score(c: SongCopy) -> tuple[int, int, int]:
        return (1 if f".{c.fmt.lower()}" in _AAC_EXTS else 0, c.bitrate, c.size)

    best = 0
    for i, c in enumerate(copies):
        if score(c) > score(copies[best]):
            best = i
    return best


def build_groups(records: list[Record]) -> list[SongGroup]:
    """Group Records into duplicate SongGroups (>=2 copies of one recording)."""
    by_key: dict[tuple[str, str], list[Record]] = {}
    for r in records:
        if not r.title_key:          # nothing to match on — skip for safety
            continue
        by_key.setdefault((r.artist_key, r.title_key), []).append(r)

    out: list[SongGroup] = []
    for recs in by_key.values():
        by_path = {r.copy.path: r for r in recs}          # display lookup
        for cluster in _cluster_by_duration([r.copy for r in recs]):
            if len(cluster) < 2:
                continue
            cluster.sort(key=lambda c: (c.folder, c.filename))
            head = by_path[cluster[0].path]
            out.append(SongGroup(
                artist=head.artist, title=head.title,
                duration=max(c.duration for c in cluster),
                copies=cluster, suggested_keep=_suggest_keep(cluster)))
    out.sort(key=lambda g: (g.artist.lower(), g.title.lower()))
    return out


def find_music_duplicates(root: str) -> list[SongGroup]:
    """Convenience (non-streaming): read every audio file's tags and group."""
    records = [r for p in iter_audio_files(root) if (r := read_record(p, root))]
    return build_groups(records)


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


if __name__ == "__main__":
    import sys

    groups = find_music_duplicates(sys.argv[1] if len(sys.argv) > 1 else ".")
    reclaim = sum(g.reclaimable_bytes for g in groups)
    print(f"Duplicate songs: {len(groups)}   reclaimable: {_human(reclaim)}")
    for g in groups[:40]:
        print(f"\n{g.artist} — {g.title}  [{int(g.duration)//60}:{int(g.duration) % 60:02d}]")
        for i, c in enumerate(g.copies):
            mark = "  <-- keep" if i == g.suggested_keep else ""
            print(f"   {c.fmt:4} {c.bitrate:4}k {_human(c.size):>8}  {c.folder}\\{c.filename}{mark}")
