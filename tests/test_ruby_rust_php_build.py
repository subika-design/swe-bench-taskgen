"""Tests for Ruby/Rust/PHP docker specs."""

from pathlib import Path

from swe_rebench_pr.php_build import DEFAULT_PHP_VERSION, ensure_php_docker_specs
from swe_rebench_pr.ruby_build import DEFAULT_RUBY_VERSION, ensure_ruby_docker_specs
from swe_rebench_pr.rust_build import DEFAULT_RUST_VERSION, ensure_rust_docker_specs
from swe_rebench_pr.swebench_align import export_install_config_for_harness


def test_ensure_ruby_docker_specs_from_ruby_version(tmp_path: Path):
    (tmp_path / ".ruby-version").write_text("3.3.1\n", encoding="utf-8")
    cfg = ensure_ruby_docker_specs({}, repo=tmp_path, language="ruby")
    assert cfg["docker_specs"]["ruby_version"] == "3.3.1-bookworm"


def test_ruby_bundle_install_disables_version_check():
    from swe_rebench_pr.ruby_build import ruby_bundle_install_cmd

    cmd = ruby_bundle_install_cmd(with_lock=True)
    assert "disable_version_check" in cmd
    assert "bundle install --jobs" in cmd


def test_ruby_apt_filter_drops_debian_ruby_packages():
    from swe_rebench_pr.ruby_build import _filter_ruby_apt_packages

    out = _filter_ruby_apt_packages(
        ["ruby-dev", "libyaml-dev", "ruby3.1-dev", "libxml2-dev"]
    )
    assert out == ["libyaml-dev", "libxml2-dev"]


def test_remediate_ruby_version_mismatch():
    from swe_rebench_pr.ruby_build import remediate_ruby_install_from_log

    cfg = {"install": "bundle install --jobs 4", "apt-pkgs": ["ruby-dev", "git"]}
    log = "Your Ruby version is 3.4.9, but your Gemfile specified 3.4.8"
    out = remediate_ruby_install_from_log(cfg, log)
    assert "disable_version_check" in out["install"]
    assert "ruby-dev" not in (out.get("apt-pkgs") or [])


def test_ruby_body_passes_targets_to_rspec():
    from swe_rebench_pr.docker_entry import _ruby_body

    body = _ruby_body(False, {"ruby_test_runner": "rspec"}, repo_dir="/testbed")
    assert "_run_ruby_tests" in body
    assert 'bundle exec rspec "${T[@]}"' in body
    assert "RspecJunitFormatter" in body
    assert "base + test_patch only" in body
    assert "test_patch + impl.patch" in body
    assert "reset to base_commit" in body
    assert "_ruby_ensure_junit_formatter" in body
    assert "empty_junit_if_missing.py" in body
    assert "SKIP_REPO_JUNIT_HARVEST=1" in body
    assert "if [[ ${#T[@]} -gt 0 ]]; then" in body
    assert "2>/dev/null || bundle exec rspec" not in body


def test_ruby_body_minitest_runner():
    from swe_rebench_pr.docker_entry import _ruby_body

    body = _ruby_body(False, {"ruby_test_runner": "minitest"}, repo_dir="/testbed")
    assert 'RUBY_TEST_RUNNER="minitest"' in body
    assert "/w/minitest_junit_runner.rb" in body
    assert "_ruby_ensure_minitest_junit" in body


def test_detect_ruby_test_runner_spec_vs_test(tmp_path: Path):
    from swe_rebench_pr.ruby_build import detect_ruby_test_runner

    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "spec_helper.rb").write_text("", encoding="utf-8")
    assert detect_ruby_test_runner(tmp_path, ["spec/foo_spec.rb"]) == "rspec"
    (tmp_path / "test").mkdir(exist_ok=True)
    assert detect_ruby_test_runner(tmp_path, ["test/models/user_test.rb"]) == "minitest"


def test_export_install_config_includes_ruby_test_runner():
    out = export_install_config_for_harness(
        {"language": "ruby", "install": "bundle install", "ruby_test_runner": "rspec"},
        language="ruby",
    )
    assert out["ruby_test_runner"] == "rspec"


