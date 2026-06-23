# Backlog

## In progress

- **Rename all drives to consistent S##E##.** The renamer operates on any folders
  you give it. The library is spread across **P, Q, R, N** (each with `TV Shows`
  and `TV Shows Matteo`). The first batch covered P (and some R); Q and N copies
  still have old names. Run the web UI with all roots pasted in (one per line):
  ```
  P:\TV Shows
  P:\TV Shows Matteo
  Q:\TV Shows
  Q:\TV Shows Matteo
  R:\TV Shows
  R:\TV Shows Matteo
  N:\TV Shows
  N:\TV Shows Matteo
  ```
  Recommend one drive at a time, high-confidence tier first.

## Built — separate tool

### Cross-drive duplicate cleanup  *(requested + built 2026-06-23)*

**SHIPPED:** finder (`dedupe.py`) + **reversible quarantine** removal
(`quarantine.py`) + web UI at **`/dedupe`** (scan → per-group keeper radio +
approve checkbox → streamed quarantine + done banner, with **in-app Undo** and
**Empty quarantine** (permanent free-space) buttons). Verified in-browser on real
data (P+R: 82 duplicate episodes, 151 GB reclaimable). Removal moves copies to a
same-drive `_plex_renamer_trash` folder (instant, fully reversible) instead of
the Recycle Bin, so undo never depends on the OS bin. Each batch logged to
`undo_logs/removed-NNNN.json`; `empty_quarantine` permanently deletes to free
space. ~18 tests across the modules + endpoints.

**Problem.** The TV library is accidentally duplicated across physical drives.
A scan on 2026-06-23 found **203 of 3,412 shows present on more than one drive**
(199 on two drives, 4 on three) — overwhelmingly **P↔R**, some **N↔P**. These are
erroneous duplicates (NOT intentional mirrors/backups/pool), so one copy of each
duplicated episode should be removed.

**Why after renaming.** Once every drive uses canonical `S##E##`, a duplicate
episode is simply the same `(library, show, season, episode)` appearing on more
than one drive. Without consistent naming the match is fuzzy and error-prone, so
**run the renamer across all drives first**, then dedupe.

**Status:** finder logic BUILT (`dedupe.py` + `tests/test_dedupe.py`, read-only).
A real scan on 2026-06-23 found **1,258 cross-drive duplicate episodes,
~2 TB reclaimable**. Remaining: review UI + Recycle-Bin removal engine + undo.

**Design (mirror the renamer's safety model):**
1. ✅ **Scan** all drives, group video files by `(library, show, S##E##)`. Works
   even pre-rename (parses messy names via `scanner.parse`). `dedupe.find_duplicates`
   returns groups with per-copy drive/size/resolution + a suggested keeper.
2. **Review UI** — for each duplicate set show every copy with its **drive, full
   path, file size, resolution** (parse `1080p/2160p` from name), and a suggested
   "keep" pick. Per-group radio to choose which copy to keep; bulk policies.
3. **Apply** — move the non-kept copies to the **Windows Recycle Bin** (via
   `send2trash`), never a hard delete, so it's recoverable. Log every move.
4. **Undo** — restore from the log / Recycle Bin.

**Decisions:**
- **Keep-which-copy = MANUAL per group (decided 2026-06-23).** The user must
  approve which copy to keep for each duplicate set; nothing is removed without an
  explicit per-group selection. The tool *suggests* a default (highest resolution,
  tie-break largest file) purely to speed review — it is a pre-selection, never an
  auto-action.

**Open decisions (resolve before building):**
- Near-duplicates: same episode at different quality (1080p vs 2160p) — treat as
  duplicate (keep best) or keep both? Likely keep-best by default, flag for review.
- Partial overlap: a show on two drives where the *episode sets differ* (each has
  some the other lacks) is NOT a full duplicate — surface as "merge candidate",
  don't auto-delete.
- Whether to also consolidate the kept copies onto one drive (a "move/merge" mode)
  — probably out of scope for v1 (dedupe only; manual consolidation later).

**Safety bar:** this DELETES files. Recycle-Bin only, full review before any
action, dry-run default, complete undo log. Same gates as the renamer (ruff +
pytest, tests for the grouping logic and the Recycle-Bin move path with `send2trash`
mocked).

## Done

- Renamer core: parser, web UI (scan/approve/bulk/streamed-apply/undo), CLI,
  TMDB+TheTVDB daily lookup, multi-folder scan, append-only audit log.
- CI: GitHub Actions (ruff + pytest + coverage) on py3.11–3.13.
