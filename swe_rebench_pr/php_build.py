"""PHP/Composer helpers for SWE-bench harness Docker image builds."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .apt_from_log import ensure_base_build_apt, merge_apt_into_config, remediate_apt_install_from_log

DEFAULT_PHP_VERSION = "8.2-cli-bookworm"

_PHP_APT_BASE = ("unzip", "libzip-dev", "libxml2-dev", "libcurl4-openssl-dev", "libicu-dev")
_DOCKER_PHP_EXT_NAMES: dict[str, str] = {
    "intl": "intl",
    "zip": "zip",
    "curl": "curl",
    "mbstring": "mbstring",
    "xml": "xml",
    "xmlreader": "xmlreader",
    "xmlwriter": "xmlwriter",
    "bcmath": "bcmath",
    "gd": "gd",
    "soap": "soap",
    "xsl": "xsl",
    "sodium": "sodium",
    "bz2": "bz2",
    "ldap": "ldap",
    "ffi": "ffi",
    "sqlite3": "pdo_sqlite",
    "pdo_mysql": "pdo_mysql",
    "pdo_pgsql": "pdo_pgsql",
}


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
    from .manifest_extract import composer_ext_apt_packages

    cfg = merge_apt_into_config(cfg, list(_PHP_APT_BASE) + composer_ext_apt_packages(repo))
    pre = list(cfg.get("pre_install") or [])
    ext_cmd = php_docker_ext_install_cmd(repo)
    if ext_cmd and ext_cmd not in pre:
        pre.append(ext_cmd)
    if pre:
        cfg["pre_install"] = pre
    cfg["install"] = "composer install --no-interaction --prefer-dist --no-progress"
    runner, test_cmd = php_test_cmd_for_repo(repo)
    cfg["php_test_runner"] = runner
    cfg["test_cmd"] = test_cmd
    return cfg


def _composer_json(repo: Path) -> dict[str, Any] | None:
    composer = repo / "composer.json"
    if not composer.is_file():
        return None
    try:
        data = json.loads(composer.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def repo_uses_symfony_phpunit_bridge(repo: Path) -> bool:
    """True when Composer declares Symfony PHPUnit Bridge (``simple-phpunit``)."""
    data = _composer_json(repo)
    if not data:
        return False
    for section in ("require-dev", "require"):
        block = data.get(section) or {}
        if isinstance(block, dict) and block.get("symfony/phpunit-bridge"):
            return True
    scripts = data.get("scripts") or {}
    if isinstance(scripts, dict):
        blob = json.dumps(scripts)
        if "simple-phpunit" in blob:
            return True
    return False


def composer_required_php_extensions(repo: Path) -> list[str]:
    """``ext-*`` names from ``composer.json`` ``require`` (without ``ext-`` prefix)."""
    data = _composer_json(repo)
    if not data:
        return []
    reqs = data.get("require") or {}
    if not isinstance(reqs, dict):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for key in reqs:
        if not isinstance(key, str) or not key.lower().startswith("ext-"):
            continue
        ext = key[4:].lower()
        if ext and ext not in seen:
            seen.add(ext)
            out.append(ext)
    return out


# Extensions already compiled in ``harness/dockerfiles/php.py`` base image.
PHP_BASE_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {"gd", "zip", "gmp", "ftp", "curl", "pcntl", "json"}
)


def php_extensions_to_install(
    required: Iterable[str],
    *,
    extra_from_ci: Iterable[str] | None = None,
) -> list[str]:
    """Return docker-php-ext names not already in the PHP harness base image."""
    names: list[str] = []
    seen: set[str] = set()
    for raw in list(required or []) + list(extra_from_ci or []):
        ext = str(raw).strip().lower()
        if not ext or ext in PHP_BASE_IMAGE_EXTENSIONS:
            continue
        name = _DOCKER_PHP_EXT_NAMES.get(ext, ext)
        if name in PHP_BASE_IMAGE_EXTENSIONS or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def php_docker_ext_install_cmd_for_extensions(exts: list[str]) -> str:
    if not exts:
        return ""
    return f"docker-php-ext-install -j$(nproc) {' '.join(exts)}"


def php_docker_ext_install_cmd(repo: Path, *, ci_extensions: Iterable[str] | None = None) -> str:
    """``docker-php-ext-install`` for Composer ``ext-*`` + CI setup-php extensions."""
    required = composer_required_php_extensions(repo)
    to_install = php_extensions_to_install(required, extra_from_ci=ci_extensions)
    return php_docker_ext_install_cmd_for_extensions(to_install)


def php_test_cmd_for_repo(repo: Path) -> tuple[str, str]:
    """
    Return ``(php_test_runner, test_cmd)`` with ``__JUNIT_OUT__`` placeholder.

    Prefers Symfony ``simple-phpunit``, then Laravel ``artisan test``, then ``phpunit``.
    """
    from .repo_detect import repo_uses_artisan_phpunit

    if repo_uses_artisan_phpunit(repo):
        return (
            "artisan",
            "php artisan test --log-junit __JUNIT_OUT__ 2>/dev/null "
            "|| vendor/bin/phpunit --log-junit __JUNIT_OUT__",
        )
    if repo_uses_symfony_phpunit_bridge(repo):
        return (
            "simple-phpunit",
            "vendor/bin/simple-phpunit --log-junit __JUNIT_OUT__ 2>/dev/null "
            "|| vendor/bin/simple-phpunit --log-junit __JUNIT_OUT__",
        )
    return (
        "phpunit",
        "vendor/bin/phpunit --log-junit __JUNIT_OUT__ 2>/dev/null "
        "|| vendor/bin/phpunit --log-junit __JUNIT_OUT__",
    )


def log_indicates_php_composer_or_phpunit_failure(log: str) -> bool:
    low = (log or "").lower()
    return any(
        m in low
        for m in (
            "composer install",
            "your requirements could not be resolved",
            "ext-intl",
            "ext-zip",
            "simple-phpunit",
            "phpunit",
            "class not found",
        )
    )


def php_junit_nodeid_in_test_patch_paths(nodeid: str, paths: list[str]) -> bool:
    """Match PHPUnit JUnit node ids to ``test_patch`` file paths."""
    if not nodeid or not paths:
        return False
    from .diff_split import _nodeid_leading_relpath

    head = _nodeid_leading_relpath(nodeid).replace("\\", "/").strip().lstrip("/")
    path_set: set[str] = set()
    for raw in paths:
        rel = raw.replace("\\", "/").strip().lstrip("/")
        if not rel:
            continue
        path_set.add(rel)
        path_set.add(Path(rel).name)
        if rel.endswith(".php"):
            stem = Path(rel).stem
            path_set.add(stem)
            path_set.add(rel.replace("/", "\\"))
        if rel.endswith("Test.php"):
            fq = rel[:-4].replace("/", "\\")
            path_set.add(fq.replace("\\", "."))
            path_set.add(fq)
    if head in path_set:
        return True
    head_name = Path(head).name if "/" in head else head
    if head_name in path_set:
        return True
    head_norm = head.replace("\\", "/")
    for p in path_set:
        if not p or "/" not in p:
            continue
        if head_norm == p or head_norm.endswith("/" + p) or p.endswith(head_norm):
            return True
        if head_norm.endswith(p.split("/")[-1]):
            return True
    if "::" in nodeid:
        class_part = nodeid.split("::", 1)[0].strip()
        class_path = class_part.replace(".", "/") + ".php"
        class_tail = class_part.rsplit(".", 1)[-1] + ".php"
        for p in path_set:
            if p == class_path or p.endswith(class_path) or p.endswith(class_tail):
                return True
            if class_tail in p:
                return True
    return False


def remediate_php_install_from_log(cfg: dict[str, Any], log: str, *, repo: Path | None = None) -> dict[str, Any]:
    out = remediate_apt_install_from_log(cfg, log)
    out = merge_apt_into_config(out, list(_PHP_APT_BASE))
    if repo is not None:
        from .manifest_extract import composer_ext_apt_packages

        out = merge_apt_into_config(out, composer_ext_apt_packages(repo))
        runner, test_cmd = php_test_cmd_for_repo(repo)
        out["php_test_runner"] = runner
        out["test_cmd"] = test_cmd
        out["install"] = "composer install --no-interaction --prefer-dist --no-progress"
        ext_cmd = php_docker_ext_install_cmd(repo)
        if ext_cmd:
            pre = list(out.get("pre_install") or [])
            if ext_cmd not in pre:
                pre.append(ext_cmd)
            out["pre_install"] = pre
    return out


def repo_has_bin_composer(repo: Path) -> bool:
    return (repo / "bin" / "composer").is_file() or (repo / "bin" / "composer.bat").is_file()


def count_composer_install_in_ci_runs(runs: Iterable[str]) -> int:
    n = 0
    for line in runs:
        low = str(line).lower()
        if re.search(r"\bcomposer\s+install\b", low) or re.search(r"\bbin/composer\s+install\b", low):
            n += 1
    return n


def self_hosting_composer_install_cmd(*, test_env: dict[str, str] | None = None) -> str:
    flags = str((test_env or {}).get("COMPOSER_FLAGS") or "").strip()
    if not flags:
        flags = "--ansi --no-interaction --no-progress --prefer-dist"
    return (
        f"composer install {flags} && "
        f"bin/composer install {flags}"
    )


def merge_php_ci_signals_into_config(
    cfg: dict[str, Any],
    *,
    php_extensions: list[str] | None = None,
    php_tools: list[str] | None = None,
    test_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Apply setup-php / workflow env signals to ``install_config``."""
    out = dict(cfg)
    if test_env:
        env = dict(out.get("test_env") or {})
        env.update({str(k): str(v) for k, v in test_env.items()})
        out["test_env"] = env

    pre = list(out.get("pre_install") or [])
    ci_exts = list(php_extensions or [])
    ext_cmd = php_docker_ext_install_cmd_for_extensions(
        php_extensions_to_install([], extra_from_ci=ci_exts)
    )
    if ext_cmd and ext_cmd not in pre:
        pre.append(ext_cmd)

    tools = [str(t).lower() for t in (php_tools or [])]
    if any("composer" in t for t in tools):
        snap = (
            "curl -sS https://getcomposer.org/installer | "
            "php -- --install-dir=/usr/local/bin --filename=composer 2>/dev/null || true"
        )
        if snap not in pre:
            pre.append(snap)
    if pre:
        out["pre_install"] = pre
    return ensure_php_docker_specs(out, language="php")


