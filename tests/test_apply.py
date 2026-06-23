"""Apply + undo engine tests — the irreversible part, so it gets the coverage."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apply as apply_mod  # noqa: E402


def _touch(d, name):
    path = os.path.join(d, name)
    open(path, "w").close()
    return path


def test_rename_and_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(apply_mod, "UNDO_DIR", str(tmp_path / "undo"))
    src = _touch(str(tmp_path), "old.mkv")
    results, undo = apply_mod.apply_renames([{"path": src, "new_name": "new - S01E01.mkv"}])
    assert results[0].ok
    assert os.path.exists(os.path.join(str(tmp_path), "new - S01E01.mkv"))
    assert undo and os.path.exists(undo)

    # undo restores the original
    rev = apply_mod.undo_batch(undo)
    assert all(r.ok for r in rev)
    assert os.path.exists(src)
    assert not os.path.exists(os.path.join(str(tmp_path), "new - S01E01.mkv"))


def test_never_clobbers_existing_target(tmp_path, monkeypatch):
    monkeypatch.setattr(apply_mod, "UNDO_DIR", str(tmp_path / "undo"))
    src = _touch(str(tmp_path), "a.mkv")
    _touch(str(tmp_path), "taken.mkv")
    results, _ = apply_mod.apply_renames([{"path": src, "new_name": "taken.mkv"}])
    assert not results[0].ok
    assert results[0].error == "target exists"
    assert os.path.exists(src)  # untouched


def test_rejects_path_in_new_name(tmp_path, monkeypatch):
    monkeypatch.setattr(apply_mod, "UNDO_DIR", str(tmp_path / "undo"))
    src = _touch(str(tmp_path), "a.mkv")
    results, _ = apply_mod.apply_renames([{"path": src, "new_name": "../escape.mkv"}])
    assert not results[0].ok
    assert os.path.exists(src)


def test_master_log_records_every_rename(tmp_path, monkeypatch):
    monkeypatch.setattr(apply_mod, "UNDO_DIR", str(tmp_path / "undo"))
    a = _touch(str(tmp_path), "a.mkv")
    b = _touch(str(tmp_path), "b.mkv")
    apply_mod.apply_renames([{"path": a, "new_name": "a - S01E01.mkv"},
                             {"path": b, "new_name": "b - S01E02.mkv"}])
    master = apply_mod.master_log_path()
    assert os.path.exists(master)
    lines = [json.loads(x) for x in open(master, encoding="utf-8").read().splitlines()]
    assert len(lines) == 2
    assert {os.path.basename(rec["from"]) for rec in lines} == {"a.mkv", "b.mkv"}
    assert all("ts" in rec and "batch" in rec for rec in lines)


def test_missing_source_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(apply_mod, "UNDO_DIR", str(tmp_path / "undo"))
    results, undo = apply_mod.apply_renames(
        [{"path": str(tmp_path / "ghost.mkv"), "new_name": "x.mkv"}])
    assert not results[0].ok
    assert undo is None  # nothing succeeded -> no log written
