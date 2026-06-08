"""Go toolchain helpers for SWE-bench harness Docker image builds."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DEFAULT_GO_VERSION = "1.22.12"

# Minor series -> latest patch tarball on dl.google.com/go (SWE-bench go constants).
_GO_MINOR_TO_PATCH: dict[str, str] = {
    "1.17": "1.17.13",
    "1.18": "1.18.10",
    "1.19": "1.19.13",
    "1.20": "1.20.14",
    "1.21": "1.21.13",
    "1.22": "1.22.12",
    "1.23": "1.23.8",
    "1.24": "1.24.2",
}

_GO_VERSION_STRIP_RE = re.compile(r"^[\^~>=<v]+", re.IGNORECASE)
_GO_TEST_FUNC_IN_PATCH_RE = re.compile(r"^\+.*\bfunc\s+(Test\w+)\s*\(", re.MULTILINE)


def normalize_go_version(raw: str) -> str:
    """
    Map CI semver ranges, ``go.mod`` directives, or partial versions to a full
    Go release tarball tag (e.g. ``^1.22`` → ``1.22.12``).
    """
    v = str(raw or "").strip().removeprefix("go").strip()
    v = _GO_VERSION_STRIP_RE.sub("", v).strip()
    if not v:
        return DEFAULT_GO_VERSION
    if re.fullmatch(r"\d+\.\d+\.\d+", v):
        return v
    m = re.fullmatch(r"(\d+)\.(\d+)", v)
    if m:
        minor = f"{m.group(1)}.{m.group(2)}"
        return _GO_MINOR_TO_PATCH.get(minor, DEFAULT_GO_VERSION)
    return DEFAULT_GO_VERSION


def _normalize_go_version(raw: str) -> str:
    """Backward-compatible alias."""
    return normalize_go_version(raw)


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
    return normalize_go_version(m.group(1))


def go_packages_from_test_paths(paths: list[str]) -> list[str]:
    """``./pkg`` import paths for ``go test`` from ``*_test.go`` file paths."""
    pkgs: set[str] = set()
    for raw in paths:
        if not raw.endswith("_test.go"):
            continue
        parts = raw.replace("\\", "/").split("/")
        if len(parts) <= 1:
            pkgs.add("./...")
            continue
        pkg_dir = "/".join(parts[:-1])
        pkgs.add(f"./{pkg_dir}")
    return sorted(pkgs) or ["./..."]


def go_test_names_from_test_patch(test_patch: str) -> set[str]:
    """``Test*`` function names introduced/modified in a unified test patch."""
    names: set[str] = set()
    if not test_patch:
        return names
    for m in _GO_TEST_FUNC_IN_PATCH_RE.finditer(test_patch):
        names.add(m.group(1))
    return names


def _go_test_name_guess_from_file(path: str) -> set[str]:
    """
    Heuristic: ``bash_completions_test.go`` → ``TestBashCompletions``.

    Used when the patch does not include explicit ``func Test*`` lines.
    """
    name = Path(path.replace("\\", "/")).name
    if not name.endswith("_test.go"):
        return set()
    stem = name[: -len("_test.go")]
    if not stem:
        return set()
    parts = [p for p in stem.split("_") if p]
    if not parts:
        return set()
    pascal = "".join(p[:1].upper() + p[1:] for p in parts)
    return {f"Test{pascal}"} if pascal else set()


def gotest_log_key_in_test_patch_paths(
    nodeid: str,
    paths: list[str],
    *,
    test_patch: str = "",
) -> bool:
    """
    Match ``go test -v`` log keys (``TestFoo``, ``TestFoo/sub``) to ``test_patch`` files.

    SWE-bench grading uses these names in ``FAIL_TO_PASS``, not ``*_test.go`` paths.
    """
    if not nodeid or not paths:
        return False
    root = nodeid.split("/", 1)[0].strip()
    if not root.startswith("Test"):
        return False
    names = go_test_names_from_test_patch(test_patch)
    for raw in paths:
        names |= _go_test_name_guess_from_file(raw)
        low_path = raw.replace("\\", "/").lower()
        if Path(low_path).name.replace("_test.go", "") in root.lower():
            return True
    if root in names:
        return True
    return any(name in root or root.startswith(name + "/") for name in names)


def resolve_go_test_invocation(
    test_cmd: str | None,
    targets: list[str],
) -> str:
    """
    Shell command for discover-time ``go test`` (single line, no redirects).

    Prefer CI ``test_cmd`` when it uses ``-run``; otherwise scope to packages
    touched by ``*_test.go`` paths in the patch.
    """
    tc = str(test_cmd or "").strip()
    scoped_pkgs = go_packages_from_test_paths(targets)
    if tc and re.search(r"-run\b", tc):
        if "-count=" not in tc:
            if tc.startswith("go test"):
                tc = tc.replace("go test", "go test -count=1", 1)
            else:
                tc = f"go test -count=1 {tc}"
        return tc
    use_scoped = bool(targets) and scoped_pkgs != ["./..."]
    if (
        tc
        and re.search(r"\bgo\s+test\b", tc)
        and "./" in tc
        and not use_scoped
    ):
        if "-count=" not in tc and "-v" not in tc:
            tc = tc.replace("go test", "go test -v -count=1", 1)
        elif "-count=" not in tc:
            tc = tc.replace("go test", "go test -count=1", 1)
        return tc
    return f"go test -v -count=1 {' '.join(scoped_pkgs)}"


def remediate_go_install_from_log(
    cfg: dict[str, Any],
    log_tail: str,
    *,
    docker_exit: int = 0,
    n_patch: int = 0,
) -> dict[str, Any]:
    """Avoid C/cmake apt heuristics when Go tests ran or only scope/labels failed."""
    out = dict(cfg)
    low = (log_tail or "").lower()
    if docker_exit == 0 and (
        n_patch > 0
        or "[docker] go test" in low
        or "--- pass:" in low
        or "--- fail:" in low
    ):
        return out
    apt = list(out.get("apt-pkgs") or [])
    for pkg in ("git", "ca-certificates"):
        if pkg not in apt:
            apt.append(pkg)
    if apt:
        out["apt-pkgs"] = apt
    return out


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
    raw = str(specs.get("go_version") or "").strip()
    if raw:
        specs["go_version"] = normalize_go_version(raw)
    else:
        gv: str | None = None
        if repo is not None:
            gv = resolve_go_version_for_repo(repo)
        specs["go_version"] = gv or DEFAULT_GO_VERSION
    out["docker_specs"] = specs
    return out


def go_install_config_for_repo(
    repo: Path,
    *,
    base: dict[str, Any] | None = None,
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Heuristic ``install_config`` for Go repos from ``go.mod`` and test paths."""
    from .languages import get_language_spec

    cfg = dict(base or get_language_spec("go").default_install_config)
    cfg["language"] = "go"
    cfg = ensure_go_docker_specs(cfg, repo=repo, language="go")
    cfg["install"] = "go mod download"
    paths = list(test_paths or [])
    cfg["test_cmd"] = resolve_go_test_invocation(str(cfg.get("test_cmd") or ""), paths)
    return cfg


def merge_go_build_into_config(
    cfg: dict[str, Any],
    repo: Path,
    test_paths: list[str],
) -> dict[str, Any]:
    """Apply Go heuristics before Docker discover / image build."""
    from .ci_fidelity import should_preserve_ci_test_cmd

    tc_in = str(cfg.get("test_cmd") or "").strip()
    base = go_install_config_for_repo(repo, base=cfg, test_paths=test_paths)
    out = dict(cfg)
    if should_preserve_ci_test_cmd(out) and re.search(r"-run\b", tc_in):
        out = ensure_go_docker_specs(out, repo=repo, language="go")
        if not str(out.get("install") or "").strip():
            out["install"] = base.get("install")
    else:
        for key in ("install", "test_cmd", "docker_specs"):
            if base.get(key):
                out[key] = base[key]
    out["language"] = "go"
    return out
