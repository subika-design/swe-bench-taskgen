"""Gradle project resolution from settings + fallback (LLM mocked)."""

from pathlib import Path
from unittest.mock import patch

from swe_rebench_pr.java_build import _gradle_test_tasks, java_install_config_for_repo
from swe_rebench_pr.java_gradle_llm import (
    coerce_gradle_project,
    discover_gradle_projects_from_settings,
    resolve_gradle_projects_for_test_paths,
)


def test_discover_gradle_projects_module_layout(tmp_path: Path):
    (tmp_path / "settings.gradle.kts").write_text(
        """
        include("spring-boot-jms")
        include("spring-boot-web-server")
        project(":spring-boot-jms").projectDir = file("module/spring-boot-jms")
        project(":spring-boot-web-server").projectDir = file("module/spring-boot-web-server")
        """.strip(),
        encoding="utf-8",
    )
    idx = discover_gradle_projects_from_settings(tmp_path)
    assert ":spring-boot-jms" in idx.projects
    assert ("module/spring-boot-jms", ":spring-boot-jms") in idx.dir_to_project


def test_coerce_strips_module_prefix_when_real_project_exists(tmp_path: Path):
    idx = discover_gradle_projects_from_settings(tmp_path)
    (tmp_path / "settings.gradle").write_text(
        'include("spring-boot-web-server")\n'
        'project(":spring-boot-web-server").projectDir = file("module/spring-boot-web-server")\n',
        encoding="utf-8",
    )
    idx = discover_gradle_projects_from_settings(tmp_path)
    assert coerce_gradle_project(":module:spring-boot-web-server", idx) == ":spring-boot-web-server"


def test_fallback_maps_module_include_colon_project(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text(
        'include "module:spring-boot-cache"\n',
        encoding="utf-8",
    )
    tp = (
        "module/spring-boot-cache/src/test/java/"
        "org/springframework/boot/cache/metrics/FooTests.java"
    )
    mapping = resolve_gradle_projects_for_test_paths(tmp_path, [tp], api_key=None)
    assert mapping[tp] == ":module:spring-boot-cache"


def test_fallback_maps_module_dir_to_colon_project(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text(
        'include("spring-boot-jms")\n'
        'project(":spring-boot-jms").projectDir = file("module/spring-boot-jms")\n',
        encoding="utf-8",
    )
    tp = (
        "module/spring-boot-jms/src/test/java/"
        "org/springframework/boot/jms/autoconfigure/JmsPoolConnectionFactoryFactoryTests.java"
    )
    mapping = resolve_gradle_projects_for_test_paths(tmp_path, [tp], api_key=None)
    assert mapping[tp] == ":spring-boot-jms"
    assert ":module:spring-boot-jms" not in mapping.values()


def test_gradle_test_cmd_uses_llm_mapping(tmp_path: Path):
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    tp = (
        "module/spring-boot-jms/src/test/java/"
        "org/springframework/boot/jms/FooTests.java"
    )
    mapping = {":spring-boot-jms": ":spring-boot-jms"}
    # mapping keys are test paths
    path_map = {tp: ":spring-boot-jms"}
    cfg = java_install_config_for_repo(
        tmp_path, test_paths=[tp], gradle_path_by_test_path=path_map
    )
    assert ":spring-boot-jms:test" in cfg["test_cmd"]
    assert "--tests 'org.springframework.boot.jms.FooTests'" in cfg["test_cmd"]
    assert ":module:spring-boot-jms" not in cfg["test_cmd"]


def test_llm_mapping_used_when_mocked(tmp_path: Path):
    (tmp_path / "settings.gradle").write_text('include("spring-boot-jms")\n', encoding="utf-8")
    tp = "module/spring-boot-jms/src/test/java/org/example/FooTests.java"
    with patch(
        "swe_rebench_pr.java_gradle_llm.llm_resolve_gradle_projects_for_test_paths",
        return_value={tp: ":spring-boot-jms"},
    ):
        mapping = resolve_gradle_projects_for_test_paths(
            tmp_path,
            [tp],
            api_key="k",
            base_url="http://localhost",
            model="m",
            timeout_s=1,
        )
    assert mapping[tp] == ":spring-boot-jms"
    cmd = _gradle_test_tasks([], [tp], gradle_path_by_test_path=mapping)
    assert ":spring-boot-jms:test" in cmd
    assert "--tests" in cmd
