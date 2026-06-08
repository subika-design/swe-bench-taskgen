"""Tests for Meson C harness, PHP phpunit discovery, patch-aware Gradle resolution."""

from pathlib import Path

from swe_rebench_pr.c_build import (
    ensure_c_install_config,
    is_meson_repo,
    log_indicates_meson_tool_missing,
    meson_install_config_for_repo,
    remediate_c_install_from_log,
)
from swe_rebench_pr.c_harness_router import HarnessKind, resolve_c_harness_kind
from swe_rebench_pr.docker_entry import _php_body
from swe_rebench_pr.java_build import _gradle_test_tasks, java_install_config_for_repo
from swe_rebench_pr.java_gradle_llm import (
    discover_gradle_projects,
    discover_gradle_projects_from_patches,
    parse_gradle_projects_command_output,
    resolve_gradle_projects_for_test_paths,
)
from swe_rebench_pr.php_build import (
    discover_phpunit_bin_from_makefile,
    discover_phpunit_bin_rel,
    inferred_composer_bin_phpunit_rel,
    log_indicates_php_phpunit_missing,
    php_install_config_for_repo,
    remediate_php_install_from_log,
)


def test_is_meson_repo_detects_native_c_project(tmp_path: Path):
    (tmp_path / "meson.build").write_text("project('mpv', 'c')\n", encoding="utf-8")
    assert is_meson_repo(tmp_path)


def test_is_meson_repo_skips_python_meson_python_backend(tmp_path: Path):
    (tmp_path / "meson.build").write_text("project('pandas', 'c')\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["meson-python"]\nbuild-backend = "mesonpy"\n',
        encoding="utf-8",
    )
    assert not is_meson_repo(tmp_path)


def test_sanitize_does_not_apply_pandas_meson_to_native_c_repo(tmp_path: Path):
    from swe_rebench_pr.install_llm import sanitize_install_config_for_docker

    (tmp_path / "meson.build").write_text("project('mpv', 'c')\n", encoding="utf-8")
    cfg = sanitize_install_config_for_docker(
        {"language": "c", "install": "true", "test_cmd": "meson test -C build"},
        "mpv-player/mpv",
        repo=tmp_path,
    )
    assert "pandas" not in str(cfg.get("install") or "").lower()
    assert "meson setup" in str(cfg.get("install") or "")


def test_meson_install_config_includes_build_tools(tmp_path: Path):
    (tmp_path / "meson.build").write_text("project('app', 'c')\n", encoding="utf-8")
    cfg = meson_install_config_for_repo(tmp_path)
    assert cfg["c_build_system"] == "meson"
    assert "meson setup" in cfg["install"]
    assert "meson test" in cfg["test_cmd"]
    joined = " ".join(cfg.get("pre_install") or []) + " " + " ".join(cfg.get("apt-pkgs") or [])
    assert "meson" in joined
    assert "ninja-build" in joined


def test_remediate_c_install_adds_meson_when_command_missing(tmp_path: Path):
    (tmp_path / "meson.build").write_text("project('app', 'c')\n", encoding="utf-8")
    cfg = {"language": "c", "install": "meson setup build"}
    log = "/w/project_install.sh: line 4: meson: command not found"
    assert log_indicates_meson_tool_missing(log)
    out = remediate_c_install_from_log(cfg, log, repo=tmp_path)
    assert out.get("c_build_system") == "meson"
    joined = " ".join(out.get("pre_install") or []) + " " + " ".join(out.get("apt-pkgs") or [])
    assert "meson" in joined


def test_ensure_c_install_config_meson_repo(tmp_path: Path):
    (tmp_path / "meson.build").write_text("project('app', 'c')\n", encoding="utf-8")
    out = ensure_c_install_config({}, repo=tmp_path)
    assert out.get("c_build_system") == "meson"
    assert "meson compile" in out["install"]


def test_c_harness_router_selects_meson(tmp_path: Path):
    (tmp_path / "meson.build").write_text("project('app', 'c')\n", encoding="utf-8")
    assert resolve_c_harness_kind(tmp_path, "", "") == HarnessKind.MESON


def test_discover_phpunit_bin_composer_bin_layout_vendor_links(tmp_path: Path):
    phpunit = tmp_path / "tools" / "phpunit" / "vendor" / "bin" / "phpunit"
    phpunit.parent.mkdir(parents=True)
    phpunit.write_text("#!/usr/bin/env php\n", encoding="utf-8")
    (tmp_path / "composer.json").write_text(
        '{"require-dev":{"bamarni/composer-bin-plugin":"^1.8"}}',
        encoding="utf-8",
    )
    rel = discover_phpunit_bin_rel(tmp_path)
    assert rel == "tools/phpunit/vendor/bin/phpunit"


