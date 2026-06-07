"""C / CMake / Premake install helpers for SWE-bench Docker discover and LLM remediation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .apt_from_log import (
    apt_packages_from_build_log,
    merge_apt_into_config,
    remediate_apt_install_from_log,
)

_CMAKE_BASE_APT = ("cmake",)
_PREMAKE_BASE_APT = ("uuid-dev",)
_MESON_BASE_APT = ("meson", "ninja-build")
_C_COMMON_APT = ("git", "build-essential", "pkg-config")
_MESON_NOT_FOUND_RE = re.compile(r"\bmeson:\s*command not found\b", re.I)
_NINJA_NOT_FOUND_RE = re.compile(r"\bninja:\s*command not found\b", re.I)
_MESON_INSTALL = (
    'meson setup build --wipe 2>/dev/null || rm -rf build && meson setup build && '
    'meson compile -C build -j"$(nproc)"'
)
_MESON_TEST_CMD = "meson test -C build --print-errorlogs"
_CMAKE_FIND_PACKAGE_RE = re.compile(r"find_package\s*\(\s*([A-Za-z0-9_+\-.]+)", re.IGNORECASE)
_CMAKE_PKG_CHECK_RE = re.compile(r"pkg_check_modules\s*\([^)]*\b([A-Za-z0-9_+\-.]+)\b", re.IGNORECASE)
_CMAKE_PACKAGE_APT: dict[str, tuple[str, ...]] = {
    "openssl": ("libssl-dev",),
    "libpsl": ("libpsl-dev",),
    "nghttp2": ("libnghttp2-dev",),
    "libidn2": ("libidn2-dev",),
    "brotli": ("libbrotli-dev",),
    "zstd": ("libzstd-dev",),
    "zlib": ("zlib1g-dev",),
}
_PREMAKE_DECLARE_RE = re.compile(
    r"""test\.declare\s*\(\s*["']([^"']+)["']\s*\)""",
)
_PREMAKE_INSTALL = "PLATFORM=x64 CONFIG=release ./Bootstrap.sh"


def is_premake_repo(repo: Path) -> bool:
    """True when the repo bootstraps via Premake's ``Bootstrap.*`` scripts."""
    return (
        (repo / "premake5.lua").is_file()
        and ((repo / "Bootstrap.mak").is_file() or (repo / "Bootstrap.sh").is_file())
    )


def is_meson_repo(repo: Path) -> bool:
    """True for native C/C++ Meson projects (not Python meson-python backends)."""
    if not (repo / "meson.build").is_file():
        return False
    from .repo_detect import repo_uses_meson_python_backend

    if repo_uses_meson_python_backend(repo) and (repo / "pyproject.toml").is_file():
        return False
    return True


def is_meson_config(cfg: dict[str, Any], *, repo: Path | None = None) -> bool:
    if str(cfg.get("c_build_system") or "").lower() == "meson":
        return True
    if repo is not None and is_meson_repo(repo):
        return True
    install = str(cfg.get("install") or "").lower()
    test_cmd = str(cfg.get("test_cmd") or "").lower()
    return "meson setup" in install or "meson compile" in install or "meson test" in test_cmd


def log_indicates_meson_tool_missing(log: str) -> bool:
    return bool(_MESON_NOT_FOUND_RE.search(log or "") or _NINJA_NOT_FOUND_RE.search(log or ""))


def is_premake_config(cfg: dict[str, Any], *, repo: Path | None = None) -> bool:
    if str(cfg.get("c_build_system") or "").lower() == "premake":
        return True
    if repo is not None and is_premake_repo(repo):
        return True
    install = str(cfg.get("install") or "").lower()
    test_cmd = str(cfg.get("test_cmd") or "").lower()
    return "bootstrap.sh" in install or "premake5" in test_cmd


def premake_is_manifest_lua(rel: str) -> bool:
    name = Path(rel.replace("\\", "/")).name
    return name == "_tests.lua"


def premake_suite_from_lua_content(text: str) -> str | None:
    match = _PREMAKE_DECLARE_RE.search(text or "")
    return match.group(1) if match else None


def _premake_patch_chunk_for_path(test_patch: str, rel: str) -> str:
    rel = rel.replace("\\", "/").strip()
    marker = f"diff --git a/{rel} b/{rel}"
    start = test_patch.find(marker)
    if start < 0:
        return ""
    next_diff = test_patch.find("\ndiff --git ", start + 1)
    if next_diff < 0:
        return test_patch[start:]
    return test_patch[start:next_diff]


