from pathlib import Path

from swe_rebench_pr.ci_extract import (
    CiExtractDraft,
    apt_packages_from_ci_workflows,
    extract_ci_draft,
    merge_ci_draft_into_config,
)
from swe_rebench_pr.install_config_build import build_install_config_for_repo
from swe_rebench_pr.install_cache import install_config_cache_key, load_cached_install_config
from swe_rebench_pr.languages import get_language_spec
from swe_rebench_pr.manifest_extract import composer_ext_apt_packages, merge_manifest_into_config


def test_apt_packages_from_ci_workflows(tmp_path: Path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "      - run: sudo apt-get install -y libssl-dev pkg-config cmake\n",
        encoding="utf-8",
    )
    pkgs = apt_packages_from_ci_workflows(tmp_path)
    assert "libssl-dev" in pkgs
    assert "cmake" in pkgs


def test_extract_ci_draft_install_and_test(tmp_path: Path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        """
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[test]"
      - run: pytest -rA tests/
""",
        encoding="utf-8",
    )
    draft = extract_ci_draft(tmp_path)
    assert draft.install and "pip install" in draft.install
    assert draft.test_cmd and "pytest" in draft.test_cmd
    assert draft.python == "3.11"


def test_merge_ci_draft_overrides_default_python_install(tmp_path: Path):
    draft = CiExtractDraft(install="pip install -r requirements-dev.txt", test_cmd="pytest -rA tests/unit")
    base = dict(get_language_spec("python").default_install_config)
    merged = merge_ci_draft_into_config(base, draft, language="python")
    assert "requirements-dev" in str(merged.get("install") or "")


def test_composer_ext_apt_packages(tmp_path: Path):
    (tmp_path / "composer.json").write_text(
        '{"require": {"php": "^8.2", "ext-zip": "*", "ext-xml": "*"}}',
        encoding="utf-8",
    )
    pkgs = composer_ext_apt_packages(tmp_path)
    assert "libzip-dev" in pkgs
    assert "libxml2-dev" in pkgs


def test_build_install_config_uses_ci(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("      - run: pip install -e .\n      - run: pytest -rA\n", encoding="utf-8")
    cfg = build_install_config_for_repo(
        tmp_path,
        "python",
        "org/repo",
        use_cache=False,
        llm_install=None,
    )
    assert cfg.get("install")
    assert "pytest" in str(cfg.get("test_cmd") or "")


def test_install_config_cache_key_stable(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.com\n\ngo 1.22\n", encoding="utf-8")
    k1 = install_config_cache_key("org/repo", tmp_path)
    k2 = install_config_cache_key("org/repo", tmp_path)
    assert k1 == k2


def test_load_cached_install_config(tmp_path: Path):
    from swe_rebench_pr.install_cache import save_cached_install_config

    cache_dir = tmp_path / "cache"
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    cfg = {"install": "bundle install", "test_cmd": "bundle exec rspec"}
    save_cached_install_config("rubocop/rubocop", tmp_path, cfg, cache_dir=cache_dir)
    loaded = load_cached_install_config("rubocop/rubocop", tmp_path, cache_dir=cache_dir)
    assert loaded is not None
    assert loaded.get("install") == "bundle install"
