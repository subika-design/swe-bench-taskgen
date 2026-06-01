"""Tests for Premake install and log parsing."""

from pathlib import Path

from swe_rebench_pr.c_build import (
    is_premake_repo,
    premake_install_config_for_repo,
    premake_is_manifest_lua,
    premake_suite_for_target,
    premake_suite_from_test_path,
    premake_target_runnable_on_base,
    premake_test_cmd_for_targets,
)
from swe_rebench_pr.diff_split import _nodeid_in_test_patch_paths, _path_filter_sets
from swe_rebench_pr.test_log_parsers import parse_googletest_log

_UNICODE_PATCH = """\
diff --git a/tests/base/test_os_unicode.lua b/tests/base/test_os_unicode.lua
new file mode 100644
--- /dev/null
+++ b/tests/base/test_os_unicode.lua
@@ -0,0 +1,5 @@
+if not _UTF8_ENABLED then
+    return
+end
+local suite = test.declare("base_os_unicode")
"""


def test_premake_suite_from_test_path():
    assert premake_suite_from_test_path("tests/base/test_os_unicode.lua") == "base_os_unicode"
    assert premake_suite_from_test_path("tests/base/test_os.lua") == "base_os"
    assert premake_suite_from_test_path("tests/test_lua_unicode.lua") == "lua_unicode"
    assert premake_is_manifest_lua("tests/_tests.lua")
    assert premake_suite_from_test_path("tests/_tests.lua") is None


def test_premake_suite_from_patch():
    suite = premake_suite_for_target(
        "tests/test_lua_unicode.lua",
        test_patch=(
            "diff --git a/tests/test_lua_unicode.lua b/tests/test_lua_unicode.lua\n"
            "+++ b/tests/test_lua_unicode.lua\n"
            "+local suite = test.declare(\"lua_unicode\")\n"
        ),
    )
    assert suite == "lua_unicode"


def test_premake_base_phase_skips_utf8_gated_suites():
    assert not premake_target_runnable_on_base(
        "tests/base/test_os_unicode.lua",
        test_patch=_UNICODE_PATCH,
    )
    base_cmd = premake_test_cmd_for_targets(
        ["tests/base/test_os.lua", "tests/base/test_os_unicode.lua"],
        test_patch=_UNICODE_PATCH,
        base_phase=True,
    )
    assert "--test-only=base_os" in base_cmd
    assert "base_os_unicode" not in base_cmd
    patch_cmd = premake_test_cmd_for_targets(
        ["tests/base/test_os.lua", "tests/base/test_os_unicode.lua"],
        test_patch=_UNICODE_PATCH,
        base_phase=False,
    )
    assert "--test-only=base_os_unicode" in patch_cmd


def test_premake_test_cmd_scopes_to_suites():
    cmd = premake_test_cmd_for_targets(
        ["tests/base/test_os_unicode.lua", "tests/test_lua_unicode.lua"],
        test_patch=(
            "diff --git a/tests/test_lua_unicode.lua b/tests/test_lua_unicode.lua\n"
            "+local suite = test.declare(\"lua_unicode\")\n"
        ),
    )
    assert "premake5 test" in cmd
    assert "--test-only=base_os_unicode" in cmd
    assert "--test-only=lua_unicode" in cmd
    assert "_tests" not in cmd


def test_premake_install_config_for_repo(tmp_path: Path):
    (tmp_path / "premake5.lua").write_text("premake5 = {}\n", encoding="utf-8")
    (tmp_path / "Bootstrap.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    assert is_premake_repo(tmp_path)
    cfg = premake_install_config_for_repo(
        tmp_path,
        test_paths=["tests/base/test_os_unicode.lua"],
        test_patch=(
            "diff --git a/tests/base/test_os_unicode.lua b/tests/base/test_os_unicode.lua\n"
            "+local suite = test.declare(\"base_os_unicode\")\n"
        ),
    )
    assert cfg["c_build_system"] == "premake"
    assert cfg["result_format"] == "googletest_log"
    assert "Bootstrap.sh" in cfg["install"]
    assert "uuid-dev" in (cfg.get("apt-pkgs") or [])
    assert "--test-only=base_os_unicode" in cfg["test_cmd"]


def test_parse_googletest_log_premake_style():
    log = """
[ RUN      ] base_os_unicode.chdir_unicode
[       OK ] base_os_unicode.chdir_unicode (1 ms)
[  FAILED  ] base_os_unicode.bad_path (2 ms)
"""
    out = parse_googletest_log(log)
    assert out["base_os_unicode.chdir_unicode"] == "PASSED"
    assert out["base_os_unicode.bad_path"] == "FAILED"


def test_premake_nodeid_matches_test_patch_paths():
    paths = ["tests/base/test_os_unicode.lua", "tests/test_lua_unicode.lua"]
    _, dotted, _ = _path_filter_sets(paths)
    assert "base_os_unicode" in dotted
    assert "lua_unicode" in dotted
    assert _nodeid_in_test_patch_paths(
        "base_os_unicode.chdir_unicode", frozenset(), dotted, frozenset()
    )
    assert not _nodeid_in_test_patch_paths(
        "base_os.chdir", frozenset(), dotted, frozenset()
    )
