"""Tests for the music duplicate finder — tag-based grouping, duration
clustering, and the AAC-preferred keeper. Grouping logic is exercised with
synthetic Records (no real audio files needed); read_record is tested by
monkeypatching mutagen.File."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import music  # noqa: E402


def _copy(path, fmt, *, bitrate=128, size=1000, duration=200.0, folder="Artist/Album"):
    return music.SongCopy(path, os.path.basename(path), folder, fmt, bitrate, size, duration)


def _rec(artist, title, copy):
    return music.Record(music._norm(artist), music._norm(title), artist, title, copy)


# ---- normalization --------------------------------------------------------

def test_norm_is_case_and_punct_insensitive():
    assert music._norm("One In a Million!") == music._norm("one in A million")
    assert music._norm("  Tom's Diner  ") == "tom s diner"


def test_norm_keeps_version_words_distinct():
    # (Radio Edit) vs (Extended) must NOT collapse — they are different versions
    assert music._norm("Song (Radio Edit)") != music._norm("Song (Extended Mix)")


# ---- grouping + duration clustering ---------------------------------------

def test_cross_format_same_song_is_grouped():
    recs = [
        _rec("Guns N' Roses", "One In a Million", _copy("/m/a.m4a", "m4a", duration=368.66)),
        _rec("Guns N' Roses", "One In A Million", _copy("/m/b.mp3", "mp3", duration=368.49)),
    ]
    groups = music.build_groups(recs)
    assert len(groups) == 1
    assert len(groups[0].copies) == 2


def test_same_title_different_duration_not_grouped():
    # a "Part 1"/"Part 2" style collision: same normalized title, far-apart length
    recs = [
        _rec("X", "Interlude", _copy("/m/a.mp3", "mp3", duration=60.0)),
        _rec("X", "Interlude", _copy("/m/b.mp3", "mp3", duration=240.0)),
    ]
    assert music.build_groups(recs) == []


def test_zero_duration_never_matches():
    recs = [
        _rec("X", "Mystery", _copy("/m/a.mp3", "mp3", duration=0.0)),
        _rec("X", "Mystery", _copy("/m/b.mp3", "mp3", duration=0.0)),
    ]
    assert music.build_groups(recs) == []


def test_cross_folder_same_song_grouped_whole_library():
    # whole-library: the same recording on an album AND a greatest-hits folder
    recs = [
        _rec("2 Pac", "California Love", _copy("/m/a.mp3", "mp3", duration=387.0, folder="2 Pac/Greatest Hits")),
        _rec("2 Pac", "California Love", _copy("/m/b.mp3", "mp3", duration=387.0, folder="2 Pac/Unknown Album")),
    ]
    groups = music.build_groups(recs)
    assert len(groups) == 1
    assert {c.folder for c in groups[0].copies} == {"2 Pac/Greatest Hits", "2 Pac/Unknown Album"}


def test_singleton_not_a_duplicate():
    recs = [_rec("X", "Only Copy", _copy("/m/a.mp3", "mp3"))]
    assert music.build_groups(recs) == []


# ---- keeper preference ----------------------------------------------------

def test_keeper_prefers_m4a_over_higher_bitrate_mp3():
    copies = [
        _copy("/m/a.mp3", "mp3", bitrate=320, size=9000),   # higher bitrate...
        _copy("/m/b.m4a", "m4a", bitrate=256, size=7000),   # ...but m4a wins
    ]
    assert music._suggest_keep(copies) == 1


def test_keeper_falls_back_to_bitrate_then_size():
    copies = [
        _copy("/m/a.mp3", "mp3", bitrate=128, size=4000),
        _copy("/m/b.mp3", "mp3", bitrate=320, size=9000),   # highest bitrate
    ]
    assert music._suggest_keep(copies) == 1


def test_group_to_dict_shape():
    recs = [
        _rec("A", "Song", _copy("/m/a.m4a", "m4a", bitrate=256, size=8000, duration=185.0)),
        _rec("A", "Song", _copy("/m/b.mp3", "mp3", bitrate=128, size=4000, duration=185.3)),
    ]
    d = music.build_groups(recs)[0].to_dict()
    assert d["artist"] == "A" and d["title"] == "Song"
    assert d["length"] == "3:05"
    assert len(d["copies"]) == 2
    assert d["copies"][0]["length"] == "3:05"
    assert d["reclaimable_bytes"] == 4000          # the non-kept mp3


# ---- read_record (mutagen mocked) -----------------------------------------

class _FakeInfo:
    def __init__(self, length, bitrate):
        self.length = length
        self.bitrate = bitrate


class _FakeMF(dict):
    def __init__(self, tags, length, bitrate):
        super().__init__(tags)
        self.info = _FakeInfo(length, bitrate)


def test_read_record_uses_tags(tmp_path, monkeypatch):
    f = tmp_path / "Artist" / "Album" / "05 Song.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 500)
    monkeypatch.setattr(music.mutagen, "File",
                        lambda *a, **k: _FakeMF({"artist": ["The Artist"], "title": ["The Song"]},
                                                183.4, 256000))
    r = music.read_record(str(f), str(tmp_path))
    assert r.artist == "The Artist" and r.title == "The Song"
    assert r.copy.bitrate == 256 and r.copy.duration == 183.4 and r.copy.size == 500
    assert r.copy.fmt == "mp3"


def test_read_record_falls_back_to_folder_and_filename(tmp_path, monkeypatch):
    f = tmp_path / "Some Artist" / "Album" / "mystery.m4a"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x" * 10)
    monkeypatch.setattr(music.mutagen, "File", lambda *a, **k: None)   # untaggable
    r = music.read_record(str(f), str(tmp_path))
    assert r.artist == "Some Artist"      # from the top folder
    assert r.title == "mystery"           # from the filename stem
