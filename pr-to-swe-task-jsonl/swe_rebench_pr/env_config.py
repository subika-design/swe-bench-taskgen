"""Load monorepo .env and apply TASKGEN_* overrides for pr-to-swe-task-jsonl."""

from __future__ import annotations

import os
from pathlib import Path

_TASKGEN_APPLIED = False


def monorepo_root() -> Path | None:
    explicit = (os.environ.get("SWE_BENCH_TASKGEN_ROOT") or "").strip()
    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "run_pipeline.sh").is_file() and (parent / "pr-to-swe-task-jsonl").is_dir():
            return parent
    return None


def load_env() -> None:
    """Load root .env and map TASKGEN_* to runtime variables (once)."""
    global _TASKGEN_APPLIED
    if _TASKGEN_APPLIED:
        return

    env_file = (os.environ.get("SWE_BENCH_TASKGEN_ENV") or "").strip()
    if env_file and Path(env_file).is_file():
        _load_dotenv(Path(env_file))
    else:
        root = monorepo_root()
        if root and (root / ".env").is_file():
            _load_dotenv(root / ".env")

    _apply_taskgen_env()
    _TASKGEN_APPLIED = True


def _load_dotenv(path: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        _load_dotenv_manual(path)
        return
    load_dotenv(dotenv_path=path, override=False)


def _load_dotenv_manual(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _apply_taskgen_env() -> None:
    """Apply TASKGEN_* overrides only — never read PR_FILTER_* keys."""
    mappings = (
        ("TASKGEN_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        ("TASKGEN_OPENAI_API_KEY", "OPENAI_API_KEY"),
        ("TASKGEN_LLM_MODEL", "LLM_MODEL"),
        ("TASKGEN_LLM_MODEL", "OPENAI_MODEL"),
        ("TASKGEN_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    )
    for src, dst in mappings:
        value = os.environ.get(src, "").strip()
        if value:
            os.environ[dst] = value
