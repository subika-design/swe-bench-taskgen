"""Tests for repo-first install config improvements."""

from pathlib import Path

from swe_rebench_pr.ci_extract import CiExtractDraft, merge_ci_draft_into_config
from swe_rebench_pr.ci_fidelity import should_merge_ci_install, should_merge_ci_test_cmd
from swe_rebench_pr.ci_install_normalize import normalize_ci_install_command, normalize_ci_test_command
from swe_rebench_pr.gh_pr import BaseCommitUnreachableError, validate_base_commit_reachable
from swe_rebench_pr.go_build import go_install_config_for_repo, merge_go_build_into_config
from swe_rebench_pr.install_cache import INSTALL_CONFIG_CACHE_VERSION, install_config_cache_key
from swe_rebench_pr.install_config_build import build_install_config_for_repo
from swe_rebench_pr.languages import get_language_spec
from swe_rebench_pr.python_build import (
    finalize_python_install_config,
    merge_python_build_into_config,
    pytest_test_cmd_from_targets,
)


def test_should_merge_ci_install_overrides_plain_heuristic_for_pdm():
    defaults = get_language_spec("python").default_install_config
    cfg = dict(defaults)
    cfg["install"] = 'pip install -e ".[dev]" || pip install -e .'
    assert should_merge_ci_install(
        cfg,
        "pdm install -G tests",
        defaults,
        language="python",
        overlay={"_ci_excerpt": "run: pdm install -G tests"},
    )


def test_should_merge_ci_test_cmd_scoped_over_generic_pytest():
    defaults = get_language_spec("python").default_install_config
    cfg = dict(defaults)
    assert should_merge_ci_test_cmd(
        cfg,
        "pytest -rA tests/unit/test_foo.py",
        defaults,
        overlay={"_ci_excerpt": "run: pytest -rA tests/unit/test_foo.py"},
    )


def test_merge_ci_draft_overrides_heuristic_install_for_pdm():
    base = dict(get_language_spec("python").default_install_config)
    base["install"] = 'pip install -e ".[dev]" || pip install -e .'
    draft = CiExtractDraft(
        install="pdm install -G tests",
        test_cmd="pytest -rA tests/",
        ci_excerpt="run: pdm install -G tests\nrun: pytest -rA tests/",
    )
    merged = merge_ci_draft_into_config(base, draft, language="python")
    assert "pdm install" not in merged["install"]
    assert 'pip install -e ".[tests]"' in merged["install"] or "pip install -e" in merged["install"]


def test_normalize_tox_install():
    out = normalize_ci_install_command("tox -e py311", language="python")
    assert "pip install -e" in out


def test_normalize_nox_install():
    out = normalize_ci_install_command("nox -s tests", language="python")
    assert "pip install -e" in out


def test_normalize_yarn_workspace_install():
    out = normalize_ci_install_command("yarn install -W", language="javascript")
    assert "yarn install" in out or "npm" in out


def test_finalize_python_applies_requires_python_and_tox(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py312\n", encoding="utf-8")
    cfg = finalize_python_install_config({"install": "pip install -e ."}, tmp_path)
    assert cfg.get("python") == "3.12"
    pkgs = [p.split("[")[0].lower() for p in cfg.get("pip_packages") or []]
    assert "pytest-cov" in pkgs


def test_merge_python_build_scopes_pytest_to_test_paths(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    cfg = merge_python_build_into_config(
        dict(get_language_spec("python").default_install_config),
        tmp_path,
        ["tests/unit/test_foo.py"],
    )
    assert "tests/unit/test_foo.py" in cfg["test_cmd"]


def test_pytest_test_cmd_from_targets_appends_paths_when_ci_broad():
    scoped = "pytest -rA tests/integration"
    out = pytest_test_cmd_from_targets(["tests/unit/a.py"], scoped)
    assert "tests/unit/a.py" in out
    assert "tests/integration" not in out


def test_go_install_config_for_repo_scopes_packages(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n", encoding="utf-8")
    cfg = go_install_config_for_repo(tmp_path, test_paths=["pkg/foo/foo_test.go"])
    assert "./pkg/foo" in cfg["test_cmd"]
    assert cfg["docker_specs"]["go_version"] == "1.22.12"


def test_merge_go_build_preserves_ci_run_flag(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module m\n\ngo 1.22\n", encoding="utf-8")
    cfg = merge_go_build_into_config(
        {
            "test_cmd": 'go test -v ./... -run "^TestFoo$"',
            "_ci_test_cmd_trusted": True,
            "_ci_excerpt": "run: go test -v ./... -run ^TestFoo$",
        },
        tmp_path,
        ["pkg/a_test.go"],
    )
    assert "-run" in cfg["test_cmd"]


def test_install_cache_key_includes_version(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module m\n\ngo 1.22\n", encoding="utf-8")
    key = install_config_cache_key("org/repo", tmp_path)
    assert INSTALL_CONFIG_CACHE_VERSION == "3"
    assert len(key) == 32


def test_validate_base_commit_reachable_ok(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(cmd, **kwargs):
        from subprocess import CompletedProcess

        if "cat-file" in cmd:
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected cmd: {cmd}")

    monkeypatch.setattr("swe_rebench_pr.gh_pr.subprocess.run", fake_run)
    validate_base_commit_reachable(repo, "abc123def456")


def test_validate_base_commit_unreachable_raises(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(cmd, **kwargs):
        from subprocess import CompletedProcess

        if "cat-file" in cmd:
            return CompletedProcess(cmd, 1, stdout="", stderr="fatal: Not a valid object name")
        if "remote" in cmd and "get-url" in cmd:
            return CompletedProcess(cmd, 0, stdout="https://github.com/o/r.git", stderr="")
        raise AssertionError(f"unexpected cmd: {cmd}")

    monkeypatch.setattr("swe_rebench_pr.gh_pr.subprocess.run", fake_run)
    try:
        validate_base_commit_reachable(repo, "deadbeef" * 5)
        assert False, "expected BaseCommitUnreachableError"
    except BaseCommitUnreachableError as e:
        assert e.repo_id == "o/r"
        assert "unreachable" in str(e).lower()


def test_build_install_config_python_matches_docker_finalize(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.10"\n',
        encoding="utf-8",
    )
    (tmp_path / "tox.ini").write_text("[tox]\n", encoding="utf-8")
    cfg = build_install_config_for_repo(
        tmp_path,
        "python",
        "org/repo",
        test_paths=["tests/test_x.py"],
        use_cache=False,
    )
    assert cfg.get("python") == "3.10"
    assert "tests/test_x.py" in str(cfg.get("test_cmd") or "")
