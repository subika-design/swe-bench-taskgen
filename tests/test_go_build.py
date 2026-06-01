"""Tests for Go harness docker_specs."""

from pathlib import Path

from swe_rebench_pr.go_build import (
    DEFAULT_GO_VERSION,
    ensure_go_docker_specs,
    resolve_go_version_for_repo,
)
from swe_rebench_pr.swebench_align import export_install_config_for_harness


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
    out = export_install_config_for_harness({"language": "go", "install": "go mod download"}, language="go")
    assert out["docker_specs"]["go_version"] == DEFAULT_GO_VERSION


def test_ensure_go_docker_specs_ignores_non_go_language():
    cfg = ensure_go_docker_specs({}, language="python")
    assert "docker_specs" not in cfg
