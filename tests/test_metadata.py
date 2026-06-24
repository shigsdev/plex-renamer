"""Provider-dispatch + air-date matching tests. HTTP is mocked — no network."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metadata  # noqa: E402


def _clear_caches():
    for fn in (metadata._tmdb_show_id, metadata._tmdb_num_seasons, metadata._tmdb_season,
               metadata._tvdb_show_id, metadata._tvdb_airdate_map):
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
