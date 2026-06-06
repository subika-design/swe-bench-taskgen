"""Tests for CI install command normalization."""

from swe_rebench_pr.ci_install_normalize import (
    normalize_ci_install_command,
    normalize_ci_test_command,
)
from swe_rebench_pr.ci_extract import merge_ci_draft_into_config


def test_normalize_pdm_install():
    out = normalize_ci_install_command("pdm install -G dev", language="python")
    assert 'pip install -e ".[dev]"' in out
    assert "||" in out


def test_normalize_uv_sync():
    out = normalize_ci_install_command("uv sync --all-extras", language="python")
    assert "pip install -e" in out


def test_normalize_uv_pip_install_requirements():
    out = normalize_ci_install_command(
        "uv pip install -r requirements-tests.txt", language="python"
    )
    assert out == "pip install -r requirements-tests.txt"


def test_normalize_uv_pip_install_strips_system_flag():
    out = normalize_ci_install_command("uv pip install --system -e .", language="python")
    assert out == "pip install -e ."


def test_normalize_uv_pip_install_chained():
    from swe_rebench_pr.ci_install_normalize import compose_ci_install_sequence

    out = compose_ci_install_sequence(
        [
            "uv pip install -r requirements-tests.txt",
            "uv pip install pytest-cov",
        ],
        language="python",
    )
    assert "uv" not in out.lower()
    assert "pip install -r requirements-tests.txt" in out
    assert "pip install pytest-cov" in out


def test_normalize_uv_run_pytest():
    out = normalize_ci_test_command("uv run pytest tests/", language="python")
    assert out.startswith("pytest")
    assert "uv" not in out.lower()


def test_docker_safe_python_install():
    from swe_rebench_pr.ci_install_normalize import docker_safe_python_install

    assert docker_safe_python_install("uv pip install -r requirements-tests.txt") == (
        "pip install -r requirements-tests.txt"
    )


def test_merge_ci_draft_normalizes_uv_pip_install():
    cfg = merge_ci_draft_into_config(
        {"install": "pip install -e .", "test_cmd": "pytest -rA"},
        {
            "install": "uv pip install -r requirements-tests.txt",
            "_ci_excerpt": "run: uv pip install -r requirements-tests.txt",
        },
        language="python",
    )
    assert "uv" not in cfg["install"].lower()
    assert "pip install -r requirements-tests.txt" in cfg["install"]


def test_normalize_poetry_install_with_dev():
    out = normalize_ci_install_command("poetry install --with dev,test", language="python")
    assert 'pip install -e ".[dev]"' in out


def test_normalize_pnpm_install():
    out = normalize_ci_install_command("pnpm install --frozen-lockfile", language="javascript")
    assert out == "npm ci"


def test_normalize_pnpm_test():
    out = normalize_ci_test_command("pnpm test -- --coverage", language="javascript")
    assert out.startswith("npm run")


def test_merge_ci_draft_normalizes_pdm_install():
    cfg = merge_ci_draft_into_config(
        {"install": "pip install -e .", "test_cmd": "pytest -rA"},
        {"install": "pdm install -G tests", "_ci_excerpt": "run: pdm install -G tests"},
        language="python",
    )
    assert "pdm install" not in cfg["install"]
    assert "pip install -e" in cfg["install"]


def test_merge_ci_draft_overrides_heuristic_when_pdm_in_ci():
    cfg = merge_ci_draft_into_config(
        {
            "install": 'pip install -e ".[dev]" || pip install -e .',
            "test_cmd": "pytest -rA",
        },
        {
            "install": "pdm sync --group dev",
            "_ci_excerpt": "run: pdm sync --group dev",
        },
        language="python",
    )
    assert "pdm" not in cfg["install"].lower()
    assert "pip install -e" in cfg["install"]
