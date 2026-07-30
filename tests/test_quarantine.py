"""Quarantine engine tests — move (reversible), undo, empty, stats.

Quarantine puts files under <drive>:/_plex_renamer_trash. In tests the temp tree
is on one drive, so the quarantine root resolves under that same drive; we point
UNDO_DIR at a temp dir so real logs are never touched."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quarantine  # noqa: E402


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(quarantine, "UNDO_DIR", str(tmp_path / "undo"))
    # keep the quarantine folder inside tmp (same drive) instead of <drive>:/...
    qroot = str(tmp_path / "_q")
    monkeypatch.setattr(quarantine, "_quarantine_root", lambda path: qroot)


def _mkfile(d, name, content="xx"):
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(content)
    return str(p)


def test_quarantine_moves_and_logs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    a = _mkfile(tmp_path / "show", "a.mkv", "xxx")
    b = _mkfile(tmp_path / "show", "b.mkv", "xxxx")
    results, log = quarantine.quarantine_files(
        [{"path": a, "size": 3}, {"path": b, "size": 4}])
    assert all(r.ok for r in results)
    assert not os.path.exists(a) and not os.path.exists(b)   # moved out
    assert all(os.path.exists(r.dst) for r in results)        # now in quarantine
    assert log and os.path.exists(log)


def test_undo_restores(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    a = _mkfile(tmp_path / "show", "a.mkv")
    quarantine.quarantine_files([{"path": a, "size": 2}])
    assert not os.path.exists(a)
    results, log = quarantine.undo_last()
    assert all(r.ok for r in results)
    assert os.path.exists(a)            # original restored
    assert log.endswith("removed-0001.json")


def test_undo_with_nothing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    results, log = quarantine.undo_last()
    assert results == [] and log is None


def test_stats_reflects_quarantine(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    a = _mkfile(tmp_path / "show", "a.mkv")
    b = _mkfile(tmp_path / "show", "b.mkv")
    quarantine.quarantine_files([{"path": a, "size": 5}, {"path": b, "size": 7}])
    stats = quarantine.quarantine_stats()
    assert stats["files"] == 2
    assert stats["bytes"] == 12
    assert stats["batches"] == 1


def test_empty_stream_reports_progress_and_frees(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    a = _mkfile(tmp_path / "show", "a.mkv", "xxx")
    b = _mkfile(tmp_path / "show", "b.mkv", "xxxx")
    r, _ = quarantine.quarantine_files([{"path": a, "size": 3}, {"path": b, "size": 4}])
    dsts = [x.dst for x in r]
    events = list(quarantine.empty_stream())
    progress = [e for e in events if not e.get("done")]
    final = [e for e in events if e.get("done")][0]
    assert [e["i"] for e in progress] == [1, 2]          # one tick per file
    assert final["removed"] == 2 and final["freed_bytes"] == 7
    assert all(not os.path.exists(d) for d in dsts)      # gone for good
    assert final["quarantine"]["files"] == 0


def test_empty_stream_tolerates_already_missing_file(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    a = _mkfile(tmp_path / "show", "a.mkv", "xxx")
    r, _ = quarantine.quarantine_files([{"path": a, "size": 3}])
    os.remove(r[0].dst)                                   # vanish out-of-band
    final = [e for e in quarantine.empty_stream() if e.get("done")][0]
    assert final["removed"] == 0                          # nothing to delete...
    assert final["quarantine"]["files"] == 0             # ...but batch is cleared


def test_empty_is_permanent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    a = _mkfile(tmp_path / "show", "a.mkv")
    results, _ = quarantine.quarantine_files([{"path": a, "size": 9}])
    qpath = results[0].dst
    out = quarantine.empty_quarantine()
    assert out["removed"] == 1
    assert out["freed_bytes"] == 9
    assert not os.path.exists(qpath)                 # gone for good
    assert quarantine.quarantine_stats()["files"] == 0
    # emptied batch can no longer be undone
    res, log = quarantine.undo_last()
    assert log is None


def test_missing_source_is_reported(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    results, log = quarantine.quarantine_files([{"path": str(tmp_path / "ghost.mkv")}])
    assert not results[0].ok
    assert "missing" in results[0].error
    assert log is None


def test_stream_progress_then_summary(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    items = []
    for i in range(3):
        items.append({"path": _mkfile(tmp_path / "show", f"f{i}.mkv"), "size": 2})
    events = list(quarantine.quarantine_stream(items))
    progress = [e for e in events if not e.get("done")]
    final = [e for e in events if e.get("done")][0]
    assert [e["i"] for e in progress] == [1, 2, 3]
    assert final["removed"] == 3
    assert final["moved_bytes"] == 6
    assert final["log"]
