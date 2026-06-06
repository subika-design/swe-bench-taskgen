"""Tests for Rust Cargo.toml version, PHP simple-phpunit, Gradle root-project mapping."""

from pathlib import Path
from unittest.mock import patch

from swe_rebench_pr.docker_entry import _php_body
from swe_rebench_pr.java_build import (
    _gradle_compile_tasks,
    _gradle_test_tasks,
    gradle_junit_report_roots,
    java_install_config_for_repo,
    log_indicates_gradle_module_slice_mismatch,
)
from swe_rebench_pr.java_gradle_llm import (
    _mapping_matches_test_path,
    coerce_gradle_project,
    discover_gradle_projects_from_settings,
    gradle_task_for_project,
    resolve_gradle_projects_for_test_paths,
)
from swe_rebench_pr.php_build import (
    php_docker_ext_install_cmd,
    php_install_config_for_repo,
    repo_uses_symfony_phpunit_bridge,
    remediate_php_install_from_log,
)
from swe_rebench_pr.rust_build import (
    EDITION_2024_MIN_RUST_VERSION,
    remediate_rust_version_from_log,
    resolve_rust_version_from_cargo_toml,
    resolve_rust_version_for_repo,
)


def test_resolve_rust_version_from_cargo_toml_rust_version(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "rg"\nedition = "2024"\nrust-version = "1.85"\n',
        encoding="utf-8",
    )
    assert resolve_rust_version_from_cargo_toml(tmp_path) == "1.85-bookworm"
    assert resolve_rust_version_for_repo(tmp_path) == "1.85-bookworm"


def test_resolve_rust_version_edition_2024_without_rust_version(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "rg"\nedition = "2024"\n',
        encoding="utf-8",
    )
    assert resolve_rust_version_from_cargo_toml(tmp_path) == EDITION_2024_MIN_RUST_VERSION


def test_remediate_rust_version_from_manifest_parse_log(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nedition = "2024"\nrust-version = "1.85"\n',
        encoding="utf-8",
    )
    cfg = {"docker_specs": {"rust_version": "1.81-bookworm"}}
    log = "error: failed to parse manifest at `/testbed/Cargo.toml`"
    out = remediate_rust_version_from_log(cfg, log, repo=tmp_path)
    assert out["docker_specs"]["rust_version"] == "1.85-bookworm"


def test_php_symfony_bridge_and_simple_phpunit(tmp_path: Path):
    (tmp_path / "composer.json").write_text(
        '{"require":{"php":"^8.2","ext-intl":"*","ext-zip":"*"},'
        '"require-dev":{"symfony/phpunit-bridge":"^6.0"}}',
        encoding="utf-8",
    )
    assert repo_uses_symfony_phpunit_bridge(tmp_path)
    cfg = php_install_config_for_repo(tmp_path)
    assert cfg["php_test_runner"] == "simple-phpunit"
    assert "simple-phpunit" in cfg["test_cmd"]
    assert "|| true" not in cfg["install"]
    assert "docker-php-ext-install" in " ".join(cfg.get("pre_install") or [])
    assert "intl" in php_docker_ext_install_cmd(tmp_path)
    # zip is already in the PHP base image; only missing extensions are installed
    assert "zip" not in php_docker_ext_install_cmd(tmp_path)


def test_php_body_uses_test_cmd_from_install_config():
    body = _php_body(
        {
            "php_test_runner": "simple-phpunit",
            "test_cmd": "vendor/bin/simple-phpunit --log-junit __JUNIT_OUT__",
        },
        False,
        repo_dir="/testbed",
    )
    assert "PHP_TEST_CMD=" in body
    assert "simple-phpunit" in body
    assert "/w/test-base.log" in body


def test_remediate_php_install_sets_runner(tmp_path: Path):
    (tmp_path / "composer.json").write_text(
        '{"require-dev":{"symfony/phpunit-bridge":"^6.0"}}',
        encoding="utf-8",
    )
    out = remediate_php_install_from_log({"install": "composer install || true"}, "", repo=tmp_path)
    assert out["php_test_runner"] == "simple-phpunit"
    assert "|| true" not in out["install"]


def test_discover_gradle_parses_multiple_includes_on_one_line(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\n"
        "include 'picocli-codegen', 'picocli-tests-java8', 'picocli-tests-java9plus'\n",
        encoding="utf-8",
    )
    index = discover_gradle_projects_from_settings(tmp_path)
    assert ":picocli-codegen" in index.projects
    assert ":picocli-tests-java8" in index.projects
    assert ":picocli-tests-java9plus" in index.projects


