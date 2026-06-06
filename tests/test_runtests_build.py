"""Tests for CMake runtests.pl harness routing."""

from pathlib import Path

from swe_rebench_pr.integration_build import patch_diff_touches_libtest, repo_needs_cmake_src_tool_symlinks
from swe_rebench_pr.runtests_build import (
    apply_runtests_build_if_libtest,
    cmake_runtests_discover_active,
    collect_runtests_numbers,
    detect_runtests_harness,
    runtests_cmake_invoke_block,
    runtests_cmake_runtime_env_lines,
    runtests_eval_command_lines,
    runtests_install_config,
    runtests_log_key_in_test_patch_paths,
    runtests_prepare_lines,
    runtests_test_cmd_for_numbers,
)
from swe_rebench_pr.test_log_parsers import parse_ctest_log, parse_runtests_log


def _runtests_repo(tmp_path: Path) -> Path:
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "runtests.pl").write_text("#!/usr/bin/env perl\n", encoding="utf-8")
    (tests / "CMakeLists.txt").write_text(
        "add_custom_target(testdeps)\nadd_custom_target(libtests)\nadd_custom_target(tt)\n",
        encoding="utf-8",
    )
    for sub in ("server", "libtest"):
        d = tests / sub
        d.mkdir()
        (d / "CMakeLists.txt").write_text(f"add_custom_target({sub})\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "CMakeLists.txt").write_text(
        "add_executable(curl curl.c)\nadd_executable(curlinfo curlinfo.c)\n",
        encoding="utf-8",
    )
    return tmp_path


def test_collect_runtests_numbers():
    tp = (
        "diff --git a/tests/libtest/lib1677.c b/tests/libtest/lib1677.c\n"
        "diff --git a/tests/data/test1677 b/tests/data/test1677\n"
    )
    assert collect_runtests_numbers(tp) == ["1677"]