def premake_suite_from_test_path(rel: str) -> str | None:
    """
    Heuristic map from a Premake Lua test file to its ``test.declare`` suite name.

    ``tests/base/test_os_unicode.lua`` -> ``base_os_unicode``
    ``tests/test_lua_unicode.lua`` -> ``lua_unicode``
    """
    if premake_is_manifest_lua(rel):
        return None
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if rel.startswith("tests/"):
        rel = rel[len("tests/") :]
    rel = rel.removesuffix(".lua")
    if not rel:
        return None
    if "/" not in rel:
        return rel[len("test_") :] if rel.startswith("test_") else rel
    parts = rel.split("/")
    last = parts[-1]
    if last.startswith("test_"):
        last = last[len("test_") :]
    return "_".join([*parts[:-1], last])


def premake_suite_for_target(
    rel: str,
    *,
    repo: Path | None = None,
    test_patch: str = "",
) -> str | None:
    """Resolve suite name from repo file, ``test_patch`` hunk, or path heuristic."""
    if premake_is_manifest_lua(rel):
        return None
    if repo is not None:
        path = repo / rel.replace("\\", "/")
        if path.is_file():
            suite = premake_suite_from_lua_content(path.read_text(encoding="utf-8", errors="replace"))
            if suite:
                return suite
    chunk = _premake_patch_chunk_for_path(test_patch, rel)
    if chunk:
        added = "\n".join(
            line[1:] for line in chunk.splitlines() if line.startswith("+") and not line.startswith("+++")
        )
        suite = premake_suite_from_lua_content(added)
        if suite:
            return suite
    return premake_suite_from_test_path(rel)


