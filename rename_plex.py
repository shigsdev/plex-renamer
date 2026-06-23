#!/usr/bin/env python3
"""
rename_plex.py — Command-line interface to the Plex renamer.

The web UI (``python app.py``) is the primary, interactive way to review and
apply renames. This CLI is for quick previews and headless/scripted runs. It
shares the exact same parsing (``scanner.py``) and rename/undo engine
(``apply.py``) as the web UI, so results are identical.

Usage:
    python rename_plex.py "P:/TV Shows"                 # preview only
    python rename_plex.py "P:/TV Shows" --apply         # rename (asks y/N)
    python rename_plex.py "P:/TV Shows" --apply --yes   # rename, no prompt
    python rename_plex.py "P:/TV Shows" --min high       # only high-confidence
    python rename_plex.py "P:/TV Shows" --season 2       # force season number
    python rename_plex.py --undo                         # revert the last batch
"""

from __future__ import annotations

import argparse
import sys

import apply as apply_mod
import scanner

# Force UTF-8 so box-drawing / accented titles print on Windows cp1252 consoles.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

_RANK = {"high": 3, "medium": 2, "guess": 1}
_MARK = {"high": "  ", "medium": "? ", "guess": "⚠ "}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Normalize TV filenames to Plex S##E##.")
    ap.add_argument("directory", nargs="*", help="One or more root directories to scan (recurses).")
    ap.add_argument("--apply", action="store_true", help="Perform the renames.")
    ap.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    ap.add_argument("--season", type=int, default=None, help="Force this season number.")
    ap.add_argument("--min", choices=("high", "medium", "guess"), default="guess",
                    help="Minimum confidence to include (default: guess = all).")
    ap.add_argument("--undo", action="store_true", help="Revert the most recent batch and exit.")
    args = ap.parse_args(argv)

    if args.undo:
        results, log = apply_mod.undo_last()
        if not log:
            print("Nothing to undo.")
            return 0
        ok = sum(1 for r in results if r.ok)
        print(f"Reverted {ok}/{len(results)} from {log}.")
        return 0

    if not args.directory:
        ap.error("at least one directory is required (or use --undo)")

    floor = _RANK[args.min]
    props = []
    for d in args.directory:
        props.extend(scanner.scan(d, forced_season=args.season))
    changes = [p for p in props if p.changed and _RANK.get(p.confidence, 0) >= floor]

    if not changes:
        print("No renames to propose at this confidence level.")
        return 0

    by_show: dict[str, list] = {}
    for p in changes:
        by_show.setdefault(p.show, []).append(p)

    for show in sorted(by_show):
        print(f"\n{show}")
        for p in by_show[show]:
            print(f"  {_MARK.get(p.confidence, '  ')}{p.old_name}")
            print(f"      -> {p.new_name}   [{p.confidence}: {p.note}]")

    n_guess = sum(1 for p in changes if p.confidence in ("medium", "guess"))
    print(f"\n{len(changes)} rename(s); {n_guess} are best-guesses (marked ? / ⚠).")

    if not args.apply:
        print("Preview only. Re-run with --apply to perform these renames.")
        return 0

    if not args.yes:
        if input(f"Apply {len(changes)} rename(s)? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 0

    items = [{"path": p.path, "new_name": p.new_name} for p in changes]
    results, undo = apply_mod.apply_renames(items)
    ok = sum(1 for r in results if r.ok)
    fail = [r for r in results if not r.ok]
    print(f"\nRenamed {ok}. " + (f"{len(fail)} failed." if fail else ""))
    for r in fail:
        print(f"  FAILED {r.src}: {r.error}")
    if undo:
        print(f"Undo log: {undo}  (revert with: python rename_plex.py --undo)")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
