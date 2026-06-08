# Shared environment helpers for swe-bench-taskgen monorepo.
# Source from setup.sh / run_pipeline.sh:  source "$ROOT/lib/env.sh"

# Monorepo root (set by caller before sourcing, or derived from lib/ location).
: "${ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

load_root_env() {
  if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
  fi
}

# Map PR_FILTER_* → vars read by pr-filtering-kit (no TASKGEN_* leakage).
export_pr_filter_env() {
  load_root_env
  if [[ -n "${PR_FILTER_LLM_PROVIDER:-}" ]]; then
    export LLM_PROVIDER="$PR_FILTER_LLM_PROVIDER"
  fi
  if [[ -n "${PR_FILTER_OPENAI_API_KEY:-}" ]]; then
    export OPENAI_API_KEY="$PR_FILTER_OPENAI_API_KEY"
  fi
  if [[ -n "${PR_FILTER_ANTHROPIC_API_KEY:-}" ]]; then
    export ANTHROPIC_API_KEY="$PR_FILTER_ANTHROPIC_API_KEY"
  fi
  if [[ -n "${PR_FILTER_GOOGLE_API_KEY:-}" ]]; then
    export GOOGLE_API_KEY="$PR_FILTER_GOOGLE_API_KEY"
  fi
  if [[ -n "${PR_FILTER_LLM_MODEL:-}" ]]; then
    export LLM_MODEL="$PR_FILTER_LLM_MODEL"
  fi
  if [[ -n "${PR_FILTER_LLM_CONCURRENCY:-}" ]]; then
    export LLM_CONCURRENCY="$PR_FILTER_LLM_CONCURRENCY"
  fi
  export SWE_BENCH_TASKGEN_ROOT="$ROOT"
  export SWE_BENCH_TASKGEN_ENV="$ROOT/.env"
}

# Map TASKGEN_* → vars read by pr-to-swe-task-jsonl (no PR_FILTER_* leakage).
export_taskgen_env() {
  load_root_env
  if [[ -n "${TASKGEN_ANTHROPIC_API_KEY:-}" ]]; then
    export ANTHROPIC_API_KEY="$TASKGEN_ANTHROPIC_API_KEY"
  fi
  if [[ -n "${TASKGEN_OPENAI_API_KEY:-}" ]]; then
    export OPENAI_API_KEY="$TASKGEN_OPENAI_API_KEY"
  fi
  if [[ -n "${TASKGEN_LLM_MODEL:-}" ]]; then
    export LLM_MODEL="$TASKGEN_LLM_MODEL"
    export OPENAI_MODEL="$TASKGEN_LLM_MODEL"
  fi
  if [[ -n "${TASKGEN_OPENAI_BASE_URL:-}" ]]; then
    export OPENAI_BASE_URL="$TASKGEN_OPENAI_BASE_URL"
  fi
  export SWE_BENCH_TASKGEN_ROOT="$ROOT"
  export SWE_BENCH_TASKGEN_ENV="$ROOT/.env"
}

_pr_filter_provider() {
  echo "${PR_FILTER_LLM_PROVIDER:-${LLM_PROVIDER:-openai}}"
}

_taskgen_model() {
  echo "${TASKGEN_LLM_MODEL:-${LLM_MODEL:-claude-opus-4-6}}"
}

_taskgen_needs_anthropic() {
  local model
  model="$(_taskgen_model)"
  [[ "$model" == claude* ]]
}

