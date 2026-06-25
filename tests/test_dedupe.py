"""Tests for the cross-drive duplicate finder (read-only grouping logic)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dedupe  # noqa: E402

# ---- pure helpers ---------------------------------------------------------

def test_episode_key_canonical():
    assert dedupe.episode_key("Show S01E05 1080p", "Season 1", "Show") == (1, 5)


def test_episode_key_parseable():
    assert dedupe.episode_key("profiler.301-vfua", "Season 3", "Profiler") == (3, 1)


def test_episode_key_none_for_extra():
    assert dedupe.episode_key("Gag Reel", "Extras", "Show") is None


def test_resolution_ranking():
    assert dedupe._resolution("Show.S01E01.2160p.x265") == 2160
    assert dedupe._resolution("Show.S01E01.1080p") == 1080
    assert dedupe._resolution("Show.S01E01.720p") == 720
    assert dedupe._resolution("Show.S01E01.no.res.here") == 0


def test_suggest_keep_prefers_resolution_then_size():
    copies = [
        dedupe.Copy("/p/a.mkv", "P", "a.mkv", size=500, resolution=720),
        dedupe.Copy("/q/b.mkv", "Q", "b.mkv", size=100, resolution=2160),  # best res
        dedupe.Copy("/r/c.mkv", "R", "c.mkv", size=900, resolution=720),
    ]
    assert dedupe._suggest_keep(copies) == 1


def test_suggest_keep_tie_break_on_size():
    copies = [
        dedupe.Copy("/p/a.mkv", "P", "a.mkv", size=500, resolution=1080),
        dedupe.Copy("/q/b.mkv", "Q", "b.mkv", size=800, resolution=1080),  # larger
    ]
    assert dedupe._suggest_keep(copies) == 1


# ---- grouping -------------------------------------------------------------

def _make(root, show, season, fname):
    d = os.path.join(root, show, f"Season {season}")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, fname)
    with open(p, "w") as fh:
        fh.write("x" * 10)
    return p


def test_find_duplicates_groups_same_episode(tmp_path):
    p = str(tmp_path / "P" / "TV Shows")
    q = str(tmp_path / "Q" / "TV Shows")
    _make(p, "Profiler", 3, "Profiler S03E01 1080p.mkv")
    _make(q, "Profiler", 3, "profiler.301-vfua.avi")        # same episode, messy name
    _make(p, "Profiler", 3, "Profiler S03E02.mkv")          # unique -> not a dup
    groups = dedupe.find_duplicates([p, q], cross_drive_only=False)
    assert len(groups) == 1
    g = groups[0]
    assert (g.season, g.episode) == (3, 1)
    assert len(g.copies) == 2


def test_cross_drive_only_filters_same_drive(tmp_path, monkeypatch):
    p = str(tmp_path / "P" / "TV Shows")
    _make(p, "Show", 1, "Show S01E01 a.mkv")
    _make(p, "Show", 1, "Show.101.b.mkv")   # same episode, same (temp) drive
    # both temp files share a drive -> cross_drive_only=True excludes them
    assert dedupe.find_duplicates([p], cross_drive_only=True) == []
    assert len(dedupe.find_duplicates([p], cross_drive_only=False)) == 1


def test_cross_drive_grouping_with_distinct_drives(tmp_path, monkeypatch):
    p = str(tmp_path / "P" / "TV Shows")
    q = str(tmp_path / "Q" / "TV Shows")
    _make(p, "Show", 1, "Show S01E01 1080p.mkv")
    _make(q, "Show", 1, "Show S01E01 2160p.mkv")
    # simulate distinct physical drives from the path
    monkeypatch.setattr(dedupe, "_drive_of",
                        lambda path: "P" if f"{os.sep}P{os.sep}" in path else "Q")
    groups = dedupe.find_duplicates([p, q], cross_drive_only=True)
    assert len(groups) == 1
    g = groups[0]
    assert g.drives == {"P", "Q"}
    # 2160p copy is the suggested keeper
    assert g.copies[g.suggested_keep].resolution == 2160
    assert g.reclaimable_bytes == 10  # the other copy's size


def test_to_dict_shape(tmp_path, monkeypatch):
    p = str(tmp_path / "P" / "TV Shows")
    q = str(tmp_path / "Q" / "TV Shows")
    _make(p, "Show", 2, "Show S02E03 720p.mkv")
    _make(q, "Show", 2, "Show S02E03 1080p.mkv")
    monkeypatch.setattr(dedupe, "_drive_of",
                        lambda path: "P" if f"{os.sep}P{os.sep}" in path else "Q")
    d = dedupe.find_duplicates([p, q], cross_drive_only=True)[0].to_dict()
    assert d["kind"] == "episode"
    assert d["se"] == "S02E03" and d["label"] == "S02E03"
    assert d["drives"] == ["P", "Q"]
    assert "suggested_keep" in d and "reclaimable_bytes" in d
    assert len(d["copies"]) == 2


# ---- movies ---------------------------------------------------------------

def _make_movie(root, folder, fname, blob="x" * 10):
    d = os.path.join(root, folder)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, fname)
    with open(p, "w") as fh:
        fh.write(blob)
    return p


def test_movies_grouped_by_title_year_not_fake_episode(tmp_path, monkeypatch):
    p = str(tmp_path / "P" / "Movies")
    q = str(tmp_path / "Q" / "Movies")
    _make_movie(p, "The Matrix (1999)", "The.Matrix.1999.1080p.BluRay.mkv")
    _make_movie(q, "The Matrix (1999)", "The Matrix (1999) 2160p.mkv", blob="x" * 20)
    _make_movie(p, "Heat (1995)", "Heat.1995.720p.mkv")   # only one copy -> not a dup
    monkeypatch.setattr(dedupe, "_drive_of",
                        lambda path: "P" if f"{os.sep}P{os.sep}" in path else "Q")
    groups = dedupe.find_duplicates([p, q], cross_drive_only=True)
    assert len(groups) == 1
    g = groups[0]
    assert g.kind == "movie"
    assert g.show == "The Matrix" and g.year == 1999
    assert g.season is None and g.episode is None
    # higher-resolution copy is the suggested keeper
    assert g.copies[g.suggested_keep].resolution == 2160
    d = g.to_dict()
    assert d["kind"] == "movie" and d["label"] == "(1999)" and d["year"] == 1999


def test_multi_part_movie_not_a_self_duplicate(tmp_path, monkeypatch):
    p = str(tmp_path / "P" / "Movies")
    q = str(tmp_path / "Q" / "Movies")
    for root in (p, q):
        _make_movie(root, "Epic (2001)", "Epic.2001.CD1.mkv")
        _make_movie(root, "Epic (2001)", "Epic.2001.CD2.mkv")
    monkeypatch.setattr(dedupe, "_drive_of",
                        lambda path: "P" if f"{os.sep}P{os.sep}" in path else "Q")
    groups = dedupe.find_duplicates([p, q], cross_drive_only=True)
    # CD1 and CD2 are distinct parts -> two duplicate groups, not one of four
    assert len(groups) == 2
    assert all(len(g.copies) == 2 for g in groups)
