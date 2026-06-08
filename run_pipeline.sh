#!/usr/bin/env bash
# Run PR filtering (pr-filtering-kit) then task generation (pr-to-swe-task-jsonl).
#
# Usage:
#   ./run_pipeline.sh --check
#   ./run_pipeline.sh owner/repo
#   ./run_pipeline.sh owner/repo --include-partially-accepted
#   ./run_pipeline.sh owner/repo -- --language rust

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILTER_KIT="$ROOT/pr-filtering-kit"
TASK_GEN="$ROOT/pr-to-swe-task-jsonl"
# shellcheck disable=SC1091
source "$ROOT/lib/env.sh"

INCLUDE_PARTIAL=0
CHECK_ONLY=0
REPO=""
TASK_GEN_ARGS=()

usage() {
  cat <<'EOF'
Usage: run_pipeline.sh [--check] owner/repo [options] [-- task-gen-args...]

Filter merged PRs for a repository, then build SWE-rebench JSONL tasks.

Options:
  --check                        Verify .env, deps, gh, docker (no pipeline run)
  --include-partially-accepted   Use *_partially_accepted when accepted list is empty
  -h, --help                     Show this help

Configuration:
  Single file: ROOT/.env (see .env.example)
  PR_FILTER_*   — pr-filtering-kit LLM (rubrics, taxonomy, quality)
  TASKGEN_*     — pr-to-swe-task-jsonl LLM (patch split, install, Docker remediation)
  GH_TOKEN      — shared GitHub API token

Output (per repo):
  output/owner__repo/report.json
  output/owner__repo/accepted.txt
  output/owner__repo/partially_accepted.txt
  output/owner__repo/tasks.jsonl

Examples:
  ./run_pipeline.sh --check
  ./run_pipeline.sh curl/curl
  ./run_pipeline.sh shepmaster/snafu --include-partially-accepted
  ./run_pipeline.sh BurntSushi/ripgrep -- --language rust
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --check)
      CHECK_ONLY=1
      shift
      ;;
    --include-partially-accepted)
      INCLUDE_PARTIAL=1
      shift
      ;;
    --)
      shift
      TASK_GEN_ARGS=("$@")
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      if [[ -z "$REPO" ]]; then
        REPO="$1"
      else
        echo "Unexpected argument: $1" >&2
        usage >&2
        exit 1
      fi
      shift
      ;;
  esac
done

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON=python
fi

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  preflight_check
  exit $?
fi

if [[ -z "$REPO" ]]; then
  usage >&2
  exit 1
fi

REPO="${REPO#github:}"
REPO="${REPO%/}"
if [[ ! "$REPO" =~ ^[^/]+/[^/]+$ ]]; then
  echo "Invalid repo format (expected owner/repo): $REPO" >&2
  exit 1
fi

REPO_SLUG="${REPO//\//__}"
OUTPUT_ROOT="$ROOT/output/$REPO_SLUG"

preflight_check

mkdir -p "$OUTPUT_ROOT"

echo "==> Step 1/2: PR filtering for $REPO"
export_pr_filter_env
cd "$FILTER_KIT"
"$PYTHON" repo_evaluator.py "$REPO" --json --output "$OUTPUT_ROOT/report.json"

ACCEPTED_SRC="$OUTPUT_ROOT/${REPO_SLUG}_accepted.txt"
PARTIAL_SRC="$OUTPUT_ROOT/${REPO_SLUG}_partially_accepted.txt"
ACCEPTED="$OUTPUT_ROOT/accepted.txt"
PARTIAL="$OUTPUT_ROOT/partially_accepted.txt"

if [[ -f "$ACCEPTED_SRC" ]]; then
  cp "$ACCEPTED_SRC" "$ACCEPTED"
fi
if [[ -f "$PARTIAL_SRC" ]]; then
  cp "$PARTIAL_SRC" "$PARTIAL"
fi

URLS_FILE="$ACCEPTED"
ALLOW_LLM_TEST_PATCH=()

if [[ ! -s "$ACCEPTED" ]]; then
  if [[ "$INCLUDE_PARTIAL" -eq 1 && -s "$PARTIAL" ]]; then
    echo "No rubric-accepted PRs; using partially accepted list"
    URLS_FILE="$PARTIAL"
    ALLOW_LLM_TEST_PATCH=(--allow-llm-test-patch)
  else
    echo "No rubric-accepted PRs in $ACCEPTED" >&2
    if [[ -s "$PARTIAL" ]]; then
      echo "Hint: re-run with --include-partially-accepted" >&2
    fi
    exit 1
  fi
fi

PR_COUNT="$(wc -l < "$URLS_FILE" | tr -d ' ')"
echo "==> Step 2/2: Building JSONL tasks ($PR_COUNT URLs)"
export_taskgen_env
cd "$TASK_GEN"
TASKS_JSONL="$OUTPUT_ROOT/tasks.jsonl"

TASK_GEN_EXTRA_ARGS=()
if [[ ${#ALLOW_LLM_TEST_PATCH[@]} -gt 0 ]]; then
  TASK_GEN_EXTRA_ARGS+=("${ALLOW_LLM_TEST_PATCH[@]}")
fi
if [[ ${#TASK_GEN_ARGS[@]} -gt 0 ]]; then
  TASK_GEN_EXTRA_ARGS+=("${TASK_GEN_ARGS[@]}")
fi

"$PYTHON" -m swe_rebench_pr \
  --urls "$URLS_FILE" \
  -o "$TASKS_JSONL" \
  ${TASK_GEN_EXTRA_ARGS[@]+"${TASK_GEN_EXTRA_ARGS[@]}"}

echo ""
echo "Done — output/$REPO_SLUG/"
echo "  report.json              Full PR filtering report"
echo "  accepted.txt               Rubric-accepted PR URLs ($PR_COUNT used)"
echo "  partially_accepted.txt   Partially accepted PR URLs (if any)"
echo "  tasks.jsonl                Final SWE-rebench tasks"
