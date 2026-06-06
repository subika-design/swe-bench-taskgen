"""Rust/Cargo helpers for SWE-bench harness Docker image builds."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .apt_from_log import ensure_base_build_apt, merge_apt_into_config, remediate_apt_install_from_log

DEFAULT_RUST_VERSION = "1.81-bookworm"
# Minimum toolchain for ``edition = "2024"`` manifests (e.g. ripgrep at rust-version 1.85).
EDITION_2024_MIN_RUST_VERSION = "1.85-bookworm"

_RUST_APT_BASE = ("libssl-dev", "pkg-config")
_CARGO_RUST_VERSION_RE = re.compile(r'rust-version\s*=\s*"([^"]+)"', re.MULTILINE)
_CARGO_EDITION_RE = re.compile(r'edition\s*=\s*"(\d{4})"', re.MULTILINE)

_CFG_FEATURE_RE = re.compile(
    r"#!\[cfg\(feature\s*=\s*\"([^\"]+)\"\)\]|#\[cfg\(feature\s*=\s*\"([^\"]+)\"\)\]"
)
_CARGO_FEATURE_NAME_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*=", re.MULTILINE)
_CI_CARGO_FEATURES_RE = re.compile(
    r"cargo\s+(?:test|build|clippy|miri\s+test)[^\n]*--features\s+([^\n\\]+)"
)
_CI_MATRIX_FEATURES_RE = re.compile(r"features:\s*\[([^\]]+)\]")


def _normalize_rust_version(raw: str) -> str:
    v = raw.strip()
    if not v:
        return DEFAULT_RUST_VERSION
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", v)
    if m:
        return f"{m.group(1)}.{m.group(2)}-bookworm"
    return DEFAULT_RUST_VERSION


def _max_rust_version(a: str, b: str) -> str:
    """Pick the newer of two ``major.minor-bookworm`` tags."""
    def key(v: str) -> tuple[int, int]:
        m = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?-bookworm", v.strip())
        if not m:
            return (0, 0)
        return (int(m.group(1)), int(m.group(2)))

    return a if key(a) >= key(b) else b


def resolve_rust_version_from_cargo_toml(repo: Path) -> str | None:
    """Read ``rust-version`` / ``edition`` from ``Cargo.toml`` (package or workspace)."""
    cargo = repo / "Cargo.toml"
    if not cargo.is_file():
        return None
    try:
        text = cargo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rv: str | None = None
    m = _CARGO_RUST_VERSION_RE.search(text)
    if m:
        rv = _normalize_rust_version(m.group(1).strip())
    for em in _CARGO_EDITION_RE.finditer(text):
        if int(em.group(1)) >= 2024:
            rv = _max_rust_version(rv or DEFAULT_RUST_VERSION, EDITION_2024_MIN_RUST_VERSION)
    return rv


def resolve_rust_version_for_repo(repo: Path) -> str | None:
    from_cargo = resolve_rust_version_from_cargo_toml(repo)
    if from_cargo:
        return from_cargo
    toolchain = repo / "rust-toolchain.toml"
    if toolchain.is_file():
        try:
            text = toolchain.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        m = re.search(r'channel\s*=\s*"([^"]+)"', text)
        if m:
            ch = m.group(1).strip()
            if ch.startswith("stable"):
                return DEFAULT_RUST_VERSION
            return _normalize_rust_version(ch.removeprefix("stable").strip())
    toolchain_legacy = repo / "rust-toolchain"
    if toolchain_legacy.is_file():
        try:
            line = toolchain_legacy.read_text(encoding="utf-8", errors="replace").split()[0]
            return _normalize_rust_version(line)
        except OSError:
            pass
    return None


def _read_test_cfg_features(repo: Path, rel_paths: Iterable[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for rel in rel_paths:
        path = repo / rel
        if not path.is_file():
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:8192]
        except OSError:
            continue
        for m in _CFG_FEATURE_RE.finditer(head):
            feat = (m.group(1) or m.group(2) or "").strip()
            if feat and feat not in seen:
                seen.add(feat)
                found.append(feat)
    return found


def _parse_cargo_feature_names(repo: Path) -> set[str]:
    cargo = repo / "Cargo.toml"
    if not cargo.is_file():
        return set()
    try:
        text = cargo.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    m = re.search(r"(?ms)^\[features\]\s*(.*?)(?=^\[|\Z)", text)
    if not m:
        return set()
    return {match.group(1) for match in _CARGO_FEATURE_NAME_RE.finditer(m.group(1))}


def _parse_ci_cargo_features(repo: Path) -> list[str]:
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    matrix_features: list[str] = []
    direct_features: list[str] = []
    for wf in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
        try:
            text = wf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _CI_MATRIX_FEATURES_RE.finditer(text):
            for part in m.group(1).split(","):
                feat = part.strip().strip("'\"")
                if feat and feat not in matrix_features:
                    matrix_features.append(feat)
        for m in _CI_CARGO_FEATURES_RE.finditer(text):
            val = m.group(1).strip().strip("'\"")
            if not val or val.startswith("${{"):
                continue
            for part in re.split(r"[\s,]+", val):
                feat = part.strip().strip("'\"")
                if feat and feat not in direct_features:
                    direct_features.append(feat)
    if matrix_features:
        return [matrix_features[0]]
    if direct_features:
        return [direct_features[0]]
    return []


def _resolve_cargo_feature_names(raw: list[str], cargo_features: set[str]) -> list[str]:
    """Prefer CI-style umbrella features (e.g. ``fancy`` over ``fancy-no-backtrace``)."""
    resolved: list[str] = []
    seen: set[str] = set()
    for feat in raw:
        pick = feat
        if feat.endswith("-no-backtrace"):
            prefix = feat[: -len("-no-backtrace")]
            if prefix and prefix in cargo_features:
                pick = prefix
        elif feat == "fancy-no-backtrace" and "fancy" in cargo_features:
            pick = "fancy"
        if pick not in seen:
            seen.add(pick)
            resolved.append(pick)
    return resolved


def resolve_cargo_features(repo: Path, targets: list[str] | None = None) -> list[str]:
    """Features required to compile/run scoped integration tests (``tests/*.rs`` cfg gates)."""
    rel_targets = [p for p in (targets or []) if isinstance(p, str) and p.strip()]
    raw = _read_test_cfg_features(repo, rel_targets)
    if not raw:
        raw = _parse_ci_cargo_features(repo)
    if not raw:
        return []
    return _resolve_cargo_feature_names(raw, _parse_cargo_feature_names(repo))


def cargo_features_flag(features: list[str]) -> str:
    kept = [str(f).strip() for f in features if str(f).strip()]
    if not kept:
        return ""
    return f"--features {','.join(kept)}"


def apply_cargo_features_to_config(cfg: dict[str, Any], features: list[str]) -> dict[str, Any]:
    kept = resolve_cargo_features_from_list(features)
    if not kept:
        return cfg
    flag = cargo_features_flag(kept)
    out = dict(cfg)
    out["cargo_features"] = kept
    out["install"] = f"cargo build --tests {flag} || cargo build {flag}".strip()
    out["test_cmd"] = f"cargo test --no-fail-fast {flag}".strip()
    return out


def resolve_cargo_features_from_list(features: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for feat in features:
        name = str(feat or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def ensure_cargo_features_in_config(
    cfg: dict[str, Any],
    repo: Path,
    targets: list[str] | None = None,
) -> dict[str, Any]:
    if cfg.get("cargo_features"):
        return cfg
    feats = resolve_cargo_features(repo, targets)
    if not feats:
        return cfg
    return apply_cargo_features_to_config(cfg, feats)


def ensure_rust_docker_specs(
    cfg: dict[str, Any],
    *,
    repo: Path | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    lang = str(language or cfg.get("language") or "").lower()
    if lang not in ("", "rust", "rs"):
        return cfg
    out = dict(cfg)
    specs = dict(out.get("docker_specs") or {}) if isinstance(out.get("docker_specs"), dict) else {}
    if not specs.get("rust_version"):
        rv: str | None = None
        if repo is not None:
            rv = resolve_rust_version_for_repo(repo)
        specs["rust_version"] = rv or DEFAULT_RUST_VERSION
    out["docker_specs"] = specs
    return out


def rust_install_config_for_repo(
    repo: Path,
    *,
    base: dict[str, Any] | None = None,
    targets: list[str] | None = None,
) -> dict[str, Any]:
    from .languages import get_language_spec

    cfg = dict(base or get_language_spec("rust").default_install_config)
    cfg["language"] = "rust"
    cfg = ensure_rust_docker_specs(cfg, repo=repo, language="rust")
    cfg = merge_apt_into_config(cfg, list(_RUST_APT_BASE))
    cfg["install"] = "cargo build --tests || cargo build"
    cfg["test_cmd"] = "cargo test --no-fail-fast"
    cfg = ensure_cargo_features_in_config(cfg, repo, targets)
    return cfg


def log_indicates_rust_manifest_parse_failure(log: str) -> bool:
    low = (log or "").lower()
    return "failed to parse manifest" in low and "cargo.toml" in low


def remediate_rust_version_from_log(
    cfg: dict[str, Any],
    log: str,
    *,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Bump ``docker_specs.rust_version`` when Cargo rejects the manifest."""
    if not log_indicates_rust_manifest_parse_failure(log):
        return cfg
    out = dict(cfg)
    specs = dict(out.get("docker_specs") or {}) if isinstance(out.get("docker_specs"), dict) else {}
    current = str(specs.get("rust_version") or DEFAULT_RUST_VERSION)
    bumped = current
    if repo is not None:
        from_repo = resolve_rust_version_from_cargo_toml(repo) or resolve_rust_version_for_repo(repo)
        if from_repo:
            bumped = _max_rust_version(current, from_repo)
    if bumped == current and "edition" in log.lower():
        bumped = _max_rust_version(current, EDITION_2024_MIN_RUST_VERSION)
    if bumped != current:
        specs["rust_version"] = bumped
        out["docker_specs"] = specs
    return out


def remediate_rust_install_from_log(
    cfg: dict[str, Any],
    log: str,
    *,
    repo: Path | None = None,
    targets: list[str] | None = None,
) -> dict[str, Any]:
    out = remediate_apt_install_from_log(cfg, log)
    out = merge_apt_into_config(out, list(_RUST_APT_BASE))
    out = remediate_rust_version_from_log(out, log, repo=repo)
    low = log.lower()
    zero_tests = "running 0 tests" in low or (
        "test result:" in low and "0 passed" in low and "0 failed" in low
    )
    if repo is not None and zero_tests and not out.get("cargo_features"):
        out = ensure_cargo_features_in_config(out, repo, targets)
    return out


def merge_rust_harness_fields_after_llm(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if str(before.get("language") or "").lower() not in ("rust", "rs") and "cargo" not in str(
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
        "cargo_features",
    ):
        if before.get(key) and not out.get(key):
            out[key] = before[key]
    tc_before = str(before.get("test_cmd") or "")
    tc_after = str(out.get("test_cmd") or "")
    if "cargo test" in tc_before.lower() and "pytest" in tc_after.lower():
        out["test_cmd"] = tc_before
    return ensure_rust_docker_specs(out, language="rust")
