"""Discovery failure taxonomy, patch apply vs install, PHP JUnit matching."""

import inspect

from swe_rebench_pr.docker_discover import _docker_install_failed, merge_java_build_into_config
from swe_rebench_pr.harness_guards import (
    docker_failure_class,
    log_indicates_gradle_build_failed_during_tests,
    log_indicates_gradle_no_tests_found_for_includes,
    log_indicates_patch_apply_failed,
    needs_junit_fix_reason,
)
from swe_rebench_pr.php_build import php_junit_nodeid_in_test_patch_paths


def test_merge_java_build_available_from_docker_discover_module():
    assert callable(merge_java_build_into_config)


def test_discover_function_has_no_inner_merge_java_import():
    from swe_rebench_pr import docker_discover as mod

    src = inspect.getsource(mod.discover_fail_to_pass_pass_to_pass_docker)
    assert src.count("from .java_build import merge_java_build_into_config") == 0


def test_patch_apply_failed_not_install_failure_go():
    log = "[docker] patch apply check failed: /w/impl.patch\nerror: patch does not apply"
    assert log_indicates_patch_apply_failed(log)
    assert (
        _docker_install_failed(
            docker_exit=1,
            n_patch=0,
            n_targets=3,
            log_tail=log,
            install_config={"language": "go"},
            lang="go",
        )
        is False
    )


def test_patch_apply_failed_not_install_failure_python_native():
    assert (
        _docker_install_failed(
            docker_exit=1,
            n_patch=0,
            n_targets=6,
            log_tail="[docker] patch apply failed: /w/test.patch",
            django_runtests=False,
            install_config={"language": "python", "native_integration_build": True},
            lang="python",
        )
        is False
    )


def test_docker_failure_class_patch_phase_empty():
    assert (
        docker_failure_class(
            docker_exit=0,
            log_tail="[docker] phpunit (test_patch + impl.patch)",
            lang="php",
            install_failed=False,
            after_patch_empty=True,
            n_base=56,
            n_patch=0,
        )
        == "patch_phase_empty"
    )


def test_docker_failure_class_patch_apply():
    assert (
        docker_failure_class(
            docker_exit=1,
            log_tail="patch apply check failed: /w/impl.patch",
            lang="go",
            install_failed=True,
        )
        == "patch_apply_failed"
    )


def test_needs_junit_fix_reason_php_patch_phase():
    reason = needs_junit_fix_reason("php", n_base=56, n_patch=0)
    assert "patch phase empty" in reason
    assert "composer install" not in reason.lower() or "not composer" in reason


def test_php_junit_nodeid_matches_test_file():
    paths = ["tests/Composer/Package/VersionValidatorTest.php"]
    assert php_junit_nodeid_in_test_patch_paths(
        "tests/Composer/Package/VersionValidatorTest.php::testItWorks",
        paths,
    )
    assert php_junit_nodeid_in_test_patch_paths(
        "Composer\\Package\\VersionValidatorTest::testItWorks",
        paths,
    )


def test_gradle_build_failed_exit_zero_not_install_failure():
    log = """
[docker] applying patch test.patch
[docker] gradle test (test_patch + impl.patch)
FAILURE: Build failed with an exception.
* What went wrong:
BUILD FAILED in 13s
"""
    cfg = {"java_build_system": "gradle", "language": "java"}
    assert (
        _docker_install_failed(
            docker_exit=0,
            n_patch=0,
            n_targets=1,
            log_tail=log,
            install_config=cfg,
            lang="java",
        )
        is False
    )


def test_docker_failure_class_gradle_build_failed_over_patch_phase_empty():
    log = """
[docker] applying patch test.patch
[docker] gradle test (test_patch + impl.patch)
BUILD FAILED in 13s
"""
    assert (
        docker_failure_class(
            docker_exit=0,
            log_tail=log,
            lang="java",
            install_failed=True,
            after_patch_empty=True,
            n_base=1,
            n_patch=0,
        )
        == "build_failed"
    )


def test_gradle_no_tests_found_is_test_target_not_build_failed():
    log = """
[docker] applying patch test.patch
[docker] gradle test (test_patch + impl.patch)
> Task :picocli-tests-java9plus:test FAILED
No tests found for given includes: [picocli.AutoCompleteTest]
BUILD FAILED in 8s
"""
    assert log_indicates_gradle_no_tests_found_for_includes(log)
    assert not log_indicates_gradle_build_failed_during_tests(log)
    assert (
        docker_failure_class(
            docker_exit=0,
            log_tail=log,
            lang="java",
            install_failed=True,
            after_patch_empty=True,
            n_base=21,
            n_patch=0,
        )
        == "test_target_failed"
    )
    reason = needs_junit_fix_reason("java", log_tail=log)
    assert "module" in reason.lower() or "fqcn" in reason.lower()
    assert "install" in reason.lower()


def test_gradle_subproject_no_tests_ignored_when_patch_junit_harvested():
    """Root :test passed and JUnit harvested — subproject bleed is not a mismatch."""
    log = """
[docker] gradle test (test_patch + impl.patch)
> Task :test
picocli.AutoCompleteTest > basicFish PASSED
> Task :picocli-codegen:test FAILED
No tests found for given includes: [picocli.AutoCompleteTest]
BUILD FAILED in 4m
"""
    assert not log_indicates_gradle_no_tests_found_for_includes(log, n_patch=41)
    assert not log_indicates_gradle_no_tests_found_for_includes(log)


def test_gradle_no_tests_found_not_install_failure():
    log = """
[docker] gradle test (test_patch + impl.patch)
No tests found for given includes: [picocli.FooTest]
BUILD SUCCESSFUL
"""
    cfg = {"java_build_system": "gradle", "language": "java"}
    assert (
        _docker_install_failed(
            docker_exit=0,
            n_patch=0,
            n_targets=1,
            log_tail=log,
            install_config=cfg,
            lang="java",
        )
        is False
    )


def test_runtime_deps_restore_shell_ruby():
    from swe_rebench_pr.runtime_deps import runtime_deps_restore_shell

    sh = runtime_deps_restore_shell("ruby", {"install": "bundle install || true"})
    assert "bundle check" in sh
    assert "_restore_runtime_deps_if_needed" in sh


def test_ruby_docker_body_runs_post_patch_bundle_install():
    from swe_rebench_pr.docker_entry import _ruby_body

    body = _ruby_body(
        False,
        {"install": "bundle install --jobs 4", "test_cmd": "bundle exec rspec"},
        repo_dir="/testbed",
    )
    assert "_ruby_post_patch_bundle_install" in body
    assert "bundle check" in body
