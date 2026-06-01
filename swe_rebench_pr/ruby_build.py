"""Ruby/Bundler helpers for SWE-bench harness Docker image builds."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .apt_from_log import ensure_base_build_apt, merge_apt_into_config, remediate_apt_install_from_log

DEFAULT_RUBY_VERSION = "3.2-bookworm"

# Native libs only — never ``ruby-dev`` / Debian ``ruby3.1`` on official ``ruby:*`` images.
_RUBY_APT_BASE = ("libyaml-dev", "libxml2-dev", "libxslt1-dev", "zlib1g-dev")
_RUBY_APT_BLOCKLIST = frozenset(
    {"ruby", "ruby-dev", "ruby3.1", "ruby3.1-dev", "libruby", "libruby3.1", "rake"}
)


def _normalize_ruby_version(raw: str) -> str:
    v = raw.strip().lstrip("v")
    if not v:
        return DEFAULT_RUBY_VERSION
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", v)
    if m:
        major, minor, patch = m.group(1), m.group(2), m.group(3)
        if patch:
            # Official ``ruby:*`` images omit many ``x.y.0`` tags (e.g. ``3.1.0-bookworm``).
            if patch == "0":
                return f"{major}.{minor}-bookworm"
            return f"{major}.{minor}.{patch}-bookworm"
        return f"{major}.{minor}-bookworm"
    return DEFAULT_RUBY_VERSION


def _filter_ruby_apt_packages(packages: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for pkg in packages:
        name = str(pkg or "").strip()
        if not name or name in _RUBY_APT_BLOCKLIST or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _ruby_bundle_prefix() -> str:
    return (
        "bundle config set --local disable_version_check true 2>/dev/null || true; "
        "bundle config set --local path vendor/bundle 2>/dev/null || true; "
    )


def ruby_bundle_install_cmd(*, with_lock: bool) -> str:
    prefix = _ruby_bundle_prefix()
    if with_lock:
        return f"{prefix}bundle install --jobs 4 --retry 3"
    return f"{prefix}bundle install || true"


def resolve_ruby_version_for_repo(repo: Path) -> str | None:
    dot = repo / ".ruby-version"
    if dot.is_file():
        try:
            return _normalize_ruby_version(dot.read_text(encoding="utf-8", errors="replace").split()[0])
        except OSError:
            pass
    gemfile = repo / "Gemfile"
    if gemfile.is_file():
        try:
            text = gemfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        m = re.search(r"""ruby\s+['"]([^'"]+)['"]""", text)
        if m:
            return _normalize_ruby_version(m.group(1))
    return None


def ensure_ruby_docker_specs(
    cfg: dict[str, Any],
    *,
    repo: Path | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    lang = str(language or cfg.get("language") or "").lower()
    if lang not in ("", "ruby", "rb"):
        return cfg
    out = dict(cfg)
    specs = dict(out.get("docker_specs") or {}) if isinstance(out.get("docker_specs"), dict) else {}
    if not specs.get("ruby_version"):
        rv: str | None = None
        if repo is not None:
            rv = resolve_ruby_version_for_repo(repo)
        specs["ruby_version"] = rv or DEFAULT_RUBY_VERSION
    out["docker_specs"] = specs
    return out


def ruby_install_config_for_repo(
    repo: Path,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .languages import get_language_spec

    cfg = dict(base or get_language_spec("ruby").default_install_config)
    cfg["language"] = "ruby"
    cfg = ensure_ruby_docker_specs(cfg, repo=repo, language="ruby")
    cfg = merge_apt_into_config(cfg, list(_RUBY_APT_BASE))
    apt = _filter_ruby_apt_packages(list(cfg.get("apt-pkgs") or []))
    if apt:
        cfg["apt-pkgs"] = apt
    post = list(cfg.get("post_install") or [])
    post.extend(
        [
            "gem install bundler -N 2>/dev/null || true",
            "gem install rspec_junit_formatter -N 2>/dev/null || true",
        ]
    )
    cfg["post_install"] = post
    cfg["install"] = ruby_bundle_install_cmd(with_lock=(repo / "Gemfile.lock").is_file())
    cfg["test_cmd"] = (
        "bundle exec rspec --format RspecJunitFormatter --out __JUNIT_OUT__ "
        "2>/dev/null || bundle exec rspec"
    )
    return cfg


def remediate_ruby_install_from_log(cfg: dict[str, Any], log: str) -> dict[str, Any]:
    out = remediate_apt_install_from_log(cfg, log)
    out = merge_apt_into_config(out, list(_RUBY_APT_BASE))
    apt = _filter_ruby_apt_packages(list(out.get("apt-pkgs") or []))
    if apt:
        out["apt-pkgs"] = apt
    elif "apt-pkgs" in out:
        out["apt-pkgs"] = []
    low = log.lower()
    if "your ruby version is" in low and "gemfile specified" in low:
        out = dict(out)
        install = str(out.get("install") or "bundle install || true").strip()
        if "disable_version_check" not in install:
            if install.startswith("bundle install"):
                out["install"] = _ruby_bundle_prefix() + install
            else:
                out["install"] = ruby_bundle_install_cmd(with_lock="--jobs" in install)
    return out


def merge_ruby_harness_fields_after_llm(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if str(before.get("language") or "").lower() not in ("ruby", "rb") and "rspec" not in str(
        before.get("test_cmd") or ""
    ).lower():
        return after
    out = dict(after)
    for key in ("install", "test_cmd", "pre_install", "post_install", "apt-pkgs", "docker_specs", "language"):
        if before.get(key) and not out.get(key):
            out[key] = before[key]
    tc_before = str(before.get("test_cmd") or "")
    tc_after = str(out.get("test_cmd") or "")
    if "rspec" in tc_before.lower() and "pytest" in tc_after.lower():
        out["test_cmd"] = tc_before
    return ensure_ruby_docker_specs(out, language="ruby")
