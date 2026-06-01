"""Tests for Python install helpers and pip shell quoting."""

from pathlib import Path

from swe_rebench_pr.diff_split import has_test_patch_label_mismatch
from swe_rebench_pr.harness.test_spec.python import make_env_script_list_py
from swe_rebench_pr.python_build import (
    DATEUTIL_UPDATEZINFO_CMD,
    augment_python_install_config,
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
