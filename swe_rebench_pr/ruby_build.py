"""Ruby/Bundler helpers for SWE-bench harness Docker image builds."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from .apt_from_log import ensure_base_build_apt, merge_apt_into_config, remediate_apt_install_from_log

RubyTestRunner = Literal["rspec", "minitest"]

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


def _gemfile_text(repo: Path) -> str:
    gemfile = repo / "Gemfile"
    if not gemfile.is_file():
        return ""
    try:
        return gemfile.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ""


def _path_likely_rspec(rel: str) -> bool:
    low = rel.replace("\\", "/").lower()
    return low.endswith("_spec.rb") or "/spec/" in low


def _path_likely_minitest(rel: str) -> bool:
    low = rel.replace("\\", "/").lower().lstrip("/")
    return low.endswith("_test.rb") and (low.startswith("test/") or "/test/" in low)


def detect_ruby_test_runner(repo: Path, test_paths: list[str] | None = None) -> RubyTestRunner:
    """
    Detect RSpec vs Minitest from repo layout, Gemfile, and ``test_patch`` paths.

    RuboCop-style ``spec/*_spec.rb`` → rspec; Rails ``test/*_test.rb`` → minitest.
    """
    if (repo / ".rspec").is_file():
        return "rspec"
    gem = _gemfile_text(repo)
    has_rspec = "rspec" in gem
    has_minitest = "minitest" in gem
    paths = [p for p in (test_paths or []) if isinstance(p, str) and p.strip()]
    if paths:
        rspec_hits = sum(1 for p in paths if _path_likely_rspec(p))
        mini_hits = sum(1 for p in paths if _path_likely_minitest(p))
        if rspec_hits and not mini_hits:
            return "rspec"
        if mini_hits and not rspec_hits:
            return "minitest"
        if mini_hits >= rspec_hits:
            return "minitest"
        return "rspec"
    if (repo / "spec").is_dir() and (repo / "spec" / "spec_helper.rb").is_file():
        return "rspec"
    if has_rspec and not has_minitest:
        return "rspec"
    if has_minitest and not has_rspec:
        return "minitest"
    if (repo / "test").is_dir() and not (repo / "spec").is_dir():
        return "minitest"
    return "rspec"


def runner_from_install_config(
    cfg: dict[str, Any],
    repo: Path | None = None,
    *,
    test_paths: list[str] | None = None,
) -> RubyTestRunner:
    explicit = str(cfg.get("ruby_test_runner") or "").strip().lower()
    if explicit in ("rspec", "minitest"):
        return explicit  # type: ignore[return-value]
    tc = str(cfg.get("test_cmd") or "").lower()
    if "minitest" in tc or "rake test" in tc or "rails test" in tc:
        return "minitest"
    if "rspec" in tc:
        return "rspec"
    if repo is not None:
        return detect_ruby_test_runner(repo, test_paths)
    return "rspec"


def ruby_test_cmd_for_runner(runner: RubyTestRunner) -> str:
    """Discover-time ``test_cmd`` template (``__JUNIT_OUT__`` substituted in harness)."""
    if runner == "minitest":
        return (
            "bundle exec rake test TESTOPTS='--junit' 2>/dev/null || "
            "bundle exec ruby -Itest"
        )
    return (
        "bundle exec rspec --format RspecJunitFormatter --out __JUNIT_OUT__ "
        "2>/dev/null || bundle exec rspec"
    )


def _ruby_post_install_junit_formatter_lines(runner: RubyTestRunner) -> list[str]:
    lines = ["gem install bundler -N 2>/dev/null || true"]
    if runner == "minitest":
        lines.append("gem install minitest-reporters -N 2>/dev/null || true")
    else:
        lines.extend(
            [
                "gem install rspec_junit_formatter -N 2>/dev/null || true",
                "bundle add --group test rspec_junit_formatter 2>/dev/null || true",
            ]
        )
    return lines


def _merge_ruby_post_install(cfg: dict[str, Any], runner: RubyTestRunner | None = None) -> dict[str, Any]:
    out = dict(cfg)
    r = runner or runner_from_install_config(out)
    post = list(out.get("post_install") or [])
    for line in _ruby_post_install_junit_formatter_lines(r):
        if line not in post:
            post.append(line)
    out["post_install"] = post
    return out


def apply_ruby_runner_to_config(
    cfg: dict[str, Any],
    repo: Path,
    *,
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Set ``ruby_test_runner`` and runner-appropriate ``test_cmd`` / post_install."""
    out = dict(cfg)
    runner = runner_from_install_config(out, repo, test_paths=test_paths)
    out["ruby_test_runner"] = runner
    out["language"] = "ruby"
    tc = str(out.get("test_cmd") or "").strip()
    if not tc or tc == "true" or "pytest" in tc.lower():
        out["test_cmd"] = ruby_test_cmd_for_runner(runner)
    elif runner == "rspec" and "rspec" not in tc.lower():
        out["test_cmd"] = ruby_test_cmd_for_runner("rspec")
    elif runner == "minitest" and "rspec" in tc.lower() and "minitest" not in tc.lower():
        out["test_cmd"] = ruby_test_cmd_for_runner("minitest")
    return _merge_ruby_post_install(out, runner)


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


def rspec_junit_nodeid_in_test_patch_paths(nodeid: str, paths: list[str]) -> bool:
    """Match RSpec JUnit node ids (``spec/foo_spec.rb::example``) to ``test_patch`` paths."""
    if not nodeid or not paths:
        return False
    from .diff_split import _nodeid_leading_relpath, _ruby_path_basename_aliases

    head = _nodeid_leading_relpath(nodeid).replace("\\", "/").strip().lstrip("/")
    if not head:
        return False
    path_set = set()
    for raw in paths:
        rel = raw.replace("\\", "/").strip().lstrip("/")
        if not rel:
            continue
        path_set.add(rel)
        path_set.add(Path(rel).name)
        path_set.update(_ruby_path_basename_aliases(rel))
    if head in path_set:
        return True
    head_name = Path(head).name
    if head_name in path_set:
        return True
    for p in path_set:
        if p.endswith(head) or head.endswith(p):
            return True
    return False


def ruby_install_config_for_repo(
    repo: Path,
    *,
    base: dict[str, Any] | None = None,
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    from .languages import get_language_spec

    cfg = dict(base or get_language_spec("ruby").default_install_config)
    cfg = ensure_ruby_docker_specs(cfg, repo=repo, language="ruby")
    cfg = merge_apt_into_config(cfg, list(_RUBY_APT_BASE))
    apt = _filter_ruby_apt_packages(list(cfg.get("apt-pkgs") or []))
    if apt:
        cfg["apt-pkgs"] = apt
    cfg["install"] = ruby_bundle_install_cmd(with_lock=(repo / "Gemfile.lock").is_file())
    cfg = apply_ruby_runner_to_config(cfg, repo, test_paths=test_paths)
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
    out = dict(out)
    runner = runner_from_install_config(out)
    if "your ruby version is" in low and "gemfile specified" in low:
        install = str(out.get("install") or "bundle install || true").strip()
        if "disable_version_check" not in install:
            if install.startswith("bundle install"):
                out["install"] = _ruby_bundle_prefix() + install
            else:
                out["install"] = ruby_bundle_install_cmd(with_lock="--jobs" in install)
    formatter_missing = any(
        needle in low
        for needle in (
            "rspecjunitformatter",
            "uninitialized constant rspecjunitformatter",
            "rspec_junit_formatter",
            "cannot load such file -- rspec_junit_formatter",
            "formatter 'rspecjunitformatter'",
            "unknown formatter",
            "minitest-reporters",
            "minitest/reporters",
        )
    )
    gem_missing = any(
        needle in low
        for needle in (
            "could not find gem",
            "gem not found",
            "bundler can't satisfy",
            "bundle install",
            "installation error",
            "bundler::gemnotfound",
        )
    )
    if formatter_missing or gem_missing:
        out = _merge_ruby_post_install(out, runner)
        install = str(out.get("install") or "").strip()
        if not install or install == "true":
            out["install"] = ruby_bundle_install_cmd(with_lock=False)
        elif "bundle install" in install and "disable_version_check" not in install:
            out["install"] = _ruby_bundle_prefix() + install
        elif gem_missing and "bundle install" not in install:
            out["install"] = ruby_bundle_install_cmd(with_lock="--jobs" in install)
    if "could not find gem 'bundler'" in low or "bundler: command not found" in low:
        out = _merge_ruby_post_install(out, runner)
    return out


def merge_ruby_harness_fields_after_llm(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if str(before.get("language") or "").lower() not in ("ruby", "rb") and "rspec" not in str(
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
        "ruby_test_runner",
    ):
        if before.get(key) and not out.get(key):
            out[key] = before[key]
    tc_before = str(before.get("test_cmd") or "")
    tc_after = str(out.get("test_cmd") or "")
    if "rspec" in tc_before.lower() and "pytest" in tc_after.lower():
        out["test_cmd"] = tc_before
    if out.get("ruby_test_runner"):
        out["test_cmd"] = ruby_test_cmd_for_runner(out["ruby_test_runner"])  # type: ignore[arg-type]
    return ensure_ruby_docker_specs(out, language="ruby")
