"""Tests for shared apt-from-log heuristics."""

from swe_rebench_pr.apt_from_log import apt_packages_from_build_log, remediate_apt_install_from_log


def test_apt_packages_from_rust_openssl_log():
    log = "Could not find openssl via pkg-config"
    pkgs = apt_packages_from_build_log(log)
    assert "libssl-dev" in pkgs
    assert "pkg-config" in pkgs


def test_remediate_adds_pre_install_and_apt_pkgs():
    cfg = remediate_apt_install_from_log({}, "fatal error: uuid/uuid.h: No such file")
    assert "libuuid-dev" in (cfg.get("apt-pkgs") or [])
    pre = " ".join(cfg.get("pre_install") or [])
    assert "libuuid-dev" in pre


def test_apt_packages_from_cmake_libpsl_log():
    log = """CMake Error: Could NOT find Libpsl (missing: LIBPSL_INCLUDE_DIR LIBPSL_LIBRARY)
-- Checking for module 'libpsl'
--   No package 'libpsl' found
"""
    pkgs = apt_packages_from_build_log(log)
    assert "libpsl-dev" in pkgs


def test_apt_packages_from_cmake_http_stack_log():
    log = """Could NOT find NGHTTP2 (missing: NGHTTP2_INCLUDE_DIR)
Could NOT find Libidn2 (missing: LIBIDN2_INCLUDE_DIR)
Could NOT find Brotli (missing: BROTLI_INCLUDE_DIR)
Could NOT find Zstd (missing: ZSTD_INCLUDE_DIR)
--   No package 'libnghttp2' found
--   No package 'libidn2' found
--   No package 'libbrotlidec' found
--   No package 'libzstd' found
"""
    pkgs = apt_packages_from_build_log(log)
    assert "libnghttp2-dev" in pkgs
    assert "libidn2-dev" in pkgs
    assert "libbrotli-dev" in pkgs
    assert "libzstd-dev" in pkgs


def test_remediate_curl_style_configure_log():
    log = """Could NOT find OpenSSL
Could NOT find Libpsl
--   No package 'libpsl' found
"""
    cfg = remediate_apt_install_from_log({"language": "c"}, log)
    apt = cfg.get("apt-pkgs") or []
    assert "libssl-dev" in apt
    assert "libpsl-dev" in apt
