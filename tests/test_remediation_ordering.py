"""Tests for install_config remediation change detection."""

from swe_rebench_pr.java_build import (
    install_config_substantive_change,
    log_indicates_git_clone_failure,
)


def test_substantive_change_on_install():
    before = {"install": "pip install -e .", "apt-pkgs": []}
    after = {"install": 'pip install -e ".[test]"', "apt-pkgs": []}
    assert install_config_substantive_change(before, after)


def test_apt_only_not_substantive():
    before = {"install": "pip install -e .", "apt-pkgs": [], "pre_install": []}
    after = {
        "install": "pip install -e .",
        "apt-pkgs": ["libssl-dev"],
        "pre_install": ["apt-get update -qq"],
    }
    assert not install_config_substantive_change(before, after)


def test_pip_packages_change_is_substantive():
    before = {"install": "pip install -e .", "pip_packages": []}
    after = {"install": "pip install -e .", "pip_packages": ["pytest>=9"]}
    assert install_config_substantive_change(before, after)


def test_log_indicates_git_clone_failure():
    assert log_indicates_git_clone_failure("", docker_exit=128)
    assert log_indicates_git_clone_failure("fatal: Could not parse object 'abc'", docker_exit=0)
