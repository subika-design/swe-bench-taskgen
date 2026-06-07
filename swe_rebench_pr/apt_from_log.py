"""Shared Debian package inference from Docker / compile logs (all languages)."""

from __future__ import annotations

import re
from typing import Any, Iterable

from .install_llm import merge_pre_install_debian_packages

# Not in default Ubuntu 22.04 apt; must not fail env-image ``apt-get install``.
_APT_INTEGRATION_BLOCKLIST = frozenset({"caddy", "h2o"})

# (log substring, debian package names)
BUILD_LOG_APT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("uuid/uuid.h", ("libuuid-dev",)),
    ("could not find sdl2_mixer", ("libsdl2-mixer-dev", "libsdl2-dev")),
    ("sdl2_mixer::sdl2_mixer", ("libsdl2-mixer-dev", "libsdl2-dev")),
    ("could not find sdl2_net", ("libsdl2-net-dev", "libsdl2-dev")),
    ("sdl2_net::sdl2_net", ("libsdl2-net-dev", "libsdl2-dev")),
    ("could not find sdl2", ("libsdl2-dev",)),
    ("sdl2::sdl2", ("libsdl2-dev",)),
    ("could not find png", ("libpng-dev",)),
    ("could not find fluidsynth", ("libfluidsynth-dev",)),
    ("could not find samplerate", ("libsamplerate0-dev",)),
    ("openssl/ssl.h", ("libssl-dev",)),
    ("curl/curl.h", ("libcurl4-openssl-dev",)),
    ("zlib.h", ("zlib1g-dev",)),
    ("libxml2", ("libxml2-dev",)),
    ("libxml-2.0", ("libxml2-dev",)),
    ("package 'libxml-2.0'", ("libxml2-dev",)),
    ("libffi.h", ("libffi-dev",)),
    ("pcre.h", ("libpcre3-dev",)),
    ("readline/readline.h", ("libreadline-dev",)),
    ("ncurses.h", ("libncurses-dev",)),
    ("liblzma", ("liblzma-dev",)),
    ("libbz2", ("libbz2-dev",)),
    ("libsqlite3", ("libsqlite3-dev",)),
    ("libjpeg", ("libjpeg-dev",)),
    ("libwebp", ("libwebp-dev",)),
    ("libavcodec", ("libavcodec-dev", "libavformat-dev", "libavutil-dev")),
    ("libmemcached", ("libmemcached-dev",)),
    ("libpq", ("libpq-dev", "pkg-config")),
    ("mariadb", ("pkg-config", "libmariadb-dev")),
    ("mysqlclient", ("pkg-config", "libmariadb-dev")),
    ("pkg-config not found", ("pkg-config",)),
    ("could not find openssl", ("libssl-dev", "pkg-config")),
    # CMake FindPackage / pkg-config (common on C/C++ networking libs)
    ("could not find libpsl", ("libpsl-dev",)),
    ("libpsl_include_dir", ("libpsl-dev",)),
    ("no package 'libpsl' found", ("libpsl-dev",)),
    ("could not find nghttp2", ("libnghttp2-dev",)),
    ("nghttp2_include_dir", ("libnghttp2-dev",)),
    ("no package 'libnghttp2' found", ("libnghttp2-dev",)),
    ("could not find libidn2", ("libidn2-dev",)),
    ("libidn2_include_dir", ("libidn2-dev",)),
    ("no package 'libidn2' found", ("libidn2-dev",)),
    ("could not find brotli", ("libbrotli-dev",)),
    ("brotli_include_dir", ("libbrotli-dev",)),
    ("no package 'libbrotlidec' found", ("libbrotli-dev",)),
    ("could not find zstd", ("libzstd-dev",)),
    ("zstd_include_dir", ("libzstd-dev",)),
    ("no package 'libzstd' found", ("libzstd-dev",)),
    ("native extension requires", ("build-essential", "pkg-config")),
    ("fatal error: ffi.h", ("libffi-dev",)),
    ("yaml.h", ("libyaml-dev",)),
    ("libgmp", ("libgmp-dev",)),
    ("libmpfr", ("libmpfr-dev",)),
    ("libevent", ("libevent-dev",)),
    ("libusb", ("libusb-1.0-0-dev",)),
    ("libpcap", ("libpcap-dev",)),
    ("libpulse", ("libpulse-dev",)),
    ("libasound", ("libasound2-dev",)),
    ("libx11", ("libx11-dev",)),
    ("libgtk", ("libgtk-3-dev",)),
    ("libgstreamer", ("libgstreamer1.0-dev",)),
    ("ffmpeg", ("ffmpeg", "libavcodec-dev", "libavformat-dev", "libavutil-dev")),
    ("ext-gd", ("libgd-dev",)),
    ("ext-zip", ("libzip-dev",)),
    ("ext-curl", ("libcurl4-openssl-dev",)),
    ("ext-intl", ("libicu-dev",)),
    ("ext-xml", ("libxml2-dev",)),
    ("ext-sodium", ("libsodium-dev",)),
    ("ext-pcntl", ("libpcap-dev",)),
)

