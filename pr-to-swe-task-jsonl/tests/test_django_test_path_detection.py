from swe_rebench_pr.diff_split import split_impl_and_test_patch
from swe_rebench_pr.languages import collect_test_targets, is_test_path, get_language_spec


def test_heuristic_splits_django_tests_prefix():
    diff = (
        "diff --git a/tests/admin_scripts/tests.py b/tests/admin_scripts/tests.py\n"
        "--- a/tests/admin_scripts/tests.py\n"
        "+++ b/tests/admin_scripts/tests.py\n"
        "@@ -1 +1 @@\n"
        "+pass\n"
    )
    impl, test = split_impl_and_test_patch(diff, repo_id="django/django")
    assert impl.count("diff --git") == 0
    assert test.count("diff --git") == 1


def test_collect_test_targets_finds_tests_prefix():
    patch = (
        "diff --git a/django/foo.py b/django/foo.py\n"
        "diff --git a/tests/admin_scripts/tests.py b/tests/admin_scripts/tests.py\n"
    )
    paths = collect_test_targets("python", patch, "")
    assert "tests/admin_scripts/tests.py" in paths
    assert is_test_path("tests/admin_scripts/tests.py", get_language_spec("python"))
