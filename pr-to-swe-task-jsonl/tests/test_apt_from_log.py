"""Tests for shared apt-from-log heuristics."""

from swe_rebench_pr.apt_from_log import apt_packages_from_build_log, remediate_apt_install_from_log


def test_apt_packages_from_rust_openssl_log():
    log = "Could not find openssl via pkg-config"
    pkgs = apt_packages_from_build_log(log)
    assert "libssl-dev" in pkgs
    assert "pkg-config" in pkgs


def test_remediate_adds_pre_install_and_apt_pkgs():
    cfg = remediate_apt_install_from_log({}, "fatal error: uuid/uuid.h: No such file")
    apt = (cfg.get("apt-pkgs") or []) + (cfg.get("apt-pkgs-optional") or [])
    assert "libuuid-dev" in apt
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
    apt = (cfg.get("apt-pkgs") or []) + (cfg.get("apt-pkgs-optional") or [])
    assert "libssl-dev" in apt
    assert "libpsl-dev" in apt


def test_resilient_apt_install_uses_per_package_fallback_for_optional():
    from swe_rebench_pr.apt_from_log import resilient_apt_install_shell_lines

    lines = resilient_apt_install_shell_lines(
        ["git", "build-essential", "libxscrnsaver-dev", "libdisplay-info-dev"]
    )
    joined = "\n".join(lines)
    assert "apt-get update -qq" in joined
    assert "git" in lines[1]
    assert "libxscrnsaver-dev" in joined and "|| true" in joined
    assert "libdisplay-info-dev" in joined and "|| true" in joined


def test_remediate_missing_apt_packages_from_log():
    from swe_rebench_pr.apt_from_log import remediate_missing_apt_packages_from_log

    log = "E: Unable to locate package libxscrnsaver-dev\nE: Unable to locate package libdisplay-info-dev"
    cfg = remediate_missing_apt_packages_from_log(
        {
            "apt-pkgs": ["git", "libxscrnsaver-dev", "libavcodec-dev"],
            "pre_install": [
                "apt-get update -qq && apt-get install -y --no-install-recommends "
                "git libxscrnsaver-dev libdisplay-info-dev libavcodec-dev"
            ],
        },
        log,
    )
    assert "libxscrnsaver-dev" not in (cfg.get("apt-pkgs") or [])
    pre = "\n".join(cfg.get("pre_install") or [])
    assert "libxscrnsaver-dev" not in pre or "|| true" in pre


def test_apt_packages_from_php_libxml_pkg_config_log():
    log = """checking for libxml-2.0 >= 2.9.0... no
Package 'libxml-2.0', required by 'virtual:world', not found
"""
    pkgs = apt_packages_from_build_log(log)
    assert "libxml2-dev" in pkgs


def test_php_ext_build_apt_packages_install_strictly():
    from swe_rebench_pr.apt_from_log import resilient_apt_install_shell_lines, split_apt_packages_core_optional

    core, optional = split_apt_packages_core_optional(["libxml2-dev", "libzip-dev", "libicu-dev"])
    assert "libxml2-dev" in core
    assert "libxml2-dev" not in optional
    lines = resilient_apt_install_shell_lines(["libxml2-dev", "libzip-dev"])
    assert any("libxml2-dev" in ln and "|| true" not in ln for ln in lines)


def test_env_apt_setup_commands_tolerates_missing_optional_packages():
    from swe_rebench_pr.harness.test_spec.utils import env_apt_setup_commands

    cmds = env_apt_setup_commands(
        {
            "apt-pkgs": [
                "git",
                "build-essential",
                "meson",
                "ninja-build",
                "libxscrnsaver-dev",
                "libdisplay-info-dev",
            ]
        }
    )
    joined = "\n".join(cmds)
    assert "libxscrnsaver-dev" in joined and "|| true" in joined
    assert "meson" in joined
    assert joined.index("meson") < joined.index("|| true")
