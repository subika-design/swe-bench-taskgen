"""CMake + nested pytest discover uses C task language with Python harness."""

from pathlib import Path

from swe_rebench_pr.builder import resolve_task_language
from swe_rebench_pr.integration_build import (
    apply_native_build_if_integration,
    discover_harness_language,
    integration_pytest_paths_from_patches,
    merge_hybrid_c_integration_paths,
    native_integration_discover_active,
    resolve_integration_task_language,
)


def _cmake_pytest_repo(tmp_path: Path) -> Path:
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "conftest.py").write_text("# pytest\n", encoding="utf-8")
    return tmp_path


def test_resolve_integration_task_language(tmp_path: Path):
    repo = _cmake_pytest_repo(tmp_path)
    patch = "diff --git a/tests/http/test_a.py b/tests/http/test_a.py\n"
    assert resolve_integration_task_language(repo, test_patch=patch) == "c"


def test_apply_native_build_for_language_c(tmp_path: Path):
    repo = _cmake_pytest_repo(tmp_path)
    test_patch = "diff --git a/tests/http/test_a.py b/tests/http/test_a.py\n"
    cfg = apply_native_build_if_integration(
        {"language": "c", "install": "true"},
        repo,
        test_paths=["tests/http/test_a.py"],
        test_patch=test_patch,
    )
    assert native_integration_discover_active(cfg)
    assert cfg.get("language") == "c"
    assert "pytest" in str(cfg.get("test_cmd") or "")


def test_discover_harness_language_uses_python_image_for_native_integration():
    cfg = {"language": "c", "native_integration_build": True}
    assert discover_harness_language("c", cfg) == "python"
    assert discover_harness_language("c", {"language": "c"}) == "c"


def test_integration_pytest_paths_from_patches():
    test_patch = (
        "diff --git a/tests/http/test_60_h3_proxy.py b/tests/http/test_60_h3_proxy.py\n"
    )
    paths = integration_pytest_paths_from_patches("", test_patch)
    assert paths == ["tests/http/test_60_h3_proxy.py"]


def test_merge_hybrid_c_prefers_pytest_runner_paths():
    test_patch = (
        "diff --git a/tests/http/test_05_01.py b/tests/http/test_05_01.py\n"
        "diff --git a/tests/http/test_05_02.py b/tests/http/test_05_02.py\n"
    )
    impl = "diff --git a/lib/vquic/vquic.c b/lib/vquic/vquic.c\n"
    detection, runner = merge_hybrid_c_integration_paths(impl, test_patch)
    assert "tests/http/test_05_01.py" in runner
    assert "tests/http/test_05_02.py" in runner
    assert runner == detection or set(runner).issubset(set(detection))


def test_apply_native_build_when_only_pytest_in_test_patch(tmp_path: Path):
    repo = _cmake_pytest_repo(tmp_path)
    test_patch = (
        "diff --git a/tests/http/test_05_01.py b/tests/http/test_05_01.py\n"
    )
    impl = "diff --git a/lib/foo.c b/lib/foo.c\n"
    cfg = apply_native_build_if_integration(
        {"language": "c", "install": "true"},
        repo,
        test_paths=["lib/foo.c"],
        test_patch=test_patch,
        patch=impl,
    )
    assert native_integration_discover_active(cfg)
    assert "pytest" in str(cfg.get("test_cmd") or "")