def test_inferred_composer_bin_phpunit_bin_links_false(tmp_path: Path):
    (tmp_path / "composer.json").write_text(
        """{
  "require-dev": {"bamarni/composer-bin-plugin": "^1.8"},
  "extra": {"bamarni-bin": {"target-directory": "tools", "bin-links": false}}
}""",
        encoding="utf-8",
    )
    assert inferred_composer_bin_phpunit_rel(tmp_path) == "tools/phpunit/bin/phpunit"
    assert discover_phpunit_bin_rel(tmp_path) == "tools/phpunit/bin/phpunit"


def test_discover_phpunit_from_makefile(tmp_path: Path):
    (tmp_path / "Makefile").write_text(
        "PHPUNIT = ./tools/phpunit/bin/phpunit -c .\n"
        "tests:\n\t$(PHPUNIT)\n",
        encoding="utf-8",
    )
    found = discover_phpunit_bin_from_makefile(tmp_path)
    assert found == ("tools/phpunit/bin/phpunit", "-c .")
    cfg = php_install_config_for_repo(tmp_path)
    assert "tools/phpunit/bin/phpunit -c ." in cfg["test_cmd"]


def test_php_install_config_uses_composer_bin_phpunit(tmp_path: Path):
    phpunit = tmp_path / "tools" / "phpunit" / "bin" / "phpunit"
    phpunit.parent.mkdir(parents=True)
    phpunit.write_text("#!/usr/bin/env php\n", encoding="utf-8")
    (tmp_path / "composer.json").write_text('{"require-dev":{}}', encoding="utf-8")
    cfg = php_install_config_for_repo(tmp_path)
    assert "tools/phpunit/bin/phpunit" in cfg["test_cmd"]


def test_remediate_php_install_fixes_missing_vendor_bin_phpunit(tmp_path: Path):
    (tmp_path / "composer.json").write_text(
        """{
  "require-dev": {"bamarni/composer-bin-plugin": "^1.8"},
  "extra": {"bamarni-bin": {"target-directory": "tools", "bin-links": false}}
}""",
        encoding="utf-8",
    )
    log = "vendor/bin/phpunit: No such file or directory"
    assert log_indicates_php_phpunit_missing(log)
    out = remediate_php_install_from_log({"test_cmd": "vendor/bin/phpunit"}, log, repo=tmp_path)
    assert "tools/phpunit/bin/phpunit" in out["test_cmd"]


def test_php_runtime_deps_restore_checks_composer_bin_paths():
    from swe_rebench_pr.runtime_deps import runtime_deps_restore_shell

    sh = runtime_deps_restore_shell("php", {"install": "composer install"})
    assert "tools/*/bin/phpunit" in sh
    assert "vendor-bin/*/bin/phpunit" in sh


def test_php_pre_install_orders_ext_after_apt(tmp_path: Path):
    from swe_rebench_pr.php_build import ensure_php_pre_install_order

    (tmp_path / "composer.json").write_text(
        '{"require":{"ext-xml":"*","ext-intl":"*"}}', encoding="utf-8"
    )
    cfg = php_install_config_for_repo(tmp_path)
    pre = cfg.get("pre_install") or []
    ext_idx = next(i for i, ln in enumerate(pre) if "docker-php-ext-install" in ln)
    apt_idx = next(i for i, ln in enumerate(pre) if "apt-get install" in ln)
    assert apt_idx < ext_idx
    assert "libxml2-dev" in " ".join(pre)


def test_gradle_llm_accepts_patch_only_module_mapping(tmp_path: Path):
    from swe_rebench_pr.java_gradle_llm import (
        _mapping_matches_test_path,
        discover_gradle_projects_from_patches,
    )

    patch = """diff --git a/settings.gradle b/settings.gradle
--- a/settings.gradle
+++ b/settings.gradle
@@ -1 +1,2 @@
+include 'testcontainers-doris'
+project(':testcontainers-doris').projectDir = file('modules/doris')
"""
    idx = discover_gradle_projects_from_patches(patch)
    tp = "modules/doris/src/test/java/org/testcontainers/doris/DorisTest.java"
    assert _mapping_matches_test_path(tp, ":testcontainers-doris", idx, tmp_path)


