"""Go toolchain helpers for SWE-bench harness Docker image builds."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DEFAULT_GO_VERSION = "1.22.12"

# Minor series -> latest patch tarball on dl.google.com/go (SWE-bench go constants).
_GO_MINOR_TO_PATCH: dict[str, str] = {
    "1.18": "1.18.10",
    "1.19": "1.19.13",
    "1.20": "1.20.14",
    "1.21": "1.21.13",
    "1.22": "1.22.12",
    "1.23": "1.23.8",
    "1.24": "1.24.2",
}


def _normalize_go_version(raw: str) -> str:
    """Map ``go.mod`` directive or partial version to a full Go release tarball tag."""
    v = raw.strip().removeprefix("go").strip()
    if not v:
        return DEFAULT_GO_VERSION
    if re.fullmatch(r"\d+\.\d+\.\d+", v):
        return v
    m = re.fullmatch(r"(\d+)\.(\d+)", v)
    if m:
        minor = f"{m.group(1)}.{m.group(2)}"
        return _GO_MINOR_TO_PATCH.get(minor, DEFAULT_GO_VERSION)
    return DEFAULT_GO_VERSION


def resolve_go_version_for_repo(repo: Path) -> str | None:
    """Read ``go`` directive from ``go.mod`` when present."""
    go_mod = repo / "go.mod"
    if not go_mod.is_file():
        return None
    try:
        text = go_mod.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^\s*go\s+(\S+)", text, re.MULTILINE)
    if not m:
        return None
    return _normalize_go_version(m.group(1))


def ensure_go_docker_specs(
    cfg: dict[str, Any],
    *,
    repo: Path | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Set ``docker_specs.go_version`` for harness Go base image builds."""
    lang = str(language or cfg.get("language") or "").lower()
    if lang not in ("", "go", "golang"):
        return cfg
    out = dict(cfg)
    specs = dict(out.get("docker_specs") or {}) if isinstance(out.get("docker_specs"), dict) else {}
    if not specs.get("go_version"):
        gv: str | None = None
        if repo is not None:
            gv = resolve_go_version_for_repo(repo)
        specs["go_version"] = gv or DEFAULT_GO_VERSION
    out["docker_specs"] = specs
    return out
