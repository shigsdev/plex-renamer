"""Provider-dispatch + air-date matching tests. HTTP is mocked — no network."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata  # noqa: E402


def _clear_caches():
    for fn in (metadata._tmdb_show_id, metadata._tmdb_num_seasons, metadata._tmdb_season,
               metadata._tvdb_show_id, metadata._tvdb_episode_index):
        fn.cache_clear()
    metadata._tvdb_token = None


def test_no_key_means_no_lookup(monkeypatch):
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.delenv("TVDB_API_KEY", raising=False)
    monkeypatch.delenv("METADATA_PROVIDER", raising=False)
    assert metadata.have_key() is False
    assert metadata.lookup_by_airdate("Dateline NBC", "2016-09-09") is None


def test_provider_selection(monkeypatch):
    monkeypatch.delenv("METADATA_PROVIDER", raising=False)
    monkeypatch.setenv("TVDB_API_KEY", "x")
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    assert metadata.provider_name() == "tvdb"
    monkeypatch.setenv("TMDB_API_KEY", "y")  # TMDB wins when both present
    assert metadata.provider_name() == "tmdb"
    monkeypatch.setenv("METADATA_PROVIDER", "tvdb")  # explicit override
    assert metadata.provider_name() == "tvdb"


def test_tmdb_airdate_match(monkeypatch):
    _clear_caches()
    monkeypatch.setenv("METADATA_PROVIDER", "tmdb")
    monkeypatch.setenv("TMDB_API_KEY", "x")

    def fake_get(path, **params):
        if path == "/search/tv":
            return {"results": [{"id": 42}]}
        if path == "/tv/42":
            return {"number_of_seasons": 2}
        if path == "/tv/42/season/1":
            return {"episodes": [{"air_date": "2016-01-01", "season_number": 1, "episode_number": 1}]}
        if path == "/tv/42/season/2":
            return {"episodes": [{"air_date": "2016-09-09", "season_number": 2, "episode_number": 7}]}
        return None

    monkeypatch.setattr(metadata, "_tmdb_get", fake_get)
    hit = metadata.lookup_by_airdate("Dateline NBC", "2016-09-09")
    assert hit == {"season": 2, "episode": 7, "provider": "tmdb"}


def test_tvdb_airdate_match_with_pagination(monkeypatch):
    _clear_caches()
    monkeypatch.setenv("METADATA_PROVIDER", "tvdb")
    monkeypatch.setenv("TVDB_API_KEY", "x")
    metadata._tvdb_token = "tok"  # skip login

    pages = {
        "/series/99/episodes/default?page=0": {
            "data": {"episodes": [{"aired": "2016-01-01", "seasonNumber": 1, "number": 1}]},
            "links": {"next": "/series/99/episodes/default?page=1"},
        },
        "/series/99/episodes/default?page=1": {
            "data": {"episodes": [{"aired": "2016-09-09", "seasonNumber": 21, "number": 57}]},
            "links": {"next": None},
        },
    }

    def fake_get(path):
        if path.startswith("/search"):
            return {"data": [{"tvdb_id": 99}]}
        return pages.get(path)

    monkeypatch.setattr(metadata, "_tvdb_get", fake_get)
    hit = metadata.lookup_by_airdate("Dateline NBC", "2016-09-09")
    assert hit == {"season": 21, "episode": 57, "provider": "tvdb"}


def test_tvdb_validate_confirms_corrects_and_flags(monkeypatch):
    _clear_caches()
    monkeypatch.setenv("METADATA_PROVIDER", "tvdb")
    monkeypatch.setenv("TVDB_API_KEY", "x")
    metadata._tvdb_token = "tok"

    # a small 2-season show: S1E1..E3, S2E1..E3 (6 episodes, no absolute field)
    eps = [{"seasonNumber": s, "number": e} for s in (1, 2) for e in (1, 2, 3)]
    pages = {"/series/5/episodes/default?page=0": {
        "data": {"episodes": eps}, "links": {"next": None}}}

    def fake_get(path):
        if path.startswith("/search"):
            return {"data": [{"tvdb_id": 5}]}
        return pages.get(path)

    monkeypatch.setattr(metadata, "_tvdb_get", fake_get)
    # real episode -> confirmed
    assert metadata.validate_episode("X", 2, 3) == \
        {"season": 2, "episode": 3, "corrected": False, "exists": True, "provider": "tvdb"}
    # S1E5 doesn't exist; absolute 5 (ordered) -> S2E2 -> corrected
    assert metadata.validate_episode("X", 1, 5) == \
        {"season": 2, "episode": 2, "corrected": True, "exists": True, "provider": "tvdb"}
    # nothing matches (episode 99) -> flagged, not corrected
    out = metadata.validate_episode("X", 9, 99)
    assert out["exists"] is False and out["corrected"] is False


def test_tvdb_absolute_match(monkeypatch):
    _clear_caches()
    monkeypatch.setenv("METADATA_PROVIDER", "tvdb")
    monkeypatch.setenv("TVDB_API_KEY", "x")
    metadata._tvdb_token = "tok"

    pages = {
        "/series/77/episodes/default?page=0": {
            "data": {"episodes": [
                {"absoluteNumber": 25, "seasonNumber": 1, "number": 18},
                {"absoluteNumber": 61, "seasonNumber": 2, "number": 11},
            ]},
            "links": {"next": None},
        },
    }

    def fake_get(path):
        if path.startswith("/search"):
            return {"data": [{"tvdb_id": 77}]}
        return pages.get(path)

    monkeypatch.setattr(metadata, "_tvdb_get", fake_get)
    assert metadata.lookup_by_absolute("Garfield and Friends", 61) == \
        {"season": 2, "episode": 11, "provider": "tvdb"}
    assert metadata.lookup_by_absolute("Garfield and Friends", 99) is None
