"""Load monorepo .env and apply PR_FILTER_* overrides for pr-filtering-kit."""

from __future__ import annotations

import os
from pathlib import Path

_FILTER_APPLIED = False


def monorepo_root() -> Path | None:
    explicit = (os.environ.get("SWE_BENCH_TASKGEN_ROOT") or "").strip()
    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "run_pipeline.sh").is_file() and (parent / "pr-filtering-kit").is_dir():
            return parent
    return None


def load_env() -> None:
    """Load root .env and map PR_FILTER_* to runtime variables (once)."""
    global _FILTER_APPLIED
    if _FILTER_APPLIED:
        return

    if os.environ.get("REPO_EVAL_SKIP_DOTENV"):
        _apply_pr_filter_env()
        _FILTER_APPLIED = True
        return

    env_file = (os.environ.get("SWE_BENCH_TASKGEN_ENV") or "").strip()
    if env_file and Path(env_file).is_file():
        _load_dotenv(Path(env_file))
    else:
        root = monorepo_root()
        if root and (root / ".env").is_file():
            _load_dotenv(root / ".env")
        else:
            kit_env = Path(__file__).resolve().parent.parent / ".env"
            if kit_env.is_file():
                _load_dotenv(kit_env)

    _apply_pr_filter_env()
    _FILTER_APPLIED = True


def _load_dotenv(path: Path) -> None:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=path, override=False)


def _apply_pr_filter_env() -> None:
    """Apply PR_FILTER_* overrides only — never read TASKGEN_* keys."""
    mappings = (
        ("PR_FILTER_LLM_PROVIDER", "LLM_PROVIDER"),
        ("PR_FILTER_OPENAI_API_KEY", "OPENAI_API_KEY"),
        ("PR_FILTER_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        ("PR_FILTER_GOOGLE_API_KEY", "GOOGLE_API_KEY"),
        ("PR_FILTER_LLM_MODEL", "LLM_MODEL"),
        ("PR_FILTER_LLM_CONCURRENCY", "LLM_CONCURRENCY"),
        ("PR_FILTER_LLM_MAX_RETRIES", "LLM_MAX_RETRIES"),
        ("PR_FILTER_LLM_BACKOFF_BASE_DELAY", "LLM_BACKOFF_BASE_DELAY"),
        ("PR_FILTER_MAX_ACCEPTED_PRS", "MAX_ACCEPTED_PRS"),
    )
    for src, dst in mappings:
        value = os.environ.get(src, "").strip()
        if value:
            os.environ[dst] = value