def test_gradle_root_project_test_path_fallback(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\ninclude 'picocli-tests-java8'\n",
        encoding="utf-8",
    )
    root_test = tmp_path / "src" / "test" / "java" / "picocli"
    root_test.mkdir(parents=True)
    (root_test / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    tp = "src/test/java/picocli/AutoCompleteTest.java"
    mapping = resolve_gradle_projects_for_test_paths(tmp_path, [tp], api_key=None)
    assert mapping[tp] == ":picocli"


def test_gradle_rejects_llm_mapping_on_path_mismatch(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\ninclude 'picocli-tests-java8'\n",
        encoding="utf-8",
    )
    root_test = tmp_path / "src" / "test" / "java" / "picocli"
    root_test.mkdir(parents=True)
    (root_test / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    tp = "src/test/java/picocli/AutoCompleteTest.java"
    with patch(
        "swe_rebench_pr.java_gradle_llm.llm_resolve_gradle_projects_for_test_paths",
        return_value={tp: ":picocli-tests-java8"},
    ):
        mapping = resolve_gradle_projects_for_test_paths(
            tmp_path,
            [tp],
            api_key="k",
            base_url="http://localhost",
            model="m",
            timeout_s=1,
        )
    assert mapping[tp] == ":picocli"


def test_gradle_root_test_cmd_uses_colon_test_not_ambiguous_colon_picocli(tmp_path: Path):
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\ninclude 'picocli-tests-java8'\n",
        encoding="utf-8",
    )
    tp = "src/test/java/picocli/AutoCompleteTest.java"
    mapping = {tp: ":picocli"}
    cmd = _gradle_test_tasks([], [tp], gradle_path_by_test_path=mapping, repo=tmp_path)
    assert "./gradlew" in cmd and " :test " in f" {cmd} "
    assert ":picocli:test" not in cmd
    assert "--tests 'picocli.AutoCompleteTest'" in cmd
    assert ":picocli-tests-java8" not in cmd


def test_picocli_ambiguous_prefix_resolves_to_subproject(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\n"
        "include 'picocli-codegen', 'picocli-tests-java8', 'picocli-tests-java9plus'\n",
        encoding="utf-8",
    )
    test_dir = tmp_path / "picocli-tests-java8" / "src" / "test" / "java" / "picocli"
    test_dir.mkdir(parents=True)
    (test_dir / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    tp_wrong = "picocli/src/test/java/picocli/AutoCompleteTest.java"
    tp_right = "picocli-tests-java8/src/test/java/picocli/AutoCompleteTest.java"
    m_wrong = resolve_gradle_projects_for_test_paths(tmp_path, [tp_wrong], api_key=None)
    m_right = resolve_gradle_projects_for_test_paths(tmp_path, [tp_right], api_key=None)
    assert m_wrong[tp_wrong] == ":picocli-tests-java8"
    assert m_right[tp_right] == ":picocli-tests-java8"
    cmd = _gradle_test_tasks([], [tp_right], gradle_path_by_test_path=m_right, repo=tmp_path)
    assert ":picocli-tests-java8:test" in cmd
    assert ":picocli:test" not in cmd
    roots = gradle_junit_report_roots(
        [tp_right], gradle_path_by_test_path=m_right, repo=tmp_path
    )
    assert roots == ["picocli-tests-java8/build/test-results"]


def test_gradle_junit_roots_for_root_tests():
    tp = "src/test/java/picocli/AutoCompleteTest.java"
    assert gradle_junit_report_roots([tp]) == ["build/test-results"]


def test_picocli_prefers_main_tests_module_over_codegen_duplicate(tmp_path: Path):
    """When basename exists in both ``*-tests-java*`` and ``*-codegen-tests-*``, pick main tests."""
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\n"
        "include 'picocli-codegen', 'picocli-codegen-tests-java9plus', "
        "'picocli-tests-java8', 'picocli-tests-java9plus'\n",
        encoding="utf-8",
    )
    main = tmp_path / "picocli-tests-java9plus" / "src" / "test" / "java" / "picocli"
    codegen = (
        tmp_path / "picocli-codegen-tests-java9plus" / "src" / "test" / "java" / "picocli" / "codegen"
    )
    main.mkdir(parents=True)
    codegen.mkdir(parents=True)
    (main / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    (codegen / "AutoCompleteTest.java").write_text(
        "package picocli.codegen;\n", encoding="utf-8"
    )
    tp = "picocli/src/test/java/picocli/AutoCompleteTest.java"
    mapping = resolve_gradle_projects_for_test_paths(tmp_path, [tp], api_key=None)
    assert mapping[tp] == ":picocli-tests-java9plus"
    cmd = _gradle_test_tasks([], [tp], gradle_path_by_test_path=mapping, repo=tmp_path)
    assert ":picocli-tests-java9plus:test" in cmd
    assert ":picocli-codegen-tests-java9plus" not in cmd
    roots = gradle_junit_report_roots([tp], gradle_path_by_test_path=mapping, repo=tmp_path)
    assert roots == ["picocli-tests-java9plus/build/test-results"]


def test_picocli_prefers_java8_when_class_only_in_java8_module(tmp_path: Path):
    """Main suite lives in ``*-tests-java8``; java9plus is for JDK9+ API tests only."""
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\n"
        "include 'picocli-codegen', 'picocli-tests-java8', 'picocli-tests-java9plus'\n",
        encoding="utf-8",
    )
    java8 = tmp_path / "picocli-tests-java8" / "src" / "test" / "java" / "picocli"
    java8.mkdir(parents=True)
    (java8 / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    tp = "picocli/src/test/java/picocli/AutoCompleteTest.java"
    mapping = resolve_gradle_projects_for_test_paths(tmp_path, [tp], api_key=None)
    assert mapping[tp] == ":picocli-tests-java8"
    cmd = _gradle_test_tasks([], [tp], gradle_path_by_test_path=mapping, repo=tmp_path)
    assert ":picocli-tests-java8:test" in cmd
    assert ":picocli-tests-java9plus" not in cmd


def test_picocli_prefers_java8_over_java9plus_when_both_have_class(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\n"
        "include 'picocli-tests-java8', 'picocli-tests-java9plus'\n",
        encoding="utf-8",
    )
    for mod in ("picocli-tests-java8", "picocli-tests-java9plus"):
        d = tmp_path / mod / "src" / "test" / "java" / "picocli"
        d.mkdir(parents=True)
        (d / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    tp = "picocli/src/test/java/picocli/AutoCompleteTest.java"
    mapping = resolve_gradle_projects_for_test_paths(tmp_path, [tp], api_key=None)
    assert mapping[tp] == ":picocli-tests-java8"


def test_gradle_module_slice_mismatch_detector():
    assert log_indicates_gradle_module_slice_mismatch(n_base=21, n_patch=0, tp_tot=0)
    assert not log_indicates_gradle_module_slice_mismatch(n_base=0, n_patch=0, tp_tot=0)


def test_gradle_root_path_wins_when_duplicate_in_java8_module(tmp_path: Path):
    """PR path at repo root must map to root project even if java8 also has the class."""
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\n"
        "include 'picocli-tests-java8', 'picocli-tests-java9plus'\n",
        encoding="utf-8",
    )
    root_test = tmp_path / "src" / "test" / "java" / "picocli"
    root_test.mkdir(parents=True)
    (root_test / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    java8 = tmp_path / "picocli-tests-java8" / "src" / "test" / "java" / "picocli"
    java8.mkdir(parents=True)
    (java8 / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    tp = "src/test/java/picocli/AutoCompleteTest.java"
    mapping = resolve_gradle_projects_for_test_paths(tmp_path, [tp], api_key=None)
    assert mapping[tp] == ":picocli"
    cmd = _gradle_test_tasks([], [tp], gradle_path_by_test_path=mapping, repo=tmp_path)
    assert "./gradlew" in cmd and " :test " in f" {cmd} "
    assert ":picocli-tests-java8" not in cmd
    roots = gradle_junit_report_roots([tp], gradle_path_by_test_path=mapping, repo=tmp_path)
    assert roots == ["build/test-results"]


def test_coerce_gradle_root_project_not_replaced_by_java8_child(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\ninclude 'picocli-tests-java8'\n",
        encoding="utf-8",
    )
    idx = discover_gradle_projects_from_settings(tmp_path)
    assert coerce_gradle_project(":picocli", idx, tmp_path) == ":picocli"


def test_mapping_matches_root_vs_subproject(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\ninclude 'picocli-tests-java8'\n",
        encoding="utf-8",
    )
    root_test = tmp_path / "src" / "test" / "java" / "picocli"
    root_test.mkdir(parents=True)
    (root_test / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    index = discover_gradle_projects_from_settings(tmp_path)
    tp = "src/test/java/picocli/AutoCompleteTest.java"
    assert _mapping_matches_test_path(tp, ":picocli", index, tmp_path)
    assert not _mapping_matches_test_path(tp, ":picocli-tests-java8", index, tmp_path)


def test_gradle_root_post_install_uses_colon_compile_test_java(tmp_path: Path):
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\ninclude 'picocli-tests-java8'\n",
        encoding="utf-8",
    )
    tp = "src/test/java/picocli/AutoCompleteTest.java"
    mapping = {tp: ":picocli"}
    post = _gradle_compile_tasks(["picocli"], repo=tmp_path)
    assert ":compileTestJava" in post
    assert ":picocli:compileTestJava" not in post
    cfg = java_install_config_for_repo(
        tmp_path, test_paths=[tp], gradle_path_by_test_path=mapping
    )
    assert cfg["post_install"]
    assert ":compileTestJava" in cfg["post_install"][0]
    assert ":picocli:compileTestJava" not in cfg["post_install"][0]


def test_gradle_acme_root_vs_child_task_spelling(tmp_path: Path):
    """Generic monorepo: root name matches child prefix — use :task not :name:task."""
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'acme'\ninclude 'acme-lib'\n",
        encoding="utf-8",
    )
    idx = discover_gradle_projects_from_settings(tmp_path)
    assert gradle_task_for_project(":acme", "test", idx, tmp_path) == ":test"
    assert (
        gradle_task_for_project(":acme", "compileTestJava", idx, tmp_path)
        == ":compileTestJava"
    )
    assert gradle_task_for_project(":acme-lib", "test", idx, tmp_path) == ":acme-lib:test"
