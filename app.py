#!/usr/bin/env python3
"""
app.py — Local web UI for reviewing and applying Plex episode renames.

Run:
    pip install -r requirements.txt
    python app.py                      # opens on http://127.0.0.1:5055
    (optional) set TMDB_API_KEY=...     # enables daily-show episode lookup

Flow: enter a directory -> Scan -> review proposals (approve per-row, edit the
target name inline, or bulk-approve a confidence tier) -> Apply. Every applied
batch is reversible from the Undo button.

This binds to localhost only and operates on the local filesystem — it is a
single-user desktop tool, not a public service.
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import apply as apply_mod
import dedupe
import logs as logs_mod
import metadata
import quarantine
import scanner

# Load API keys (TVDB_API_KEY / TMDB_API_KEY / METADATA_PROVIDER) from a local
# .env file so they don't have to be set as Windows env vars. .env is gitignored.
load_dotenv()

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html", has_key=metadata.have_key(),
                           provider=metadata.provider_name())


@app.get("/dedupe")
def dedupe_page():
    return render_template("dedupe.html", quarantine=quarantine.quarantine_stats())


@app.get("/logs")
def logs_page():
    return render_template("logs.html")


@app.get("/api/logs")
def api_logs():
    return jsonify(logs_mod.read_logs())


def _parse_dirs(raw: str) -> list[str]:
    """Split a textarea/field into one or more directory paths (newline or ';')."""
    parts = [d.strip().strip('"') for d in raw.replace(";", "\n").splitlines()]
    seen, out = set(), []
    for d in parts:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


@app.post("/api/scan")
def api_scan():
    data = request.get_json(force=True)
    dirs = _parse_dirs(data.get("directory") or "")
    if not dirs:
        return jsonify(error="Enter at least one directory."), 400
    bad = [d for d in dirs if not os.path.isdir(d)]
    if bad:
        return jsonify(error="Not a directory: " + ", ".join(repr(d) for d in bad)), 400
    forced = data.get("forced_season")
    forced = int(forced) if forced not in (None, "", "auto") else None

    props = []
    for d in dirs:
        props.extend(scanner.scan(d, forced_season=forced))
    rows = [p.to_dict() for p in props if p.changed or p.kind in ("extra", "none")]

    summary = {
        "total": len(rows),
        "high": sum(1 for r in rows if r["confidence"] == "high" and r["changed"]),
        "medium": sum(1 for r in rows if r["confidence"] == "medium" and r["changed"]),
        "guess": sum(1 for r in rows if r["confidence"] == "guess" and r["changed"]),
        "date": sum(1 for r in rows if r["kind"] == "date"),
        "extra": sum(1 for r in rows if r["kind"] == "extra"),
        "none": sum(1 for r in rows if r["kind"] == "none"),
    }
    return jsonify(rows=rows, summary=summary)


@app.post("/api/lookup")
def api_lookup():
    """Resolve a file to a real S##E## via the metadata provider — by air date
    (daily shows) or by absolute episode number (e.g. DVD "episode 61")."""
    data = request.get_json(force=True)
    show = data.get("show", "")
    if not metadata.have_key():
        return jsonify(error="No metadata API key set."), 400

    if data.get("airdate"):                         # daily show -> by air date
        hit = metadata.lookup_by_airdate(show, data["airdate"])
        corrected, exists = bool(hit), bool(hit)
    elif data.get("season") is not None and data.get("episode") is not None:
        hit = metadata.validate_episode(show, int(data["season"]), int(data["episode"]))
        corrected = bool(hit and hit.get("corrected"))
        exists = bool(hit and hit.get("exists"))
    elif data.get("absolute") is not None:          # explicit absolute number
        hit = metadata.lookup_by_absolute(show, int(data["absolute"]))
        corrected, exists = bool(hit), bool(hit)
    else:
        hit = None
        corrected = exists = False

    if not hit:
        return jsonify(found=False, exists=False)
    stem, ext = os.path.splitext(os.path.basename(data.get("path", "")))
    new_name = scanner.build_episode_name(stem, ext, hit["season"], hit["episode"],
                                          data.get("episode_end"))
    return jsonify(found=True, exists=exists, corrected=corrected,
                   season=hit["season"], episode=hit["episode"], new_name=new_name)


@app.post("/api/apply")
def api_apply():
    """Stream per-file progress as newline-delimited JSON, then a final summary.
    The client reads it live to drive a progress bar + completion banner."""
    items = request.get_json(force=True).get("items", [])

    @stream_with_context
    def generate():
        for event in apply_mod.apply_stream(items):
            yield json.dumps(event) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


@app.post("/api/undo")
def api_undo():
    results, log = apply_mod.undo_last()
    if not log:
        return jsonify(error="Nothing to undo."), 400
    return jsonify(reverted=sum(1 for r in results if r.ok),
                   failed=sum(1 for r in results if not r.ok),
                   log=os.path.basename(log))


@app.get("/api/dedupe/drives")
def api_dedupe_drives():
    """List local drives + the media-library folders on each, so the dedupe
    page can offer a click-to-select picker instead of typing paths."""
    return jsonify(drives=dedupe.list_drive_libraries())


@app.post("/api/dedupe/scan")
def api_dedupe_scan():
    """Find cross-drive duplicate episodes. Read-only."""
    data = request.get_json(force=True)
    dirs = _parse_dirs(data.get("directory") or "")
    if not dirs:
        return jsonify(error="Enter at least one library folder."), 400
    bad = [d for d in dirs if not os.path.isdir(d)]
    if bad:
        return jsonify(error="Not a directory: " + ", ".join(repr(d) for d in bad)), 400

    groups = dedupe.find_duplicates(dirs, cross_drive_only=True)
    rows = [g.to_dict() for g in groups]
    summary = {
        "groups": len(rows),
        "copies": sum(len(g["copies"]) for g in rows),
        "reclaimable_bytes": sum(g["reclaimable_bytes"] for g in rows),
    }
    return jsonify(groups=rows, summary=summary, quarantine=quarantine.quarantine_stats())


@app.post("/api/dedupe/remove")
def api_dedupe_remove():
    """Stream quarantine-move progress (reversible), then a final summary."""
    items = request.get_json(force=True).get("items", [])

    @stream_with_context
    def generate():
        for event in quarantine.quarantine_stream(items):
            yield json.dumps(event) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


@app.post("/api/dedupe/undo")
def api_dedupe_undo():
    """Restore the most recent quarantine batch (in-app undo)."""
    results, log = quarantine.undo_last()
    if not log:
        return jsonify(error="Nothing to undo."), 400
    return jsonify(restored=sum(1 for r in results if r.ok),
                   failed=sum(1 for r in results if not r.ok),
                   log=os.path.basename(log),
                   quarantine=quarantine.quarantine_stats())


@app.post("/api/dedupe/empty")
def api_dedupe_empty():
    """Permanently delete everything in quarantine (frees space, irreversible)."""
    result = quarantine.empty_quarantine()
    result["quarantine"] = quarantine.quarantine_stats()
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=True)
