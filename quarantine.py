#!/usr/bin/env python3
"""
quarantine.py — Reversible removal of duplicate copies.

Removing a duplicate is a two-step, fully in-app-reversible flow:

  1. **Quarantine** — move each approved copy to a hidden ``_plex_renamer_trash``
     folder ON THE SAME DRIVE. Same-volume moves are instant (a rename, no copy),
     and every move is logged to ``undo_logs/removed-<n>.json``.
  2. **Empty** — permanently delete the quarantined files to actually free the
     space, once you're confident. This is the only irreversible step.

``undo_last`` restores the most recent batch (moves the files back). No external
dependency — recovery never relies on the OS Recycle Bin.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

UNDO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "undo_logs")
QUARANTINE_DIRNAME = "_plex_renamer_trash"


@dataclass
class MoveResult:
    src: str
    dst: str
    ok: bool
    error: str = ""


def available() -> bool:
    """Always available — no external dependency."""
    return True


def _quarantine_root(path: str) -> str:
    drive = os.path.splitdrive(os.path.abspath(path))[0]   # e.g. "R:"
    return os.path.join(drive + os.sep, QUARANTINE_DIRNAME)


def _active_logs() -> list[str]:
    if not os.path.isdir(UNDO_DIR):
        return []
    return sorted(
        os.path.join(UNDO_DIR, f) for f in os.listdir(UNDO_DIR)
        if f.startswith("removed-") and f.endswith(".json")
    )


def _next_batch() -> str:
    n = 1 + len(_active_logs())
    return f"removed-{n:04d}"


def _move_one(src: str, batch: str, idx: int) -> MoveResult:
    if not os.path.exists(src):
        return MoveResult(src, "", False, "file missing")
    qdir = os.path.join(_quarantine_root(src), batch)
    try:
        os.makedirs(qdir, exist_ok=True)
        dst = os.path.join(qdir, f"{idx:05d}__{os.path.basename(src)}")
        os.replace(src, dst)   # same drive -> instant; dst is unique
        return MoveResult(src, dst, True)
    except OSError as e:
        return MoveResult(src, "?", False, str(e))


def _write_log(done: list[dict], batch: str) -> str | None:
    if not done:
        return None
    os.makedirs(UNDO_DIR, exist_ok=True)
    path = os.path.join(UNDO_DIR, f"{batch}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(done, fh, indent=2)
    return path


def quarantine_files(items: list[dict]) -> tuple[list[MoveResult], str | None]:
    """Move each ``{"path":..., "size":...?}`` item to quarantine. Non-streaming."""
    batch = _next_batch()
    results = [_move_one(it["path"], batch, i) for i, it in enumerate(items, 1)]
    ts = datetime.now(UTC).isoformat()
    done = [{"from": r.src, "to": r.dst, "ts": ts,
             "size": next((it.get("size") for it in items if it["path"] == r.src), None)}
            for r in results if r.ok]
    return results, _write_log(done, batch)


def quarantine_stream(items: list[dict]):
    """Generator: quarantine items one at a time, yielding progress per file,
    then a final summary. One log per batch."""
    batch = _next_batch()
    total = len(items)
    ts = datetime.now(UTC).isoformat()
    results: list[MoveResult] = []
    done: list[dict] = []
    for i, it in enumerate(items, 1):
        r = _move_one(it["path"], batch, i)
        results.append(r)
        if r.ok:
            done.append({"from": r.src, "to": r.dst, "ts": ts, "size": it.get("size")})
        yield {"i": i, "total": total, "ok": r.ok,
               "name": os.path.basename(r.src), "error": r.error}
    log = _write_log(done, batch)
    moved = sum(d["size"] or 0 for d in done)
    yield {"done": True,
           "removed": sum(1 for r in results if r.ok),
           "failed": [{"name": os.path.basename(r.src), "error": r.error}
                      for r in results if not r.ok],
           "moved_bytes": moved,
           "log": os.path.basename(log) if log else None}


def undo_last() -> tuple[list[MoveResult], str | None]:
    """Restore the most recent quarantine batch (move files back)."""
    logs = _active_logs()
    if not logs:
        return [], None
    log = logs[-1]
    with open(log, encoding="utf-8") as fh:
        pairs = json.load(fh)
    results: list[MoveResult] = []
    for p in reversed(pairs):
        src, dst = p["to"], p["from"]   # restore direction
        try:
            if not os.path.exists(src):
                results.append(MoveResult(src, dst, False, "quarantined file missing"))
                continue
            if os.path.exists(dst):
                results.append(MoveResult(src, dst, False, "original name taken"))
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)
            results.append(MoveResult(src, dst, True))
        except OSError as e:
            results.append(MoveResult(src, dst, False, str(e)))
    if all(r.ok for r in results):
        os.rename(log, log + ".undone")
    return results, log


def quarantine_stats() -> dict:
    """How much is sitting in quarantine, counted from the active batch logs.

    Counts log ENTRIES (and their recorded sizes) rather than probing each file's
    existence — a per-file ``os.path.exists`` over quarantine spread across slow
    network drives made page render take ~50s. Emptying renames a batch log to
    ``.emptied`` (see ``empty_stream``), so a cleared batch drops out here
    automatically."""
    files = bytes_ = 0
    logs = _active_logs()
    for log in logs:
        try:
            with open(log, encoding="utf-8") as fh:
                for p in json.load(fh):
                    files += 1
                    bytes_ += p.get("size") or 0
        except (OSError, json.JSONDecodeError):
            continue
    return {"files": files, "bytes": bytes_, "batches": len(logs)}


def empty_stream(items: list[dict] | None = None):
    """Generator: permanently delete quarantined files one at a time, yielding
    per-file progress then a final summary. ``os.remove`` is tried directly (no
    ``os.path.exists`` probe first) so it stays responsive over network drives; a
    file that's already gone counts as freed, not an error. A batch whose files
    all deleted cleanly has its log renamed ``.emptied`` (no longer undoable)."""
    logs = _active_logs()
    entries: list[tuple[str, dict]] = []
    for log in logs:
        try:
            with open(log, encoding="utf-8") as fh:
                for p in json.load(fh):
                    entries.append((log, p))
        except (OSError, json.JSONDecodeError):
            continue

    total = len(entries)
    removed = freed = 0
    log_ok = {log: True for log in logs}
    for i, (log, p) in enumerate(entries, 1):
        try:
            os.remove(p["to"])
            removed += 1
            freed += p.get("size") or 0
        except FileNotFoundError:
            pass                       # already gone — treat as handled
        except OSError:
            log_ok[log] = False
        yield {"i": i, "total": total}

    for log, ok in log_ok.items():
        if ok:
            try:
                os.rename(log, log + ".emptied")
            except OSError:
                pass
    _cleanup_empty_dirs()
    yield {"done": True, "removed": removed, "freed_bytes": freed,
           "quarantine": quarantine_stats()}


def empty_quarantine() -> dict:
    """Non-streaming convenience (used by tests/CLI): run ``empty_stream`` to
    completion and return its final ``{removed, freed_bytes}`` summary."""
    final = {"removed": 0, "freed_bytes": 0}
    for ev in empty_stream():
        if ev.get("done"):
            final = {"removed": ev["removed"], "freed_bytes": ev["freed_bytes"]}
    return final


def _cleanup_empty_dirs() -> None:
    """Remove now-empty quarantine batch folders and roots we can see."""
    seen_roots = set()
    for log in [*_active_logs(),
                *(p for p in _all_logs() if p.endswith(".emptied"))]:
        try:
            with open(log, encoding="utf-8") as fh:
                for p in json.load(fh):
                    seen_roots.add(os.path.dirname(os.path.dirname(p["to"])))
        except (OSError, json.JSONDecodeError):
            continue
    for root in seen_roots:
        for dirpath, _dirs, _files in os.walk(root, topdown=False):
            try:
                os.rmdir(dirpath)   # only succeeds if empty
            except OSError:
                pass


def _all_logs() -> list[str]:
    if not os.path.isdir(UNDO_DIR):
        return []
    return [os.path.join(UNDO_DIR, f) for f in os.listdir(UNDO_DIR)
            if f.startswith("removed-")]
