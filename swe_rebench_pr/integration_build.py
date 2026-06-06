"""CMake native build + integration pytest harness helpers (hybrid C/C++ repos)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .apt_from_log import (
    merge_integration_apt_into_config,
    sanitize_apt_package_names,
    sanitize_native_integration_apt_config,
)
from .c_build import cmake_apt_packages_for_repo, ensure_c_install_config

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "subprojects",
        "build",
        "dist",
    }
)
from .ci_extract import (
    DEFAULT_NATIVE_HTTP3_CMAKE_DEFINITIONS,
    apt_packages_from_ci_workflows,
    ci_all_run_lines,
    cmake_definitions_from_ci_for_http3_pytest,
)

_CMAKE_NATIVE_INSTALL = (
    'mkdir -p build && cd build && cmake .. -DCMAKE_BUILD_TYPE=Release && '
    'cmake --build . -j"$(nproc)"'
)
_CMAKE_NATIVE_INSTALL_CLEAN = (
    'rm -rf build && mkdir -p build && cd build && '
    'cmake .. -DCMAKE_BUILD_TYPE=Release && '
    'cmake --build . -j"$(nproc)"'
)
# Native integration HTTP/3 / QUIC cmake deps (any CMake + nested pytest repo, not curl-only).
# SWE-bench env images use Debian bookworm: OpenSSL ngtcp2 crypto is not in default apt;
# use :func:`_ngtcp2_crypto_apt_install_shell` (ossl then gnutls, never unsigned backports).
_NATIVE_HTTP3_APT_CORE: tuple[str, ...] = (
    "ninja-build",
    "libngtcp2-dev",
    "libnghttp3-dev",
    "libc-ares-dev",
    "libssh-dev",
)
# Crypto backend + optional servers: installed in pre_install, not bulk ``apt-pkgs``.
_NATIVE_HTTP3_APT_OPTIONAL: tuple[str, ...] = ("libngtcp2-crypto-gnutls-dev",)
_NGTCP2_CRYPTO_PREINSTALL_ONLY_APT: frozenset[str] = frozenset(
    {
        "libngtcp2-crypto-ossl-dev",
        "libngtcp2-crypto-ossl0",
    }
)
# Backward-compatible aliases (tests / external imports).
_CURL_HTTP3_NATIVE_APT_CORE = _NATIVE_HTTP3_APT_CORE
_CURL_HTTP3_NATIVE_APT_OPTIONAL = _NATIVE_HTTP3_APT_OPTIONAL
_CURL_HTTP3_PREINSTALL_ONLY_APT = _NGTCP2_CRYPTO_PREINSTALL_ONLY_APT
_HTTP3_TEST_PATH_RE = re.compile(
    r"(?:^|/)(?:test_[^/]*)?(?:h3|http3|quic|ngtcp2|nghttp3)",
    re.IGNORECASE,
)
_NATIVE_HTTP3_CMAKE_HINT_RE = re.compile(
    r"USE_(?:NGTCP2|PROXY_HTTP3|QUICHE)|NGHTTP3|ENABLE_QUIC",
    re.IGNORECASE,
)
_NATIVE_INTEGRATION_FLAG = "native_integration_build"
_NATIVE_INTEGRATION_HTTP3_DISABLED = "native_integration_http3_disabled"
_NATIVE_INTEGRATION_ROOT = "native_integration_pytest_root"
_NATIVE_INTEGRATION_REPO_DIR = "native_integration_repo_dir"
# SWE-bench harness mounts the repo at /testbed (discover + eval images).
_DEFAULT_HARNESS_REPO_DIR = "/testbed"
_CI_RUN_LINE_RE = re.compile(r"^\s*-\s+run:\s*(.+)$", re.MULTILINE)
_CI_EXPORT_RE = re.compile(
    r"\bexport\s+([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]*)\"|'([^']*)'|(\S+))",
)
_CMAKE_PYTEST_TARGET_RE = re.compile(
    r"curl_add_pytests\s*\(\s*([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_CMAKE_TARGET_RE = re.compile(r"add_custom_target\s*\(\s*([A-Za-z0-9_-]+)\s*", re.IGNORECASE)
_CMAKE_EXECUTABLE_RE = re.compile(r"add_executable\s*\(\s*([A-Za-z0-9_-]+)\b", re.IGNORECASE)
_CMAKE_SERVER_BUNDLE_RE = re.compile(r"^BUNDLE\s*=\s*(\S+)", re.MULTILINE)
_RUNTESTS_HARNESS_SUBDIRS = ("server", "libtest", "unit", "tunit")
_RUNTESTS_HARNESS_DEFAULT_TOOL = {
    "server": "servers",
    "libtest": "libtests",
    "unit": "units",
    "tunit": "tunits",
}
_CMAKE_BUILD_TARGET_RE = re.compile(
    r"cmake\s+--build\s+\S+\s+--(?:target|-t)\s+([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_FIND_PROGRAM_RE = re.compile(
    r'find_program\s*\(\s*(\w+)\s+(?:"([^"]+)"|(\w+))',
    re.IGNORECASE,
)
# Only map to packages that exist in Ubuntu 22.04 default apt (SWE-bench env image).
# ``h2o`` / ``caddy`` are CMake optional tools — not installed via apt here.
_PROGRAM_APT: dict[str, tuple[str, ...]] = {
    "nghttpx": ("libnghttp2-dev",),
    "httpd": ("apache2", "apache2-dev"),
    "vsftpd": ("vsftpd",),
    "danted": ("dante-server",),
    # Do not map ``sshd`` → openssh-server: curl CI pytest omits it; Ubuntu's
    # ``/usr/sbin/sshd`` fails ``env.py``'s ``sshd -V`` probe at conftest import.
    "apxs": ("apache2-dev",),
}
# Extra apt names to drop from native-integration env images (LLM remediation too).
_NATIVE_INTEGRATION_APT_BLOCKLIST = frozenset({"openssh-server"})
_JUNIT_OUT = "__JUNIT_OUT__"
_TARGETS = "__TARGETS__"


@dataclass(frozen=True)
class IntegrationPytestProfile:
    """Native CMake repo with a pytest subtree (e.g. ``tests/http``)."""

    pytest_root: str
    has_cmake: bool = True


def _is_skipped_dir(part: str) -> bool:
    return part in _SKIP_DIR_NAMES or part.startswith(".")


def discover_pytest_integration_roots(repo: Path, *, max_depth: int = 6) -> list[str]:
    """
    Find directories that look like integration pytest suites (``conftest.py``).

    Returns repo-relative posix paths sorted shallowest-first.
    """
    found: list[str] = []
    seen: set[str] = set()
    try:
        for path in repo.rglob("conftest.py"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(repo).parts
            if any(_is_skipped_dir(p) for p in rel_parts):
                continue
            try:
                root = path.parent.relative_to(repo).as_posix()
            except ValueError:
                continue
            if not root or root == ".":
                continue
            depth = len(PurePosixPath(root).parts)
            if depth > max_depth:
                continue
            if root not in seen:
                seen.add(root)
                found.append(root)
    except OSError:
        return []
    found.sort(key=lambda r: (len(PurePosixPath(r).parts), r))
    return found


def _common_parent_dir(paths: Iterable[str]) -> str | None:
    norm = [p.replace("\\", "/").strip().lstrip("/") for p in paths if p.strip()]
    if not norm:
        return None
    split = [PurePosixPath(p).parts for p in norm]
    common: list[str] = []
    for i in range(min(len(p) for p in split)):
        col = {p[i] for p in split}
        if len(col) == 1:
            common.append(next(iter(col)))
        else:
            break
    if not common:
        return None
    return "/".join(common)


def pytest_root_for_test_paths(test_paths: list[str]) -> str | None:
    """Best pytest root directory covering PR test paths."""
    dir_paths: list[str] = []
    for raw in test_paths:
        norm = raw.replace("\\", "/").strip().lstrip("/")
        if not norm:
            continue
        pp = PurePosixPath(norm)
        if pp.suffix == ".py":
            parent = pp.parent
            dir_paths.append(parent.as_posix() if parent.parts else ".")
        else:
            dir_paths.append(norm)
    if not dir_paths:
        return None
    parent = _common_parent_dir(dir_paths)
    if not parent or parent == ".":
        return None
    return parent


def integration_profile_for_repo(
    repo: Path,
    *,
    test_paths: list[str] | None = None,
) -> IntegrationPytestProfile | None:
    """
    True when the repo uses CMake at root and pytest lives in a nested suite.

    When *test_paths* are given, the root is derived from those paths; otherwise the
  shallowest ``conftest.py`` directory under ``tests/`` is used.
    """
    if not (repo / "CMakeLists.txt").is_file():
        return None
    from .repo_detect import uses_django_runtests

    if uses_django_runtests(repo=repo):
        return None

    if test_paths:
        py_paths = filter_integration_pytest_modules(test_paths)
        if not py_paths:
            return None
        root = pytest_root_for_test_paths(py_paths)
        if root and (repo / root.replace("\\", "/")).is_dir():
            return IntegrationPytestProfile(pytest_root=root.replace("\\", "/"))
        return None

    roots = discover_pytest_integration_roots(repo)
    under_tests = [r for r in roots if r.startswith("tests/") or r == "tests"]
    pick = under_tests[0] if under_tests else (roots[0] if roots else None)
    if not pick:
        return None
    return IntegrationPytestProfile(pytest_root=pick)


def repo_has_cmake_integration(
    repo: Path | None,
    *,
    test_paths: list[str] | None = None,
) -> bool:
    if repo is None:
        return False
    return integration_profile_for_repo(repo, test_paths=test_paths) is not None


def _pip_requirement_files(repo: Path, pytest_root: str) -> list[Path]:
    root = repo / pytest_root.replace("\\", "/")
    candidates: list[Path] = []
    for name in (
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "test-requirements.txt",
    ):
        p = root / name
        if p.is_file():
            candidates.append(p)
    req_dir = root / "requirements"
    if req_dir.is_dir():
        try:
            for p in sorted(req_dir.glob("*.txt")):
                if p.is_file():
                    candidates.append(p)
        except OSError:
            pass
    return candidates


def native_integration_already_applied(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get(_NATIVE_INTEGRATION_FLAG))


def native_integration_http3_disabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get(_NATIVE_INTEGRATION_HTTP3_DISABLED))


def harness_supports_http3_cmake() -> bool:
    """SWE-bench env images (Ubuntu jammy) lack ngtcp2 quictls — never enable HTTP/3 there."""
    return False


def native_integration_discover_active(cfg: dict[str, Any] | None) -> bool:
    """True when Docker discover should run CMake + nested pytest (not plain ctest)."""
    return bool(cfg and cfg.get(_NATIVE_INTEGRATION_FLAG))


def discover_harness_language(task_language: str, install_config: dict[str, Any] | None) -> str:
    """Use Python env image for native integration pytest even when task ``language`` is ``c``."""
    if native_integration_discover_active(install_config):
        return "python"
    return task_language


def is_integration_pytest_module(path: str) -> bool:
    """True when *path* is a pytest-collectible module (``test_*.py``), not libtest/C assets."""
    p = path.replace("\\", "/").strip()
    if not p.lower().endswith(".py"):
        return False
    base = PurePosixPath(p).name.lower()
    return base.startswith("test_")


def filter_integration_pytest_modules(paths: Iterable[str]) -> list[str]:
    """Keep only runnable nested-pytest modules for native integration discover."""
    from .languages import filter_python_pytest_targets

    out: list[str] = []
    seen: set[str] = set()
    for raw in filter_python_pytest_targets(paths):
        norm = raw.replace("\\", "/").strip()
        if not norm or norm in seen or not is_integration_pytest_module(norm):
            continue
        seen.add(norm)
        out.append(norm)
    return sorted(out)


def patch_diff_touches_libtest(test_patch: str) -> bool:
    """True when *test_patch* changes curl libtest / runtests data (needs ``testdeps``)."""
    for m in re.finditer(r"^diff --git a/(\S+)", test_patch or "", re.MULTILINE):
        p = m.group(1).replace("\\", "/").lower()
        if p.startswith("tests/libtest/") or "/libtest/" in p:
            return True
        if p.startswith("tests/data/") and not p.endswith(".py"):
            return True
    return False


def integration_pytest_paths_from_patches(patch: str, test_patch: str) -> list[str]:
    """Pytest file paths from PR diffs (for CMake+pytest integration repos)."""
    from .languages import collect_test_targets

    paths = collect_test_targets("python", patch, test_patch)
    if not paths and test_patch.strip():
        paths = collect_test_targets("python", "", test_patch)
    return filter_integration_pytest_modules(paths)


def merge_hybrid_c_integration_paths(
    patch: str,
    test_patch: str,
    *,
    language: str = "c",
    test_paths: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """
    Return ``(detection_paths, runner_paths)`` for CMake repos with nested pytest.

    ``language=c`` target collection ignores ``.py`` files; runner paths prefer
    integration pytest files (e.g. curl ``tests/http/test_05_*.py``).
    """
    from .languages import collect_test_targets

    c_paths = (
        list(test_paths)
        if test_paths is not None
        else collect_test_targets(language, patch, test_patch)
    )
    int_paths = integration_pytest_paths_from_patches(patch, test_patch)
    detection = list(c_paths)
    for p in int_paths:
        if p not in detection:
            detection.append(p)
    # Runner paths must be pytest modules; never pass libtest/C runtests assets to pytest.
    runner = list(int_paths)
    return detection, runner


def resolve_integration_task_language(
    repo: Path,
    *,
    patch: str = "",
    test_patch: str = "",
) -> str | None:
    """When CMake + nested pytest matches PR paths, task language should be ``c``."""
    detection, runner = merge_hybrid_c_integration_paths(patch, test_patch)
    check_paths = runner or detection or None
    if not runner:
        return None
    if not repo_has_cmake_integration(repo, test_paths=runner):
        return None
    return "c"


def language_supports_native_integration(language: str) -> bool:
    lang = str(language or "").strip().lower()
    return lang in ("c", "python", "py", "auto", "")


def _repo_cmake_mentions_http3_quic(repo: Path) -> bool:
    """True when CMake metadata references HTTP/3 / QUIC backends (repo-agnostic)."""
    candidates = [repo / "CMakeLists.txt", repo / "tests" / "CMakeLists.txt"]
    try:
        candidates.extend(repo.glob("tests/**/CMakeLists.txt"))
    except OSError:
        pass
    seen: set[str] = set()
    for cmake in candidates:
        key = str(cmake)
        if key in seen or not cmake.is_file():
            continue
        seen.add(key)
        try:
            text = cmake.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _NATIVE_HTTP3_CMAKE_HINT_RE.search(text):
            return True
    return False


def _test_paths_suggest_http3_pytest(test_paths: Iterable[str]) -> bool:
    for raw in test_paths:
        norm = raw.replace("\\", "/").lower()
        if _HTTP3_TEST_PATH_RE.search(norm):
            return True
        name = PurePosixPath(norm).name
        if "h3" in name or "http3" in name or "quic" in name:
            return True
    return False


def _integration_tree_has_http3_pytest(repo: Path, pytest_root: str) -> bool:
    """Scan the integration pytest tree for HTTP/3-style test modules."""
    root = repo / pytest_root.replace("\\", "/").strip("/")
    if not root.is_dir():
        return False
    try:
        for path in root.rglob("test_*.py"):
            if not path.is_file():
                continue
            low = path.name.lower()
            if "h3" in low or "http3" in low or "quic" in low or "ngtcp" in low:
                return True
    except OSError:
        return False
    return False


def repo_wants_http3_pytest_cmake(
    repo: Path,
    test_paths: list[str] | None = None,
) -> bool:
    """
    Enable HTTP/3 QUIC cmake flags + ngtcp2 apt when the PR or tree targets HTTP/3 pytest.

    When *test_paths* is provided (including an empty list), only those paths decide
    HTTP/3 — CI/cmake heuristics are not used. Pass ``None`` when PR paths are unknown.
    """
    if test_paths is not None:
        return _test_paths_suggest_http3_pytest(test_paths)
    if _repo_cmake_mentions_http3_quic(repo):
        return True
    if cmake_definitions_from_ci_for_http3_pytest(repo):
        return True
    profile = integration_profile_for_repo(repo, test_paths=test_paths)
    if profile and _integration_tree_has_http3_pytest(repo, profile.pytest_root):
        return True
    return False


def _merge_core_native_apt(
    existing: Iterable[str],
    repo: Path,
) -> list[str]:
    """Keep core cmake apt (e.g. libnghttp2-dev) without HTTP/3 ngtcp2 packages."""
    from .c_build import cmake_apt_packages_for_repo

    merged: list[str] = []
    seen: set[str] = set()
    for raw in list(existing) + list(cmake_apt_packages_for_repo(repo)):
        pkg = str(raw or "").strip()
        if not pkg:
            continue
        low = pkg.lower()
        if "ngtcp2" in low or "nghttp3" in low:
            continue
        if pkg in seen:
            continue
        seen.add(pkg)
        merged.append(pkg)
    return filter_native_integration_apt_packages(merged)


def _install_looks_like_native_integration(cfg: dict[str, Any]) -> bool:
    install = str(cfg.get("install") or "").lower()
    return (
        native_integration_already_applied(cfg)
        or native_integration_discover_active(cfg)
        or "cmake" in install
        and ("pytest" in str(cfg.get("test_cmd") or "").lower() or "build/" in install)
    )


def remediate_native_integration_ngtcp2(
    cfg: dict[str, Any],
    repo: Path,
    *,
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Minimal native-integration profile when quictls / HTTP/3 backend is unavailable."""
    del test_paths
    out = dict(cfg)
    out[_NATIVE_INTEGRATION_HTTP3_DISABLED] = True
    out["install"] = native_cmake_install_command(repo, http3=False, clean_build=True)

    out["apt-pkgs"] = _merge_core_native_apt(list(out.get("apt-pkgs") or []), repo)

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
        if "ngtcp2" in low or "nghttp3" in low or "h2o" in low:
            continue
        pre.append(ln)
    out["pre_install"] = pre
    return sanitize_native_integration_apt_config(out)


