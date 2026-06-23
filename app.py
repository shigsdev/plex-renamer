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

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import apply as apply_mod
import metadata
import scanner

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html", has_key=metadata.have_key(),
                           provider=metadata.provider_name())


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
    """Upgrade a daily-show file to S##E## via TMDB air-date matching."""
    data = request.get_json(force=True)
    show = data.get("show", "")
    airdate = data.get("airdate", "")
    if not metadata.have_key():
        return jsonify(error="No TMDB_API_KEY set."), 400
    hit = metadata.lookup_by_airdate(show, airdate)
    if not hit:
        return jsonify(found=False)
    # Rebuild the name keeping any title text the date-name had.
    path = data.get("path", "")
    stem, ext = os.path.splitext(os.path.basename(path))
    new_name = scanner.build_episode_name(stem, ext, hit["season"], hit["episode"])
    return jsonify(found=True, season=hit["season"], episode=hit["episode"],
                   new_name=new_name)


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


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, debug=True)