def test_ensure_rust_docker_specs_from_toolchain(tmp_path: Path):
    (tmp_path / "rust-toolchain.toml").write_text('channel = "1.83.0"\n', encoding="utf-8")
    cfg = ensure_rust_docker_specs({}, repo=tmp_path, language="rust")
    assert cfg["docker_specs"]["rust_version"] == "1.83-bookworm"


def test_ensure_php_docker_specs_from_composer(tmp_path: Path):
    (tmp_path / "composer.json").write_text('{"require":{"php":"^8.3"}}', encoding="utf-8")
    cfg = ensure_php_docker_specs({}, repo=tmp_path, language="php")
    assert cfg["docker_specs"]["php_version"] == "8.3-cli-bookworm"


def test_php_normalize_maps_8_0_to_bookworm_default():
    from swe_rebench_pr.php_build import _normalize_php_version

    assert _normalize_php_version("8.0") == "8.2-cli-bookworm"
    assert _normalize_php_version("8.2") == "8.2-cli-bookworm"


def test_rust_body_falls_back_to_full_cargo_when_targets_mixed():
    from swe_rebench_pr.docker_entry import _rust_body

    body = _rust_body(
        {},
        False,
        repo_dir="/testbed",
        skip_install=True,
        harness_env_only=True,
    )
    assert "base + test_patch only" in body
    assert "test_patch + impl.patch" in body
    assert "use_scoped=0" in body
    assert "tests/[^/]+\\.rs" in body
    assert 'cargo test --no-fail-fast "${CARGO_FEAT_ARGS[@]}"' in body


def test_rust_body_includes_cargo_features():
    from swe_rebench_pr.docker_entry import _rust_body

    body = _rust_body({"cargo_features": ["fancy"]}, False, repo_dir="/testbed")
    assert "CARGO_FEAT_ARGS=(--features fancy)" in body


def test_resolve_cargo_features_from_integration_test_cfg(tmp_path: Path):
    from swe_rebench_pr.rust_build import resolve_cargo_features, rust_install_config_for_repo

    (tmp_path / "Cargo.toml").write_text(
        """
[features]
fancy-no-backtrace = ["owo-colors"]
fancy = ["fancy-no-backtrace", "backtrace"]
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "graphical.rs").write_text(
        '#![cfg(feature = "fancy-no-backtrace")]\nfn main() {}\n',
        encoding="utf-8",
    )
    assert resolve_cargo_features(tmp_path, ["tests/graphical.rs"]) == ["fancy"]
    cfg = rust_install_config_for_repo(tmp_path, targets=["tests/graphical.rs"])
    assert cfg["cargo_features"] == ["fancy"]
    assert "--features fancy" in cfg["test_cmd"]
    assert "--features fancy" in cfg["install"]


def test_resolve_cargo_features_from_ci_matrix(tmp_path: Path):
    from swe_rebench_pr.rust_build import resolve_cargo_features

    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        """
jobs:
  test:
    strategy:
      matrix:
        features: [fancy, syntect-highlighter]
    steps:
      - run: cargo test --all --features ${{matrix.features}}
""".strip(),
        encoding="utf-8",
    )
    assert resolve_cargo_features(tmp_path, []) == ["fancy"]


def test_ruby_normalize_maps_3_1_0_to_minor_tag():
    from swe_rebench_pr.ruby_build import _normalize_ruby_version

    assert _normalize_ruby_version("3.1.0") == "3.1-bookworm"
    assert _normalize_ruby_version("3.4.8") == "3.4.8-bookworm"


def test_export_install_config_includes_ruby_rust_php_defaults():
    for lang, default, key in (
        ("ruby", DEFAULT_RUBY_VERSION, "ruby_version"),
        ("rust", DEFAULT_RUST_VERSION, "rust_version"),
        ("php", DEFAULT_PHP_VERSION, "php_version"),
    ):
        out = export_install_config_for_harness({"language": lang, "install": "true"}, language=lang)
        assert out["docker_specs"][key] == default
