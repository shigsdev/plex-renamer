#!/usr/bin/env python3
"""
scanner.py — Classify TV video files and propose Plex-compatible names.

The job: find video files whose name lacks a Plex season/episode token
(``S01E05``) and propose a corrected name. Real-world torrent/DVD naming is
messy, so parsing is done by a list of ordered handlers (first match wins),
each tuned against a real ~3,800-file sample. See ``parse`` for the order.

Two output shapes a file can take:
  * episode token   -> "<title> - S01E05<rest>.ext"   (regular episodes)
  * date token      -> "<show> - 2016-09-09 - <title>.ext"  (daily shows; Plex
                        matches these by air date when no episode number exists)

Pure standard library. Importable (web UI uses ``scan``) and runnable
(``python scanner.py <dir>`` prints a text summary for quick validation).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import asdict, dataclass
from enum import Enum

VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".flv", ".webm",
}

# The canonical output token. Used when building names and reading folder tokens.
CANONICAL_SE = re.compile(r"[Ss]\d{1,2}[Ee]\d{1,3}")

# Plex ALSO matches S## and E## with a separator between them ("S02.E04",
# "S02 E04"), so a file already in any of those forms is left alone — no churn.
# (E must be followed directly by a digit, so "S01 EP01" is still normalized.)
PLEX_OK_SE = re.compile(r"[Ss]\d{1,2}[ ._-]*[Ee]\d{1,3}")

# 4-digit years: junk for episode-CODE matching (so "1994" never becomes S19E94),
# but KEPT in the displayed title (Plex uses the year to disambiguate, e.g.
# "Iron Man 1994"). So it lives only in _JUNK_RE, not _DISPLAY_JUNK_RE.
_YEAR = r"\b(?:19|20)\d{2}\b"

# Junk tokens that contain digits and would fool a "bare 3-4 digit episode code"
# search. Stripped (for matching only) before that handler runs. Order/scope here
# is the load-bearing defense against turning "x264" into S02E64 or "1080p" into
# S10E80. Case-insensitive, whole-token.
_JUNK_PATTERNS = [
    r"\b\d{3,4}\s*[xX]\s*\d{3,4}\b",      # WxH resolution: 1920x1080, 1280x720
    r"\b\d{3,4}p\b",                      # 480p 720p 1080p 2160p
    r"\b(?:x|h)[ ._-]?26[45](?:hi10|10bit)?\b",  # x264, x265, h264, x264hi10
    r"\bhevc\b", r"\bavc\b", r"\bxvid\b", r"\bdivx\b",
    r"\b10bit\b", r"\b8bit\b", r"\bhi10p?\b",
    r"\bdd[p]?[ ._-]?\d(?:[ ._-]?\d)?\b",  # dd5.1 ddp5.1 dd2.0
    r"\bdts(?:[ ._-]?hd)?\b", r"\btruehd\b", r"\batmos\b", r"\baac\d?\b",
    r"\bac3\b", r"\bflac\b", r"\bmp3\b",
    r"\b[257][ ._-][01]\b",                # 5 1 / 2 0 / 7 1 (channel layouts)
    r"\bblu[ ._-]?ray\b", r"\bbdrip\b", r"\bbrrip\b", r"\bweb[ ._-]?dl\b",
    r"\bwebrip\b", r"\bweb\b", r"\bhdtv\b", r"\bdvdrip\b", r"\bdvd\b",
    r"\bremux\b", r"\brepack\b", r"\bproper\b", r"\binternal\b",
    r"\bntsc\b", r"\bpal\b",                # DVD region standards
    r"\bAMZN\b", r"\bNF\b", r"\bDSNP\b", r"\bHMAX\b",
]
# For display (title building) — same junk minus the year, so years stay in titles.
_DISPLAY_JUNK_RE = re.compile("|".join(_JUNK_PATTERNS), re.IGNORECASE)
# For episode-code matching — junk PLUS years.
_JUNK_PATTERNS = _JUNK_PATTERNS + [_YEAR]
_JUNK_RE = re.compile("|".join(_JUNK_PATTERNS), re.IGNORECASE)

# Anything matching these is an extra/special/featurette — NEVER assign an
# episode number (would collide with the real S01E01). Left untouched.
_EXTRA_RE = re.compile(
    r"\b(?:extra|extras|featurette|deleted[ ._-]?scene|behind[ ._-]?the[ ._-]?scenes|"
    r"bonus|gag[ ._-]?reel|blooper|interview|reunion|making[ ._-]?of|trailer|"
    r"sneak[ ._-]?peek|promo|recap|webisode|sizzle|teaser|outtake)\b",
    re.IGNORECASE,
)

# Date in the filename: YYYY.MM.DD / YYYY-MM-DD / YYYY_MM_DD (daily shows).
_DATE_RE = re.compile(r"(?<!\d)(19|20)(\d{2})[ ._-](\d{2})[ ._-](\d{2})(?!\d)")


class Kind(Enum):
    EPISODE = "episode"      # propose S##E##
    DATE = "date"            # propose YYYY-MM-DD (daily show)
    SKIP_OK = "already_ok"   # already has S##E##
    SKIP_EXTRA = "extra"     # extra/featurette — leave alone
    NONE = "none"            # could not determine


class Confidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    GUESS = "guess"
    NONE = "none"


@dataclass
class Proposal:
    path: str                       # absolute path to original file
    show: str                       # series name (top folder under scan root)
    rel_dir: str                    # directory relative to scan root
    old_name: str
    new_name: str | None = None
    kind: str = Kind.NONE.value
    confidence: str = Confidence.NONE.value
    season: int | None = None
    episode: int | None = None
    airdate: str | None = None      # YYYY-MM-DD for daily shows
    absolute: int | None = None     # absolute episode number to resolve via API
    note: str = ""
    needs_lookup: bool = False      # date/absolute; could be upgraded via metadata API

    @property
    def changed(self) -> bool:
        return bool(self.new_name) and self.new_name != self.old_name

    def to_dict(self) -> dict:
        d = asdict(self)
        d["changed"] = self.changed
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def season_from_folder(name: str) -> int | None:
    """Season number from a folder like 'Season 2', 'S02', 'Series 3'."""
    m = re.search(r"(?:season|series)[ _.-]*(\d{1,2})\b", name, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"\b[Ss](\d{1,2})(?![Ee\d])", name)  # S02 but not S02E..
    if m:
        return int(m.group(1))
    return None


def _clean_for_code(stem: str) -> str:
    """Strip junk tokens so a bare 3-4 digit episode code stands out."""
    # Underscores are word chars, so "dvdrip_xvid" reads as one token and \b
    # junk patterns miss it — normalize to spaces first.
    return _JUNK_RE.sub(" ", stem.replace("_", " "))


def _tidy_title(text: str) -> str:
    """Collapse separators/whitespace in leftover title text."""
    text = re.sub(r"[([{]\s*[)\]}]", " ", text)   # empty () [] {} left by stripping
    text = re.sub(r"[ _.]+", " ", text)
    text = re.sub(r"\s*-\s*-\s*", " - ", text)
    text = re.sub(r"\s+([)\]}])", r"\1", text)      # " )" -> ")"
    return text.strip(" _.-")


def _strip_release_junk(text: str) -> str:
    """Remove quality/codec/source junk, [bracket] tags, and a trailing
    -RELEASEGROUP, leaving a human-readable title. Years are KEPT (Plex uses them
    to disambiguate). Conservative on trailing -token: only all-caps/lowercase."""
    text = text.replace("_", " ")                           # so "dvdrip_xvid" splits
    text = _DISPLAY_JUNK_RE.sub(" ", text)
    text = re.sub(r"\bvol(?:ume)?[ ._-]?\d{1,2}\b", " ", text, flags=re.IGNORECASE)  # DVD "vol2"
    text = re.sub(r"\[[^\]]*\]", " ", text)                 # [PRiME], [ExYu-Subs]
    # Trailing release group: only all-caps or all-lowercase tokens (BTW, ROFL,
    # vfua, lol) — NOT Titlecase words, so a title ending in "Pilot" survives.
    text = re.sub(r"-\s*(?:[A-Z]{2,12}|[a-z]{2,12})\s*$", " ", text)
    text = re.sub(r"(?<![A-Za-z0-9])\d(?![A-Za-z0-9])", " ", text)  # stray single digit (AAC2.0 -> 0)
    return text


# ---------------------------------------------------------------------------
# Parsing — returns a partly-filled Proposal (kind/conf/season/episode/airdate/note)
# ---------------------------------------------------------------------------

def parse(stem: str, parent: str, forced_season: int | None, show: str = "") -> dict:
    """Return a dict of parse results for a filename stem (no I/O)."""
    folder_season = forced_season if forced_season is not None else season_from_folder(parent)

    # 0. Extras / specials — never number. BUT only if the extra keyword isn't
    # part of the series title itself ("The Making of the Mob" is a real show,
    # not a making-of featurette), so we don't skip genuine episodes.
    em = _EXTRA_RE.search(stem)
    if em and not _EXTRA_RE.search(show):
        return dict(kind=Kind.SKIP_EXTRA, confidence=Confidence.NONE,
                    note=f"looks like an extra/special ({em.group(0)!r}) — left alone")

    # 1. Episode token lives in the PARENT FOLDER (single-file subfolders).
    fm = CANONICAL_SE.search(parent)
    if fm:
        s, e = re.findall(r"\d{1,3}", fm.group(0))
        return dict(kind=Kind.EPISODE, confidence=Confidence.HIGH,
                    season=int(s), episode=int(e),
                    note=f"S/E from folder {fm.group(0)!r}")

    # 2. Daily show: a real air-date in the name, no episode number.
    dm = _DATE_RE.search(stem)
    if dm:
        yyyy = dm.group(1) + dm.group(2)
        airdate = f"{yyyy}-{dm.group(3)}-{dm.group(4)}"
        return dict(kind=Kind.DATE, confidence=Confidence.HIGH, airdate=airdate,
                    needs_lookup=True, note=f"air date {airdate} (daily show)")

    # 3. "1x05" / "01x05"
    m = re.search(r"(?<!\d)(\d{1,2})\s*[xX]\s*(\d{1,3})(?!\d)", stem)
    if m:
        return dict(kind=Kind.EPISODE, confidence=Confidence.HIGH,
                    season=int(m.group(1)), episode=int(m.group(2)),
                    note=f"matched {m.group(0)!r}")

    # 4. "S01.E01" / "S01 E02" / "S01 EP01" (separator between S and E).
    m = re.search(r"[Ss](\d{1,2})[ ._-]+E[Pp]?[ ._-]*(\d{1,3})(?!\d)", stem)
    if m:
        return dict(kind=Kind.EPISODE, confidence=Confidence.HIGH,
                    season=int(m.group(1)), episode=int(m.group(2)),
                    note=f"matched {m.group(0)!r}")

    # 5. "Season 1 Episode 5" / "Season1.Episode.5"
    m = re.search(r"season[ _.-]*(\d{1,2}).*?(?:episode|ep)[ _.-]*(\d{1,3})", stem, re.IGNORECASE)
    if m:
        return dict(kind=Kind.EPISODE, confidence=Confidence.HIGH,
                    season=int(m.group(1)), episode=int(m.group(2)),
                    note=f"matched {m.group(0)!r}")

    # 6. "Episode 5" / "EP05" / "Ep 5" / bare "e001" (e directly before digits).
    # Season from folder if known, else default to 1 (miniseries/docs like
    # "A.Perfect.Planet.EP01" or "he-man.e001" have no season folder) — MEDIUM
    # when the season is assumed. The bare-"e" form requires a word boundary
    # before it (so "he-man" / "Se7en" don't match) and digits directly after.
    m = re.search(r"\bepisode[ ._-]*(\d{1,3})(?!\d)", stem, re.IGNORECASE) \
        or re.search(r"\bep[ ._-]*(\d{1,3})(?!\d)", stem, re.IGNORECASE) \
        or re.search(r"\be(\d{1,3})(?!\d)", stem, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        if folder_season is not None:
            return dict(kind=Kind.EPISODE, confidence=Confidence.HIGH,
                        season=folder_season, episode=n,
                        note=f"ep {n} from name, season {folder_season} from folder")
        # No season folder: the number may be ABSOLUTE (e.g. DVD "episode 61").
        # Provisionally S01E{n}, but flag it for a metadata lookup that can remap
        # the absolute number to the real season/episode.
        return dict(kind=Kind.EPISODE, confidence=Confidence.MEDIUM,
                    season=1, episode=n, absolute=n, needs_lookup=True,
                    note=f"ep {n} from name, season 1 assumed — absolute # (look up to confirm)")

    # 7. "Part 3" with season from folder (miniseries default season 1).
    m = re.search(r"\bpart[ ._-]*(\d{1,3})\b", stem, re.IGNORECASE)
    if m:
        s = folder_season if folder_season is not None else 1
        return dict(kind=Kind.EPISODE, confidence=Confidence.MEDIUM,
                    season=s, episode=int(m.group(1)),
                    note=f"Part {m.group(1)} as episode, season {s}")

    # 8. Leading episode number: "01 Title", "02 - Title" (needs a season folder).
    # Only 1-2 digits + separator + non-digit, so 3-digit codes ("104 ...") fall
    # through to the bare-code handler and years ("1922 ...") don't match.
    m = re.match(r"\s*(\d{1,2})[ ._-]+\D", stem)
    if m and folder_season is not None:
        return dict(kind=Kind.EPISODE, confidence=Confidence.MEDIUM,
                    season=folder_season, episode=int(m.group(1)),
                    note=f"leading number {m.group(1)} as episode, season {folder_season} from folder")

    # 9. Bare 3-4 digit code (after stripping quality/codec/year junk).
    cleaned = _clean_for_code(stem)
    codes = re.findall(r"(?<!\d)(\d{3,4})(?!\d)", cleaned)
    for code in codes:
        if len(code) == 4:
            s, e = int(code[:2]), int(code[2:])
        else:
            s, e = int(code[0]), int(code[1:])
        if 1 <= s <= 40 and 1 <= e <= 300:
            return dict(kind=Kind.EPISODE, confidence=Confidence.MEDIUM,
                        season=s, episode=e, note=f"parsed code {code!r}")

    # 9. "e01" bare-E prefix, or a stem that is essentially just a number.
    # Deliberately strict: a plain lone 1-2 digit number anywhere is too noisy
    # (it grabs the "26" out of "S26", or stray "5 1" channel-layout digits),
    # so we ONLY fire on an explicit e-prefix or a number-only stem.
    cleaned = _clean_for_code(stem)
    m = re.search(r"\be[ ._-]?(\d{1,2})(?!\d)", cleaned, re.IGNORECASE)
    if not m:
        m = re.fullmatch(r"\s*(\d{1,2})\s*", cleaned)
    if m:
        s = folder_season if folder_season is not None else 1
        return dict(kind=Kind.EPISODE, confidence=Confidence.GUESS,
                    season=s, episode=int(m.group(1)),
                    note=f"episode {m.group(1)} (e-prefix/number-only), season {s}")

    return dict(kind=Kind.NONE, confidence=Confidence.NONE,
                note="no season/episode/date pattern found")


# ---------------------------------------------------------------------------
# New-name construction
# ---------------------------------------------------------------------------

def build_episode_name(stem: str, ext: str, season: int, episode: int) -> str:
    """Insert a canonical S##E## while preserving the title text."""
    token = f"S{season:02d}E{episode:02d}"
    cleaned = stem
    # Drop an air-date (daily-show file resolved to an episode) — it's not title text.
    cleaned = _DATE_RE.sub(" ", cleaned)
    # Remove the source token(s) we interpreted, so we don't leave a duplicate.
    cleaned = re.sub(r"(?<!\d)\d{1,2}\s*[xX]\s*\d{1,3}(?!\d)", " ", cleaned)
    cleaned = re.sub(r"[Ss]\d{1,2}[ ._-]+E[Pp]?[ ._-]*\d{1,3}", " ", cleaned)
    cleaned = re.sub(r"season[ _.-]*\d{1,2}.*?(?:episode|ep)[ _.-]*\d{1,3}", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?:episode|ep)[ _.-]*\d{1,3}(?!\d)", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\be\d{1,3}(?!\d)", " ", cleaned, flags=re.IGNORECASE)  # bare "e001"
    cleaned = re.sub(r"\bpart[ ._-]*\d{1,3}\b", " ", cleaned, flags=re.IGNORECASE)
    # Drop a leading episode number we interpreted (e.g. "01 Title" -> "Title").
    cleaned = re.sub(rf"^\s*0*{episode}[ ._-]+", " ", cleaned)
    # Drop the bare 3-4 digit code we interpreted (e.g. "210", "0406").
    for code in (f"{season}{episode:02d}", f"{season:02d}{episode:02d}"):
        cleaned = re.sub(rf"(?<!\d){code}(?!\d)", " ", cleaned)
    cleaned = _strip_release_junk(cleaned)
    cleaned = _tidy_title(cleaned)
    return f"{cleaned} - {token}{ext}" if cleaned else f"{token}{ext}"


