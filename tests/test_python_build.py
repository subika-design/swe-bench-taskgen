"""Tests for Python install helpers and pip shell quoting."""

from pathlib import Path

from swe_rebench_pr.diff_split import has_test_patch_label_mismatch
from swe_rebench_pr.harness.test_spec.python import make_env_script_list_py
from swe_rebench_pr.python_build import (
    DATEUTIL_UPDATEZINFO_CMD,
    augment_python_install_config,
    editable_install_with_test_extras,
    infer_python_test_install_signals,
    log_indicates_python_pytest_env_failure,
    merge_python_test_install_into_config,
    remediate_python_install_from_log,
    slice_failures_are_dateutil_zoneinfo,
)
from swe_rebench_pr.ruby_build import _normalize_ruby_version
from swe_rebench_pr.shell_quote import shell_join_pip_requirements, shell_quote_token


def test_shell_quote_token_versions_with_comparators():
    assert shell_quote_token("setuptools_scm<8.0") == "'setuptools_scm<8.0'"
    assert shell_quote_token("pytest>=3.0") == "'pytest>=3.0'"
    assert shell_quote_token("wheel") == "wheel"


def test_shell_join_pip_requirements():
    joined = shell_join_pip_requirements(["wheel", "setuptools_scm<8.0", "pytest>=3.0"])
    assert joined == "wheel 'setuptools_scm<8.0' 'pytest>=3.0'"


def test_harness_env_script_quotes_pip_packages():
    instance = {"instance_id": "dateutil__dateutil-1350", "repo": "dateutil/dateutil"}
    specs = {
        "python": "3.11",
        "pip_packages": ["setuptools_scm<8.0", "pytest>=3.0", "wheel"],
    }
    script = "\n".join(make_env_script_list_py(instance, specs, "testbed"))
    assert "'setuptools_scm<8.0'" in script
    assert "'pytest>=3.0'" in script
    assert "python -m pip install" in script


def test_augment_python_install_config_dateutil_zoneinfo(tmp_path: Path):
    (tmp_path / "updatezinfo.py").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "zonefile_metadata.json").write_text("{}", encoding="utf-8")
    cfg = augment_python_install_config({"install": "pip install -e ."}, repo=tmp_path)
    post = cfg.get("post_install") or []
    eval_cmds = cfg.get("eval_commands") or []
    assert DATEUTIL_UPDATEZINFO_CMD in post
    assert DATEUTIL_UPDATEZINFO_CMD in eval_cmds


def test_augment_python_install_config_ignores_comment_only_updatezinfo(tmp_path: Path):
    (tmp_path / "updatezinfo.py").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "zonefile_metadata.json").write_text("{}", encoding="utf-8")
    cfg = augment_python_install_config(
        {"post_install": ["# run updatezinfo.py if needed"]},
        repo=tmp_path,
    )
    assert DATEUTIL_UPDATEZINFO_CMD in (cfg.get("post_install") or [])


def test_slice_failures_are_dateutil_zoneinfo():
    zone = "dateutil-zoneinfo.tar.gz"
    assert slice_failures_are_dateutil_zoneinfo(
        [("t", f"missing {zone}")],
        [],
    )
    assert not slice_failures_are_dateutil_zoneinfo(
        [("t", f"missing {zone}")],
        [("t", "other error")],
    )


def test_ruby_normalize_drops_zero_patch_for_docker():
    assert _normalize_ruby_version("3.1.0") == "3.1-bookworm"
    assert _normalize_ruby_version("3.4.8") == "3.4.8-bookworm"


def test_python_body_reruns_eval_commands_after_git_clean():
    from swe_rebench_pr.docker_entry import _python_body
    from swe_rebench_pr.python_build import DATEUTIL_UPDATEZINFO_CMD

    body = _python_body(
        {"eval_commands": [DATEUTIL_UPDATEZINFO_CMD]},
        False,
        repo_dir="/testbed",
        skip_install=True,
        harness_conda=True,
        harness_env_only=True,
    )
    assert body.count(DATEUTIL_UPDATEZINFO_CMD) == 2
    assert "dateutil-zoneinfo.tar.gz" in body


