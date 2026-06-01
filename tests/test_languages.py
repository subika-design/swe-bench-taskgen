from pathlib import Path

from swe_rebench_pr.builder import resolve_task_language
from swe_rebench_pr.languages import (
    SUPPORTED_LANGUAGES,
    collect_test_targets,
    detect_language_from_changed_paths,
    detect_language_from_paths,
    detect_language_from_repo_build_markers,
    get_language_spec,
    is_test_path,
    normalize_language,
)
from swe_rebench_pr.test_log_parsers import parse_gotest_log


def test_supported_languages():
    assert "python" in SUPPORTED_LANGUAGES
    assert len(SUPPORTED_LANGUAGES) == 8


def test_collect_go_test_paths():
    patch = "diff --git a/pkg/foo/bar_test.go b/pkg/foo/bar_test.go\n"
    targets = collect_test_targets("go", patch, "")
    assert targets == ["pkg/foo/bar_test.go"]


def test_collect_python_test_paths():
    patch = "diff --git a/tests/test_api.py b/tests/test_api.py\n"
    targets = collect_test_targets("python", patch, "")
    assert targets == ["tests/test_api.py"]


def test_is_test_path_javascript___tests__at_repo_root():
    spec = get_language_spec("javascript")
    assert is_test_path("__tests__/test-status.js", spec)
    assert is_test_path("__tests__/test-statusMatrix-in-submodule.js", spec)
    assert not is_test_path("src/api/status.js", spec)


def test_collect_javascript___tests__from_test_patch():
    test_patch = (
        "diff --git a/__tests__/test-status.js b/__tests__/test-status.js\n"
        "diff --git a/__tests__/test-statusMatrix.js b/__tests__/test-statusMatrix.js\n"
    )
    targets = collect_test_targets("javascript", "", test_patch)
    assert targets == ["__tests__/test-status.js", "__tests__/test-statusMatrix.js"]


def test_collect_javascript_excludes_snapshot_artifacts():
    test_patch = (
        "diff --git a/__integration__/__snapshots__/android.test.snap.js "
        "b/__integration__/__snapshots__/android.test.snap.js\n"
        "diff --git a/__tests__/common/transforms.test.js "
        "b/__tests__/common/transforms.test.js\n"
    )
    targets = collect_test_targets("javascript", "", test_patch)
    assert targets == ["__tests__/common/transforms.test.js"]


def test_detect_language_from_paths_go():
    paths = ["pkg/x_test.go", "README.md"]
    assert detect_language_from_paths(paths) == "go"


def test_is_test_path_java():
    spec = get_language_spec("java")
    assert is_test_path("src/test/java/com/example/FooTest.java", spec)


def test_parse_gotest_log():
    log = "--- PASS: TestFoo (0.01s)\n--- FAIL: TestBar (0.02s)\n"
    m = parse_gotest_log(log)
    assert m["TestFoo"] == "PASSED"
    assert m["TestBar"] == "FAILED"


def test_normalize_language_aliases():
    assert normalize_language("py") == "python"
    assert normalize_language("golang") == "go"


def test_detect_language_from_changed_paths_java():
    patch = (
        "diff --git a/core/src/main/java/com/example/Foo.java "
        "b/core/src/main/java/com/example/Foo.java\n"
    )
    assert detect_language_from_changed_paths(patch, "") == "java"


def test_resolve_task_language_prefers_gradle_repo(tmp_path: Path):
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    patch = (
        "diff --git a/module/src/main/java/org/example/App.java "
        "b/module/src/main/java/org/example/App.java\n"
    )
    assert resolve_task_language("auto", repo=tmp_path, patch=patch, test_patch="") == "java"


def test_detect_language_from_repo_build_markers_gradle(tmp_path: Path):
    (tmp_path / "gradlew").write_text("", encoding="utf-8")
    assert detect_language_from_repo_build_markers(tmp_path) == "java"


def test_detect_language_python_before_package_json(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert detect_language_from_repo_build_markers(tmp_path) == "python"


def test_resolve_task_language_django_repo_id():
    assert (
        resolve_task_language("auto", repo_id="django/django", patch="", test_patch="")
        == "python"
    )
