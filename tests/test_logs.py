"""Activity-log reader tests."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logs as logs_mod  # noqa: E402


def _write(d, name, entries):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        json.dump(entries, fh)


def test_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(logs_mod, "UNDO_DIR", str(tmp_path / "undo"))
    out = logs_mod.read_logs()
    assert out["batches"] == []
    assert out["master"]["total_renames"] == 0


def test_reads_rename_and_removal_batches(tmp_path, monkeypatch):
    d = str(tmp_path / "undo")
    monkeypatch.setattr(logs_mod, "UNDO_DIR", d)
    _write(d, "rename-0001.json", [{"from": "/x/a.mkv", "to": "/x/a - S01E01.mkv"}])
    _write(d, "rename-0002.json.undone", [{"from": "/x/b.mkv", "to": "/x/b - S01E02.mkv"}])
    _write(d, "removed-0001.json", [{"from": "/x/dup.mkv", "to": "/q/dup.mkv", "size": 100}])
    _write(d, "removed-0002.json.emptied", [{"from": "/x/old.mkv", "to": "/q/old.mkv", "size": 50}])

    out = logs_mod.read_logs()
    by_id = {b["id"]: b for b in out["batches"]}
    assert by_id["rename-0001.json"]["type"] == "rename"
    assert by_id["rename-0001.json"]["status"] == "active"
    assert by_id["rename-0002.json.undone"]["status"] == "undone"
    assert by_id["removed-0001.json"]["type"] == "removal"
    assert by_id["removed-0001.json"]["status"] == "active"
    assert by_id["removed-0001.json"]["size"] == 100
    assert by_id["removed-0002.json.emptied"]["status"] == "emptied"
    # entries are basenamed for display
    assert by_id["rename-0001.json"]["entries"][0]["to"] == "a - S01E01.mkv"


def test_newest_first(tmp_path, monkeypatch):
    d = str(tmp_path / "undo")
    monkeypatch.setattr(logs_mod, "UNDO_DIR", d)
    _write(d, "rename-0001.json", [{"from": "a", "to": "b"}])
    _write(d, "rename-0003.json", [{"from": "c", "to": "d"}])
    _write(d, "rename-0002.json", [{"from": "e", "to": "f"}])
    ids = [b["id"] for b in logs_mod.read_logs()["batches"]]
    assert ids == ["rename-0003.json", "rename-0002.json", "rename-0001.json"]


def test_master_log_counted_and_ignored_as_batch(tmp_path, monkeypatch):
    d = str(tmp_path / "undo")
    monkeypatch.setattr(logs_mod, "UNDO_DIR", d)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "_all_renames.jsonl"), "w", encoding="utf-8") as fh:
        fh.write('{"from":"a","to":"b"}\n{"from":"c","to":"d"}\n')
    out = logs_mod.read_logs()
    assert out["master"]["total_renames"] == 2
    assert out["batches"] == []   # master is not a batch


def test_api_logs_endpoint(tmp_path, monkeypatch):
    import app as appmod
    d = str(tmp_path / "undo")
    monkeypatch.setattr(logs_mod, "UNDO_DIR", d)
    _write(d, "rename-0001.json", [{"from": "/x/a.mkv", "to": "/x/a - S01E01.mkv"}])
    data = appmod.app.test_client().get("/api/logs").get_json()
    assert data["batches"][0]["id"] == "rename-0001.json"


def test_logs_page_renders():
    import app as appmod
    html = appmod.app.test_client().get("/logs").get_data(as_text=True)
    assert "Activity Log" in html