def apply_self_hosting_composer_install(
    cfg: dict[str, Any],
    repo: Path,
    *,
    ci_runs: Iterable[str] | None = None,
) -> dict[str, Any]:
    """When repo ships ``bin/composer``, use two-step bootstrap install (CI pattern)."""
    if not repo_has_bin_composer(repo):
        return cfg
    runs = list(ci_runs or [])
    if runs and count_composer_install_in_ci_runs(runs) < 1:
        return cfg
    out = dict(cfg)
    env = dict(out.get("test_env") or {})
    out["install"] = self_hosting_composer_install_cmd(test_env=env)
    return out


def merge_php_harness_fields_after_llm(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if str(before.get("language") or "").lower() != "php" and "phpunit" not in str(
        before.get("test_cmd") or ""
    ).lower():
        return after
    out = dict(after)
    for key in (
        "install",
        "test_cmd",
        "pre_install",
        "post_install",
        "apt-pkgs",
        "docker_specs",
        "language",
        "php_test_runner",
        "eval_commands",
    ):
        if before.get(key) and not out.get(key):
            out[key] = before[key]
    from .harness_guards import restore_test_cmd_if_invalid

    out = restore_test_cmd_if_invalid(before, out, language="php")
    tc_before = str(before.get("test_cmd") or "")
    tc_after = str(out.get("test_cmd") or "")
    if "phpunit" in tc_before.lower() and "pytest" in tc_after.lower():
        out["test_cmd"] = tc_before
    return ensure_php_docker_specs(out, language="php")