def test_cargo_label_mismatch_skipped_for_integration_targets():
    case_map = {"no_context::test_foo": "PASSED", "opaque::test_bar": "PASSED"}
    paths = ["tests/no_context.rs", "tests/opaque.rs"]
    assert not has_test_patch_label_mismatch(case_map, paths, language="rust")


TYPER_LIKE_PYPROJECT = '''
[build-system]
requires = ["pdm-backend"]
build-backend = "pdm.backend"

[project]
name = "example"
requires-python = ">=3.10"

[dependency-groups]
dev = [
    { include-group = "tests" },
]
tests = [
    "pytest >=9.0.0",
    "pytest-cov >=7.0.0",
    "pytest-xdist >=3.6.1",
]

[tool.pytest]
minversion = "9.0"
'''


def test_infer_python_test_install_signals_pdm_dependency_groups(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(TYPER_LIKE_PYPROJECT, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    signals = infer_python_test_install_signals(tmp_path)
    assert "pdm-backend" in signals["build_backends"]
    assert any("pytest>=" in p for p in signals["pip_packages"])
    assert any("pytest-cov" in p for p in signals["pip_packages"])
    assert signals["pytest_minversion"] == "9.0"
    assert signals["extra_groups"] == []


def test_merge_python_test_install_adds_build_backend_and_pytest(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(TYPER_LIKE_PYPROJECT, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    cfg = merge_python_test_install_into_config({"install": "pip install -e ."}, tmp_path)
    assert "pdm-backend" in (cfg.get("pip_packages") or [])
    assert any("pytest>=" in p for p in cfg.get("pip_packages") or [])
    assert cfg["install"] == "pip install -e ."


def test_merge_python_test_install_uses_optional_extras(tmp_path: Path):
    text = '''
[project.optional-dependencies]
test = ["pytest>=8.0"]
'''
    (tmp_path / "pyproject.toml").write_text(text, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    cfg = merge_python_test_install_into_config({"install": "pip install -e ."}, tmp_path)
    assert 'pip install -e ".[test]"' in cfg["install"]


def test_editable_install_with_test_extras_fallback_chain():
    cmd = editable_install_with_test_extras(["dev", "tests"])
    assert 'pip install -e ".[tests]"' in cmd
    assert 'pip install -e ".[dev]"' in cmd
    assert cmd.endswith("pip install -e .")


def test_remediate_python_install_from_pytest_exit(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(TYPER_LIKE_PYPROJECT, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    out = remediate_python_install_from_log(
        {"install": "pip install -e ."},
        "ERROR: usage error",
        repo=tmp_path,
        docker_exit=4,
    )
    assert "pdm-backend" in (out.get("pip_packages") or [])
    assert any("pytest" in p.lower() for p in out.get("pip_packages") or [])


def test_remediate_python_install_uv_not_found():
    out = remediate_python_install_from_log(
        {"install": "uv pip install -r requirements-tests.txt"},
        "/w/project_install.sh: line 4: uv: command not found\n",
        repo=None,
    )
    assert out["install"] == "pip install -r requirements-tests.txt"


def test_log_indicates_python_pytest_env_failure():
    assert log_indicates_python_pytest_env_failure("", docker_exit=4)
    assert log_indicates_python_pytest_env_failure("No module named pytest", docker_exit=0)
    assert log_indicates_python_pytest_env_failure("pytest requires pytest>=9.0 minversion", docker_exit=0)


def test_augment_python_test_install_before_dateutil(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(TYPER_LIKE_PYPROJECT, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "updatezinfo.py").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "zonefile_metadata.json").write_text("{}", encoding="utf-8")
    cfg = augment_python_install_config({"install": "pip install -e ."}, repo=tmp_path)
    assert "pdm-backend" in (cfg.get("pip_packages") or [])
    assert DATEUTIL_UPDATEZINFO_CMD in (cfg.get("post_install") or [])
