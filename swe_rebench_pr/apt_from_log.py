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


def merge_apt_into_config(cfg: dict[str, Any], deb_packages: list[str]) -> dict[str, Any]:
    """Merge Debian packages into ``pre_install`` and ``apt-pkgs``."""
    deb_packages = sanitize_apt_package_names(deb_packages)
    if not deb_packages:
        return cfg
    out = dict(cfg)
    pre = list(out.get("pre_install") or [])
    out["pre_install"] = merge_pre_install_debian_packages(pre, list(deb_packages))
    apt = sanitize_apt_package_names(list(out.get("apt-pkgs") or []) + deb_packages)
    out["apt-pkgs"] = apt
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
    install = str(cfg.get("install") or "")
    return "USE_NGTCP2" in install.upper() or "USE_PROXY_HTTP3" in install.upper()


def sanitize_native_integration_apt_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Strip blocklisted apt names and unsafe shell lines from native-integration configs."""
    if not cfg.get("native_integration_build"):
        return cfg
    from .integration_build import (
        filter_native_integration_apt_packages,
        native_integration_eval_commands,
        strip_unsafe_native_shell_lines,
    )

    http3 = _native_integration_http3_install(cfg)
    out = dict(cfg)
    def _sanitize_pkgs(pkgs: list[str]) -> list[str]:
        cleaned = sanitize_apt_package_names(pkgs)
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
    out = ensure_base_build_apt(dict(cfg))
    deb = apt_packages_from_build_log(log)
    out = merge_apt_into_config(out, deb)
    if out.get("native_integration_build"):
        out = sanitize_native_integration_apt_config(out)
    return out
