"""Cache install_config drafts per repository fingerprint."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

_CACHE_ENV = "SWE_REBENCH_INSTALL_CACHE"

# Bump when repo-first heuristics change so stale cached configs are not reused.
INSTALL_CONFIG_CACHE_VERSION = "3"

_FINGERPRINT_FILES = (
    ".github/workflows",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
    "composer.json",
    "composer.lock",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "Gemfile",
    "Gemfile.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "tox.ini",
    "Dockerfile",
    ".travis.yml",
)


def _default_cache_dir() -> Path:
    raw = os.environ.get(_CACHE_ENV, "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "swe_rebench_pr" / "install_config"


def _file_digest(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()[:16]


def install_config_cache_key(repo_id: str, repo: Path) -> str:
    """Stable key from repo slug + hashed install-relevant files."""
    parts: list[str] = [f"v:{INSTALL_CONFIG_CACHE_VERSION}", repo_id.replace("/", "__")]
    wf = repo / ".github" / "workflows"
    if wf.is_dir():
        try:
            for wf_file in sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml")):
                parts.append(f"wf:{wf_file.name}:{_file_digest(wf_file)}")
        except OSError:
            pass
    for rel in _FINGERPRINT_FILES:
        if rel == ".github/workflows":
            continue
        p = repo / rel
        if p.is_file():
            parts.append(f"{rel}:{_file_digest(p)}")
    blob = "|".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def load_cached_install_config(
    repo_id: str,
    repo: Path,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any] | None:
    root = cache_dir or _default_cache_dir()
    key = install_config_cache_key(repo_id, repo)
    path = root / f"{key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("repo_id") != repo_id:
        return None
    cfg = data.get("install_config")
    return dict(cfg) if isinstance(cfg, dict) else None


def save_cached_install_config(
    repo_id: str,
    repo: Path,
    install_config: dict[str, Any],
    *,
    cache_dir: Path | None = None,
) -> None:
    root = cache_dir or _default_cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    key = install_config_cache_key(repo_id, repo)
    path = root / f"{key}.json"
    payload = {
        "repo_id": repo_id,
        "cache_key": key,
        "install_config": install_config,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
