from swe_rebench_pr.ci_fidelity import (
    ci_test_cmd_trusted,
    mark_ci_test_cmd_trusted,
    pytest_cmd_has_scoped_paths,
    pytest_cmd_needs_explicit_paths,
    should_merge_ci_install,
    should_preserve_ci_test_cmd,
)
from swe_rebench_pr.ci_extract import merge_ci_draft_into_config
from swe_rebench_pr.languages import get_language_spec


def test_mark_ci_test_cmd_trusted():
    cfg = mark_ci_test_cmd_trusted({"test_cmd": "pytest tests"})
    assert ci_test_cmd_trusted(cfg)
    assert should_preserve_ci_test_cmd(cfg)


def test_ci_excerpt_heuristic_trusted():
    cfg = {
        "test_cmd": "bundle exec rspec spec",
        "_ci_excerpt": "run: bundle exec rspec spec",
    }
    assert ci_test_cmd_trusted(cfg)


def test_merge_ci_draft_stamps_trusted():
    defaults = get_language_spec("python").default_install_config
    cfg = dict(defaults)
    draft = {"test_cmd": "pytest tests -q", "_ci_excerpt": "pytest tests"}
    out = merge_ci_draft_into_config(cfg, draft, language="python")
    assert out["test_cmd"] == "pytest tests -q"
    assert out.get("_ci_test_cmd_trusted")


def test_bare_tests_dir_is_not_scoped_pytest_cmd():
    assert not pytest_cmd_has_scoped_paths("pytest tests/")
    assert not pytest_cmd_has_scoped_paths("pytest tests")
    assert pytest_cmd_has_scoped_paths("pytest tests/test_foo.py")
    assert pytest_cmd_has_scoped_paths("pytest -k foo")
    assert pytest_cmd_needs_explicit_paths(
        "pytest tests/", ["tests/test_a.py"]
    )


def test_should_merge_ci_install_modern_pm():
    defaults = get_language_spec("python").default_install_config
    cfg = dict(defaults)
    cfg["install"] = 'pip install -e ".[tests]" || pip install -e .'
    assert should_merge_ci_install(
        cfg,
        "uv sync --all-extras",
        defaults,
        language="python",
        overlay={"_ci_excerpt": "run: uv sync --all-extras"},
    )
