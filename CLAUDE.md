# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repo. See
`README.md` for what the tool does and how its parser decides season/episode.

## Workflow: branch → PR → merge

Every change, however small, goes through a pull request. Never commit
directly to `main`.

1. Start from an up-to-date `main` (`git switch main && git pull --ff-only`),
   then create a branch: `feat/…`, `fix/…`, `chore/…`, `docs/…`.
2. Commit on the branch, push it, and open a PR against `main` (ready for
   review, not a draft) with a description of what changed, why, and how it
   was tested.
3. Merge once CI is green and the PR is mergeable. Squash-merge and delete
   the branch. If CI fails, fix it on the branch first; never merge red.
4. Afterwards, switch back to `main`, pull, and delete the local branch.

Carry this through to the merge without asking for approval at each step.

## Dev setup

The app runs from a project-local virtual environment in `.venv`
(gitignored). On Windows, `start.bat` creates it on first launch.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python app.py        # web UI at http://127.0.0.1:5055
```

## Checks (must pass before opening a PR)

These match CI (`.github/workflows/ci.yml`, Python 3.11/3.12/3.13):

```powershell
ruff check .
pytest --cov=. --cov-report=term-missing --cov-fail-under=80
```

`bash scripts/check.sh` runs the same gate. Tests redirect `undo_logs/` to a
temp dir and mock all HTTP, so they never touch real media or the network.

## Things to keep in mind

- Never rename or delete real media while developing or testing. Exercise
  the rename/undo engine only through the test suite or a throwaway folder.
- `undo_logs/` is real runtime data (including the append-only
  `_all_renames.jsonl` audit trail). Never commit it or bulk-delete it.
- `.env` holds API keys (TMDB/TheTVDB). It is gitignored; never commit it.
- The app binds to `127.0.0.1` only. Keep it that way.
