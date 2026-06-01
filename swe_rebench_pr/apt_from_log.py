"""Shared Debian package inference from Docker / compile logs (all languages)."""

from __future__ import annotations

from typing import Any

from .install_llm import merge_pre_install_debian_packages

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


def merge_apt_into_config(cfg: dict[str, Any], deb_packages: list[str]) -> dict[str, Any]:
    """Merge Debian packages into ``pre_install`` and ``apt-pkgs``."""
    if not deb_packages:
        return cfg
    out = dict(cfg)
    pre = list(out.get("pre_install") or [])
    out["pre_install"] = merge_pre_install_debian_packages(pre, list(deb_packages))
    apt = list(out.get("apt-pkgs") or [])
    seen = set(apt)
    for pkg in deb_packages:
        if pkg not in seen:
            seen.add(pkg)
            apt.append(pkg)
    out["apt-pkgs"] = apt
    return out


def ensure_base_build_apt(cfg: dict[str, Any]) -> dict[str, Any]:
    """Ensure git/build-essential/pkg-config appear in pre_install."""
    return merge_apt_into_config(cfg, list(_BASE_BUILD_APT))


def remediate_apt_install_from_log(cfg: dict[str, Any], log: str) -> dict[str, Any]:
    """Apply log-driven apt package fixes (language-agnostic)."""
    out = ensure_base_build_apt(dict(cfg))
    deb = apt_packages_from_build_log(log)
    return merge_apt_into_config(out, deb)