def test_detect_runtests_harness(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    tp = "diff --git a/tests/data/test42 b/tests/data/test42\n"
    assert detect_runtests_harness(tp, repo)
    assert not detect_runtests_harness("", repo)
    assert patch_diff_touches_libtest(tp)


def test_apply_runtests_build_if_libtest(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    tp = "diff --git a/tests/data/test1677 b/tests/data/test1677\n"
    cfg = apply_runtests_build_if_libtest(
        {"language": "c", "install": "cmake .. -DUSE_NGTCP2=ON"},
        repo,
        test_patch=tp,
    )
    assert cmake_runtests_discover_active(cfg)
    assert cfg.get("result_format") == "runtests_log"
    assert "USE_NGTCP2" not in str(cfg.get("install") or "").upper()
    assert "1677" in str(cfg.get("test_cmd") or "")
    assert "runtests.pl" in str(cfg.get("test_cmd") or "")


def test_runtests_test_cmd_layout_adapter():
    cmd = runtests_test_cmd_for_numbers(
        ["1677"], repo_dir="/testbed", curl_tool_symlinks=True, layout_adapter=True
    )
    assert cmd == "cd tests && ./runtests.pl -a -am -c ../build/src/curl -p 1677"
    legacy = runtests_test_cmd_for_numbers(
        ["1677"], repo_dir="/testbed", curl_tool_symlinks=True, layout_adapter=False
    )
    assert legacy == "./tests/runtests.pl -a -am -c /testbed/build/src/curl -p 1677"
    generic = runtests_test_cmd_for_numbers(["42"], curl_tool_symlinks=False, layout_adapter=True)
    assert "-c " not in generic
    assert generic == "cd tests && ./runtests.pl -a -am -p 42"


def test_runtests_install_config_minimal(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    cfg = runtests_install_config({}, repo, numbers=["99"])
    assert cfg["cmake_runtests_numbers"] == ["99"]
    assert "cd tests && ./runtests.pl" in cfg["test_cmd"]
    assert "-c ../build/src/curl" in cfg["test_cmd"]
    assert cfg.get("runtests_setup_base")
    assert cfg.get("runtests_setup_patch")
    assert any("testdeps" in ln for ln in cfg["runtests_setup_base"])
    assert any("src/curl" in ln for ln in cfg["runtests_setup_base"])
    assert cfg.get("runtests_cmake_tool_symlinks")
    assert cfg.get("runtests_cmake_layout_adapter")
    assert any("LD_LIBRARY_PATH" in ln for ln in cfg.get("eval_commands") or [])
    assert any("CURL=" in ln for ln in cfg.get("eval_commands") or [])


def test_runtests_prepare_layout_symlinks(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    lines = runtests_prepare_lines(repo, include_testdeps=True)
    assert any("tests/server" in ln for ln in lines)
    assert any("tests/libtest" in ln for ln in lines)


def test_runtests_prepare_includes_servers(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    server = tmp_path / "tests" / "server"
    (server / "CMakeLists.txt").write_text("add_executable(servers EXCLUDE_FROM_ALL servers.c)\n", encoding="utf-8")
    lines = runtests_prepare_lines(repo, include_testdeps=True)
    assert any("servers" in ln for ln in lines)
    assert any("tests/server" in ln for ln in lines)


def test_runtests_prepare_libtest_from_makefile_inc(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    libtest = tmp_path / "tests" / "libtest"
    (libtest / "CMakeLists.txt").write_text(
        "add_executable(${BUNDLE} EXCLUDE_FROM_ALL bundle.c)\n", encoding="utf-8"
    )
    (libtest / "Makefile.inc").write_text("BUNDLE = libtests\n", encoding="utf-8")
    lines = runtests_prepare_lines(repo, include_testdeps=True)
    assert any("--target libtests" in ln for ln in lines)


def test_runtests_prepare_servers_from_makefile_inc(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    server = tmp_path / "tests" / "server"
    (server / "CMakeLists.txt").write_text("add_executable(${BUNDLE} EXCLUDE_FROM_ALL bundle.c)\n", encoding="utf-8")
    (server / "Makefile.inc").write_text("BUNDLE = servers\n", encoding="utf-8")
    lines = runtests_prepare_lines(repo, include_testdeps=True)
    assert any("--target servers" in ln for ln in lines)


def test_runtests_install_config_harness_subdirs(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    cfg = runtests_install_config({}, repo, numbers=["99"])
    assert "server" in (cfg.get("runtests_cmake_harness_subdirs") or [])
    assert "libtest" in (cfg.get("runtests_cmake_harness_subdirs") or [])


def test_runtests_cmake_runtime_env(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    env = runtests_cmake_runtime_env_lines(repo, repo_dir="/testbed")
    assert any("LD_LIBRARY_PATH" in ln and "/testbed/build/lib" in ln for ln in env)
    assert any("/testbed/build/src/curl" in ln for ln in env)
    generic = runtests_cmake_runtime_env_lines(None, repo_dir="/w/repo", curl_tool_symlinks=False)
    assert any("LD_LIBRARY_PATH" in ln for ln in generic)
    assert not any("CURL=" in ln for ln in generic)


def test_runtests_prepare_and_eval(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    lines = runtests_prepare_lines(repo, include_testdeps=True)
    assert any("testdeps" in ln for ln in lines)
    assert any("ln -sfn" in ln for ln in lines)
    env = runtests_eval_command_lines(repo, repo_dir="/testbed")
    assert any("LD_LIBRARY_PATH" in ln for ln in env)
    assert any('/testbed/build/src/curl' in ln for ln in env)
    assert repo_needs_cmake_src_tool_symlinks(repo)


def test_runtests_cmake_invoke_block():
    block = runtests_cmake_invoke_block(repo_dir="/testbed", curl_tool_symlinks=True)
    assert "_runtests_invoke()" in block
    assert "LD_LIBRARY_PATH=" in block
    assert "/testbed/build/src/curl" in block
    assert "export LD_LIBRARY_PATH=" not in block


def test_runtests_setup_echo_quotes_dollar_vars():
    from swe_rebench_pr.docker_entry import _cmake_runtests_setup_shell

    cmd = (
        "mkdir -p tests/libtest && "
        'for f in build/tests/libtest/*; do '
        '[[ -e "$f" ]] || continue; '
        'bn=$(basename "$f"); '
        'ln -sfn "../../build/tests/libtest/$bn" "tests/libtest/$bn"; '
        "done"
    )
    sh = _cmake_runtests_setup_shell([cmd], repo_dir="/testbed", log_tag="base")
    assert 'echo "[docker] runtests setup (base): "' in sh
    assert "'mkdir -p tests/libtest && for f in build/tests/libtest/*" in sh
    assert f'echo "[docker] runtests setup (base): {cmd}"' not in sh


def test_cmake_runtests_docker_entry_reinstall(tmp_path: Path):
    from swe_rebench_pr.docker_entry import write_entry_script

    repo = _runtests_repo(tmp_path)
    cfg = runtests_install_config({}, repo, numbers=["1677"])
    work = tmp_path / "work"
    work.mkdir()
    write_entry_script(
        work,
        "c",
        ["tests/data/test1677"],
        cfg,
        harness_image=True,
        tests_only=True,
    )
    script = (work / "docker_entry.sh").read_text(encoding="utf-8")
    assert "_runtests_cmake_reinstall" in script
    assert "_runtests_verify_tools" in script
    assert "_runtests_invoke()" in script
    assert "export LD_LIBRARY_PATH=" not in script
    assert "-c ../build/src/curl" in script
    assert "cd tests && ./runtests.pl" in script
    assert "./libtest/libtests" in script
    assert '_runtests_invoke "$RUNTESTS_CMD"' in script
    assert 'bash -c "$RUNTESTS_CMD"' not in script
    assert 'bash -lc "$RUNTESTS_CMD"' not in script
    assert "runtests setup (base)" in script
    assert "re-running cmake install for runtests" in script
    assert "cmake build missing after reset" in script


def test_parse_runtests_log_ignored():
    log = "1677: IGNORED: The tool set in the test case for this: 'libtests' does not exist\n"
    m = parse_runtests_log(log)
    assert m["test1677"] == "FAILED"


def test_parse_runtests_log_missing_test():
    log = "No such test: 1677 in the test suite\n"
    m = parse_runtests_log(log)
    assert m["test1677"] == "FAILED"


def test_parse_runtests_log_automake():
    log = "PASS: 1677 - test1677 - checks something\nFAIL: 42 - test42 - broken\n"
    m = parse_runtests_log(log)
    assert m["test1677"] == "PASSED"
    assert m["test42"] == "FAILED"


def test_parse_runtests_log_verbose():
    log = (
        "Test 1677...[libtest check]\n"
        "--pd---e-v- OK (1677  out of 1, remaining: 00:00, took 0.1s, duration: 00:00)\n"
    )
    m = parse_runtests_log(log)
    assert m["test1677"] == "PASSED"


def test_parse_ctest_log():
    log = (
        "1/2 Test #1: foo ........................   Passed    0.01 sec\n"
        "2/2 Test #2: bar ........................***Failed    0.02 sec\n"
    )
    m = parse_ctest_log(log)
    assert m["foo"] == "PASSED"
    assert m["bar"] == "FAILED"


def test_runtests_log_key_matches_paths():
    paths = ["tests/data/test1677", "tests/libtest/lib1677.c"]
    assert runtests_log_key_in_test_patch_paths("test1677", paths)
    assert not runtests_log_key_in_test_patch_paths("test999", paths)
