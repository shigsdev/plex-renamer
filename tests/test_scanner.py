"""Parser tests — focused on the real-world patterns and the danger zones
(years, quality tags, season packs, extras) that earlier versions got wrong."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scanner  # noqa: E402
from scanner import Confidence, Kind  # noqa: E402


def p(stem, parent="", season=None, show=""):
    return scanner.parse(stem, parent, season, show)


# ---- clean, high-confidence forms -----------------------------------------

def test_NxNN():
    r = p("Prison Break - 3x01 - Orientation")
    assert (r["season"], r["episode"]) == (3, 1)
    assert r["confidence"] is Confidence.HIGH


def test_S_sep_E():
    r = p("Gangland.2007.S01.E04.American.Gangster.1080p")
    assert (r["season"], r["episode"]) == (1, 4)
    assert r["confidence"] is Confidence.HIGH


def test_S_space_EP():
    r = p("Lost S01 EP02 1080p BluRay", parent="Lost S01 1080p")
    assert (r["season"], r["episode"]) == (1, 2)


def test_worded_season_episode():
    r = p("Ally.McBeal.Season1.Episode.11.Silver.Bells.DVDRip")
    assert (r["season"], r["episode"]) == (1, 11)
    assert r["confidence"] is Confidence.HIGH


# ---- season inference + new handlers --------------------------------------

def test_episode_only_defaults_season_1_without_folder():
    r = p("A.Perfect.Planet.EP01.2021.2160p")
    assert (r["season"], r["episode"]) == (1, 1)
    assert r["confidence"] is Confidence.MEDIUM


def test_part_n_with_folder_season():
    r = p("JFK Declassified - Part 3 - Oswald Goes Dark", parent="Season 1")
    assert (r["season"], r["episode"]) == (1, 3)


def test_bare_3_digit_code():
    r = p("murder.in.the.first.210.hdtv-lol")
    assert (r["season"], r["episode"]) == (2, 10)


def test_folder_carries_episode_token():
    r = p("Dateline.2016.11.12.The.Officers.Wife", parent="S21E57 - Dateline.2016.11.12")
    assert (r["season"], r["episode"]) == (21, 57)
    assert r["confidence"] is Confidence.HIGH


# ---- daily shows ----------------------------------------------------------

def test_daily_show_is_date_kind():
    r = p("Dateline.NBC.2016.09.09.Who.Killed.JonBenet.WEB.h264-ROFL")
    assert r["kind"] is Kind.DATE
    assert r["airdate"] == "2016-09-09"
    assert r["needs_lookup"] is True


# ---- DANGER ZONES: must NOT fabricate episode numbers ---------------------

def test_year_is_not_parsed_as_code():
    # 2007 must not become S20E07
    r = p("Some.Show.2007.1080p.BluRay.x264-GROUP", parent="Season 5")
    assert r["kind"] is not Kind.EPISODE or (r["season"], r["episode"]) != (20, 7)


def test_quality_tag_not_parsed_as_code():
    # 1080 / 264 must not become an episode
    r = p("Law and Order SVU S26 1080p AMZN WEB-DL DDP5 1 H 264-NTb", parent="Season 26")
    assert r["kind"] is Kind.NONE  # season pack, no episode -> undetermined


def test_show_name_robust_to_scan_depth(tmp_path):
    # the bug: scanning above the library lumped no-season shows under "TV Shows"
    f = tmp_path / "P" / "TV Shows" / "A Perfect Planet"
    f.mkdir(parents=True)
    assert scanner._show_name(str(tmp_path / "P"), str(f)) == "A Perfect Planet"
    assert scanner._show_name(str(tmp_path / "P" / "TV Shows"), str(f)) == "A Perfect Planet"
    # season folder still wins
    s = tmp_path / "P" / "TV Shows" / "Dateline NBC" / "Season 30"
    s.mkdir(parents=True)
    assert scanner._show_name(str(tmp_path / "P"), str(s)) == "Dateline NBC"


def test_scan_skips_recycle_and_system_folders(tmp_path):
    lib = tmp_path / "TV Shows"
    (lib / "MyShow" / "Season 1").mkdir(parents=True)
    (lib / "MyShow" / "Season 1" / "MyShow.1x01.mkv").write_text("")
    # NAS recycle + snapshot copies that must be ignored
    for junk in ("@Recycle", "@Recently-Snapshot", "#recycle", "$RECYCLE.BIN"):
        d = lib / junk / "MyShow"
        d.mkdir(parents=True)
        (d / "MyShow.1x02.mkv").write_text("")
    props = [p for p in scanner.scan(str(tmp_path)) if p.changed]
    # only the real Season 1 file — the 4 recycle/system copies are skipped
    assert len(props) == 1
    assert os.path.join("Season 1", "MyShow.1x01.mkv") in props[0].path


def test_show_name_does_not_treat_show_titled_like_library_as_container():
    # "Show Me a Hero" must not be read as a "Shows" library folder
    assert scanner._show_name("P:/TV Shows", "P:/TV Shows/Show Me a Hero/Season 1") == "Show Me a Hero"


# ---- movies ---------------------------------------------------------------

def test_is_movie_path_detects_movie_library():
    assert scanner.is_movie_path("P:/Movies/The Matrix (1999)/The Matrix.mkv")
    assert scanner.is_movie_path("N:/Films/Heat (1995)/heat.mkv")
    assert scanner.is_movie_path("P:/Movies 4K/Dune (2021)/dune.mkv")


def test_is_movie_path_false_for_tv_and_lookalike_titles():
    assert not scanner.is_movie_path("P:/TV Shows/Profiler/Season 3/x.mkv")
    # a film/show whose TITLE starts with "Movie" must NOT be read as the library
    assert not scanner.is_movie_path("P:/TV Shows/Movie 43/Season 1/x.mkv")


def test_movie_identity_from_plex_folder():
    assert scanner.movie_identity(
        "The.Matrix.1999.1080p.BluRay.x264", "The Matrix (1999)") == ("The Matrix", 1999, 0)


def test_movie_identity_from_filename_when_folder_has_no_year():
    # loose file directly under the library — parent is "Movies", year from name
    assert scanner.movie_identity(
        "Heat.1995.720p.BrRip", "Movies") == ("Heat", 1995, 0)


def test_movie_identity_parenthesized_year_wins_over_title_number():
    # "Blade Runner 2049 (2017)" -> keep 2049 in the title, year is 2017
    assert scanner.movie_identity(
        "Blade.Runner.2049.2017.2160p", "Blade Runner 2049 (2017)") == ("Blade Runner 2049", 2017, 0)


def test_movie_identity_multi_part_kept_distinct():
    assert scanner.movie_identity("Movie.2001.CD2", "Movie (2001)") == ("Movie", 2001, 2)
    assert scanner.movie_identity("Movie.2001.CD1", "Movie (2001)") == ("Movie", 2001, 1)


def test_movie_identity_no_year_falls_back_to_title():
    title, year, part = scanner.movie_identity("Some.Obscure.Film", "Some Obscure Film")
    assert title == "Some Obscure Film" and year is None and part == 0


def test_movie_identity_keeps_sequel_number():
    # a lone digit is a sequel number for movies, not an audio-tag stray digit
    assert scanner.movie_identity("Deadpool.2.2018.Theatrical.2160p", "")[:2] == ("Deadpool 2", 2018)


def test_movie_identity_sequel_not_merged_with_original_when_no_year():
    # the Die Hard regression: "Die Hard 2" must not collapse to "Die Hard"
    a = scanner.movie_identity("Die.Hard.2.BluRay.1080p.x264", "")
    b = scanner.movie_identity("Die.Hard.BluRay.1080p.x264", "")
    assert a[0].lower() != b[0].lower()


def test_separator_SE_is_plex_ok_and_left_alone(tmp_path):
    # "S02.E04" and "S02 E04" are Plex-readable, so don't churn them
    d = tmp_path / "Gangland (2007)" / "Season 2"
    d.mkdir(parents=True)
    (d / "Gangland.2007.S02.E04.Maniacal.1080p.Blu-ray.X.265.AC3.2.0.mkv").write_text("")
    (d / "Gangland 2007 S02 E05 Lords.mkv").write_text("")
    assert scanner.scan(str(tmp_path)) == []


def test_year_based_season_is_plex_ok(tmp_path):
    # Shark Week uses the YEAR as the season (S2023E15) — already Plex-readable,
    # so leave it alone instead of mangling "2023" into S20E23
    d = tmp_path / "Shark Week 1987" / "Season 2023"
    d.mkdir(parents=True)
    (d / "Shark Week 1987 S2023E15 1080p AMZN WEB-DL H265 SDR DDP 2 0 English-HONE.mkv").write_text("")
    assert [p for p in scanner.scan(str(tmp_path)) if p.changed] == []


def test_S_space_EP_is_still_normalized(tmp_path):
    # "S01 EP01" (E followed by a letter) is NOT Plex-readable -> still renamed
    d = tmp_path / "Lost" / "Season 1"
    d.mkdir(parents=True)
    (d / "Lost S01 EP02 1080p.mkv").write_text("")
    props = [p for p in scanner.scan(str(tmp_path)) if p.changed]
    assert len(props) == 1
    assert "S01E02" in props[0].new_name


def test_year_kept_in_built_title():
    name = scanner.build_episode_name("Iron.Man.1994.1x01.DVDRip.x264.[gd]", ".mkv", 1, 1)
    assert name == "Iron Man 1994 - S01E01.mkv"


def test_daily_file_resolved_to_episode_strips_the_date():
    # when a daily-show file is matched to an episode via lookup, the air-date
    # must not linger in the title
    name = scanner.build_episode_name(
        "Dateline.NBC.2022.04.15.The.Real.Thing.About.Pam.1080p.WEB.h264-DiRT", ".mkv", 30, 22)
    assert "2022" not in name
    assert "04 15" not in name and "04.15" not in name
    assert name.endswith("- S30E22.mkv")
    assert "The Real Thing About Pam" in name


def test_multi_episode_bare_e_range():
    # the reported bug: "e01-02" is a combined two-episode file -> S01E01E02
    r = p("lois.and.clark.e01-02.pilot.ntsc.dvd.dd2.0.x264hi10-lightspeed", parent="Season 1")
    assert (r["season"], r["episode"], r["episode_end"]) == (1, 1, 2)
    name = scanner.build_episode_name(
        "lois.and.clark.e01-02.pilot.ntsc.dvd.dd2.0.x264hi10-lightspeed", ".mkv", 1, 1, 2)
    assert name == "lois and clark pilot - S01E01E02.mkv"


def test_multi_episode_nxnn_range():
    for stem in ("show.1x01-02.title", "show.1x01-1x02.title"):
        r = p(stem)
        assert (r["episode"], r["episode_end"]) == (1, 2)
        assert "S01E01E02" in scanner.build_episode_name(stem, ".mkv", 1, 1, 2)


def test_three_episode_range():
    name = scanner.build_episode_name("show.e01-03.title", ".mkv", 1, 1, 3)
    assert name == "show title - S01E01E02E03.mkv"


def test_single_episode_not_read_as_range():
    # "e05" followed by "1080p" must stay single (the hyphen guard)
    r = p("show.e05.1080p.x264-grp", parent="Season 2")
    assert r["episode"] == 5
    assert r.get("episode_end") is None


def test_bare_e_episode_not_codec():
    # "e001" is the episode; "x264hi10" must NOT leak "264" as S02E64
    r = p("he-man.e001.diamond.ray.of.disappearance.ntsc.dvd.dd2.0.x264hi10-lightspeed",
          parent="Season 1", show="He-Man")
    assert (r["season"], r["episode"]) == (1, 1)
    name = scanner.build_episode_name(
        "he-man.e001.diamond.ray.of.disappearance.ntsc.dvd.dd2.0.x264hi10-lightspeed", ".mkv", 1, 1)
    assert "264" not in name
    assert name.endswith("- S01E01.mkv")


def test_episode_without_season_is_absolute_candidate():
    # "episode_61" with no season folder -> provisional S01E61, flagged for an
    # absolute-number lookup
    r = p("garfield_and_friends.vol3.episode_61.dvdrip_xvid-fov",
          parent="Garfield and Friends", show="Garfield and Friends")
    assert (r["season"], r["episode"]) == (1, 61)
    assert r["absolute"] == 61
    assert r["needs_lookup"] is True


def test_episode_with_season_folder_is_validated_not_absolute():
    # folder gives the season, but a bare "episode N" number could be absolute,
    # so it's flagged for a TVDB *validation* (not an absolute-only candidate)
    r = p("show.episode.4", parent="Season 2", show="Show")
    assert (r["season"], r["episode"]) == (2, 4)
    assert r.get("absolute") is None       # not an absolute-only candidate
    assert r.get("needs_lookup") is True   # but validated against TVDB


def test_bare_e_does_not_overmatch_show_titles():
    # word-boundary guard: "Se7en" / embedded e+digit must not be read as an episode
    assert p("Se7en bonus content", parent="Season 1")["kind"] is not Kind.EPISODE or \
        p("Se7en bonus content", parent="Season 1")["episode"] != 7


def test_wxh_resolution_not_parsed_as_episode():
    # "1920x1080" is a resolution — must NOT become S19E20 (the A Football Life bug)
    r = p("Some Show Name (1920x1080)", parent="Season 4")
    assert r["kind"] is not Kind.EPISODE or (r["season"], r["episode"]) != (19, 20)


def test_leading_episode_number_with_season_folder():
    r = p("01 A Football Life Bill Belichick, Pt. 1 (1920x1080) [Phr0stY]",
          parent="Season 1", show="A Football Life")
    assert (r["season"], r["episode"]) == (1, 1)


def test_three_digit_leading_is_still_a_code_not_episode_104():
    # "104 Title" -> S01E04 via the bare-code handler, not episode 104
    r = p("104 Title Goes Here", parent="Season 1")
    assert (r["season"], r["episode"]) == (1, 4)


def test_leading_year_is_not_an_episode():
    r = p("1922 The Movie", parent="Season 1")
    assert r["kind"] is Kind.NONE


def test_build_name_strips_leading_number_and_resolution():
    # leading "04" matches the episode, so it is stripped from the title
    name = scanner.build_episode_name(
        "04 A Football Life Kurt Warner (1920x1080) [Phr0stY] ", ".mkv", 1, 4)
    assert "1920x1080" not in name
    assert "[Phr0stY]" not in name
    assert "( )" not in name and "()" not in name
    assert name.endswith(" - S01E04.mkv")
    assert name.startswith("A Football Life Kurt Warner")


def test_lone_S_number_not_grabbed():
    r = p("Game of Thrones - S08S07 - The Last Watch", parent="Season 8")
    # ambiguous junk -> should not confidently emit an episode
    assert r["kind"] in (Kind.NONE, Kind.SKIP_EXTRA)


# ---- extras ---------------------------------------------------------------

def test_extra_is_skipped():
    r = p("Gag Reel", parent="Extras")
    assert r["kind"] is Kind.SKIP_EXTRA


def test_extra_keyword_in_show_title_is_not_skipped():
    # "The Making of the Mob" is a real series, not a making-of featurette.
    r = p("The.Making.Of.The.Mob.Chicago.Part.2.720p", parent="Season 1",
          show="The Making of the Mob Chicago")
    assert r["kind"] is Kind.EPISODE
    assert r["episode"] == 2


# ---- name construction ----------------------------------------------------

def test_build_episode_name_strips_code_and_junk():
    name = scanner.build_episode_name("heroes.406.tabula.rasa-sitv", ".mkv", 4, 6)
    assert name == "heroes tabula rasa - S04E06.mkv"


def test_build_date_name_is_clean():
    name = scanner.build_date_name(
        "Dateline.2017.02.10.Fury.720p.WEBRip.AAC2.0.x264-BTW", ".mkv",
        "Dateline NBC", "2017-02-10")
    assert name == "Dateline NBC - 2017-02-10 - Fury.mkv"


def test_already_canonical_is_left_alone(tmp_path):
    f = tmp_path / "Show S01E01 1080p.mkv"
    f.write_text("")
    props = scanner.scan(str(tmp_path))
    assert props == []  # nothing needs attention
