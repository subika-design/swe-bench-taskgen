"""CI vs heuristic install/test command precedence (family-wide, not per-repo)."""

from __future__ import annotations

import re
from typing import Any

_CI_MODERN_INSTALL_RE = re.compile(
    r"\b("
    r"pdm\s+(?:install|sync)|"
    r"uv\s+(?:sync|pip\s+install)|"
    r"poetry\s+install|"
    r"tox\s+-e|"
    r"nox\s+-|"
    r"hatch\s+run|"
    r"npm\s+ci|"
    r"pnpm\s+install|"
    r"yarn\s+(?:install|workspaces)"
    r")\b",
    re.IGNORECASE,
)

_PLAIN_PIP_INSTALLS = frozenset({"pip install -e .", "pip install ."})


def mark_ci_test_cmd_trusted(cfg: dict[str, Any], *, trusted: bool = True) -> dict[str, Any]:
    out = dict(cfg)
    if trusted:
        out["_ci_test_cmd_trusted"] = True
    else:
        out.pop("_ci_test_cmd_trusted", None)
    return out


def ci_test_cmd_trusted(cfg: dict[str, Any] | None) -> bool:
    """True when ``test_cmd`` came from CI merge and should not be replaced by runner templates."""
    if not isinstance(cfg, dict):
        return False
    if cfg.get("_ci_test_cmd_trusted"):
        return True
    tc = str(cfg.get("test_cmd") or "").strip()
    if not tc or tc == "true":
        return False
    excerpt = str(cfg.get("_ci_excerpt") or "").strip()
    if not excerpt:
        return False
    # Heuristic: excerpt documents the same command family as test_cmd.
    tc_low = tc.lower()
    if "pytest" in tc_low and "pytest" in excerpt.lower():
        return True
    if "rspec" in tc_low and "rspec" in excerpt.lower():
        return True
    if "bundle exec rake" in tc_low and "rake" in excerpt.lower():
        return True
    if "jest" in tc_low and "jest" in excerpt.lower():
        return True
    if "mocha" in tc_low and "mocha" in excerpt.lower():
        return True
    if "vitest" in tc_low and "vitest" in excerpt.lower():
        return True
    if "go test" in tc_low and "go test" in excerpt.lower():
        return True
    if "cargo test" in tc_low and "cargo test" in excerpt.lower():
        return True
    if "phpunit" in tc_low and "phpunit" in excerpt.lower():
        return True
    if "npm test" in tc_low or "npm run test" in tc_low:
        if "npm" in excerpt.lower() and "test" in excerpt.lower():
            return True
    return False


def should_preserve_ci_test_cmd(cfg: dict[str, Any]) -> bool:
    return ci_test_cmd_trusted(cfg) and bool(str(cfg.get("test_cmd") or "").strip())


def pytest_cmd_has_scoped_paths(cmd: str) -> bool:
    """True when *cmd* names concrete test files or explicit pytest filters."""
    s = str(cmd or "")
    if re.search(r"(?:^|\s)-[km]\s+", s) or "--ignore=" in s:
        return True
    skip = frozenset(
        {
            "pytest",
            "python",
            "-m",
            "||",
            "&&",
            "npm",
            "run",
            "test",
            "go",
            "cargo",
            "bundle",
            "exec",
            "tests",
            "tests/",
            "test",
            "test/",
        }
    )
    for token in s.split():
        if not token or token.startswith("-") or token.lower() in skip:
            continue
        if token.endswith(".py"):
            return True
        if "/" in token and token.endswith(".py"):
            return True
    return False


def pytest_cmd_needs_explicit_paths(cmd: str, test_paths: list[str]) -> bool:
    """True when discover should append ``test_patch`` ``.py`` paths to CI pytest cmd."""
    if not test_paths:
        return False
    if not any(p.endswith(".py") for p in test_paths):
        return False
    return not pytest_cmd_has_scoped_paths(cmd)


# Backward-compatible aliases (avoid pytest collecting ``test_*`` names).
test_cmd_has_scoped_paths = pytest_cmd_has_scoped_paths
test_cmd_needs_explicit_pytest_paths = pytest_cmd_needs_explicit_paths


def ci_install_is_modern(ci_install: str) -> bool:
    return bool(_CI_MODERN_INSTALL_RE.search(str(ci_install or "")))


def should_merge_ci_install(
    cfg: dict[str, Any],
    ci_install: str,
    defaults: dict[str, Any],
    *,
    language: str,
    overlay: dict[str, Any] | None = None,
) -> bool:
    """Whether CI install should replace heuristic/default install."""
    if not str(ci_install or "").strip():
        return False
    current = str(cfg.get("install") or "").strip()
    default_install = str(defaults.get("install") or "").strip()
    if not current or current == "true":
        return True
    if current == default_install:
        return True
    if ci_install_is_modern(ci_install):
        return True
    if current in _PLAIN_PIP_INSTALLS:
        ci_low = str(ci_install).lower()
        if "pip install" in ci_low and ("-r " in ci_low or ".[" in ci_install):
            return True
    excerpt = str((overlay or {}).get("_ci_excerpt") or cfg.get("_ci_excerpt") or "")
    if excerpt and str(ci_install).strip() in excerpt:
        return True
    from .ci_install_normalize import normalize_ci_install_command

    norm = normalize_ci_install_command(str(ci_install), language=language)
    if norm != current and excerpt and norm.split()[0] in excerpt:
        return True
    return False


def should_merge_ci_test_cmd(
    cfg: dict[str, Any],
    ci_test: str,
    defaults: dict[str, Any],
    *,
    overlay: dict[str, Any] | None = None,
) -> bool:
    """Whether CI test_cmd should replace heuristic/default test_cmd."""
    if not str(ci_test or "").strip():
        return False
    if should_preserve_ci_test_cmd(cfg):
        return False
    current = str(cfg.get("test_cmd") or "").strip()
    default_test = str(defaults.get("test_cmd") or "").strip()
    if not current or current == default_test:
        return True
    if pytest_cmd_has_scoped_paths(str(ci_test)) and not pytest_cmd_has_scoped_paths(current):
        return True
    excerpt = str((overlay or {}).get("_ci_excerpt") or cfg.get("_ci_excerpt") or "")
    probe = {"test_cmd": str(ci_test), "_ci_excerpt": excerpt}
    if ci_test_cmd_trusted(probe):
        return True
    if excerpt and str(ci_test).strip() in excerpt:
        return True
    return False
