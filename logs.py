#!/usr/bin/env python3
"""
logs.py — Read the undo_logs/ directory into a screen-friendly activity history.

Every rename batch (``rename-NNNN.json``) and every quarantine removal batch
(``removed-NNNN.json``) is summarized into one record with its type, status,
file count, size, timestamp, and per-file entries. Read-only — this only
reports what happened; acting on it lives in apply.py / quarantine.py.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

UNDO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "undo_logs")
MASTER_LOG = "_all_renames.jsonl"


def _when(entries: list[dict], path: str) -> str:
    """Best timestamp: an entry's recorded ts, else the file's mtime."""
    for e in entries:
        if e.get("ts"):
            return e["ts"]
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=UTC).isoformat()
    except OSError:
        return ""


def _classify(fname: str) -> tuple[str, str] | None:
    """Return (type, status) for a log filename, or None to ignore it."""
    if fname.startswith("rename-"):
        kind = "rename"
    elif fname.startswith("removed-"):
        kind = "removal"
    else:
        return None
    if fname.endswith(".json"):
        status = "active"
    elif fname.endswith(".json.undone"):
        status = "undone" if kind == "rename" else "restored"
    elif fname.endswith(".json.emptied"):
        status = "emptied"
    else:
        return None
    return kind, status


def read_logs() -> dict:
    """Return {"batches": [...newest first...], "master": {...}}."""
    batches: list[dict] = []
    if os.path.isdir(UNDO_DIR):
        for fname in os.listdir(UNDO_DIR):
            info = _classify(fname)
            if not info:
                continue
            kind, status = info
            path = os.path.join(UNDO_DIR, fname)
            try:
                with open(path, encoding="utf-8") as fh:
                    entries = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            size = sum(e.get("size") or 0 for e in entries)
            batches.append({
                "id": fname,
                "type": kind,
                "status": status,
                "count": len(entries),
                "size": size,
                "when": _when(entries, path),
                "entries": [{
                    "from": os.path.basename(e.get("from", "")),
                    "to": os.path.basename(e.get("to", "")),
                    "size": e.get("size"),
                } for e in entries],
            })
    # newest first by id (rename-0007 > rename-0001), grouping is fine mixed
    batches.sort(key=lambda b: b["id"], reverse=True)

    master_count = 0
    master_path = os.path.join(UNDO_DIR, MASTER_LOG)
    if os.path.isfile(master_path):
        try:
            with open(master_path, encoding="utf-8") as fh:
                master_count = sum(1 for line in fh if line.strip())
        except OSError:
            master_count = 0

    return {"batches": batches, "master": {"total_renames": master_count}}
