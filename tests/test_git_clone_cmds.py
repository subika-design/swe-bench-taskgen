"""Tests for harness git clone fetch fallback."""

from swe_rebench_pr.harness.constants import MAP_REPO_TO_EXT
from swe_rebench_pr.harness.git_clone_cmds import git_fetch_and_reset_commands
from swe_rebench_pr.harness.test_spec.create_scripts import make_repo_clone_script_list


def test_git_fetch_and_reset_commands():
    cmds = git_fetch_and_reset_commands("deadbeef")
    text = "\n".join(cmds)
    assert "git cat-file -e deadbeef" in text
    assert "git fetch origin deadbeef" in text
    assert "git reset --hard deadbeef" in text


def test_make_repo_clone_script_list_py_includes_fetch_fallback():
    MAP_REPO_TO_EXT["fastapi/typer"] = "py"
    try:
        cmds = make_repo_clone_script_list({}, "fastapi/typer", "/testbed", "abc123", "testbed")
    finally:
        MAP_REPO_TO_EXT.pop("fastapi/typer", None)
    text = "\n".join(cmds)
    assert "git clone" in text
    assert "git fetch origin abc123" in text
    assert "git reset --hard abc123" in text
