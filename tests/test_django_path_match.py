from pathlib import Path

from swe_rebench_pr.diff_split import (
    _classname_to_pytest_prefix,
    _nodeid_in_test_patch_paths,
    _path_filter_sets,
    junit_outcome_counts_for_paths,
)


def test_path_filter_accepts_django_junit_head():
    tp = ["tests/view_tests/tests/test_json.py"]
    path_set, dotted, _java = _path_filter_sets(tp)
    assert _nodeid_in_test_patch_paths(
        "view_tests/tests/test_json.py::TestCase::test_foo",
        path_set,
        dotted,
    )
    assert _nodeid_in_test_patch_paths(
        "tests/view_tests/tests/test_json.py::TestCase::test_foo",
        path_set,
        dotted,
    )


def test_junit_outcome_counts_with_django_alias():
    case_map = {
        "view_tests/tests/test_json.py::TestCase::test_foo": "passed",
        "other/tests/test_x.py::test_y": "passed",
    }
    pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(
        case_map, ["tests/view_tests/tests/test_json.py"]
    )
    assert tot == 1
    assert pa == 1
    assert fa == ea == sk == 0


def test_module_level_dotted_nodeid_matches_test_patch():
    tp = [
        "tests/admin_views/test_autocomplete_view.py",
        "tests/view_tests/tests/test_i18n.py",
    ]
    path_set, dotted, _java = _path_filter_sets(tp)
    assert _nodeid_in_test_patch_paths(
        "tests.admin_views.test_autocomplete_view", path_set, dotted
    )
    assert _nodeid_in_test_patch_paths("tests.view_tests.tests.test_i18n", path_set, dotted)


def test_junit_counts_module_level_dotted_errors():
    case_map = {
        "tests.admin_views.test_autocomplete_view": "error",
        "tests.view_tests.tests.test_i18n": "error",
    }
    pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(
        case_map,
        [
            "tests/admin_views/test_autocomplete_view.py",
            "tests/view_tests/tests/test_i18n.py",
        ],
    )
    assert tot == 2
    assert ea == 2
    assert pa == fa == sk == 0


def test_classname_resolves_under_tests_prefix(tmp_path: Path):
    repo = tmp_path / "repo"
    py = repo / "tests" / "view_tests" / "tests" / "test_json.py"
    py.parent.mkdir(parents=True)
    py.write_text("def test_x(): pass\n", encoding="utf-8")
    rel, qual = _classname_to_pytest_prefix(
        "view_tests.tests.test_json.TestCase", repo
    )
    assert rel == "tests/view_tests/tests/test_json.py"
    assert qual == "TestCase"