_BASE_BUILD_APT = ("git", "build-essential", "pkg-config")

# Must succeed in env/pre_install; everything else is best-effort (``|| true``).
# PHP ``docker-php-ext-install`` dev libs — must succeed before compiling extensions.
_PHP_EXT_BUILD_APT = frozenset(
    {
        "libxml2-dev",
        "libzip-dev",
        "libicu-dev",
        "libcurl4-openssl-dev",
    }
)

_APT_REQUIRED_CORE = frozenset(
    {
        *_BASE_BUILD_APT,
        *_PHP_EXT_BUILD_APT,
        "cmake",
        "meson",
        "ninja-build",
        "unzip",
        "curl",
        "ca-certificates",
        "python3",
        "python3-pip",
        "python3-dev",
        "python3-venv",
    }
)

_APT_NOT_FOUND_RE = re.compile(r"E:\s*Unable to locate package\s+(\S+)", re.I)
_APT_INSTALL_LINE = re.compile(
    r"^apt-get install -y(?:\s+--no-install-recommends)?\s+(.*)$",
    re.IGNORECASE,
)
_APT_UPDATE_INSTALL_LINE = re.compile(
    r"^(apt-get update(?:\s+-qq)?)\s*&&\s*apt-get install -y(?:\s+--no-install-recommends)?\s+(.*)$",
    re.IGNORECASE,
)


def apt_packages_from_build_log(log: str) -> list[str]:
    """Infer missing Debian packages from install/compile/test logs."""
    if not log:
        return []
    low = log.lower()
    found: list[str] = []
    seen: set[str] = set()
    for needle, pkgs in BUILD_LOG_APT_HINTS:
        if needle in low:
            for pkg in pkgs:
                if pkg not in seen:
                    seen.add(pkg)
                    found.append(pkg)
    return found


def sanitize_apt_package_names(pkgs: Iterable[str]) -> list[str]:
    """Drop blocklisted / invalid Debian package tokens (integration server tools)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in pkgs:
        tok = str(raw).strip()
        if not tok:
            continue
        low = tok.lower()
        if low in _APT_INTEGRATION_BLOCKLIST:
            continue
        if not re.match(r"^[a-z0-9][a-z0-9+.-]*$", tok, re.IGNORECASE):
            continue
        if low not in seen:
            seen.add(low)
            out.append(tok)
    return out


def split_apt_packages_core_optional(pkgs: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split Debian packages into required core vs best-effort optional installs."""
    core: list[str] = []
    optional: list[str] = []
    seen: set[str] = set()
    for raw in pkgs:
        pkg = str(raw).strip()
        if not pkg:
            continue
        low = pkg.lower()
        if low in seen:
            continue
        seen.add(low)
        if low in _APT_REQUIRED_CORE:
            core.append(pkg)
        else:
            optional.append(pkg)
    return core, optional


def apt_packages_not_found_in_log(log: str) -> list[str]:
    """Parse ``E: Unable to locate package …`` lines from apt/build logs."""
    if not log:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _APT_NOT_FOUND_RE.finditer(log):
        pkg = m.group(1).strip()
        low = pkg.lower()
        if pkg and low not in seen:
            seen.add(low)
            out.append(pkg)
    return out


def log_indicates_apt_package_not_found(log: str) -> bool:
    return bool(apt_packages_not_found_in_log(log))


