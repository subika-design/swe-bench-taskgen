"""CMake libtest / runtests.pl harness (curl-style ``tests/data/testNNNN``)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .c_build import ensure_c_install_config
from .integration_build import (
    cmake_runtests_harness_subdirs,
    cmake_runtests_layout_symlink_lines,
    harness_supports_http3_cmake,
    native_cmake_install_command,
    native_integration_cmake_src_symlink_lines,
    patch_diff_touches_libtest,
    repo_needs_cmake_src_tool_symlinks,
    strip_http3_cmake_flags,
)

_CMAKE_RUNTESTS_FLAG = "cmake_runtests_build"
_CMAKE_RUNTESTS_NUMBERS = "cmake_runtests_numbers"
_RUNTEST_NUMBER_RE = re.compile(
    r"(?:^|/)(?:test|lib)(\d{1,5})(?:\.[a-z]+)?$",
    re.IGNORECASE,
)
_RUNTEST_DATA_RE = re.compile(r"tests/data/test(\d+)", re.IGNORECASE)
_RUNTEST_LIB_RE = re.compile(r"tests/libtest/lib(\d+)", re.IGNORECASE)


_CMAKE_RUNTESTS_LAYOUT = "runtests_cmake_layout_adapter"


def repo_has_cmake_runtests(repo: Path) -> bool:
    """True when the tree ships curl-style ``tests/runtests.pl``."""
    return (repo / "tests" / "runtests.pl").is_file()


def repo_needs_cmake_runtests_layout_adapter(repo: Path) -> bool:
    """
    True when CMake out-of-tree layout must be bridged for ``runtests.pl``.

    ``runtests.pl`` resolves ``../src/curlinfo`` and ``./server/servers`` relative
    to ``tests/``; invoke with ``cd tests &&`` and symlink cmake outputs first.
    """
    return repo_has_cmake_runtests(repo) and (repo / "CMakeLists.txt").is_file()


def detect_runtests_harness(test_patch: str, repo: Path | None) -> bool:
    """
    Pattern B: libtest / runtests assets in *test_patch* on a runtests.pl repo.

    Not curl-specific — any CMake repo with ``tests/runtests.pl`` + libtest paths.
    """
    if not test_patch.strip() or repo is None:
        return False
    if not repo_has_cmake_runtests(repo):
        return False
    return patch_diff_touches_libtest(test_patch)


def collect_runtests_numbers(test_patch: str) -> list[str]:
    """Extract runtests numeric ids from ``tests/data/test1677`` / ``lib1677.c`` paths."""
    seen: set[str] = set()
    out: list[str] = []
    for m in re.finditer(r"^diff --git a/(\S+)", test_patch or "", re.MULTILINE):
        rel = m.group(1).replace("\\", "/")
        for pat in (_RUNTEST_DATA_RE, _RUNTEST_LIB_RE, _RUNTEST_NUMBER_RE):
            hit = pat.search(rel)
            if not hit:
                continue
            num = hit.group(1).lstrip("0") or "0"
            # runtests.pl expects unpadded numbers in some modes; keep canonical int string
            num = str(int(num)) if num.isdigit() else num
            if num not in seen:
                seen.add(num)
                out.append(num)
            break
    return sorted(out, key=lambda x: int(x) if x.isdigit() else x)


def cmake_runtests_discover_active(cfg: dict[str, Any] | None) -> bool:
    return bool(cfg and cfg.get(_CMAKE_RUNTESTS_FLAG))


def uses_cmake_runtests_test_cmd(install_config: dict[str, Any]) -> bool:
    tc = str(install_config.get("test_cmd") or "")
    return "runtests.pl" in tc


def runtests_test_cmd_for_numbers(
    numbers: Iterable[str],
    *,
    repo_dir: str = "/testbed",
    curl_tool_symlinks: bool = False,
    layout_adapter: bool = False,
) -> str:
    """Scoped ``runtests.pl`` with automake-style output for log parsing."""
    nums = [str(n).strip() for n in numbers if str(n).strip()]
    root = repo_dir.rstrip("/")
    if layout_adapter:
        cmd = "cd tests && ./runtests.pl -a -am"
        if curl_tool_symlinks:
            cmd += " -c ../build/src/curl"
    else:
        cmd = "./tests/runtests.pl -a -am"
        if curl_tool_symlinks:
            cmd += f" -c {root}/build/src/curl"
    if nums:
        cmd += f" -p {' '.join(nums)}"
    else:
        cmd += " -p"
    return cmd


def _runtests_testdeps_lines(repo: Path, *, layout_adapter: bool) -> list[str]:
    from .integration_build import cmake_native_prepare_targets

    lines: list[str] = []
    for target in cmake_native_prepare_targets(
        repo,
        include_libtest_targets=True,
        include_harness_binaries=layout_adapter,
    ):
        cmd = f'cmake --build build --target {target} -j"$(nproc)"'
        if cmd not in lines:
            lines.append(cmd)
    return lines


def runtests_prepare_lines(repo: Path, *, include_testdeps: bool) -> list[str]:
    """Post-patch cmake targets + layout symlinks before ``runtests.pl``."""
    layout = repo_needs_cmake_runtests_layout_adapter(repo)
    lines: list[str] = []
    if include_testdeps:
        lines.extend(_runtests_testdeps_lines(repo, layout_adapter=layout))
    if repo_needs_cmake_src_tool_symlinks(repo):
        curlinfo_build = 'cmake --build build --target curlinfo -j"$(nproc)"'
        if curlinfo_build not in lines:
            lines.append(curlinfo_build)
    for ln in native_integration_cmake_src_symlink_lines(repo):
        if ln not in lines:
            lines.append(ln)
    if layout:
        for ln in cmake_runtests_layout_symlink_lines(repo):
            if ln not in lines:
                lines.append(ln)
    return lines


def _runtests_cmake_tool_symlinks_flag(
    repo: Path | None,
    curl_tool_symlinks: bool | None,
) -> bool:
    if curl_tool_symlinks is not None:
        return curl_tool_symlinks
    return repo is not None and repo_needs_cmake_src_tool_symlinks(repo)


def runtests_cmake_runtime_env_lines(
    repo: Path | None,
    *,
    repo_dir: str = "/testbed",
    curl_tool_symlinks: bool | None = None,
) -> list[str]:
    """Export lines for SWE-bench ``eval_commands`` (harness test runs)."""
    root = repo_dir.rstrip("/")
    ld_parts = [f"{root}/build/lib", f"{root}/build/lib64"]
    lines = [
        f'export LD_LIBRARY_PATH="{":".join(ld_parts)}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"',
        f'export PATH="{root}/build/src:{root}/build:$PATH"',
    ]
    if _runtests_cmake_tool_symlinks_flag(repo, curl_tool_symlinks):
        lines.extend(
            [
                f'export CURL="{root}/build/src/curl"',
                f'export CURLINFO="{root}/build/src/curlinfo"',
                "export CURL_CI=1",
            ]
        )
    return lines


def runtests_cmake_invoke_block(
    *,
    repo_dir: str = "/testbed",
    curl_tool_symlinks: bool = True,
) -> str:
    """
    Bash helper that runs a command under runtests runtime env in a subshell.

    Avoids exporting ``LD_LIBRARY_PATH`` globally so cmake does not load
    in-tree ``libcurl.so`` during reinstall/setup.
    """
    root = repo_dir.rstrip("/")
    ld = f"{root}/build/lib:{root}/build/lib64"
    env_lines = [
        f'LD_LIBRARY_PATH="{ld}${{LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}}"',
        f'PATH="{root}/build/src:{root}/build:$PATH"',
    ]
    if curl_tool_symlinks:
        env_lines.extend(
            [
                f'CURL="{root}/build/src/curl"',
                f'CURLINFO="{root}/build/src/curlinfo"',
                "CURL_CI=1",
            ]
        )
    env_body = " \\\n    ".join(env_lines)
    qroot = root.replace("'", "'\\''")
    return f"""
