from swe_rebench_pr.diff_split import has_test_patch_label_mismatch


def test_django_mismatch_when_keys_are_descriptions():
    case_map = {
        "The command outputs URL patterns.": "PASSED",
        "test_foo (admin_scripts.test_listurls.FooTests.test_foo)": "PASSED",
    }
    paths = ["tests/admin_scripts/test_listurls.py"]
    assert has_test_patch_label_mismatch(case_map, paths, django_runtests=True) is False
    assert (
        has_test_patch_label_mismatch(
            {"The command outputs URL patterns.": "PASSED"},
            paths,
            django_runtests=True,
        )
        is True
    )


def test_junit_mismatch_by_file_path():
    case_map = {"tests/foo.py::test_bar": "passed"}
    assert has_test_patch_label_mismatch(case_map, ["tests/other/test_x.py"]) is True
    assert has_test_patch_label_mismatch(case_map, ["tests/foo.py"]) is False