def build_date_name(stem: str, ext: str, show: str, airdate: str) -> str:
    """Build '<show> - YYYY-MM-DD - <title>' for a daily-show file."""
    # Drop the raw date and junk, keep any human title text that remains.
    title = _DATE_RE.sub(" ", stem)
    title = _strip_release_junk(title)
    # Drop a leading repeat of the show name (e.g. "Dateline NBC ...").
    title = re.sub(re.escape(show), " ", title, flags=re.IGNORECASE)
    if "dateline" in show.lower():
        title = re.sub(r"\bdateline\b|\bnbc\b", " ", title, flags=re.IGNORECASE)
    title = _tidy_title(title)
    if title:
        return f"{show} - {airdate} - {title}{ext}"
    return f"{show} - {airdate}{ext}"


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

_SEASON_FOLDER_RE = re.compile(r"^(?:season|series)\b|^s\d{1,2}$|^specials$", re.IGNORECASE)
# Library/container folders that are NOT a show — the show is the folder INSIDE
# them. Prevents lumping every show under "TV Shows" when scanning from above the
# library (e.g. scanning "P:\" instead of "P:\TV Shows").
# Specific enough not to match real show titles ("Show Me a Hero", "Movie 43").
_LIBRARY_FOLDER_RE = re.compile(r"^(?:tv[ ._-]?shows|movies|anime)\b", re.IGNORECASE)


