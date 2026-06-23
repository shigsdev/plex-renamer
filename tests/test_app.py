"""Web-layer tests — exercise the Flask endpoints through the test client,
including the streamed /api/apply progress protocol. Undo writes are redirected
to a temp dir so the real undo_logs/ is never touched."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import apply as apply_mod  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(apply_mod, "UNDO_DIR", str(tmp_path / "undo"))
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


@pytest.fixture
def library(tmp_path):
    """A small show with three rename-needing files + one already-correct."""
    d = tmp_path / "lib" / "MyShow" / "Season 1"
    d.mkdir(parents=True)
    for name in ["MyShow.1x01.mkv", "MyShow.1x02.mkv", "MyShow.1x03.mkv",
                 "MyShow S01E04.mkv", "Gag Reel.mkv"]:
        (d / name).write_text("")
    return str(tmp_path / "lib")


def _stream_lines(resp):
    return [json.loads(x) for x in resp.get_data(as_text=True).splitlines() if x.strip()]


# ---- scan -----------------------------------------------------------------

def test_scan_returns_rows_and_summary(client, library):
    r = client.post("/api/scan", json={"directory": library})
    assert r.status_code == 200
    data = r.get_json()
    assert data["summary"]["high"] == 3          # three 1x0N files
    assert data["summary"]["extra"] == 1          # Gag Reel
    assert any(row["new_name"] and "S01E01" in row["new_name"] for row in data["rows"])


def test_scan_rejects_missing_directory(client):
    r = client.post("/api/scan", json={"directory": "Z:/definitely/not/here"})
    assert r.status_code == 400
    assert "Not a directory" in r.get_json()["error"]


def test_scan_rejects_empty(client):
    r = client.post("/api/scan", json={"directory": "  \n ; "})
    assert r.status_code == 400


def test_scan_accepts_multiple_dirs(client, tmp_path, library):
    other = tmp_path / "lib2" / "OtherShow" / "Season 2"
    other.mkdir(parents=True)
    (other / "OtherShow.2x05.mkv").write_text("")
    raw = f"{library}\n{tmp_path / 'lib2'}"
    r = client.post("/api/scan", json={"directory": raw})
    data = r.get_json()
    shows = {row["show"] for row in data["rows"]}
    assert {"MyShow", "OtherShow"} <= shows


# ---- apply (streamed) + undo ----------------------------------------------

def test_apply_streams_progress_then_done(client, library):
    rows = client.post("/api/scan", json={"directory": library}).get_json()["rows"]
    items = [{"path": r["path"], "new_name": r["new_name"]} for r in rows if r["changed"]]
    resp = client.post("/api/apply", json={"items": items})
    assert resp.mimetype == "application/x-ndjson"
    events = _stream_lines(resp)
    progress = [e for e in events if not e.get("done")]
    final = [e for e in events if e.get("done")][0]
    # one progress event per file, counting up to the total
    assert [e["i"] for e in progress] == list(range(1, len(items) + 1))
    assert all(e["total"] == len(items) for e in progress)
    assert final["applied"] == len(items)
    assert final["undo_log"]
    # files actually renamed on disk
    season = os.path.join(library, "MyShow", "Season 1")
    assert any(n.endswith("S01E01.mkv") for n in os.listdir(season))


def test_apply_then_undo_restores(client, library):
    rows = client.post("/api/scan", json={"directory": library}).get_json()["rows"]
    items = [{"path": r["path"], "new_name": r["new_name"]} for r in rows if r["changed"]]
    # consume the streamed response so the generator actually runs the renames
    client.post("/api/apply", json={"items": items}).get_data()
    u = client.post("/api/undo", json={})
    assert u.status_code == 200
    assert u.get_json()["reverted"] == len(items)
    season = os.path.join(library, "MyShow", "Season 1")
    assert "MyShow.1x01.mkv" in os.listdir(season)  # original name back


def test_undo_with_nothing_to_undo(client):
    r = client.post("/api/undo", json={})
    assert r.status_code == 400


# ---- lookup ---------------------------------------------------------------

def test_lookup_without_key_returns_400(client, monkeypatch):
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    monkeypatch.delenv("TVDB_API_KEY", raising=False)
    r = client.post("/api/lookup", json={"show": "Dateline", "airdate": "2016-09-09"})
    assert r.status_code == 400


# ---- page render ----------------------------------------------------------

def test_index_renders(client):
    html = client.get("/").get_data(as_text=True)
    assert "Plex Renamer" in html
    assert "window.PROVIDER" in html