def _extract_apt_packages_from_pre_install(pre_install: Iterable[str]) -> list[str]:
    pkgs: list[str] = []
    seen: set[str] = set()
    for ln in pre_install:
        stripped = str(ln).strip()
        combined = _APT_UPDATE_INSTALL_LINE.match(stripped)
        if combined:
            for pkg in combined.group(2).split():
                if pkg not in seen:
                    seen.add(pkg)
                    pkgs.append(pkg)
            continue
        m = _APT_INSTALL_LINE.match(stripped)
        if m:
            for pkg in m.group(1).split():
                if pkg not in seen:
                    seen.add(pkg)
                    pkgs.append(pkg)
    return pkgs


def _is_apt_install_shell_line(line: str) -> bool:
    stripped = str(line).strip().lower()
    return stripped.startswith("apt-get install") or (
        "apt-get update" in stripped and "apt-get install" in stripped
    )


def resilient_apt_install_shell_lines(
    pkgs: Iterable[str],
    *,
    include_update: bool = True,
) -> list[str]:
    """
    Emit apt install commands that tolerate missing optional packages.

    Core build tools must install; CI/LLM-inferred dev libs install with ``|| true``.
    """
    sanitized = sanitize_apt_package_names(list(pkgs))
    if not sanitized:
        return []
    core, optional = split_apt_packages_core_optional(sanitized)
    lines: list[str] = []
    if include_update:
        lines.append("apt-get update -qq")
    if core:
        lines.append(
            "apt-get install -y --no-install-recommends " + " ".join(core)
        )
    for pkg in optional:
        lines.append(
            f"apt-get install -y --no-install-recommends {pkg} || true"
        )
    return lines


def remove_apt_packages_from_config(
    cfg: dict[str, Any],
    remove: Iterable[str],
) -> dict[str, Any]:
    """Drop unavailable Debian packages from ``apt-pkgs``, optional list, and ``pre_install``."""
    drop = {str(p).strip().lower() for p in remove if str(p).strip()}
    if not drop:
        return cfg
    out = dict(cfg)

    def _filter_pkgs(vals: list[str]) -> list[str]:
        return [p for p in vals if str(p).strip().lower() not in drop]

    apt = _filter_pkgs(list(out.get("apt-pkgs") or []))
    if apt:
        out["apt-pkgs"] = apt
    else:
        out.pop("apt-pkgs", None)

    opt = _filter_pkgs(list(out.get("apt-pkgs-optional") or []))
    if opt:
        out["apt-pkgs-optional"] = opt
    else:
        out.pop("apt-pkgs-optional", None)

    pre = list(out.get("pre_install") or [])
    non_apt = [ln for ln in pre if not _is_apt_install_shell_line(ln)]
    kept_pkgs = [
        p
        for p in _extract_apt_packages_from_pre_install(pre)
        if p.lower() not in drop
    ]
    out["pre_install"] = non_apt + resilient_apt_install_shell_lines(kept_pkgs)
    return out


def remediate_missing_apt_packages_from_log(cfg: dict[str, Any], log: str) -> dict[str, Any]:
    """Remove apt packages that the target image's apt index cannot locate."""
    missing = apt_packages_not_found_in_log(log)
    if not missing:
        return cfg
    return remove_apt_packages_from_config(cfg, missing)


def merge_apt_into_config(cfg: dict[str, Any], deb_packages: list[str]) -> dict[str, Any]:
    """Merge Debian packages into ``pre_install``, ``apt-pkgs``, and ``apt-pkgs-optional``."""
    deb_packages = sanitize_apt_package_names(deb_packages)
    if not deb_packages:
        return cfg
    core, optional = split_apt_packages_core_optional(deb_packages)
    out = dict(cfg)
    if core or optional:
        pre = list(out.get("pre_install") or [])
        out["pre_install"] = merge_pre_install_debian_packages(pre, [*core, *optional])
    if core:
        apt = sanitize_apt_package_names(list(out.get("apt-pkgs") or []) + core)
        out["apt-pkgs"] = apt
    if optional:
        existing = sanitize_apt_package_names(out.get("apt-pkgs-optional") or [])
        seen = set(existing)
        merged_opt = list(existing)
        for pkg in optional:
            if pkg not in seen:
                seen.add(pkg)
                merged_opt.append(pkg)
        out["apt-pkgs-optional"] = merged_opt
    return out


