from swe_rebench_pr.swebench_images import (
    install_config_to_harness_specs,
    language_to_harness_ext,
    row_to_swebench_instance,
)


def test_install_config_to_harness_specs_merges_post_install():
    ic = {
        "python": "3.11",
        "install": "pip install -e .",
        "test_cmd": "pytest -rA",
        "post_install": ["python -m pip install pytest"],
        "pip_packages": ["wheel"],
    }
    specs = install_config_to_harness_specs(ic)
    assert specs["python"] == "3.11"
    assert "pytest" in specs["install"]
    assert specs["test_cmd"] == "pytest -rA"
    assert "pip_packages" in specs


def test_language_to_harness_ext():
    assert language_to_harness_ext("python") == "py"
    assert language_to_harness_ext("javascript") == "js"


def test_row_to_swebench_instance_repo_slash():
    row = {
        "instance_id": "acme__widget-1",
        "repo": "acme/widget",
        "version": "0.0-deadbeef",
        "base_commit": "deadbeef",
        "patch": "",
        "test_patch": "",
        "FAIL_TO_PASS": "[]",
        "PASS_TO_PASS": "[]",
    }
    inst = row_to_swebench_instance(row)
    assert inst["repo"] == "acme/widget"
    assert inst["version"] == "0.0-deadbeef"
