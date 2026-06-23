#!/usr/bin/env python3
"""
trash.py — Send duplicate copies to the Recycle Bin (recoverable), with a log.

Removal is the destructive half of the dedupe tool, so it is deliberately gentle:
files are moved to the OS Recycle Bin via ``send2trash`` (NEVER a hard delete),
every batch is recorded to ``undo_logs/removed-<n>.json``, and a streaming API
yields per-file progress like the renamer's apply. Recovery is via the Recycle
Bin (Explorer → Restore); the log records exactly what was removed and from where.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

UNDO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "undo_logs")

try:
    from send2trash import send2trash
except ImportError:  # dependency optional until installed
    send2trash = None


@dataclass
class TrashResult:
    path: str
    ok: bool
    error: str = ""


def available() -> bool:
    return send2trash is not None


def _trash_one(path: str) -> TrashResult:
    if send2trash is None:
        return TrashResult(path, False, "send2trash not installed (pip install Send2Trash)")
    if not os.path.exists(path):
        return TrashResult(path, False, "file missing")
    try:
        send2trash(os.path.normpath(path))
        return TrashResult(path, True)
    except OSError as e:
        return TrashResult(path, False, str(e))


def _write_removal_log(done: list[dict]) -> str | None:
    if not done:
        return None
    os.makedirs(UNDO_DIR, exist_ok=True)
    n = 1 + len([f for f in os.listdir(UNDO_DIR)
                 if f.startswith("removed-") and f.endswith(".json")])
    path = os.path.join(UNDO_DIR, f"removed-{n:04d}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(done, fh, indent=2)
    return path


def trash_files(items: list[dict]) -> tuple[list[TrashResult], str | None]:
    """Trash each ``{"path": ..., "size": ...?}`` item. Returns (results, log)."""
    results = [_trash_one(it["path"]) for it in items]
    ts = datetime.now(UTC).isoformat()
    done = [{"path": r.path, "ts": ts,
             "size": next((it.get("size") for it in items if it["path"] == r.path), None)}
            for r in results if r.ok]
    return results, _write_removal_log(done)


def trash_stream(items: list[dict]):
    """Generator: trash items one at a time, yielding a progress dict per file,
    then a final summary. Writes one removal log at the end."""
    total = len(items)
    results: list[TrashResult] = []
    ts = datetime.now(UTC).isoformat()
    done: list[dict] = []
    for i, it in enumerate(items, 1):
        r = _trash_one(it["path"])
        results.append(r)
        if r.ok:
            done.append({"path": r.path, "ts": ts, "size": it.get("size")})
        yield {"i": i, "total": total, "ok": r.ok,
               "name": os.path.basename(r.path), "error": r.error}
    log = _write_removal_log(done)
    freed = sum(d["size"] or 0 for d in done)
    yield {"done": True,
           "removed": sum(1 for r in results if r.ok),
           "failed": [{"name": os.path.basename(r.path), "error": r.error}
                      for r in results if not r.ok],
           "freed_bytes": freed,
           "log": os.path.basename(log) if log else None}
