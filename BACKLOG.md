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
- **Dedupe: also find same-drive duplicates (2026-06-26).** The scan was
  cross-drive-only (`find_duplicates(cross_drive_only=True)`), so two copies of a
  movie/episode within ONE drive never showed — the user's same-drive duplicate
  movies were invisible. Now the API defaults to finding ALL duplicates (within
  and across drives); a `/dedupe` checkbox "across different drives only"
  (`cross_drive_only`) narrows back to the accidental-mirror case. Also fixed two
  movie issues this exposed: (1) trailers/featurettes in a movie folder share the
  film's title+year and were grouped as a duplicate of the film — now skipped via
  `scanner._EXTRA_RE`; (2) the title cleaner stripped lone digits (for audio tags
  like "AAC 2 0"), collapsing "Die Hard 2" → "Die Hard" and merging different
  films — `movie_identity` now passes `keep_digits=True` so sequel numbers
  survive (also fixes "Deadpool 2" mislabeled "Deadpool"). Real scan of P:\Movies:
  31 groups → 29 after the Die Hard false-positive fix.
- **Dedupe: safer keeper when resolution is unknown (2026-06-25).** The keeper
  suggestion used to rank resolution-first, so a copy whose name omits "1080p"
  (resolution `?`) lost to a smaller copy that spelled it out — e.g. a 3 GB
  `The Crown_S01E04_Act of God.mp4` was marked for removal in favor of a 1 GB
  `...S01E04.1080p.H265.mkv`. Now: if any copy in a group has unknown resolution,
  `_suggest_keep` ranks by **size first** (keep the largest — the safer bet
  against discarding the better encode). Added an **ffprobe fallback**
  (`_resolution_from_file`) that reads the real video height when the name has no
  token — runs only on actual duplicate-group members, and only if ffprobe is
  installed (gracefully no-ops otherwise; the size-first rule still protects the
  larger file). Note: ffprobe is NOT currently installed on the user's machine,
  so `?` stays until they install ffmpeg/ffprobe — the keeper fix works regardless.
- **Dedupe: drive picker (2026-06-25).** `/dedupe` now auto-detects local drives
  (`GET /api/dedupe/drives` → `dedupe.list_drive_libraries`, Windows logical-drive
  bitmask with an A:..Z: fallback) and the media-library folders on each, and
  shows them as click-to-select checkboxes (per-library, with a drive-level
  select-all) instead of typing paths. Manual paste kept behind an "or paste
  folder paths manually" disclosure. Scans the checked **library** folders (not
  bare drive roots) so cross-drive matching still keys on the library name.
- **Dedupe: movies categorized at a movie level (2026-06-25).** Files under a
  `Movies`/`Films` library are no longer parsed as TV episodes (which mis-fired a
  fake `S##E##` from stray numbers in movie names). They're now identified by
  `(title, year)` via `scanner.is_movie_path` + `scanner.movie_identity`
  (parenthesized year wins over a title number like "Blade Runner 2049 (2017)";
  CD/Part splits kept distinct), grouped under their own **Movies** section in the
  `/dedupe` UI as one card per film titled `Title (Year)`. Tests added in
  `test_scanner.py` + `test_dedupe.py`.
