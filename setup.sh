#!/usr/bin/env bash
# One-time bootstrap: install deps, create .env, flatten nested git repos.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$ROOT/lib/env.sh"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python
fi

echo "==> swe-bench-taskgen setup"
echo "    Root: $ROOT"

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "Created $ROOT/.env from .env.example — edit it with your tokens and API keys."
else
  echo "OK    .env already exists"
fi

for sub in pr-filtering-kit pr-to-swe-task-jsonl; do
  if [[ -d "$ROOT/$sub/.git" ]]; then
    echo "==> Removing nested .git in $sub (monorepo uses single root repo)"
    rm -rf "$ROOT/$sub/.git"
  fi
done

echo "==> Installing pr-filtering-kit dependencies"
"$PYTHON" -m pip install -r "$ROOT/pr-filtering-kit/requirements.txt"

echo "==> Installing pr-to-swe-task-jsonl (editable)"
"$PYTHON" -m pip install -e "$ROOT/pr-to-swe-task-jsonl"

mkdir -p "$ROOT/output"

echo ""
echo "Setup complete. Next steps:"
echo "  1. Edit $ROOT/.env"
echo "  2. gh auth login          # if not already authenticated"
echo "  3. ./run_pipeline.sh --check"
echo "  4. ./run_pipeline.sh owner/repo"
