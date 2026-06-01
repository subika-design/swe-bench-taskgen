from swe_rebench_pr.docker_entry import _common_header


def test_java_harness_header_skips_conda():
    hdr = _common_header(repo_dir="/testbed", skip_install=True, harness_conda=False)
    assert "miniconda3" not in hdr
    assert "bash /w/project_install.sh" in hdr
    assert "pip_packages.sh" not in hdr


def test_python_harness_header_activates_conda():
    hdr = _common_header(repo_dir="/testbed", skip_install=True, harness_conda=True)
    assert "conda activate testbed" in hdr
    assert "pip_packages.sh" in hdr


def test_env_only_header_clones_then_installs():
    hdr = _common_header(
        repo_dir="/testbed",
        skip_install=True,
        harness_conda=True,
        harness_env_only=True,
    )
    assert "cd /\nbash /w/setup_repo.sh" in hdr
    assert "git reset --hard HEAD" not in hdr
    assert "bash /w/post_install.sh" in hdr


def test_tests_only_header_skips_clone_and_install():
    hdr = _common_header(
        repo_dir="/testbed",
        skip_install=True,
        harness_conda=True,
        harness_env_only=True,
        tests_only=True,
    )
    assert "setup_repo.sh" not in hdr
    assert "project_install.sh" not in hdr
    assert "SWEBENCH_BASE_COMMIT" in hdr
    assert "conda activate testbed" in hdr