def _premake_target_text(
    rel: str,
    *,
    repo: Path | None = None,
    test_patch: str = "",
) -> str:
    parts: list[str] = []
    if repo is not None:
        path = repo / rel.replace("\\", "/")
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    chunk = _premake_patch_chunk_for_path(test_patch, rel)
    if chunk:
        parts.append(
            "\n".join(
                line[1:]
                for line in chunk.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
        )
    return "\n".join(parts)


def premake_target_runnable_on_base(
    rel: str,
    *,
    repo: Path | None = None,
    test_patch: str = "",
) -> bool:
    """False when a suite file early-returns unless impl enables UTF-8 support."""
    text = _premake_target_text(rel, repo=repo, test_patch=test_patch)
    return "_UTF8_ENABLED" not in text


def premake_test_cmd_for_targets(
    targets: list[str] | None,
    *,
    repo: Path | None = None,
    test_patch: str = "",
    base_phase: bool = False,
) -> str:
    """Build ``premake5 test`` command scoped to suites touched by ``test_patch``."""
    suites: list[str] = []
    for raw in targets or []:
        if not raw.endswith(".lua"):
            continue
        if base_phase and not premake_target_runnable_on_base(raw, repo=repo, test_patch=test_patch):
            continue
        suite = premake_suite_for_target(raw, repo=repo, test_patch=test_patch)
        if suite and suite not in suites:
            suites.append(suite)
    flags = " ".join(f"--test-only={suite}" for suite in suites)
    if flags:
        return f"bin/release/premake5 test {flags}"
    if base_phase:
        return "true"
    return "bin/release/premake5 test --test-all"


def meson_install_config_for_repo(
    repo: Path,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Heuristic ``install_config`` for native Meson C/C++ repos."""
    cfg = dict(base or {})
    cfg["language"] = "c"
    cfg["c_build_system"] = "meson"
    cfg["result_format"] = "googletest_log"
    cfg["install"] = _MESON_INSTALL
    cfg["test_cmd"] = _MESON_TEST_CMD
    for key in ("pip_packages", "reqs_path", "pytest_plugins", "python"):
        cfg.pop(key, None)
    post = list(cfg.get("post_install") or [])
    cfg["post_install"] = [
        ln for ln in post if "pip install" not in str(ln).lower() and "meson-python" not in str(ln).lower()
    ]
    out = remediate_apt_install_from_log(dict(cfg), "")
    out = merge_apt_into_config(out, list(_C_COMMON_APT))
    out = merge_apt_into_config(out, list(_MESON_BASE_APT))
    return out


def premake_install_config_for_repo(
    repo: Path,
    *,
    base: dict[str, Any] | None = None,
    test_paths: list[str] | None = None,
    test_patch: str = "",
) -> dict[str, Any]:
    """Heuristic ``install_config`` for Premake repos (Bootstrap + ``premake5 test``)."""
    cfg = dict(base or {})
    cfg["language"] = "c"
    cfg["c_build_system"] = "premake"
    cfg["result_format"] = "googletest_log"
    cfg["install"] = _PREMAKE_INSTALL
    cfg["premake_test_cmd_base"] = premake_test_cmd_for_targets(
        test_paths,
        repo=repo,
        test_patch=test_patch,
        base_phase=True,
    )
    cfg["test_cmd"] = premake_test_cmd_for_targets(
        test_paths,
        repo=repo,
        test_patch=test_patch,
        base_phase=False,
    )
    out = remediate_apt_install_from_log(dict(cfg), "")
    out = merge_apt_into_config(out, list(_C_COMMON_APT))
    out = merge_apt_into_config(out, list(_PREMAKE_BASE_APT))
    return out


def is_c_harness_config(cfg: dict[str, Any], *, repo: Path | None = None) -> bool:
    lang = str(cfg.get("language") or "").lower()
    if lang == "c":
        return True
    if repo is not None and is_premake_repo(repo):
        return True
    if repo is not None and is_meson_repo(repo):
        return True
    if repo is not None and (repo / "CMakeLists.txt").is_file():
        return True
    install = str(cfg.get("install") or "").lower()
    test_cmd = str(cfg.get("test_cmd") or "").lower()
    return (
        "cmake" in install
        or "ctest" in test_cmd
        or "make " in install
        or is_premake_config(cfg)
        or is_meson_config(cfg, repo=repo)
    )


def _base_apt_for_config(cfg: dict[str, Any], *, repo: Path | None = None) -> tuple[str, ...]:
    if is_premake_config(cfg, repo=repo):
        return _PREMAKE_BASE_APT
    if is_meson_config(cfg, repo=repo):
        return _MESON_BASE_APT
    return _CMAKE_BASE_APT


def _cmake_file_paths(repo: Path) -> list[Path]:
    paths: list[Path] = []
    root = repo / "CMakeLists.txt"
    if root.is_file():
        paths.append(root)
    cmake_dir = repo / "CMake"
    if cmake_dir.is_dir():
        try:
            for p in sorted(cmake_dir.rglob("*.cmake")):
                if p.is_file():
                    paths.append(p)
                if len(paths) >= 200:
                    break
        except OSError:
            pass
    return paths


def cmake_apt_packages_for_repo(repo: Path) -> list[str]:
    """Best-effort apt preseed from CMake package declarations."""
    if not (repo / "CMakeLists.txt").is_file():
        return []
    seen: set[str] = set()
    inferred: list[str] = []
    for path in _cmake_file_paths(repo):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names = [m.group(1) for m in _CMAKE_FIND_PACKAGE_RE.finditer(text)]
        names.extend(m.group(1) for m in _CMAKE_PKG_CHECK_RE.finditer(text))
        for raw in names:
            name = str(raw or "").strip().lower()
            if not name:
                continue
            for needle, pkgs in _CMAKE_PACKAGE_APT.items():
                if needle in name:
                    for pkg in pkgs:
                        if pkg not in seen:
                            seen.add(pkg)
                            inferred.append(pkg)
    return inferred


def ensure_c_base_pre_install(cfg: dict[str, Any], *, repo: Path | None = None) -> dict[str, Any]:
    """Ensure C projects have build tools in pre_install."""
    out = remediate_apt_install_from_log(dict(cfg), "")
    out = merge_apt_into_config(out, list(_C_COMMON_APT))
    out = merge_apt_into_config(out, list(_base_apt_for_config(out, repo=repo)))
    if repo is not None and not is_premake_config(out, repo=repo):
        out = merge_apt_into_config(out, cmake_apt_packages_for_repo(repo))
    return out


def remediate_c_install_from_log(
    cfg: dict[str, Any],
    log: str,
    *,
    repo: Path | None = None,
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Apply log-driven apt package fixes for C/CMake/Premake install failures."""
    if not is_c_harness_config(cfg, repo=repo):
        return cfg
    from .harness_guards import log_indicates_ngtcp2_quictls_missing
    from .integration_build import (
        _test_paths_suggest_http3_pytest,
        strip_http3_cmake_flags,
    )

    out = remediate_apt_install_from_log(dict(cfg), log)
    out = merge_apt_into_config(out, list(_C_COMMON_APT))
    out = merge_apt_into_config(out, list(_base_apt_for_config(out, repo=repo)))
    if log_indicates_meson_tool_missing(log):
        out = merge_apt_into_config(out, list(_MESON_BASE_APT))
        if repo is not None and is_meson_repo(repo):
            out = meson_install_config_for_repo(repo, base=out)
    if log_indicates_ngtcp2_quictls_missing(log) and repo is not None:
        from .integration_build import remediate_quictls_native_integration

        out = remediate_quictls_native_integration(
            out, repo, test_paths=test_paths
        )
    return out


def ensure_c_install_config(
    cfg: dict[str, Any],
    *,
    repo: Path | None = None,
    log: str | None = None,
    test_paths: list[str] | None = None,
    test_patch: str = "",
) -> dict[str, Any]:
    """Normalize C install_config: base tools, optional log-driven apt merge."""
    if repo is not None and is_premake_repo(repo):
        out = premake_install_config_for_repo(
            repo,
            base=cfg,
            test_paths=test_paths,
            test_patch=test_patch,
        )
        if log:
            out = remediate_c_install_from_log(
                out, log, repo=repo, test_paths=test_paths
            )
        return out
    if repo is not None and is_meson_repo(repo):
        out = meson_install_config_for_repo(repo, base=cfg)
        if log:
            out = remediate_c_install_from_log(
                out, log, repo=repo, test_paths=test_paths
            )
        return out
    if not is_c_harness_config(cfg, repo=repo):
        return cfg
    out = ensure_c_base_pre_install(dict(cfg), repo=repo)
    if repo is not None and not is_premake_config(out, repo=repo):
        from .runtests_build import sanitize_cmake_http3_for_harness

        out = sanitize_cmake_http3_for_harness(
            out, repo, test_paths=test_paths
        )
        tc = str(out.get("test_cmd") or "").lower()
        if "ctest" in tc and not out.get("result_format"):
            out["result_format"] = "ctest_log"
    if log:
        out = remediate_c_install_from_log(
            out, log, repo=repo, test_paths=test_paths
        )
    return out


_C_HARNESS_PRESERVE_KEYS: tuple[str, ...] = (
    "install",
    "test_cmd",
    "pre_install",
    "post_install",
    "apt-pkgs",
    "language",
    "c_build_system",
    "result_format",
    "premake_test_cmd_base",
    "cmake_runtests_build",
    "cmake_runtests_numbers",
    "runtests_test_cmd_base",
    "runtests_setup_patch",
    "runtests_setup_base",
    "runtests_cmake_tool_symlinks",
    "runtests_cmake_layout_adapter",
    "runtests_cmake_harness_subdirs",
)


def merge_c_harness_fields_after_llm(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Restore C harness fields when install LLM returns a Python-shaped config."""
    if not is_c_harness_config(before):
        return after
    out = dict(after)
    for key in _C_HARNESS_PRESERVE_KEYS:
        if before.get(key) and not out.get(key):
            out[key] = before[key]
    tc_before = str(before.get("test_cmd") or "")
    tc_after = str(out.get("test_cmd") or "")
    premake_before = "premake5" in tc_before
    if premake_before and "premake5" not in tc_after:
        out["test_cmd"] = tc_before
    elif ("ctest" in tc_before or "cmake" in tc_before) and "pytest" in tc_after:
        out["test_cmd"] = tc_before
    inst_before = str(before.get("install") or "")
    inst_after = str(out.get("install") or "")
    if ("bootstrap.sh" in inst_before.lower() or "premake5" in tc_before) and "cmake" in inst_after:
        out["install"] = inst_before
    elif ("cmake" in inst_before or "make" in inst_before) and "pip install" in inst_after:
        out["install"] = inst_before
    meson_before = "meson" in inst_before.lower() or "meson" in tc_before.lower()
    if meson_before and "meson" not in inst_after.lower() and "meson" not in tc_after.lower():
        out["install"] = inst_before
        if tc_before:
            out["test_cmd"] = tc_before
    if is_premake_config(before) and not is_premake_config(out):
        out["c_build_system"] = before.get("c_build_system") or "premake"
        out["result_format"] = before.get("result_format") or "googletest_log"
    if is_meson_config(before) and not is_meson_config(out):
        out["c_build_system"] = before.get("c_build_system") or "meson"
        out["result_format"] = before.get("result_format") or "googletest_log"
    return ensure_c_base_pre_install(out)


# Backward-compatible alias used by tests
apt_packages_from_c_build_log = apt_packages_from_build_log
merge_c_apt_into_config = merge_apt_into_config
