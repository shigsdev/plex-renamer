# plex-renamer

Normalize TV video filenames to Plex's `S##E##` convention, with an
**interactive web UI** to review every change before it happens.

Plex matches an episode to a series by finding a token like `S01E05`
(case-insensitive) in the filename. Files missing that token don't get matched.
This tool scans a directory tree, proposes a fix for every file that lacks one,
lets you approve/edit each rename (or bulk-approve a whole confidence tier), and
only renames when you say so. Every applied batch is **reversible**.

## Quick start

```bash
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5055
```

Type a directory (e.g. `P:\TV Shows`), click **Scan**, review, then **Apply**.

Optional — enable real episode-number lookup for daily shows (see below):

```bash
set TMDB_API_KEY=your_key_here     # Windows
export TMDB_API_KEY=your_key_here  # macOS/Linux
```

## How it decides season/episode

Parsing runs as ordered handlers (first match wins); each file gets a
**confidence** so you know how hard to scrutinize it:

| Source in filename | Example | Becomes | Confidence |
|---|---|---|---|
| already has `S##E##` | `Show S01E05` | *skipped* | — |
| episode token in the **folder** | folder `S25E07 - …` | `S25E07` | high |
| `N x NN` | `Prison Break 3x01` | `S03E01` | high |
| `S01.E01` / `S01 E02` / `S01 EP01` | `Gangland S01.E04` | `S01E04` | high |
| `Season 1 Episode 5` | `Ally.McBeal.Season1.Episode.11` | `S01E11` | high |
| `Episode N` / `EP01` (+ season folder) | `Lost S01 EP02` | `S01E02` | high |
| `Episode N` / `EP01` (no season folder) | `A.Perfect.Planet.EP01` | `S01E01` | medium |
| `Part N` | `JFK Declassified - Part 3` | `S01E03` | medium |
| bare 3–4 digit code | `heroes.406` / `murder…210` | `S04E06` | medium |
| `e01` prefix / number-only | `e01-Pilot` | `S01E01` | guess |
| **air date**, no episode # | `Dateline.2016.09.09.Title` | `Dateline NBC - 2016-09-09 - Title` (daily) | high |
| extra / featurette / gag reel | `Gag Reel`, `Season 01 Extra 01` | *left alone* | — |
| nothing recognizable | — | *left alone* | — |

**Danger-zone guards:** before reading a bare 3–4 digit episode code, the parser
strips quality/codec/source tokens (`1080p`, `x264`, `h265`, `DD5.1`, `WEB-DL`)
and 4-digit **years**, so `x264` never becomes `S02E64` and `2007` never becomes
`S20E07`. Season packs (`…S26 1080p…`) and ambiguous junk are left **undetermined**
rather than fabricating an episode number.

**Extras are never numbered** — anything matching extra/featurette/bonus/gag-reel
keywords is left alone so it can't collide with a real `S01E01`. (Exception: if
the *show's own name* contains the keyword, e.g. *The Making of the Mob*, it's
treated as a real episode.)

## Daily shows + TMDB lookup

Shows like Dateline carry an air date but no episode number. By default these
are renamed to Plex's daily format — `Dateline NBC - 2016-09-09 - Title` — which
Plex matches by air date. If you set a free **TMDB** API key
(https://www.themoviedb.org/settings/api, v3 key), each daily row in the UI gets
a **🔍 ep #** button that looks up the real `S##E##` by matching the air date and
rewrites the proposal.

## Command line (optional)

The CLI shares the same parser and rename engine as the web UI:

```bash
python rename_plex.py "P:/TV Shows"                 # preview
python rename_plex.py "P:/TV Shows" --apply         # rename (asks y/N)
python rename_plex.py "P:/TV Shows" --min high      # only high-confidence
python rename_plex.py --undo                         # revert the last batch
```

## Safety

- **Nothing is renamed until you approve it.** Scan and preview are read-only.
- **Never clobbers** — a rename whose target already exists is skipped.
- **Live progress** — Apply streams per-file progress to a bar and shows a clear
  "✓ Done. Renamed N file(s)" banner when finished.
- **Reversible** — every applied batch is written to `undo_logs/rename-NNNN.json`;
  the UI's *Undo last batch* button (or `rename_plex.py --undo`) reverses it.
- **Audit trail** — every successful rename is also appended to
  `undo_logs/_all_renames.jsonl` (append-only, never rotated), so undo can be
  reconstructed even if a per-batch log is lost. Don't bulk-delete `undo_logs/`.
- **In-place only** — files are renamed within their folder, never moved.
- Binds to `127.0.0.1` only — it's a local single-user desktop tool.

## Layout

```
scanner.py    parsing + proposal building (pure logic; tested)
metadata.py   optional TMDB air-date -> S##E## lookup
apply.py      rename + undo-log engine (tested)
app.py        Flask web UI
rename_plex.py thin CLI over the same engine
templates/ static/   the web UI
tests/        pytest (parser danger-zones + apply/undo)
```

## Development & testing

```bash
pip install -r requirements-dev.txt   # Flask + pytest + pytest-cov + ruff
bash scripts/check.sh                  # the full local gate (lint + tests + coverage)
```

`scripts/check.sh` mirrors CI exactly:

- **ruff** — lint (`ruff check .`)
- **pytest** — full suite with an **80% coverage floor**

Tests live in `tests/` and cover the parser danger-zones, the rename/undo engine
(incl. the append-only master log), the metadata provider dispatch (HTTP mocked,
no network), the Flask endpoints (incl. the streamed `/api/apply` progress
protocol), and the CLI. Every test redirects `undo_logs/` to a temp dir, so the
suite never touches real data.

**CI:** GitHub Actions (`.github/workflows/ci.yml`) runs ruff + pytest + coverage
on Python 3.11/3.12/3.13 for every push and pull request to `main`.