def _show_name(root: str, dirpath: str) -> str:
    """Series name, robust to how deep you scan.

    Priority: (1) the folder directly above a "Season N" / "S0N" / "Specials"
    folder; (2) the folder directly inside a library container ("TV Shows",
    "Movies", …); (3) the first folder under the scan root. Uses the file's
    ABSOLUTE path so it works whether you scan the library root, a single show,
    a season folder, or even the whole drive."""
    parts = os.path.normpath(os.path.abspath(dirpath)).split(os.sep)
    for i, part in enumerate(parts):
        if _SEASON_FOLDER_RE.match(part) and i > 0:
            return parts[i - 1]
    for i, part in enumerate(parts[:-1]):
        if _LIBRARY_FOLDER_RE.match(part):
            return parts[i + 1]
    rel = os.path.relpath(dirpath, root)
    if rel == ".":
        return os.path.basename(os.path.abspath(root))
    return rel.split(os.sep)[0]


def scan(root: str, *, forced_season: int | None = None,
         include_ok: bool = False) -> list[Proposal]:
    """Walk ``root`` and return a Proposal per video file that needs attention.

    By default already-correct files are omitted (``include_ok=True`` to keep
    them, e.g. for stats).
    """
    out: list[Proposal] = []
    for dirpath, _dirs, files in os.walk(root):
        parent = os.path.basename(dirpath)
        show = _show_name(root, dirpath)
        rel_dir = os.path.relpath(dirpath, root)
        for fname in sorted(files):
            stem, ext = os.path.splitext(fname)
            if ext.lower() not in VIDEO_EXTS:
                continue
            full = os.path.join(dirpath, fname)

            if PLEX_OK_SE.search(stem):
                if include_ok:
                    out.append(Proposal(full, show, rel_dir, fname,
                                        kind=Kind.SKIP_OK.value,
                                        confidence=Confidence.NONE.value,
                                        note="already Plex-readable (S##E## / S##.E## / S## E##)"))
                continue

            r = parse(stem, parent, forced_season, show)
            p = Proposal(full, show, rel_dir, fname,
                         kind=r["kind"].value, confidence=r["confidence"].value,
                         season=r.get("season"), episode=r.get("episode"),
                         airdate=r.get("airdate"), absolute=r.get("absolute"),
                         note=r["note"], needs_lookup=r.get("needs_lookup", False))

            if r["kind"] is Kind.EPISODE:
                p.new_name = build_episode_name(stem, ext, r["season"], r["episode"])
            elif r["kind"] is Kind.DATE:
                p.new_name = build_date_name(stem, ext, show, r["airdate"])
            # SKIP_EXTRA / NONE keep new_name = None (left alone).
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# CLI summary (validation aid — not the primary interface)
# ---------------------------------------------------------------------------

def _summary(root: str) -> int:
    if not os.path.isdir(root):
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2
    props = scan(root)
    from collections import Counter
    by_conf = Counter(p.confidence for p in props if p.changed)
    n_extra = sum(1 for p in props if p.kind == Kind.SKIP_EXTRA.value)
    n_date = sum(1 for p in props if p.kind == Kind.DATE.value)
    n_none = sum(1 for p in props if p.kind == Kind.NONE.value)
    n_change = sum(1 for p in props if p.changed)
    print(f"Scanned: {root}")
    print(f"  proposed renames : {n_change}")
    for c in ("high", "medium", "guess"):
        print(f"      {c:7}: {by_conf.get(c, 0)}")
    print(f"  date (daily)     : {n_date}")
    print(f"  extras (skipped) : {n_extra}")
    print(f"  undetermined     : {n_none}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_summary(sys.argv[1] if len(sys.argv) > 1 else "."))
