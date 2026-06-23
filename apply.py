#!/usr/bin/env python3
"""
apply.py — Perform approved renames and record an undo log.

Every applied batch is written to ``undo_logs/rename-<n>.json`` as a list of
{from, to} pairs. ``undo_last`` reverses the most recent batch. Renames are
refused if the target already exists (never clobber), and both names must live
in the same directory (this tool renames in place, it does not move files).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime

UNDO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "undo_logs")

# Append-only audit trail of EVERY successful rename, across all batches. Never
# rotated or deleted. If a per-batch undo log is ever lost, undo can still be
# reconstructed from this file. Derived from UNDO_DIR at call time so tests that
# redirect UNDO_DIR don't touch the real one.
MASTER_LOG_NAME = "_all_renames.jsonl"


def master_log_path() -> str:
    return os.path.join(UNDO_DIR, MASTER_LOG_NAME)


@dataclass
class RenameResult:
    src: str
    dst: str
    ok: bool
    error: str = ""


def _safe_target(src: str, new_name: str) -> str:
    """Resolve the destination path, guarding against path escapes."""
    if not new_name or new_name != os.path.basename(new_name):
        raise ValueError(f"new name must be a bare filename, got {new_name!r}")
    return os.path.join(os.path.dirname(src), new_name)


def _rename_one(it: dict) -> RenameResult:
    """Rename a single ``{"path": ..., "new_name": ...}`` item (with guards)."""
    src = it["path"]
    try:
        dst = _safe_target(src, it["new_name"])
        if os.path.abspath(src) == os.path.abspath(dst):
            return RenameResult(src, dst, False, "no change")
        if not os.path.exists(src):
            return RenameResult(src, dst, False, "source missing")
        if os.path.exists(dst):
            return RenameResult(src, dst, False, "target exists")
        os.rename(src, dst)
        return RenameResult(src, dst, True)
    except (OSError, ValueError, KeyError) as e:
        return RenameResult(src, it.get("new_name", "?"), False, str(e))


def _write_undo(done: list[dict]) -> str | None:
    """Persist a batch's old->new pairs; returns the log path (None if empty).
    Also appends every pair to the never-rotated master audit log."""
    if not done:
        return None
    os.makedirs(UNDO_DIR, exist_ok=True)
    n = 1 + len([f for f in os.listdir(UNDO_DIR)
                 if f.startswith("rename-") and f.endswith(".json")])
    undo_path = os.path.join(UNDO_DIR, f"rename-{n:04d}.json")
    with open(undo_path, "w", encoding="utf-8") as fh:
        json.dump(done, fh, indent=2)
    ts = datetime.now(UTC).isoformat()
    with open(master_log_path(), "a", encoding="utf-8") as fh:
        for pair in done:
            fh.write(json.dumps({"ts": ts, "batch": os.path.basename(undo_path), **pair}) + "\n")
    return undo_path


def apply_renames(items: list[dict]) -> tuple[list[RenameResult], str | None]:
    """Rename each item and return (results, undo_log_path). One undo log per
    batch, written only if at least one rename succeeded."""
    results = [_rename_one(it) for it in items]
    done = [{"from": r.src, "to": r.dst} for r in results if r.ok]
    return results, _write_undo(done)


def apply_stream(items: list[dict]):
    """Generator: rename items one at a time, yielding a progress dict per file,
    then a final summary dict. Writes ONE undo log at the end (same semantics as
    ``apply_renames``) so a streamed batch is undone in a single click."""
    total = len(items)
    results: list[RenameResult] = []
    done: list[dict] = []
    for i, it in enumerate(items, 1):
        r = _rename_one(it)
        results.append(r)
        if r.ok:
            done.append({"from": r.src, "to": r.dst})
        yield {"i": i, "total": total, "ok": r.ok,
               "name": os.path.basename(r.dst if r.ok else r.src),
               "error": r.error}
    undo = _write_undo(done)
    yield {"done": True,
           "applied": sum(1 for r in results if r.ok),
           "failed": [{"src": os.path.basename(r.src), "error": r.error}
                      for r in results if not r.ok],
           "undo_log": os.path.basename(undo) if undo else None}


def list_undo_logs() -> list[str]:
    if not os.path.isdir(UNDO_DIR):
        return []
    return sorted(
        (os.path.join(UNDO_DIR, f) for f in os.listdir(UNDO_DIR) if f.endswith(".json")),
        reverse=True,
    )


def undo_batch(log_path: str) -> list[RenameResult]:
    """Reverse a batch: rename every ``to`` back to its ``from``."""
    with open(log_path, encoding="utf-8") as fh:
        pairs = json.load(fh)
    results: list[RenameResult] = []
    for p in reversed(pairs):
        src, dst = p["to"], p["from"]   # reverse direction
        try:
            if not os.path.exists(src):
                results.append(RenameResult(src, dst, False, "renamed file missing"))
                continue
            if os.path.exists(dst):
                results.append(RenameResult(src, dst, False, "original name taken"))
                continue
            os.rename(src, dst)
            results.append(RenameResult(src, dst, True))
        except OSError as e:
            results.append(RenameResult(src, dst, False, str(e)))
    if all(r.ok for r in results):
        os.rename(log_path, log_path + ".undone")
    return results


def undo_last() -> tuple[list[RenameResult], str | None]:
    logs = [p for p in list_undo_logs() if not p.endswith(".undone")]
    if not logs:
        return [], None
    return undo_batch(logs[0]), logs[0]