_runtests_invoke() {{
  local cmd="$1"
  (cd '{qroot}' && env \\
    {env_body} \\
    bash -c "$cmd")
}}
"""


def runtests_eval_command_lines(repo: Path, *, repo_dir: str = "/testbed") -> list[str]:
    """``export`` lines for cmake ``runtests.pl`` (``build/src/curl`` layout)."""
    return runtests_cmake_runtime_env_lines(repo, repo_dir=repo_dir)


def sanitize_cmake_http3_for_harness(
    cfg: dict[str, Any],
    repo: Path | None = None,
    *,
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """
    Strip HTTP/3 cmake flags and ngtcp2 apt packages for SWE-bench images.

    Install/apt only — never sets harness flags (``native_integration_build``, etc.).
    """
    del test_paths
    if harness_supports_http3_cmake():
        return cfg
    out = dict(cfg)
    install = str(out.get("install") or "")
    stripped = strip_http3_cmake_flags(install)
    if stripped != install:
        out["install"] = stripped
        out["native_integration_http3_disabled"] = True
    elif "USE_NGTCP2" in install.upper() or "USE_PROXY_HTTP3" in install.upper():
        out["native_integration_http3_disabled"] = True
    if repo is not None and (
        "USE_NGTCP2" in str(out.get("install") or "").upper()
        or "USE_PROXY_HTTP3" in str(out.get("install") or "").upper()
    ):
        out["install"] = native_cmake_install_command(repo, http3=False, clean_build=True)
        out["native_integration_http3_disabled"] = True
    apt = [
        p
        for p in list(out.get("apt-pkgs") or [])
        if "ngtcp2" not in str(p).lower() and "nghttp3" not in str(p).lower()
    ]
    out["apt-pkgs"] = apt
    opt = [
        p
        for p in list(out.get("apt-pkgs-optional") or [])
        if "ngtcp2" not in str(p).lower() and "nghttp3" not in str(p).lower()
    ]
    if opt:
        out["apt-pkgs-optional"] = opt
    elif "apt-pkgs-optional" in out:
        out["apt-pkgs-optional"] = []
    pre: list[str] = []
    for ln in list(out.get("pre_install") or []):
        low = str(ln).lower()
        if "ngtcp2" in low or "nghttp3" in low:
            continue
        pre.append(ln)
    out["pre_install"] = pre
    return out


def runtests_install_config(
    cfg: dict[str, Any],
    repo: Path,
    *,
    test_patch: str = "",
    numbers: list[str] | None = None,
) -> dict[str, Any]:
    """Install + runtests discover profile for libtest / ``tests/data/testNNNN`` PRs."""
    nums = numbers if numbers is not None else collect_runtests_numbers(test_patch)
    from .c_harness_router import clear_all_harness_flags

    out = clear_all_harness_flags(dict(cfg))
    out = ensure_c_install_config(out, repo=repo, test_patch=test_patch)
    out = sanitize_cmake_http3_for_harness(out, repo)
    out[_CMAKE_RUNTESTS_FLAG] = True
    out[_CMAKE_RUNTESTS_NUMBERS] = nums
    out["language"] = "c"
    out["result_format"] = "runtests_log"
    out["c_build_system"] = "cmake"
    out["install"] = native_cmake_install_command(repo, http3=False, clean_build=False)
    tool_symlinks = repo_needs_cmake_src_tool_symlinks(repo)
    layout_adapter = repo_needs_cmake_runtests_layout_adapter(repo)
    out["runtests_cmake_tool_symlinks"] = tool_symlinks
    out[_CMAKE_RUNTESTS_LAYOUT] = layout_adapter
    out["runtests_cmake_harness_subdirs"] = cmake_runtests_harness_subdirs(repo)
    out["test_cmd"] = runtests_test_cmd_for_numbers(
        nums,
        repo_dir="/testbed",
        curl_tool_symlinks=tool_symlinks,
        layout_adapter=layout_adapter,
    )
    out["runtests_test_cmd_base"] = runtests_test_cmd_for_numbers(
        nums,
        repo_dir="/testbed",
        curl_tool_symlinks=tool_symlinks,
        layout_adapter=layout_adapter,
    )
    setup_base = runtests_prepare_lines(repo, include_testdeps=True)
    setup_patch = runtests_prepare_lines(repo, include_testdeps=True)
    out["runtests_setup_base"] = setup_base
    out["runtests_setup_patch"] = setup_patch
    out["eval_commands"] = runtests_cmake_runtime_env_lines(repo, repo_dir="/testbed")
    post = list(out.get("post_install") or [])
    for ln in setup_patch:
        if ln not in post:
            post.append(ln)
    out["post_install"] = post
    return out


def runtests_cmake_harness_preflight_shell(harness_subdirs: Iterable[str]) -> str:
    """Bash fragment (inside ``cd tests``) verifying bridged harness binaries."""
    from .integration_build import cmake_runtests_harness_tool_name

    parts: list[str] = []
    for raw in harness_subdirs:
        sub = str(raw).strip()
        tool = cmake_runtests_harness_tool_name(sub)
        if not sub or not tool:
            continue
        parts.extend(
            [
                f"if [[ -x ./{sub}/{tool} ]]; then",
                f'  echo "[docker] runtests preflight: ./{sub}/{tool} ok" >&2',
                "else",
                f'  echo "[docker] runtests preflight: ./{sub}/{tool} missing" >&2',
                "fi",
            ]
        )
    return "\n    ".join(parts)


def apply_runtests_build_if_libtest(
    cfg: dict[str, Any],
    repo: Path | None,
    *,
    test_patch: str = "",
    patch: str = "",
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper; prefer :func:`apply_c_harness_router`."""
    if repo is None:
        return cfg
    from .c_harness_router import HarnessKind, apply_c_harness_router, resolve_c_harness_kind

    if resolve_c_harness_kind(repo, patch, test_patch, test_paths=test_paths) != HarnessKind.RUNTESTS:
        return cfg
    return apply_c_harness_router(
        cfg, repo, patch=patch, test_patch=test_patch, test_paths=test_paths
    )


def runtests_log_key_in_test_patch_paths(key: str, test_patch_paths: list[str]) -> bool:
    """Match runtests log keys like ``test1677`` to ``tests/data/test1677`` paths."""
    low = (key or "").strip().lower()
    if not low:
        return False
    m = re.match(r"test(\d+)$", low)
    if not m:
        return False
    num = m.group(1)
    for raw in test_patch_paths:
        p = raw.replace("\\", "/").lower()
        if f"test{num}" in p or f"lib{num}" in p:
            return True
    return False
