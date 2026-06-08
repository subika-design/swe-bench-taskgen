"""PR-driven harness router for CMake/C discover (runtests / native pytest / ctest)."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from .runtests_build import (
    collect_runtests_numbers,
    detect_runtests_harness,
    runtests_install_config,
    sanitize_cmake_http3_for_harness,
)


class HarnessKind(str, Enum):
    RUNTESTS = "runtests"
    NATIVE_PYTEST = "native_pytest"
    CTEST = "ctest"
    PREMAKE = "premake"
    MESON = "meson"


HARNESS_FLAG_KEYS: tuple[str, ...] = (
    "native_integration_build",
    "native_integration_http3_disabled",
    "native_integration_pytest_root",
    "native_integration_repo_dir",
    "native_integration_cmake_pytest_target",
    "native_integration_setup",
    "cmake_runtests_build",
    "cmake_runtests_numbers",
    "runtests_test_cmd_base",
    "runtests_setup_patch",
    "runtests_setup_base",
    "runtests_cmake_tool_symlinks",
    "runtests_cmake_layout_adapter",
    "runtests_cmake_harness_subdirs",
)


def clear_all_harness_flags(cfg: dict[str, Any]) -> dict[str, Any]:
    """Remove mutually exclusive harness discriminator keys."""
    out = dict(cfg)
    for key in HARNESS_FLAG_KEYS:
        out.pop(key, None)
    return out


def resolve_c_harness_kind(
    repo: Path,
    patch: str,
    test_patch: str,
    *,
    test_paths: list[str] | None = None,
) -> HarnessKind:
    """
    Choose discover harness from PR patch + repo layout (never CI ``test_cmd`` shape).

    Priority: runtests (libtest) > native nested pytest > plain ctest > premake.
    """
    from .c_build import is_meson_repo, is_premake_repo

    if is_premake_repo(repo):
        return HarnessKind.PREMAKE
    if detect_runtests_harness(test_patch, repo):
        return HarnessKind.RUNTESTS
    from .integration_build import merge_hybrid_c_integration_paths, repo_has_cmake_integration

    _detection, runner = merge_hybrid_c_integration_paths(
        patch, test_patch, test_paths=test_paths
    )
    if runner and repo_has_cmake_integration(repo, test_paths=runner):
        return HarnessKind.NATIVE_PYTEST
    if is_meson_repo(repo):
        return HarnessKind.MESON
    if (repo / "CMakeLists.txt").is_file():
        return HarnessKind.CTEST
    return HarnessKind.CTEST


def plain_cmake_install_config(
    cfg: dict[str, Any],
    repo: Path,
    *,
    test_patch: str = "",
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Default CMake profile: CI/heuristic install + ``ctest_log`` grading."""
    from .c_build import ensure_c_install_config

    out = clear_all_harness_flags(dict(cfg))
    out = ensure_c_install_config(
        out,
        repo=repo,
        test_patch=test_patch,
        test_paths=test_paths,
    )
    tc = str(out.get("test_cmd") or "").lower()
    if "ctest" in tc or not str(out.get("test_cmd") or "").strip():
        if not str(out.get("test_cmd") or "").strip():
            out["test_cmd"] = "cd build && ctest --output-on-failure -j\"$(nproc)\""
        out["result_format"] = "ctest_log"
    out["c_build_system"] = out.get("c_build_system") or "cmake"
    out["language"] = "c"
    return out


def apply_c_harness_router(
    cfg: dict[str, Any],
    repo: Path | None,
    *,
    patch: str = "",
    test_patch: str = "",
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """
    Sanitize install (HTTP/3 off), clear stale harness flags, apply PR-selected harness.

    Overrides any prior ``native_integration_build`` from CI cache or HTTP/3 remediation.
    """
    if repo is None:
        return cfg
    kind = resolve_c_harness_kind(repo, patch, test_patch, test_paths=test_paths)
    out = sanitize_cmake_http3_for_harness(dict(cfg), repo, test_paths=test_paths)
    if kind == HarnessKind.PREMAKE:
        from .c_build import ensure_c_install_config

        return ensure_c_install_config(
            out, repo=repo, test_paths=test_paths, test_patch=test_patch
        )
    if kind == HarnessKind.MESON:
        from .c_build import meson_install_config_for_repo

        return meson_install_config_for_repo(repo, base=out)
    out = clear_all_harness_flags(out)
    if kind == HarnessKind.RUNTESTS:
        nums = collect_runtests_numbers(test_patch)
        return runtests_install_config(out, repo, test_patch=test_patch, numbers=nums or None)
    if kind == HarnessKind.NATIVE_PYTEST:
        from .integration_build import merge_hybrid_c_integration_paths, native_build_install_config

        _detection, runner = merge_hybrid_c_integration_paths(
            patch, test_patch, test_paths=test_paths
        )
        out = native_build_install_config(
            out,
            repo,
            test_paths=runner,
            test_patch=test_patch,
        )
        out["language"] = "c"
        return out
    return plain_cmake_install_config(
        out, repo, test_patch=test_patch, test_paths=test_paths
    )


def c_harness_runner_label(cfg: dict[str, Any]) -> str:
    """Human-readable runner name for discover logs."""
    if cfg.get("native_integration_build"):
        return "pytest (native integration)"
    if cfg.get("cmake_runtests_build"):
        return "runtests.pl"
    from .c_build import is_meson_config, is_premake_config

    if is_premake_config(cfg):
        return "premake5 test"
    if is_meson_config(cfg):
        return "meson test"
    return str(cfg.get("c_build_system") or "cmake")
