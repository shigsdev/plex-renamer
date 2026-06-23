"""Recycle-Bin removal engine tests. send2trash is mocked (it deletes the temp
file so existence checks behave), so nothing real is ever trashed."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trash  # noqa: E402


def _mock_trash(monkeypatch, tmp_path, recorder=None):
    monkeypatch.setattr(trash, "UNDO_DIR", str(tmp_path / "undo"))

    def fake(path):
        if recorder is not None:
            recorder.append(path)
        os.remove(path)   # simulate move-to-bin

    monkeypatch.setattr(trash, "send2trash", fake)


def test_trash_files_moves_and_logs(tmp_path, monkeypatch):
    calls = []
    _mock_trash(monkeypatch, tmp_path, calls)
    a = tmp_path / "a.mkv"
    a.write_text("x")
    b = tmp_path / "b.mkv"
    b.write_text("x")
    results, log = trash.trash_files([{"path": str(a), "size": 1}, {"path": str(b), "size": 2}])
    assert all(r.ok for r in results)
    assert len(calls) == 2
    assert not a.exists() and not b.exists()
    assert log and os.path.exists(log)


def test_trash_missing_file_reported(tmp_path, monkeypatch):
    _mock_trash(monkeypatch, tmp_path)
    results, log = trash.trash_files([{"path": str(tmp_path / "ghost.mkv")}])
    assert not results[0].ok
    assert "missing" in results[0].error
    assert log is None   # nothing succeeded -> no log


def test_trash_unavailable_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(trash, "send2trash", None)
    f = tmp_path / "a.mkv"
    f.write_text("x")
    results, _ = trash.trash_files([{"path": str(f)}])
    assert not results[0].ok
    assert "not installed" in results[0].error
    assert f.exists()    # untouched when the dependency is missing


def test_trash_stream_progress_then_summary(tmp_path, monkeypatch):
    _mock_trash(monkeypatch, tmp_path)
    items = []
    for i in range(3):
        f = tmp_path / f"f{i}.mkv"
        f.write_text("xx")
        items.append({"path": str(f), "size": 2})
    events = list(trash.trash_stream(items))
    progress = [e for e in events if not e.get("done")]
    final = [e for e in events if e.get("done")][0]
    assert [e["i"] for e in progress] == [1, 2, 3]
    assert final["removed"] == 3
    assert final["freed_bytes"] == 6
    assert final["log"]
