#!/usr/bin/env python3
"""
metadata.py — Optional online lookups to upgrade a daily-show file to S##E##.

Daily shows (Dateline, etc.) carry an air date but no episode number. Online
databases store each episode's air date, so given the show name + a date we can
find the real season/episode. Best-effort: with no API key every function
degrades to "no suggestion" and the rest of the app keeps working.

Two providers are supported; pick one with the ``METADATA_PROVIDER`` env var:

  * ``tmdb``  (default) — The Movie Database. Easiest free key (instant signup,
    a single v3 key). Set ``TMDB_API_KEY``.
    Key: https://www.themoviedb.org/settings/api

  * ``tvdb``  — TheTVDB v4. What Plex uses natively. Set ``TVDB_API_KEY`` (and
    ``TVDB_PIN`` if your key is a user-subscription key). More setup, but its
    season/episode numbering is the one Plex's TVDB agent will agree with.
    Key: https://thetvdb.com/dashboard/account/apikey

If ``METADATA_PROVIDER`` is unset, whichever provider has a key configured is
used (TMDB wins if both are set). Pure stdlib HTTP so Flask stays the only dep.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

def _provider() -> str:
    choice = os.environ.get("METADATA_PROVIDER", "").strip().lower()
    if choice in ("tmdb", "tvdb"):
        return choice
    if os.environ.get("TMDB_API_KEY"):
        return "tmdb"
    if os.environ.get("TVDB_API_KEY"):
        return "tvdb"
    return "tmdb"  # default; have_key() will report False


def have_key() -> bool:
    p = _provider()
    return bool(os.environ.get("TMDB_API_KEY" if p == "tmdb" else "TVDB_API_KEY"))


def provider_name() -> str:
    return _provider()


def lookup_by_airdate(show_name: str, airdate: str) -> dict | None:
    """Find the season/episode whose air date matches ``airdate`` (YYYY-MM-DD).

    Returns {"season": s, "episode": e, "provider": ...} or None.
    """
    if _provider() == "tvdb":
        return _tvdb_lookup_by_airdate(show_name, airdate)
    return _tmdb_lookup_by_airdate(show_name, airdate)


def lookup_by_absolute(show_name: str, absolute: int) -> dict | None:
    """Map an ABSOLUTE episode number (e.g. DVD "episode 61") to its real
    season/episode. Returns {"season": s, "episode": e, "provider": ...} or None.
    """
    if _provider() == "tvdb":
        return _tvdb_lookup_by_absolute(show_name, absolute)
    return _tmdb_lookup_by_absolute(show_name, absolute)


def validate_episode(show_name: str, season: int, episode: int) -> dict | None:
    """Validate a provisional season/episode against the provider, correcting it
    when possible. Returns None if it can't be validated (no key / show not
    found / nothing matches). Otherwise:
      {"season", "episode", "corrected": bool, "exists": bool, "provider"}
    - exists=True, corrected=False  -> the proposal is a real episode (confirmed)
    - corrected=True                -> S##E## didn't exist; resolved the number as
                                       an ABSOLUTE position to the real episode
    """
    if _provider() == "tvdb":
        return _tvdb_validate(show_name, season, episode)
    return _tmdb_validate(show_name, season, episode)


# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------

def _http_json(url: str, *, headers: dict | None = None,
               data: bytes | None = None) -> dict | None:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    if data is not None and "Content-Type" not in (headers or {}):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.load(resp)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, ValueError):
        return None


# ---------------------------------------------------------------------------
# TMDB (The Movie Database) — v3 API key
# ---------------------------------------------------------------------------

_TMDB = "https://api.themoviedb.org/3"


def _tmdb_get(path: str, **params) -> dict | None:
    key = os.environ.get("TMDB_API_KEY")
    if not key:
        return None
    params["api_key"] = key
    return _http_json(f"{_TMDB}{path}?{urllib.parse.urlencode(params)}")


@lru_cache(maxsize=512)
def _tmdb_show_id(name: str) -> int | None:
    data = _tmdb_get("/search/tv", query=name)
    if not data or not data.get("results"):
        return None
    return data["results"][0]["id"]


@lru_cache(maxsize=64)
def _tmdb_num_seasons(show_id: int) -> int:
    data = _tmdb_get(f"/tv/{show_id}")
    return data.get("number_of_seasons", 0) if data else 0


@lru_cache(maxsize=256)
def _tmdb_season(show_id: int, season: int) -> tuple[tuple[str, int, int], ...]:
    data = _tmdb_get(f"/tv/{show_id}/season/{season}")
    if not data:
        return ()
    return tuple((ep["air_date"], ep["season_number"], ep["episode_number"])
                 for ep in data.get("episodes", []) if ep.get("air_date"))


def _tmdb_lookup_by_airdate(show_name: str, airdate: str) -> dict | None:
    show_id = _tmdb_show_id(show_name)
    if not show_id:
        return None
    for season in range(1, _tmdb_num_seasons(show_id) + 1):
        for ad, s, e in _tmdb_season(show_id, season):
            if ad == airdate:
                return {"season": s, "episode": e, "provider": "tmdb"}
    return None


def _tmdb_ordered(show_id: int) -> list[tuple[int, int]]:
    """All (season, episode) flattened in season/episode order (best-effort
    absolute order, since TMDB has no absolute numbers)."""
    out: list[tuple[int, int]] = []
    for season in range(1, _tmdb_num_seasons(show_id) + 1):
        for _ad, s, e in sorted(_tmdb_season(show_id, season), key=lambda t: (t[1], t[2])):
            out.append((s, e))
    return out


def _tmdb_lookup_by_absolute(show_name: str, absolute: int) -> dict | None:
    show_id = _tmdb_show_id(show_name)
    if not show_id:
        return None
    ordered = _tmdb_ordered(show_id)
    if 1 <= absolute <= len(ordered):
        s, e = ordered[absolute - 1]
        return {"season": s, "episode": e, "provider": "tmdb"}
    return None


def _tmdb_validate(show_name: str, season: int, episode: int) -> dict | None:
    show_id = _tmdb_show_id(show_name)
    if not show_id:
        return None
    ordered = _tmdb_ordered(show_id)
    if not ordered:
        return None
    exists = set(ordered)
    if (season, episode) in exists:
        return {"season": season, "episode": episode, "corrected": False,
                "exists": True, "provider": "tmdb"}
    if 1 <= episode <= len(ordered) and ordered[episode - 1] in exists:
        s, e = ordered[episode - 1]
        return {"season": s, "episode": e, "corrected": True, "exists": True,
                "provider": "tmdb"}
    return {"season": season, "episode": episode, "corrected": False,
            "exists": False, "provider": "tmdb"}


# ---------------------------------------------------------------------------
# TheTVDB — v4 API (login -> bearer token -> paged episodes)
# ---------------------------------------------------------------------------

_TVDB = "https://api4.thetvdb.com/v4"
_tvdb_token: str | None = None


def _tvdb_login() -> str | None:
    global _tvdb_token
    if _tvdb_token:
        return _tvdb_token
    key = os.environ.get("TVDB_API_KEY")
    if not key:
        return None
    body = {"apikey": key}
    if os.environ.get("TVDB_PIN"):
        body["pin"] = os.environ["TVDB_PIN"]
    data = _http_json(f"{_TVDB}/login", data=json.dumps(body).encode())
    if data and data.get("data", {}).get("token"):
        _tvdb_token = data["data"]["token"]
    return _tvdb_token


def _tvdb_get(path: str) -> dict | None:
    token = _tvdb_login()
    if not token:
        return None
    url = path if path.startswith("http") else f"{_TVDB}{path}"
    return _http_json(url, headers={"Authorization": f"Bearer {token}"})


@lru_cache(maxsize=512)
def _tvdb_show_id(name: str) -> int | None:
    data = _tvdb_get(f"/search?{urllib.parse.urlencode({'query': name, 'type': 'series'})}")
    if not data or not data.get("data"):
        return None
    first = data["data"][0]
    raw = first.get("tvdb_id") or first.get("id")
    try:
        return int(str(raw).split("-")[-1])
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=32)
def _tvdb_episode_index(show_id: int) -> dict:
    """Page a series' episodes ONCE and build indexes so lookups are O(1):
      air     {airdate: (s, e)}
      abs     {absoluteNumber: (s, e)}  (TVDB's explicit field; may be sparse)
      ordered [(s, e), ...] sorted by (season, number), excluding specials —
              used as a computed absolute position when ``abs`` is missing
      exists  frozenset of (s, e) — for validating a proposed episode
    """
    by_air: dict[str, tuple[int, int]] = {}
    by_abs: dict[int, tuple[int, int]] = {}
    all_eps: list[tuple[int, int]] = []
    url = f"/series/{show_id}/episodes/default?page=0"
    for _ in range(50):  # hard page cap
        data = _tvdb_get(url)
        if not data:
            break
        for ep in data.get("data", {}).get("episodes", []):
            se = (ep.get("seasonNumber"), ep.get("number"))
            if None in se:
                continue
            all_eps.append(se)
            if ep.get("aired"):
                by_air.setdefault(ep["aired"], se)
            if ep.get("absoluteNumber") is not None:
                by_abs.setdefault(ep["absoluteNumber"], se)
        nxt = data.get("links", {}).get("next")
        if not nxt:
            break
        url = nxt
    ordered = tuple(se for se in sorted(set(all_eps)) if se[0] and se[0] > 0)
    return {"air": by_air, "abs": by_abs, "ordered": ordered, "exists": frozenset(all_eps)}


def _abs_from_index(idx: dict, absolute: int) -> tuple[int, int] | None:
    """Explicit absolute number, else the Nth episode in season/number order."""
    if absolute in idx["abs"]:
        return idx["abs"][absolute]
    if 1 <= absolute <= len(idx["ordered"]):
        return idx["ordered"][absolute - 1]
    return None


def _tvdb_lookup_by_airdate(show_name: str, airdate: str) -> dict | None:
    show_id = _tvdb_show_id(show_name)
    if not show_id:
        return None
    se = _tvdb_episode_index(show_id)["air"].get(airdate)
    return {"season": se[0], "episode": se[1], "provider": "tvdb"} if se else None


def _tvdb_lookup_by_absolute(show_name: str, absolute: int) -> dict | None:
    show_id = _tvdb_show_id(show_name)
    if not show_id:
        return None
    se = _abs_from_index(_tvdb_episode_index(show_id), absolute)
    return {"season": se[0], "episode": se[1], "provider": "tvdb"} if se else None


def _tvdb_validate(show_name: str, season: int, episode: int) -> dict | None:
    show_id = _tvdb_show_id(show_name)
    if not show_id:
        return None
    idx = _tvdb_episode_index(show_id)
    if not idx["exists"]:
        return None
    if (season, episode) in idx["exists"]:
        return {"season": season, "episode": episode, "corrected": False,
                "exists": True, "provider": "tvdb"}
    # S##E## isn't real — the number may be an absolute position; resolve it.
    se = _abs_from_index(idx, episode)
    if se and se in idx["exists"]:
        return {"season": se[0], "episode": se[1], "corrected": True,
                "exists": True, "provider": "tvdb"}
    return {"season": season, "episode": episode, "corrected": False,
            "exists": False, "provider": "tvdb"}
