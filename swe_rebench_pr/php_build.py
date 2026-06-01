"""PHP/Composer helpers for SWE-bench harness Docker image builds."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .apt_from_log import ensure_base_build_apt, merge_apt_into_config, remediate_apt_install_from_log

DEFAULT_PHP_VERSION = "8.2-cli-bookworm"

_PHP_APT_BASE = ("unzip", "libzip-dev", "libxml2-dev", "libcurl4-openssl-dev")


def _normalize_php_version(raw: str) -> str:
    v = raw.strip().lstrip("v").replace(" ", "")
    if not v:
        return DEFAULT_PHP_VERSION
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", v.replace("^", "").split(",")[0])
    if m:
        major, minor = int(m.group(1)), int(m.group(2))
        # Official ``php:*-cli-bookworm`` tags start at 8.2; 8.0/8.1 use bullseye or are absent.
        if major == 8 and minor < 2:
            return "8.2-cli-bookworm"
        patch = m.group(3)
        if patch:
            return f"{major}.{minor}-cli-bookworm"
        return f"{major}.{minor}-cli-bookworm"
    return DEFAULT_PHP_VERSION


def resolve_php_version_for_repo(repo: Path) -> str | None:
    composer = repo / "composer.json"
    if not composer.is_file():
        return None
    try:
        data = json.loads(composer.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    req = data.get("require") or {}
    if isinstance(req, dict) and req.get("php"):
        return _normalize_php_version(str(req["php"]))
    return None


def ensure_php_docker_specs(
    cfg: dict[str, Any],
    *,
    repo: Path | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    lang = str(language or cfg.get("language") or "").lower()
    if lang not in ("", "php"):
        return cfg
    out = dict(cfg)
    specs = dict(out.get("docker_specs") or {}) if isinstance(out.get("docker_specs"), dict) else {}
    if not specs.get("php_version"):
        pv: str | None = None
        if repo is not None:
            pv = resolve_php_version_for_repo(repo)
        specs["php_version"] = pv or DEFAULT_PHP_VERSION
    out["docker_specs"] = specs
    return out


def php_install_config_for_repo(
    repo: Path,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .languages import get_language_spec

    cfg = dict(base or get_language_spec("php").default_install_config)
    cfg["language"] = "php"
    cfg = ensure_php_docker_specs(cfg, repo=repo, language="php")
    cfg = merge_apt_into_config(cfg, list(_PHP_APT_BASE))
    cfg["install"] = "composer install --no-interaction --prefer-dist --no-progress || true"
    from .repo_detect import repo_uses_artisan_phpunit

    if repo_uses_artisan_phpunit(repo):
        cfg["test_cmd"] = (
            "php artisan test --log-junit __JUNIT_OUT__ 2>/dev/null "
            "|| vendor/bin/phpunit --log-junit __JUNIT_OUT__"
        )
    else:
        cfg["test_cmd"] = (
            "vendor/bin/phpunit --log-junit __JUNIT_OUT__ 2>/dev/null "
            "|| vendor/bin/phpunit"
        )
    return cfg


def remediate_php_install_from_log(cfg: dict[str, Any], log: str) -> dict[str, Any]:
    out = remediate_apt_install_from_log(cfg, log)
    return merge_apt_into_config(out, list(_PHP_APT_BASE))


def merge_php_harness_fields_after_llm(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if str(before.get("language") or "").lower() != "php" and "phpunit" not in str(
        before.get("test_cmd") or ""
    ).lower():
        return after
    out = dict(after)
    for key in ("install", "test_cmd", "pre_install", "post_install", "apt-pkgs", "docker_specs", "language"):
        if before.get(key) and not out.get(key):
            out[key] = before[key]
    tc_before = str(before.get("test_cmd") or "")
    tc_after = str(out.get("test_cmd") or "")
    if "phpunit" in tc_before.lower() and "pytest" in tc_after.lower():
        out["test_cmd"] = tc_before
    return ensure_php_docker_specs(out, language="php")