def test_gradle_install_skips_check_in_repair():
    from swe_rebench_pr.java_build import repair_gradle_install_config_for_harness

    cfg = repair_gradle_install_config_for_harness(
        {
            "java_build_system": "gradle",
            "install": "./gradlew --no-daemon clean build -x test --continue",
            "test_cmd": "./gradlew :app:test",
        }
    )
    assert "-x check" in cfg["install"]


def test_php_body_includes_find_fallback_for_phpunit():
    body = _php_body({"test_cmd": "vendor/bin/phpunit --log-junit __JUNIT_OUT__"}, False)
    assert "_php_test_cmd_probe_bin" in body
    assert "tools/*/bin/phpunit" in body
    assert "*/vendor/bin/phpunit" in body


def test_gradle_patch_overlay_maps_testcontainers_style_module(tmp_path: Path):
    patch = """diff --git a/settings.gradle b/settings.gradle
index 1111111..2222222 100644
--- a/settings.gradle
+++ b/settings.gradle
@@ -1,3 +1,4 @@
 rootProject.name = 'testcontainers-java'
 include 'testcontainers-doris'
+project(':testcontainers-doris').projectDir = file('modules/doris')
"""
    idx = discover_gradle_projects_from_patches(patch)
    assert ":testcontainers-doris" in idx.projects
    assert ("modules/doris", ":testcontainers-doris") in idx.dir_to_project

    tp = "modules/doris/src/test/java/org/testcontainers/doris/DorisTest.java"
    mapping = resolve_gradle_projects_for_test_paths(
        tmp_path,
        [tp],
        api_key=None,
        patch=patch,
    )
    assert mapping[tp] == ":testcontainers-doris"
    assert ":modules:doris" not in mapping.values()


def test_gradle_rejects_wrapper_colon_path_without_declared_project(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text("rootProject.name = 'app'\n", encoding="utf-8")
    tp = "modules/doris/src/test/java/org/example/FooTest.java"
    mapping = resolve_gradle_projects_for_test_paths(tmp_path, [tp], api_key=None)
    assert tp not in mapping or mapping[tp] != ":modules:doris"


def test_gradle_projects_command_output_merge(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text("rootProject.name = 'app'\n", encoding="utf-8")
    tp = "modules/doris/src/test/java/org/example/FooTest.java"
    patch = """diff --git a/settings.gradle b/settings.gradle
--- a/settings.gradle
+++ b/settings.gradle
@@ -1 +1,2 @@
 rootProject.name = 'app'
+include 'testcontainers-doris'
+project(':testcontainers-doris').projectDir = file('modules/doris')
"""
    projects_log = """
Root project 'app'
+--- Project ':testcontainers-doris'
"""
    mapping = resolve_gradle_projects_for_test_paths(
        tmp_path,
        [tp],
        api_key=None,
        patch=patch,
        gradle_projects_output=projects_log,
    )
    assert mapping[tp] == ":testcontainers-doris"
    parsed = parse_gradle_projects_command_output(projects_log)
    assert ":testcontainers-doris" in parsed.projects


def test_gradle_test_cmd_uses_patch_resolved_module(tmp_path: Path):
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    patch = """diff --git a/settings.gradle b/settings.gradle
--- a/settings.gradle
+++ b/settings.gradle
@@ -1 +1,2 @@
+include 'testcontainers-doris'
+project(':testcontainers-doris').projectDir = file('modules/doris')
"""
    tp = "modules/doris/src/test/java/org/testcontainers/doris/DorisTest.java"
    mapping = resolve_gradle_projects_for_test_paths(
        tmp_path, [tp], api_key=None, patch=patch
    )
    cfg = java_install_config_for_repo(
        tmp_path, test_paths=[tp], gradle_path_by_test_path=mapping
    )
    assert ":testcontainers-doris:test" in cfg["test_cmd"]
    cmd = _gradle_test_tasks([], [tp], gradle_path_by_test_path=mapping, repo=tmp_path)
    assert ":testcontainers-doris:test" in cmd
    assert ":modules:doris" not in cmd


def test_discover_gradle_projects_merges_repo_and_patch(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'app'\ninclude 'core'\n", encoding="utf-8"
    )
    patch = """diff --git a/settings.gradle b/settings.gradle
--- a/settings.gradle
+++ b/settings.gradle
@@ -1,2 +1,3 @@
 include 'core'
+include 'testcontainers-doris'
+project(':testcontainers-doris').projectDir = file('modules/doris')
"""
    idx = discover_gradle_projects(tmp_path, patch)
    assert ":core" in idx.projects
    assert ":testcontainers-doris" in idx.projects
