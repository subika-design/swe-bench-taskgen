"""Tests for Go harness docker_specs and gotest label matching."""

from pathlib import Path

from swe_rebench_pr.diff_split import has_test_patch_label_mismatch, junit_outcome_counts_for_paths
from swe_rebench_pr.docker_discover import _filter_f2p_to_test_patch_scope
from swe_rebench_pr.go_build import (
    DEFAULT_GO_VERSION,
    ensure_go_docker_specs,
    gotest_log_key_in_test_patch_paths,
    normalize_go_version,
    resolve_go_test_invocation,
    resolve_go_version_for_repo,
)
from swe_rebench_pr.swebench_align import export_install_config_for_harness


def test_normalize_go_version_strips_semver_range():
    assert normalize_go_version("^1.22") == "1.22.12"
    assert normalize_go_version("~1.21") == "1.21.13"
    assert normalize_go_version("v1.17") == "1.17.13"


def test_ensure_go_docker_specs_rewrites_ci_range():
    cfg = ensure_go_docker_specs(
        {"docker_specs": {"go_version": "^1.22"}},
        language="go",
    )
    assert cfg["docker_specs"]["go_version"] == "1.22.12"


def test_ensure_go_docker_specs_default():
    cfg = ensure_go_docker_specs({}, language="go")
    assert cfg["docker_specs"]["go_version"] == DEFAULT_GO_VERSION


def test_ensure_go_docker_specs_uses_go_mod(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.com/foo\n\ngo 1.21\n", encoding="utf-8")
    cfg = ensure_go_docker_specs({}, repo=tmp_path, language="go")
    assert cfg["docker_specs"]["go_version"] == "1.21.13"


def test_resolve_go_version_for_repo_full_patch(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module m\n\ngo 1.22.5\n", encoding="utf-8")
    assert resolve_go_version_for_repo(tmp_path) == "1.22.5"


def test_export_install_config_for_harness_includes_go_version():
    out = export_install_config_for_harness(
        {"language": "go", "install": "go mod download", "docker_specs": {"go_version": "^1.22"}},
        language="go",
    )
    assert out["docker_specs"]["go_version"] == "1.22.12"


def test_ensure_go_docker_specs_ignores_non_go_language():
    cfg = ensure_go_docker_specs({}, language="python")
    assert "docker_specs" not in cfg


def test_gotest_key_matches_test_file_heuristic():
    paths = ["bash_completions_test.go"]
    assert gotest_log_key_in_test_patch_paths("TestBashCompletions", paths)
    assert gotest_log_key_in_test_patch_paths("TestBashCompletions/sub", paths)


def test_gotest_key_matches_func_in_patch():
    patch = "+func TestCustomThing(t *testing.T) {\n"
    paths = ["foo_test.go"]
    assert gotest_log_key_in_test_patch_paths(
        "TestCustomThing", paths, test_patch=patch
    )


def test_has_test_patch_label_mismatch_false_for_gotest():
    case_map = {"TestA": "PASSED", "TestB": "PASSED"}
    paths = ["a_test.go"]
    assert not has_test_patch_label_mismatch(
        case_map, paths, language="go", test_patch="+func TestA(t *testing.T) {}\n"
    )


def test_junit_outcome_counts_for_gotest_paths():
    case_map = {
        "TestKeep": "PASSED",
        "TestOther": "PASSED",
    }
    pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(
        case_map,
        ["keep_test.go"],
        language="go",
        test_patch="+func TestKeep(t *testing.T) {}\n",
    )
    assert tot == 1
    assert pa == 1


def test_filter_f2p_keeps_gotest_names():
    f2p = ["TestA", "TestB"]
    kept = _filter_f2p_to_test_patch_scope(
        f2p,
        ["a_test.go"],
        "go",
        test_patch="+func TestA(t *testing.T) {}\n",
    )
    assert kept == ["TestA"]


def test_resolve_go_test_invocation_run_flag():
    cmd = resolve_go_test_invocation(
        'go test -v ./... -run "^TestFoo$"',
        ["pkg/foo_test.go"],
    )
    assert "-run" in cmd
    assert "TestFoo" in cmd


def test_resolve_go_test_invocation_scoped_packages():
    cmd = resolve_go_test_invocation(
        "go test -v ./...",
        ["cobra/cmd/add_test.go"],
    )
    assert "./cobra/cmd" in cmd