def merge_integration_apt_into_config(
    cfg: dict[str, Any],
    deb_packages: list[str],
    *,
    optional: list[str] | None = None,
) -> dict[str, Any]:
    """
    Merge required apt packages; optional ones install with ``|| true`` (env image only).
    """
    required = sanitize_apt_package_names(deb_packages)
    opt = sanitize_apt_package_names(optional or [])
    out = merge_apt_into_config(cfg, required) if required else dict(cfg)
    if opt:
        existing = sanitize_apt_package_names(out.get("apt-pkgs-optional") or [])
        seen = set(existing)
        merged_opt = list(existing)
        for pkg in opt:
            if pkg not in seen:
                seen.add(pkg)
                merged_opt.append(pkg)
        out["apt-pkgs-optional"] = merged_opt
    return out


def _native_integration_http3_install(cfg: dict[str, Any]) -> bool:
    from .integration_build import native_integration_http3_disabled

    if native_integration_http3_disabled(cfg):
        return False
    install = str(cfg.get("install") or "")
    return "USE_NGTCP2" in install.upper() or "USE_PROXY_HTTP3" in install.upper()


def sanitize_native_integration_apt_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Strip blocklisted apt names and unsafe shell lines from native-integration configs."""
    if not cfg.get("native_integration_build"):
        return cfg
    from .integration_build import (
        filter_native_integration_apt_packages,
        native_integration_eval_commands,
        native_integration_http3_disabled,
        strip_unsafe_native_shell_lines,
    )

    http3 = _native_integration_http3_install(cfg)
    out = dict(cfg)
    def _sanitize_pkgs(pkgs: list[str]) -> list[str]:
        cleaned = sanitize_apt_package_names(pkgs)
        if native_integration_http3_disabled(cfg):
            cleaned = [
                p
                for p in cleaned
                if "ngtcp2" not in str(p).lower() and "nghttp3" not in str(p).lower()
            ]
        if http3:
            for name in ("h2o",):
                if name in {p.lower() for p in pkgs} and name not in {p.lower() for p in cleaned}:
                    cleaned.append(name)
        return filter_native_integration_apt_packages(cleaned)

    out["apt-pkgs"] = _sanitize_pkgs(list(out.get("apt-pkgs") or []))
    opt = _sanitize_pkgs(list(out.get("apt-pkgs-optional") or []))
    if opt:
        out["apt-pkgs-optional"] = opt
    else:
        out.pop("apt-pkgs-optional", None)
    pre = list(out.get("pre_install") or [])
    if pre:
        from .install_llm import _APT_INSTALL_LINE

        cleaned: list[str] = []
        for ln in pre:
            stripped = ln.strip()
            m = _APT_INSTALL_LINE.match(stripped)
            if m:
                pkgs = sanitize_apt_package_names(m.group(1).split())
                if http3:
                    for name in ("h2o",):
                        if name in {p.lower() for p in m.group(1).split()} and name not in {
                            p.lower() for p in pkgs
                        }:
                            pkgs.append(name)
                if not pkgs:
                    continue
                prefix = (
                    "apt-get install -y --no-install-recommends"
                    if "--no-install-recommends" in stripped
                    else "apt-get install -y"
                )
                cleaned.append(f"{prefix} {' '.join(pkgs)}")
            else:
                cleaned.append(ln)
        out["pre_install"] = cleaned
    for key in ("post_install", "native_integration_setup"):
        vals = out.get(key)
        if isinstance(vals, list) and vals:
            out[key] = strip_unsafe_native_shell_lines(vals)
    out["eval_commands"] = native_integration_eval_commands(out)
    return out


def ensure_base_build_apt(cfg: dict[str, Any]) -> dict[str, Any]:
    """Ensure git/build-essential/pkg-config appear in pre_install."""
    return merge_apt_into_config(cfg, list(_BASE_BUILD_APT))


def remediate_apt_install_from_log(cfg: dict[str, Any], log: str) -> dict[str, Any]:
    """Apply log-driven apt package fixes (language-agnostic)."""
    out = remediate_missing_apt_packages_from_log(dict(cfg), log)
    out = ensure_base_build_apt(out)
    deb = apt_packages_from_build_log(log)
    out = merge_apt_into_config(out, deb)
    if out.get("native_integration_build"):
        out = sanitize_native_integration_apt_config(out)
    return out
