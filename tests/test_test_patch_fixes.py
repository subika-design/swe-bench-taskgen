from pathlib import Path

from swe_rebench_pr.test_patch_fixes import (
    build_failure_source_context,
    is_pytest_argv_child_args_mismatch,
    pytest_argv_mismatch_hint,
)


def test_is_pytest_argv_mismatch():
    msg = (
        "AssertionError: Lists differ: ['-m', 'pytest', 'runserver'] "
        "!= ['/w/repo/tests/utils_tests/test_autoreload.py', 'runserver']"
    )
    assert is_pytest_argv_child_args_mismatch(msg)


def test_pytest_argv_hint():
    failures = [
        (
            "tests/utils_tests/test_autoreload.py::TestChildArguments::test_xoptions",
            "AssertionError: Lists differ: '-m' 'pytest'",
        )
    ]
    hint = pytest_argv_mismatch_hint(failures)
    assert "__main__.__spec__" in hint


def test_build_failure_source_context(tmp_path: Path):
    rel = "tests/foo.py"
    f = tmp_path / rel
    f.parent.mkdir(parents=True)
    f.write_text(
        "class T:\n    def test_bar(self):\n        assert 1 == 2\n",
        encoding="utf-8",
    )
    ctx = build_failure_source_context(
        tmp_path,
        [("tests/foo.py::T::test_bar", "AssertionError")],
        [],
    )
    assert "test_bar" in ctx
    assert "def test_bar" in ctx
