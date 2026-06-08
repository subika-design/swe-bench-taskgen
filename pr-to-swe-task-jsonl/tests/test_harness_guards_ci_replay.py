"""Tests for CI replay, harness guards, and install normalization."""

from pathlib import Path

from swe_rebench_pr.ci_extract import extract_ci_draft, merge_ci_draft_into_config
from swe_rebench_pr.ci_install_normalize import (
    compose_ci_install_sequence,
    normalize_ci_install_command,
)
from swe_rebench_pr.harness_guards import (
    extract_structured_failure_log,
    is_valid_java_test_cmd,
    is_valid_php_test_cmd,
    restore_test_cmd_if_invalid,
)
from swe_rebench_pr.java_build import merge_java_harness_fields_after_llm
from swe_rebench_pr.php_build import (
    PHP_BASE_IMAGE_EXTENSIONS,
    apply_self_hosting_composer_install,
    merge_php_harness_fields_after_llm,
    php_extensions_to_install,
    repo_has_bin_composer,
)


def test_normalize_gradlew_build_not_chmod_only():
    out = normalize_ci_install_command(
        "./gradlew --stop;./gradlew clean;./gradlew build --no-daemon",
        language="java",
    )
    assert "chmod +x ./gradlew" in out
    assert "./gradlew" in out
    assert "build" in out
    assert "-x check" in out
    assert out.count("chmod +x ./gradlew") == 1


def test_compose_ci_install_sequence_composer_two_step():
    steps = [
        "composer install $COMPOSER_FLAGS",
        "bin/composer install $COMPOSER_FLAGS",
    ]
    out = compose_ci_install_sequence(steps, language="php")
    assert "composer install" in out
    assert "bin/composer install" in out
    assert " && " in out


def test_extract_ci_draft_install_steps(tmp_path: Path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        """
      - uses: shivammathur/setup-php@v2
        with:
          extensions: "intl, zip"
          ini-values: "phar.readonly=0"
          tools: "composer:snapshot"
      - run: composer install $COMPOSER_FLAGS
      - run: bin/composer install $COMPOSER_FLAGS
      - run: vendor/bin/simple-phpunit --verbose
""",
        encoding="utf-8",
    )
    draft = extract_ci_draft(tmp_path)
    assert len(draft.install_steps) >= 2
    assert "intl" in draft.php_extensions
    assert draft.test_cmd and "simple-phpunit" in draft.test_cmd


def test_merge_ci_draft_composer_sequence(tmp_path: Path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "composer").write_text("#!/usr/bin/env php\n", encoding="utf-8")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        """
env:
  COMPOSER_FLAGS: "--ansi --no-interaction --no-progress --prefer-dist"
      - run: composer install $COMPOSER_FLAGS
      - run: bin/composer install $COMPOSER_FLAGS
""",
        encoding="utf-8",
    )
    draft = extract_ci_draft(tmp_path)
    merged = merge_ci_draft_into_config(
        {"language": "php", "install": "composer install", "test_cmd": "vendor/bin/phpunit"},
        draft,
        language="php",
    )
    assert "bin/composer install" in str(merged.get("install") or "")


def test_php_extensions_skip_base_image():
    missing = php_extensions_to_install(["zip", "intl"], extra_from_ci=["zip", "intl"])
    assert "zip" not in missing
    assert "intl" in missing
    assert "intl" not in PHP_BASE_IMAGE_EXTENSIONS


def test_harness_guard_rejects_garbage_java_test_cmd():
    before = {
        "language": "java",
        "java_build_system": "gradle",
        "test_cmd": "./gradlew :picocli:test --tests 'Foo'",
    }
    after = {
        "test_cmd": "mkdir -p gradle && echo aW1wb3J0",
    }
    out = merge_java_harness_fields_after_llm(before, after)
    assert "./gradlew" in out["test_cmd"]
    assert is_valid_java_test_cmd(out["test_cmd"], out)


def test_harness_guard_rejects_garbage_php_test_cmd():
    before = {"language": "php", "test_cmd": "vendor/bin/simple-phpunit --log-junit __JUNIT_OUT__"}
    after = {"test_cmd": "mkdir -p && echo foo"}
    out = merge_php_harness_fields_after_llm(before, after)
    assert "simple-phpunit" in out["test_cmd"]
    assert is_valid_php_test_cmd(out["test_cmd"])


def test_extract_structured_failure_log_gradle():
    log = """
[docker] gradle test (base + test_patch only)
FAILURE: Build failed with an exception.
What went wrong:
Execution failed for task ':picocli:compileJava'.
Caused by: org.gradle.api.internal.tasks.compile.CompilationFailedException
BUILD FAILED in 14s
"""
    excerpt = extract_structured_failure_log(log, language="java")
    assert "What went wrong" in excerpt or "BUILD FAILED" in excerpt


def test_self_hosting_composer_install(tmp_path: Path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "composer").write_text("#!/usr/bin/env php\n", encoding="utf-8")
    assert repo_has_bin_composer(tmp_path)
    cfg = apply_self_hosting_composer_install({"test_env": {}}, tmp_path)
    assert "composer install" in cfg["install"]
    assert "bin/composer install" in cfg["install"]


def test_restore_test_cmd_if_invalid():
    before = {"language": "java", "test_cmd": "./gradlew test"}
    after = {"test_cmd": "echo broken"}
    out = restore_test_cmd_if_invalid(before, after, language="java")
    assert out["test_cmd"] == "./gradlew test"
