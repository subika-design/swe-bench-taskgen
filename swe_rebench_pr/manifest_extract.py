"""Map repository manifests to apt packages and docker_specs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Composer ext-* -> Debian dev packages (Ubuntu bookworm).
_COMPOSER_EXT_APT: dict[str, tuple[str, ...]] = {
    "zip": ("libzip-dev", "unzip"),
    "xml": ("libxml2-dev",),
    "xmlreader": ("libxml2-dev",),
    "xmlwriter": ("libxml2-dev",),
    "curl": ("libcurl4-openssl-dev",),
    "mbstring": ("libonig-dev",),
    "intl": ("libicu-dev",),
    "gd": ("libpng-dev", "libjpeg-dev", "libfreetype-dev"),
    "imagick": ("libmagickwand-dev",),
    "pgsql": ("libpq-dev",),
    "pdo_pgsql": ("libpq-dev",),
    "pdo_mysql": ("default-libmysqlclient-dev",),
    "sqlite3": ("libsqlite3-dev",),
    "xsl": ("libxslt1-dev",),
    "soap": ("libxml2-dev",),
    "sodium": ("libsodium-dev",),
    "bz2": ("libbz2-dev",),
    "ldap": ("libldap-dev",),
    "ffi": ("libffi-dev",),
}

_CARGO_LINK_APT: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"openssl", re.I), ("libssl-dev", "pkg-config")),
    (re.compile(r"pq|postgres", re.I), ("libpq-dev",)),
    (re.compile(r"sqlite", re.I), ("libsqlite3-dev",)),
)


def composer_ext_apt_packages(repo: Path) -> list[str]:
    """Debian packages implied by ``composer.json`` ``ext-*`` requirements."""
    composer = repo / "composer.json"
    if not composer.is_file():
        return []
    try:
        data = json.loads(composer.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    reqs = data.get("require") or {}
    if not isinstance(reqs, dict):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for key in reqs:
        if not isinstance(key, str) or not key.lower().startswith("ext-"):
            continue
        ext = key[4:].lower()
        for pkg in _COMPOSER_EXT_APT.get(ext, ()):
            if pkg not in seen:
                seen.add(pkg)
                out.append(pkg)
    return out


def cargo_manifest_apt_packages(repo: Path) -> list[str]:
    """Apt hints from ``Cargo.toml`` dependency names."""
    cargo = repo / "Cargo.toml"
    if not cargo.is_file():
        return []
    try:
        text = cargo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for pat, pkgs in _CARGO_LINK_APT:
        if pat.search(text):
            for pkg in pkgs:
                if pkg not in seen:
                    seen.add(pkg)
                    out.append(pkg)
    return out


def apply_manifest_docker_specs(
    cfg: dict[str, Any],
    repo: Path,
    language: str,
) -> dict[str, Any]:
    """Fill ``docker_specs`` from manifests when not already set."""
    lang = language.strip().lower()
    out = dict(cfg)
    specs = dict(out.get("docker_specs") or {}) if isinstance(out.get("docker_specs"), dict) else {}

    if lang == "php":
        from .php_build import ensure_php_docker_specs, resolve_php_version_for_repo

        pv = resolve_php_version_for_repo(repo)
        if pv and not specs.get("php_version"):
            specs["php_version"] = pv
        out["docker_specs"] = specs
        return ensure_php_docker_specs(out, repo=repo, language="php")

    if lang == "javascript":
        from .js_build import ensure_js_docker_specs, resolve_node_version_for_repo

        nv = resolve_node_version_for_repo(repo)
        if nv and not specs.get("node_version"):
            specs["node_version"] = nv
        out["docker_specs"] = specs
        return ensure_js_docker_specs(out, repo=repo, language="javascript")

    if lang == "ruby":
        from .ruby_build import ensure_ruby_docker_specs, resolve_ruby_version_for_repo

        rv = resolve_ruby_version_for_repo(repo)
        if rv and not specs.get("ruby_version"):
            specs["ruby_version"] = rv
        out["docker_specs"] = specs
        return ensure_ruby_docker_specs(out, repo=repo, language="ruby")

    if lang == "go":
        from .go_build import ensure_go_docker_specs, resolve_go_version_for_repo

        gv = resolve_go_version_for_repo(repo)
        if gv and not specs.get("go_version"):
            specs["go_version"] = gv
        out["docker_specs"] = specs
        return ensure_go_docker_specs(out, repo=repo, language="go")

    return out


def manifest_apt_packages(repo: Path, language: str) -> list[str]:
    lang = language.strip().lower()
    if lang == "php":
        return composer_ext_apt_packages(repo)
    if lang == "rust":
        return cargo_manifest_apt_packages(repo)
    return []


def merge_manifest_into_config(cfg: dict[str, Any], repo: Path, language: str) -> dict[str, Any]:
    from .apt_from_log import merge_apt_into_config

    out = apply_manifest_docker_specs(cfg, repo, language)
    apt = manifest_apt_packages(repo, language)
    if apt:
        out = merge_apt_into_config(out, apt)
    return out
