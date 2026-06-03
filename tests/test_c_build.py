"""Tests for C/CMake install remediation."""

from pathlib import Path

from swe_rebench_pr.c_build import (
    apt_packages_from_c_build_log,
    cmake_apt_packages_for_repo,
    ensure_c_install_config,
    merge_c_apt_into_config,
    merge_c_harness_fields_after_llm,
    remediate_c_install_from_log,
)


def test_apt_packages_from_premake_uuid_log():
    log = "src/host/os_uuid.c:12:10: fatal error: uuid/uuid.h: No such file or directory"
    assert "libuuid-dev" in apt_packages_from_c_build_log(log)


def test_apt_packages_from_chocolate_doom_sdl_log():
    log = """
    Could NOT find SDL2 (missing: SDL2_INCLUDE_DIR SDL2_LIBRARIES)
    Could NOT find SDL2_mixer (missing: SDL2_MIXER_INCLUDE_DIR SDL2_MIXER_LIBRARY)
    Could NOT find SDL2_net (missing: SDL2_NET_INCLUDE_DIR SDL2_NET_LIBRARY)
    """
    pkgs = apt_packages_from_c_build_log(log)
    assert "libsdl2-dev" in pkgs
    assert "libsdl2-mixer-dev" in pkgs
    assert "libsdl2-net-dev" in pkgs


def test_remediate_c_install_from_log_merges_pre_install():
    cfg = {"language": "c", "install": "mkdir -p build && cd build && cmake .. && cmake --build ."}
    log = "fatal error: uuid/uuid.h: No such file or directory"
    out = remediate_c_install_from_log(cfg, log)
    pre = " ".join(out.get("pre_install") or [])
    assert "libuuid-dev" in pre
    assert "libuuid-dev" in (out.get("apt-pkgs") or [])


def test_merge_c_apt_into_config_deduplicates():
    cfg = {"pre_install": ["apt-get update -qq", "apt-get install -y git build-essential"]}
    out = merge_c_apt_into_config(cfg, ["libuuid-dev", "libuuid-dev"])
    assert (out.get("apt-pkgs") or []).count("libuuid-dev") == 1


def test_merge_c_harness_fields_after_llm_keeps_c_test_cmd():
    before = {
        "language": "c",
        "test_cmd": "cd build && ctest --output-on-failure",
        "install": "mkdir -p build && cd build && cmake .. && cmake --build .",
    }
    after = {"test_cmd": "pytest -rA", "install": "pip install -e ."}
    out = merge_c_harness_fields_after_llm(before, after)
    assert "ctest" in out["test_cmd"]
    assert "cmake" in out["install"]


def test_ensure_c_install_config_from_repo(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n", encoding="utf-8")
    out = ensure_c_install_config({}, repo=tmp_path)
    pre = " ".join(out.get("pre_install") or [])
    assert "cmake" in pre
    assert "build-essential" in pre


def test_cmake_apt_packages_for_repo_find_package_scan(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text(
        "\n".join(
            [
                "cmake_minimum_required(VERSION 3.18)",
                "find_package(OpenSSL REQUIRED)",
                "find_package(Libpsl REQUIRED)",
                "find_package(Zstd REQUIRED)",
            ]
        ),
        encoding="utf-8",
    )
    pkgs = cmake_apt_packages_for_repo(tmp_path)
    assert "libssl-dev" in pkgs
    assert "libpsl-dev" in pkgs
    assert "libzstd-dev" in pkgs


def test_ensure_c_install_config_premake_repo(tmp_path: Path):
    (tmp_path / "premake5.lua").write_text("premake5 = {}\n", encoding="utf-8")
    (tmp_path / "Bootstrap.mak").write_text("all:\n", encoding="utf-8")
    out = ensure_c_install_config({}, repo=tmp_path, test_paths=["tests/base/test_os.lua"])
    assert out.get("c_build_system") == "premake"
    assert "Bootstrap.sh" in out["install"]
    assert "uuid-dev" in (out.get("apt-pkgs") or [])
    assert "cmake" not in " ".join(out.get("pre_install") or [])