preflight_check() {
  local errors=0
  local warnings=0

  load_root_env

  echo "==> swe-bench-taskgen preflight"
  echo "    Root: $ROOT"

  if [[ ! -f "$ROOT/.env" ]]; then
    echo "FAIL  Missing $ROOT/.env — run: cp .env.example .env" >&2
    errors=$((errors + 1))
  else
    echo "OK    .env present"
  fi

  local py="${PYTHON:-python3}"
  if ! command -v "$py" >/dev/null 2>&1; then
    py=python
  fi
  if ! command -v "$py" >/dev/null 2>&1; then
    echo "FAIL  python3 not found" >&2
    errors=$((errors + 1))
  else
    echo "OK    Python: $($py --version 2>&1)"
  fi

  if [[ -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ]]; then
    echo "FAIL  GH_TOKEN or GITHUB_TOKEN not set in .env" >&2
    errors=$((errors + 1))
  else
    echo "OK    GitHub token configured"
  fi

  local fp
  fp="$(_pr_filter_provider)"
  case "$fp" in
    openai)
      if [[ -z "${PR_FILTER_OPENAI_API_KEY:-}" ]]; then
        echo "FAIL  PR_FILTER_OPENAI_API_KEY required (PR_FILTER_LLM_PROVIDER=openai)" >&2
        errors=$((errors + 1))
      else
        echo "OK    PR filter LLM: openai"
      fi
      ;;
    anthropic)
      if [[ -z "${PR_FILTER_ANTHROPIC_API_KEY:-}" ]]; then
        echo "FAIL  PR_FILTER_ANTHROPIC_API_KEY required (PR_FILTER_LLM_PROVIDER=anthropic)" >&2
        errors=$((errors + 1))
      else
        echo "OK    PR filter LLM: anthropic"
      fi
      ;;
    google)
      if [[ -z "${PR_FILTER_GOOGLE_API_KEY:-}" ]]; then
        echo "FAIL  PR_FILTER_GOOGLE_API_KEY required (PR_FILTER_LLM_PROVIDER=google)" >&2
        errors=$((errors + 1))
      else
        echo "OK    PR filter LLM: google"
      fi
      ;;
    *)
      echo "FAIL  Unknown PR_FILTER_LLM_PROVIDER: $fp" >&2
      errors=$((errors + 1))
      ;;
  esac

  if _taskgen_needs_anthropic; then
    if [[ -z "${TASKGEN_ANTHROPIC_API_KEY:-}" ]]; then
      echo "FAIL  TASKGEN_ANTHROPIC_API_KEY required for model $(_taskgen_model)" >&2
      errors=$((errors + 1))
    else
      echo "OK    Task gen LLM: anthropic ($(_taskgen_model))"
    fi
  else
    if [[ -z "${TASKGEN_OPENAI_API_KEY:-}" ]]; then
      echo "FAIL  TASKGEN_OPENAI_API_KEY required for model $(_taskgen_model)" >&2
      errors=$((errors + 1))
    else
      echo "OK    Task gen LLM: openai-compatible ($(_taskgen_model))"
    fi
  fi

  if ! command -v gh >/dev/null 2>&1; then
    echo "FAIL  gh CLI not found — https://cli.github.com/" >&2
    errors=$((errors + 1))
  elif ! gh auth status >/dev/null 2>&1; then
    echo "WARN  gh not authenticated — run: gh auth login" >&2
    warnings=$((warnings + 1))
  else
    echo "OK    gh authenticated"
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "WARN  docker not found — task generation needs Docker (or pass --no-discover-tests-docker)" >&2
    warnings=$((warnings + 1))
  elif ! docker info >/dev/null 2>&1; then
    echo "WARN  docker daemon not running" >&2
    warnings=$((warnings + 1))
  else
    echo "OK    docker available"
  fi

  if ! "$py" -c "import requests" 2>/dev/null; then
    echo "FAIL  pr-filtering-kit deps missing — run ./setup.sh" >&2
    errors=$((errors + 1))
  else
    echo "OK    pr-filtering-kit deps importable"
  fi

  if ! "$py" -c "import swe_rebench_pr" 2>/dev/null; then
    echo "FAIL  pr-to-swe-task-jsonl not installed — run ./setup.sh" >&2
    errors=$((errors + 1))
  else
    echo "OK    pr-to-swe-task-jsonl installed"
  fi

  echo ""
  if [[ $errors -gt 0 ]]; then
    echo "Preflight failed ($errors error(s), $warnings warning(s))." >&2
    return 1
  fi
  echo "Preflight passed ($warnings warning(s))."
  return 0
}
