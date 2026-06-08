from swe_rebench_pr.diff_split import junit_outcome_counts_for_paths
from swe_rebench_pr.python_build import (
    expand_pytest_discover_targets,
    pytest_junit_nodeid_in_test_patch_paths,
    python_docker_test_cmd_for_entry,
)


def test_pytest_module_classname_matches_test_patch_path():
    paths = ["tests/test_others.py"]
    assert pytest_junit_nodeid_in_test_patch_paths(
        "tests.test_others::test_install_invalid_shell", paths
    )
    assert pytest_junit_nodeid_in_test_patch_paths(
        "tests/test_others.py::test_install_invalid_shell", paths
    )


def test_pytest_junit_counts_with_module_classname():
    case_map = {
        "tests.test_others::test_install_invalid_shell": "passed",
        "tests/unrelated.py::test_x": "passed",
    }
    pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(
        case_map, ["tests/test_others.py"], language="python"
    )
    assert tot == 1
    assert pa == 1


def test_expand_pytest_discover_targets_adds_tests_dir():
    paths = ["tests/test_a.py", "tests/test_b.py"]
    expanded = expand_pytest_discover_targets(paths)
    assert "tests" in expanded
    assert "tests/test_a.py" in expanded


def test_python_docker_test_cmd_injects_junit_placeholder():
    cmd = python_docker_test_cmd_for_entry({"test_cmd": "pytest tests -q"})
    assert "__JUNIT_OUT__" in cmd
