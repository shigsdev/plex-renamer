#!/usr/bin/env bash
# Local quality gate — mirrors CI. Run before committing.
#   bash scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "── ruff ──────────────────────────────────────"
ruff check .

echo "── pytest (+coverage) ────────────────────────"
pytest --cov=. --cov-report=term-missing --cov-fail-under=80

echo
echo "ALL GATES GREEN"