def remediate_quictls_native_integration(
    cfg: dict[str, Any],
    repo: Path,
    *,
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Apply minimal cmake profile when log shows quictls missing or HTTP/3 was configured."""
    if native_integration_http3_disabled(cfg):
        return cfg
    install = str(cfg.get("install") or "")
    needs = (
        "USE_NGTCP2" in install.upper()
        or "USE_PROXY_HTTP3" in install.upper()
        or _install_looks_like_native_integration(cfg)
    )
    if not needs:
        stripped = strip_http3_cmake_flags(install)
        if stripped != install:
            out = dict(cfg)
            out["install"] = stripped
            out[_NATIVE_INTEGRATION_HTTP3_DISABLED] = True
            return out
        return cfg
    return remediate_native_integration_ngtcp2(
        cfg, repo, test_paths=test_paths
    )


def strip_http3_cmake_flags(install: str) -> str:
    """Remove HTTP/3 QUIC ``-D`` flags when ngtcp2 crypto backend is unavailable."""
    out: list[str] = []
    for token in str(install or "").split():
        up = token.upper()
        if "USE_NGTCP2" in up or "USE_PROXY_HTTP3" in up:
            continue
        out.append(token)
    return " ".join(out).strip()


def native_cmake_install_command(
    repo: Path,
    *,
    http3: bool,
    clean_build: bool = False,
) -> str:
    """CMake configure + build under ``build/`` (optionally with CI HTTP/3 ``-D`` flags)."""
    if not http3:
        return _CMAKE_NATIVE_INSTALL_CLEAN if clean_build else _CMAKE_NATIVE_INSTALL
    flags = cmake_definitions_from_ci_for_http3_pytest(repo)
    if not flags:
        flags = list(DEFAULT_NATIVE_HTTP3_CMAKE_DEFINITIONS)
    flag_s = " ".join(flags)
    return (
        "mkdir -p build && cd build && "
        f"cmake -G Ninja .. {flag_s} && "
        'cmake --build . -j"$(nproc)"'
    )


def filter_http3_apt_for_harness(packages: Iterable[str]) -> list[str]:
    """Drop bookworm-missing ngtcp2 OpenSSL dev packages from bulk ``apt-pkgs`` installs."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in packages:
        if not isinstance(raw, str):
            continue
        low = raw.strip().lower()
        if not low or low in seen or low in _NGTCP2_CRYPTO_PREINSTALL_ONLY_APT:
            continue
        seen.add(low)
        out.append(raw.strip())
    return out


def _is_redundant_c_apt_preinstall_line(line: str) -> bool:
    """Drop generic C ``apt-get`` lines when native HTTP/3 pre_install already handles apt."""
    stripped = line.strip()
    if not stripped.startswith("apt-get"):
        return False
    if "libngtcp2-crypto" in stripped:
        return False
    if "ninja-build" in stripped and "libnghttp3-dev" in stripped:
        return False
    if stripped.endswith("|| true") and "h2o" in stripped:
        return False
    if stripped.startswith("(") or "||" in stripped:
        return False
    return True


def _strip_redundant_c_apt_preinstall(pre_install: list[str]) -> list[str]:
    return [ln for ln in pre_install if not _is_redundant_c_apt_preinstall_line(ln)]


def _merge_apt_pkgs_field_only(cfg: dict[str, Any], deb_packages: list[str]) -> dict[str, Any]:
    """Extend ``apt-pkgs`` without duplicating ``pre_install`` apt lines."""
    deb_packages = filter_native_integration_apt_packages(
        filter_http3_apt_for_harness(deb_packages)
    )
    if not deb_packages:
        return cfg
    out = dict(cfg)
    apt = filter_http3_apt_for_harness(
        filter_native_integration_apt_packages(list(out.get("apt-pkgs") or []) + deb_packages)
    )
    if apt:
        out["apt-pkgs"] = apt
    return out


def _ngtcp2_crypto_apt_install_shell() -> str:
    """
    Install ngtcp2 QUIC TLS crypto backend without unsigned apt repositories.

    Prefer OpenSSL backend when the distro ships it; Debian bookworm (SWE-bench default)
    falls back to ``libngtcp2-crypto-gnutls-dev`` in main apt. Do not enable backports
    (GPG/keyring is not set up in harness images).
    """
    return (
        "("
        "apt-get install -y --no-install-recommends libngtcp2-crypto-ossl-dev"
        " || apt-get install -y --no-install-recommends libngtcp2-crypto-gnutls-dev"
        ")"
    )


def _optional_integration_server_apt_lines(repo: Path) -> list[str]:
    """Best-effort apt for CMake ``find_program`` servers (h2o, etc.) under ``tests/``."""
    lines: list[str] = []
    seen: set[str] = set()
    try:
        for cmake in repo.glob("tests/**/CMakeLists.txt"):
            if not cmake.is_file():
                continue
            try:
                text = cmake.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(r'find_program\s*\(\s*H2O\b', text, re.IGNORECASE):
                if "h2o" not in seen:
                    seen.add("h2o")
                    lines.append("apt-get install -y --no-install-recommends h2o || true")
                break
    except OSError:
        pass
    return lines


def _native_http3_pre_install_lines(repo: Path | None = None) -> list[str]:
    """Install ngtcp2/nghttp3/crypto (+ optional servers) before cmake configure."""
    core = " ".join(_NATIVE_HTTP3_APT_CORE)
    lines = [
        "export DEBIAN_FRONTEND=noninteractive",
        f"apt-get update -qq && apt-get install -y --no-install-recommends {core}",
        _ngtcp2_crypto_apt_install_shell(),
    ]
    lines.append("apt-get install -y --no-install-recommends h2o || true")
    if repo is not None:
        for ln in _optional_integration_server_apt_lines(repo):
            if ln not in lines:
                lines.append(ln)
    return lines


def _http3_native_pre_install_lines() -> list[str]:
    """Backward-compatible wrapper; pass *repo* via :func:`_native_http3_pre_install_lines`."""
    return _native_http3_pre_install_lines(None)


def native_integration_test_cmd(pytest_root: str, *, xdist: bool = False) -> str:
    """
    Discover-time pytest template for Docker entry (``__JUNIT_OUT__`` / ``__TARGETS__``).

    ``docker_entry`` substitutes placeholders and appends target paths.
    """
    xdist_flag = " -n auto" if xdist else ""
    pytest = (
        "python3 -m pytest --no-header -rA --tb=line --color=no "
        f"-p no:cacheprovider{xdist_flag} --junitxml={_JUNIT_OUT} {_TARGETS}"
    )
    _ = pytest_root  # suite directory: ``docker_entry`` ``NATIVE_PYTEST_ROOT`` block
    return pytest


def cmake_pytest_targets_from_repo(repo: Path) -> list[str]:
    """Custom CMake targets that run pytest (e.g. ``curl-pytest-ci``)."""
    found: list[str] = []
    seen: set[str] = set()
    for cmake in [repo / "CMakeLists.txt", repo / "tests" / "CMakeLists.txt"]:
        if not cmake.is_file():
            continue
        try:
            text = cmake.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _CMAKE_PYTEST_TARGET_RE.finditer(text):
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                found.append(name)
    return found


def cmake_pytest_ci_target(repo: Path) -> str | None:
    """Prefer a ``*-pytest-ci`` target from CMake or CI workflows."""
    from_ci: list[str] = []
    wf_dir = repo / ".github" / "workflows"
    if wf_dir.is_dir():
        for wf in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
            try:
                text = wf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in _CMAKE_BUILD_TARGET_RE.finditer(text):
                t = m.group(1)
                if "pytest" in t.lower():
                    from_ci.append(t)
    for cand in from_ci + cmake_pytest_targets_from_repo(repo):
        if cand.endswith("-pytest-ci") or cand == "pytest-ci":
            return cand
    targets = cmake_pytest_targets_from_repo(repo)
    return targets[0] if targets else None


def cmake_runtests_harness_subdirs(repo: Path) -> list[str]:
    """``tests/<subdir>/`` harness trees that ``runtests.pl`` resolves relative to ``tests/``."""
    if not (repo / "tests" / "runtests.pl").is_file():
        return []
    out: list[str] = []
    for sub in _RUNTESTS_HARNESS_SUBDIRS:
        d = repo / "tests" / sub
        if d.is_dir() or (d / "CMakeLists.txt").is_file():
            out.append(sub)
    return out


def cmake_runtests_harness_tool_name(subdir: str) -> str | None:
    """Default primary executable name under ``tests/<subdir>/`` (e.g. ``libtests``)."""
    return _RUNTESTS_HARNESS_DEFAULT_TOOL.get(subdir)


def _cmake_harness_subdir_targets(repo: Path, subdir: str) -> set[str]:
    """
    CMake target names for a ``tests/<subdir>`` harness (e.g. ``libtests``, ``servers``).

    curl bundles use ``add_executable(${BUNDLE} ...)`` with ``BUNDLE = name`` in Makefile.inc.
    """
    targets: set[str] = set()
    sub_cmake = repo / "tests" / subdir / "CMakeLists.txt"
    if not sub_cmake.is_file():
        return targets
    try:
        text = sub_cmake.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return targets
    targets.update(m.group(1) for m in _CMAKE_TARGET_RE.finditer(text))
    targets.update(m.group(1) for m in _CMAKE_EXECUTABLE_RE.finditer(text))
    makefile_inc = repo / "tests" / subdir / "Makefile.inc"
    if makefile_inc.is_file():
        try:
            inc = makefile_inc.read_text(encoding="utf-8", errors="replace")
            bundle = _CMAKE_SERVER_BUNDLE_RE.search(inc)
            if bundle:
                targets.add(bundle.group(1))
        except OSError:
            pass
    return targets


def _cmake_harness_server_targets(repo: Path) -> set[str]:
    """Backward-compatible alias for ``tests/server`` bundle targets."""
    return _cmake_harness_subdir_targets(repo, "server")


def cmake_native_prepare_targets(
    repo: Path,
    *,
    include_libtest_targets: bool = True,
    include_harness_servers: bool = False,
    include_harness_binaries: bool | None = None,
) -> list[str]:
    """
    Build targets that must exist before integration pytest.

    ``testdeps`` / ``libtests`` compile bundled libtests and fail at base+test_patch
    when the PR adds libtest stubs that need impl-only API symbols. Skip them for
    HTTP pytest-only PRs; keep ``build-certs`` for integration server fixtures.

    When *include_harness_binaries* is set (``runtests.pl`` discover), also build
    harness targets under ``tests/server``, ``tests/libtest``, etc.
    """
    harness_binaries = (
        include_harness_binaries
        if include_harness_binaries is not None
        else include_harness_servers
    )
    present: set[str] = set()
    cmake_files = (
        repo / "tests" / "CMakeLists.txt",
        repo / "tests" / "certs" / "CMakeLists.txt",
    )
    if harness_binaries:
        for sub in cmake_runtests_harness_subdirs(repo):
            cmake_files = cmake_files + (repo / "tests" / sub / "CMakeLists.txt",)
    for cmake in cmake_files:
        if not cmake.is_file():
            continue
        try:
            text = cmake.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        present.update(m.group(1) for m in _CMAKE_TARGET_RE.finditer(text))
    if harness_binaries:
        for sub in cmake_runtests_harness_subdirs(repo):
            present.update(_cmake_harness_subdir_targets(repo, sub))
    if include_libtest_targets:
        order: tuple[str, ...] = ("testdeps", "libtests", "build-certs")
        if harness_binaries:
            order = order + ("servers",)
        order = order + ("tt",)
        if harness_binaries:
            for extra in ("units", "tunits"):
                if extra in present:
                    order = order + (extra,)
    else:
        order = ("build-certs",)
    return [t for t in order if t in present]


_PRE_PYTEST_SKIP_RE = re.compile(
    r"(?:^|\s)(?:pytest\b|pip\s+install|npm\s|yarn\s|apt-get|apt\s+install|"
    r"actions/checkout|git\s+clone)",
    re.IGNORECASE,
)
_PRE_PYTEST_KEEP_RE = re.compile(
    r"cmake\s+--build\s+\S+\s+--(?:target|-t)\s+|"
    r"cmake\s+--build\s+build\b|\bcp\s+|\bmv\s+|config\.ini|\.ini\.in|"
    r"build-certs|testdeps|libtests",
    re.IGNORECASE,
)
_PRE_PYTEST_DENY_RE = re.compile(
    r"\bautoreconf\b|\bsudo\b|/home/runner|/home/|\blinuxbrew\b|"
    r"cmake\s+-B\s+bld\b|make\s+-C\s+bld\b|\bdpkg\s+-i\b|\bbrew\s+install\b|"
    r"\|\s*$|^\s*\|\s*$|MATRIX_|GITHUB_WORKSPACE",
    re.IGNORECASE,
)
_NATIVE_UNSAFE_SHELL_RE = _PRE_PYTEST_DENY_RE
_PYTEST_CI_TARGET_RE = re.compile(r"pytest-ci|_pytest\b", re.IGNORECASE)


def integration_sync_config_from_build(repo: Path, pytest_root: str) -> list[str]:
    """
    Copy CMake-generated config into the source pytest tree.

    ``configure_file(... config.ini.in .../config.ini)`` writes under ``build/``;
    pytest imports expect ``{pytest_root}/config.ini`` in the source tree.
    """
    root = pytest_root.replace("\\", "/").strip("/")
    if not root:
        return []
    in_cfg = repo / root / "config.ini.in"
    http_cmake = repo / root / "CMakeLists.txt"
    if not in_cfg.is_file() and not http_cmake.is_file():
        return []
    return [
        f'if [[ -f build/{root}/config.ini ]]; then '
        f'cp -f build/{root}/config.ini {root}/config.ini; '
        f'elif [[ -f {root}/config.ini.in ]]; then '
        f'cp -f {root}/config.ini.in {root}/config.ini; fi',
    ]


def integration_sanitize_config_ini_lines(pytest_root: str) -> list[str]:
    """
    Clear tools curl CI does not install / that break ``tests/http/testenv/env.py``.

    Ubuntu ``sshd`` rejects ``-V``; ``env.py`` asserts instead of disabling sshd when
    ``config.ini`` points at ``/usr/sbin/sshd``.
    """
    root = pytest_root.replace("\\", "/").strip("/")
    if not root:
        return []
    cfg = f"{root}/config.ini"
    return [
        f'if [[ -f {cfg} ]]; then '
        f"sed -i -e 's/^sshd = .*/sshd =/' -e 's/^sftpd = .*/sftpd =/' {cfg}; fi",
    ]


def filter_native_integration_apt_packages(packages: Iterable[str]) -> list[str]:
    """Drop apt packages that break or exceed curl HTTP pytest CI."""
    return filter_http3_apt_for_harness(
        _filter_native_integration_apt_packages_inner(packages)
    )


def _filter_native_integration_apt_packages_inner(packages: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in packages:
        if not isinstance(raw, str):
            continue
        low = raw.strip().lower()
        if not low or low in seen or low in _NATIVE_INTEGRATION_APT_BLOCKLIST:
            continue
        seen.add(low)
        out.append(raw.strip())
    return out


def _ci_run_lines_mention_pytest(runs: list[str]) -> bool:
    """True when at least one scraped single-line ``run:`` invokes pytest."""
    return any(re.search(r"\bpytest\b", line, re.IGNORECASE) for line in runs)


def strip_unsafe_native_shell_lines(lines: Iterable[str]) -> list[str]:
    """Drop CI/host-specific commands that break minimal SWE-bench containers."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        if not isinstance(raw, str):
            continue
        line = raw.strip()
        if not line or line in seen:
            continue
        if _NATIVE_UNSAFE_SHELL_RE.search(line):
            continue
        seen.add(line)
        out.append(line)
    return out


def ci_pre_pytest_setup_lines(repo: Path, *, max_files: int = 40) -> list[str]:
    """
    CI ``run:`` lines before the first pytest invocation (allowlisted setup only).

    When pytest only appears in multiline ``run: |`` blocks (common in curl CI),
    returns [] — do not treat every earlier workflow line as pre-pytest setup.
    """
    runs = ci_all_run_lines(repo, max_files=max_files)
    if not _ci_run_lines_mention_pytest(runs):
        return []
    pytest_at = len(runs)
    for i, line in enumerate(runs):
        if re.search(r"\bpytest\b", line, re.IGNORECASE):
            pytest_at = i
            break
    out: list[str] = []
    seen: set[str] = set()
    for line in runs[:pytest_at]:
        low = line.lower()
        if "pytest-ci" in low or _PYTEST_CI_TARGET_RE.search(line):
            continue
        if re.search(r"--target\s+\S*pytest", line, re.IGNORECASE):
            continue
        if _PRE_PYTEST_DENY_RE.search(line):
            continue
        if not _PRE_PYTEST_KEEP_RE.search(line):
            continue
        if _PRE_PYTEST_SKIP_RE.search(line):
            continue
        if "${{" in line:
            continue
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out[:20]


def repo_needs_cmake_src_tool_symlinks(repo: Path) -> bool:
    """
    True when pytest expects autotools-style ``src/curl`` paths but CMake builds under ``build/src/``.

    curl ``tests/http/testenv/env.py`` hardcodes ``TOP_PATH/src/curlinfo`` and only honors ``CURL`` in
    the environment, not ``CURLINFO``.
    """
    src_cmake = repo / "src" / "CMakeLists.txt"
    if not src_cmake.is_file():
        return False
    try:
        text = src_cmake.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "curlinfo" in text and "add_executable" in text


def native_integration_cmake_src_symlink_lines(repo: Path) -> list[str]:
    """Symlink ``src/curl`` and ``src/curlinfo`` to CMake outputs under ``build/src/``."""
    if not repo_needs_cmake_src_tool_symlinks(repo):
        return []
    return [
        "mkdir -p src",
        "ln -sfn ../build/src/curl src/curl",
        "ln -sfn ../build/src/curlinfo src/curlinfo",
    ]


def _cmake_runtests_layout_symlink_line(subdir: str) -> str:
    """Symlink ``build/tests/<subdir>/*`` into ``tests/<subdir>/`` for ``runtests.pl``."""
    return (
        f"mkdir -p tests/{subdir} && "
        f'for f in build/tests/{subdir}/*; do '
        f'[[ -e "$f" ]] || continue; '
        f'bn=$(basename "$f"); '
        f'ln -sfn "../../build/tests/{subdir}/$bn" "tests/{subdir}/$bn"; '
        "done"
    )


def cmake_runtests_layout_symlink_lines(repo: Path) -> list[str]:
    """
    Bridge CMake harness outputs into autotools paths ``runtests.pl`` expects.

    For each ``tests/<subdir>/`` (server, libtest, unit, tunit), link
    ``build/tests/<subdir>/*`` → ``tests/<subdir>/*``.
    """
    if not (repo / "tests" / "runtests.pl").is_file():
        return []
    if not (repo / "CMakeLists.txt").is_file():
        return []
    lines: list[str] = []
    for sub in cmake_runtests_harness_subdirs(repo):
        ln = _cmake_runtests_layout_symlink_line(sub)
        if ln not in lines:
            lines.append(ln)
    return lines


def cmake_runtests_server_symlink_lines(repo: Path) -> list[str]:
    """Deprecated: use :func:`cmake_runtests_layout_symlink_lines`."""
    return cmake_runtests_layout_symlink_lines(repo)


def native_integration_setup_lines(
    repo: Path,
    pytest_root: str,
    *,
    test_patch: str = "",
    pytest_paths: list[str] | None = None,
) -> list[str]:
    """Shell lines to run after install / after ``git reset`` before integration pytest."""
    del pytest_paths  # reserved: future patch-aware server target selection
    if test_patch.strip():
        include_libtest = patch_diff_touches_libtest(test_patch)
    else:
        include_libtest = True
    lines: list[str] = []
    for prep in cmake_native_prepare_targets(
        repo, include_libtest_targets=include_libtest
    ):
        cmd = f'cmake --build build --target {prep} -j"$(nproc)"'
        if cmd not in lines:
            lines.append(cmd)
    if repo_needs_cmake_src_tool_symlinks(repo):
        curlinfo_build = 'cmake --build build --target curlinfo -j"$(nproc)"'
        if curlinfo_build not in lines:
            lines.append(curlinfo_build)
    lines.extend(integration_sync_config_from_build(repo, pytest_root))
    lines.extend(integration_sanitize_config_ini_lines(pytest_root))
    lines.extend(native_integration_cmake_src_symlink_lines(repo))
    return strip_unsafe_native_shell_lines(lines)


_NATIVE_SETUP_SHELL_RE = re.compile(
    r"cmake\s+--build\b|config\.ini|build-certs|\btestdeps\b|\blibtests\b|"
    r"\bln\s+-sfn|\bcurlinfo\b|\bsed\s+-i",
    re.IGNORECASE,
)


def is_native_integration_setup_line(line: str) -> bool:
    """True for cmake/config sync lines that belong in ``native_integration_setup`` only."""
    return bool(_NATIVE_SETUP_SHELL_RE.search(line))


def native_integration_repo_dir(cfg: dict[str, Any]) -> str:
    raw = str(cfg.get(_NATIVE_INTEGRATION_REPO_DIR) or _DEFAULT_HARNESS_REPO_DIR).strip()
    return raw or _DEFAULT_HARNESS_REPO_DIR


def native_integration_cmake_tool_env(
    cfg: dict[str, Any] | None = None,
    *,
    repo_dir: str | None = None,
) -> dict[str, str]:
    """Env vars for curl HTTP pytest (CMake places binaries under ``build/src/``)."""
    root = (repo_dir or (native_integration_repo_dir(cfg) if cfg else None) or _DEFAULT_HARNESS_REPO_DIR).rstrip("/")
    return {
        "CURL": f"{root}/build/src/curl",
        "CURLINFO": f"{root}/build/src/curlinfo",
        "CURL_CI": "1",
    }


def native_integration_eval_commands(cfg: dict[str, Any]) -> list[str]:
    """``eval_commands`` safe between git reset phases (PATH + curl tool exports)."""
    repo = native_integration_repo_dir(cfg)
    build_path = f'export PATH="{repo}/build/src:$PATH:{repo}/build:$PATH:${{PATH}}"'
    out: list[str] = []
    seen: set[str] = set()
    for key, val in native_integration_cmake_tool_env(repo_dir=repo).items():
        ln = f'export {key}="{val}"'
        seen.add(ln)
        out.append(ln)
    for raw in cfg.get("eval_commands") or []:
        if not isinstance(raw, str):
            continue
        ln = raw.strip()
        if not ln or ln in seen or is_native_integration_setup_line(ln):
            continue
        if ln.startswith("export "):
            seen.add(ln)
            out.append(ln)
    if build_path not in seen:
        out.append(build_path)
    return out


def _merge_setup_into_config_lists(cfg: dict[str, Any], setup: list[str]) -> dict[str, Any]:
    setup = strip_unsafe_native_shell_lines(setup)
    if not setup:
        return cfg
    out = dict(cfg)
    out["native_integration_setup"] = list(setup)
    post = strip_unsafe_native_shell_lines(list(out.get("post_install") or []))
    for ln in setup:
        if ln not in post:
            post.append(ln)
    out["post_install"] = post
    out["eval_commands"] = native_integration_eval_commands(out)
    return out


def apt_packages_from_cmake_find_program(repo: Path) -> list[str]:
    """Map ``find_program()`` hints under ``tests/`` to likely Debian packages."""
    seen: set[str] = set()
    out: list[str] = []
    try:
        cmake_files = list(repo.glob("tests/**/CMakeLists.txt"))
    except OSError:
        cmake_files = []
    if (repo / "tests" / "CMakeLists.txt").is_file():
        cmake_files.append(repo / "tests" / "CMakeLists.txt")
    for cmake in cmake_files:
        try:
            text = cmake.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _FIND_PROGRAM_RE.finditer(text):
            prog = (m.group(2) or m.group(3) or m.group(1) or "").lower()
            key = Path(prog).name if prog else m.group(1).lower()
            for apt in _PROGRAM_APT.get(key, ()):
                low = apt.lower()
                if low not in seen:
                    seen.add(low)
                    out.append(apt)
    return out


def cmake_pytest_uses_xdist(repo: Path) -> bool:
    tests_cmake = repo / "tests" / "CMakeLists.txt"
    if not tests_cmake.is_file():
        return False
    try:
        text = tests_cmake.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "-n auto" in text or "pytest-xdist" in text.lower()


def ci_pytest_run_lines(repo: Path, *, max_files: int = 40) -> list[str]:
    """Non-comment ``run:`` lines from GitHub workflows that mention pytest."""
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    out: list[str] = []
    seen: set[str] = set()
    count = 0
    for wf in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
        if count >= max_files:
            break
        count += 1
        try:
            text = wf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _CI_RUN_LINE_RE.finditer(text):
            line = m.group(1).strip()
            if "pytest" not in line.lower():
                continue
            if line not in seen:
                seen.add(line)
                out.append(line)
    return out


def ci_export_env_from_workflows(repo: Path, *, max_files: int = 40) -> dict[str, str]:
    """Simple ``export VAR=...`` assignments scraped from workflow ``run:`` blocks."""
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return {}
    env: dict[str, str] = {}
    count = 0
    for wf in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
        if count >= max_files:
            break
        count += 1
        try:
            text = wf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _CI_EXPORT_RE.finditer(text):
            key = m.group(1)
            val = m.group(2) or m.group(3) or m.group(4) or ""
            if val.startswith("${{") or "$" in val:
                continue
            env[key] = val
    return env


def native_build_install_config(
    cfg: dict[str, Any],
    repo: Path,
    *,
    test_paths: list[str] | None = None,
    test_patch: str = "",
) -> dict[str, Any]:
    """
    Merge CMake build + CI/cmake apt + integration pytest pip deps into *cfg*.

    Sets ``native_integration_build`` so Docker discover can treat empty JUnit as
    test discovery failure rather than repeating apt-only remediation.
    """
    profile = integration_profile_for_repo(repo, test_paths=test_paths)
    if profile is None:
        return cfg

    out = dict(cfg)
    prior = str(cfg.get("language") or "").strip().lower()
    if prior in ("c", "python", "py"):
        out["language"] = "python" if prior == "py" else prior
    else:
        out["language"] = "c"
    out[_NATIVE_INTEGRATION_FLAG] = True
    out[_NATIVE_INTEGRATION_ROOT] = profile.pytest_root
    out[_NATIVE_INTEGRATION_REPO_DIR] = _DEFAULT_HARNESS_REPO_DIR

    if native_integration_http3_disabled(out) or not harness_supports_http3_cmake():
        http3 = False
    else:
        http3 = repo_wants_http3_pytest_cmake(repo, test_paths)

    out = ensure_c_install_config(out, repo=repo)
    out["install"] = native_cmake_install_command(repo, http3=http3)

    deb: list[str] = []
    deb.extend(cmake_apt_packages_for_repo(repo))
    deb.extend(apt_packages_from_ci_workflows(repo))
    deb.extend(apt_packages_from_cmake_find_program(repo))
    if http3:
        deb.extend(_NATIVE_HTTP3_APT_CORE)
    deb = filter_native_integration_apt_packages(deb)
    if deb:
        if http3:
            out = _merge_apt_pkgs_field_only(out, deb)
        else:
            out = merge_integration_apt_into_config(out, deb)
    if http3:
        opt = filter_native_integration_apt_packages(list(_NATIVE_HTTP3_APT_OPTIONAL))
        if opt:
            base = dict(out)
            opt_existing = list(base.get("apt-pkgs-optional") or [])
            for pkg in opt:
                if pkg not in opt_existing:
                    opt_existing.append(pkg)
            base["apt-pkgs-optional"] = opt_existing
            out = base

    if http3:
        pre = _strip_redundant_c_apt_preinstall(list(out.get("pre_install") or []))
        for ln in _native_http3_pre_install_lines(repo):
            if ln not in pre:
                pre.insert(0, ln)
        out["pre_install"] = pre

    post = list(out.get("post_install") or [])
    for req in _pip_requirement_files(repo, profile.pytest_root):
        rel = req.relative_to(repo).as_posix()
        post.append(f"python3 -m pip install -q -r {rel}")
    pip_need = ["pytest"]
    if cmake_pytest_uses_xdist(repo):
        pip_need.append("pytest-xdist")
    for pkg in pip_need:
        if not any(pkg in ln for ln in post):
            post.append(f"python3 -m pip install -q {pkg}")
    repo_dir = native_integration_repo_dir(out)
    build_path = f'export PATH="{repo_dir}/build/src:$PATH:{repo_dir}/build:$PATH:${{PATH}}"'
    if build_path not in post:
        post.append(build_path)
    out["post_install"] = post
    setup = native_integration_setup_lines(
        repo,
        profile.pytest_root,
        test_patch=test_patch,
        pytest_paths=test_paths,
    )
    out = _merge_setup_into_config_lists(out, setup)
    out["eval_commands"] = native_integration_eval_commands(out)

    ci_target = cmake_pytest_ci_target(repo)
    if ci_target:
        out["native_integration_cmake_pytest_target"] = ci_target
        # Document CI entrypoint; scoped discover still uses pytest on PR test paths.
        # Full ``cmake --build --target *pytest-ci`` runs the entire suite — not used here.

    out["test_cmd"] = native_integration_test_cmd(
        profile.pytest_root,
        xdist=cmake_pytest_uses_xdist(repo),
    )

    test_env = dict(out.get("test_env") or {})
    test_env.update(native_integration_cmake_tool_env(out))
    test_env.update(ci_export_env_from_workflows(repo))
    if test_env:
        out["test_env"] = test_env

    return sanitize_native_integration_apt_config(out)


def apply_native_build_if_integration(
    cfg: dict[str, Any],
    repo: Path | None,
    *,
    test_paths: list[str] | None = None,
    test_patch: str = "",
    patch: str = "",
) -> dict[str, Any]:
    """Apply :func:`native_build_install_config` when the repo matches the pattern."""
    if repo is None:
        return cfg
    if native_integration_http3_disabled(cfg):
        return cfg
    if native_integration_already_applied(cfg):
        return cfg
    lang = str(cfg.get("language") or "").strip().lower()
    if not language_supports_native_integration(lang):
        return cfg
    detection, runner = merge_hybrid_c_integration_paths(
        patch, test_patch, test_paths=test_paths
    )
    if not runner:
        return cfg
    if not repo_has_cmake_integration(repo, test_paths=runner):
        return cfg
    profile_paths = runner
    cfg = native_build_install_config(
        cfg,
        repo,
        test_paths=profile_paths,
        test_patch=test_patch,
    )
    return sanitize_native_integration_apt_config(cfg)
