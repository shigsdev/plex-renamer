"""CLI tests for rename_plex.py — preview, apply, and undo paths."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apply as apply_mod  # noqa: E402
import rename_plex  # noqa: E402


def _make_lib(tmp_path):
    d = tmp_path / "Profiler" / "Season 3"
    d.mkdir(parents=True)
    for i in (1, 2, 3):
        (d / f"profiler.30{i}-vfua.avi").write_text("")
    return d


def test_cli_preview_changes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(apply_mod, "UNDO_DIR", str(tmp_path / "undo"))
    d = _make_lib(tmp_path)
    rc = rename_plex.main([str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "S03E01" in out
    assert "Preview only" in out
    # nothing renamed
    assert "profiler.301-vfua.avi" in os.listdir(d)


def test_cli_min_high_filters_out_medium(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(apply_mod, "UNDO_DIR", str(tmp_path / "undo"))
    _make_lib(tmp_path)  # bare-code -> medium confidence
    rc = rename_plex.main([str(tmp_path), "--min", "high"])
    assert rc == 0
    assert "No renames to propose" in capsys.readouterr().out


def test_cli_apply_then_undo(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(apply_mod, "UNDO_DIR", str(tmp_path / "undo"))
    d = _make_lib(tmp_path)
    rc = rename_plex.main([str(tmp_path), "--apply", "--yes"])
    assert rc == 0
    names = os.listdir(d)
    assert any(n.endswith("S03E01.avi") for n in names)
    assert "profiler.301-vfua.avi" not in names

    rc2 = rename_plex.main(["--undo"])
    assert rc2 == 0
    assert "profiler.301-vfua.avi" in os.listdir(d)


def test_cli_requires_directory_or_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(apply_mod, "UNDO_DIR", str(tmp_path / "undo"))
    # argparse calls sys.exit on error
    import pytest
    with pytest.raises(SystemExit):
        rename_plex.main([])
