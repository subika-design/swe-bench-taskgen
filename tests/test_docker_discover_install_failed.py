from swe_rebench_pr.docker_discover import _docker_install_failed


def test_c_empty_patch_junit_not_install_failure_when_docker_succeeds():
    assert (
        _docker_install_failed(
            docker_exit=0,
            n_patch=0,
            n_targets=1,
            log_tail="ctest run completed",
            django_runtests=False,
            install_config={"language": "c"},
            lang="c",
        )
        is False
    )


def test_c_nonzero_exit_still_install_failure():
    assert (
        _docker_install_failed(
            docker_exit=1,
            n_patch=0,
            n_targets=1,
            log_tail="cmake error",
            django_runtests=False,
            install_config={"language": "c"},
            lang="c",
        )
        is True
    )


def test_python_native_integration_exit1_with_pytest_not_install_failure():
    assert (
        _docker_install_failed(
            docker_exit=1,
            n_patch=0,
            n_targets=6,
            log_tail="[docker] integration pytest (6 path(s))\n[docker] reset to base_commit",
            django_runtests=False,
            install_config={"language": "python", "native_integration_build": True},
            lang="python",
        )
        is False
    )


def test_python_native_integration_exit1_patch_apply_still_install_failure():
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
        is True
    )


def test_python_empty_junit_without_native_flag_still_install_failure():
    assert (
        _docker_install_failed(
            docker_exit=0,
            n_patch=0,
            n_targets=3,
            log_tail="",
            django_runtests=False,
            install_config={"language": "python"},
            lang="python",
        )
        is True
    )


def test_ruby_empty_junit_not_install_failure_when_docker_succeeds():
    assert (
        _docker_install_failed(
            docker_exit=0,
            n_patch=0,
            n_targets=6,
            log_tail="[docker] rspec (test_patch + impl.patch)",
            django_runtests=False,
            install_config={"language": "ruby"},
            lang="ruby",
        )
        is False
    )


def test_ruby_nonzero_exit_still_install_failure():
    assert (
        _docker_install_failed(
            docker_exit=1,
            n_patch=0,
            n_targets=6,
            log_tail="bundle install failed",
            django_runtests=False,
            install_config={"language": "ruby"},
            lang="ruby",
        )
        is True
    )
