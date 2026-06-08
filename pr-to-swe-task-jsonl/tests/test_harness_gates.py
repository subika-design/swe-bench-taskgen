"""Tests for Python runnable-test gate and patch hunk filtering."""

from pathlib import Path

from swe_rebench_pr.patch_paths import has_runnable_python_tests
from swe_rebench_pr.patch_validate import _filter_patch_to_applying_hunks, validate_git_patch


def test_has_runnable_python_tests_true():
    tp = "diff --git a/tests/test_foo.py b/tests/test_foo.py\n"
    assert has_runnable_python_tests(tp, "python")


def test_has_runnable_python_tests_false_for_docs():
    tp = "diff --git a/docs/guide.md b/docs/guide.md\n"
    assert not has_runnable_python_tests(tp, "python")


def test_filter_patch_to_applying_hunks(tmp_path: Path):
    from swe_rebench_pr.diff_split import _iter_diff_chunks
    from swe_rebench_pr.gh_pr import strip_mailbox_to_unified

    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "feature.rs"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    good_patch = (
        "diff --git a/feature.rs b/feature.rs\n"
        "index 1111111..2222222 100644\n"
        "--- a/feature.rs\n"
        "+++ b/feature.rs\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+changed\n"
        " line3\n"
        "@@ -99,3 +99,3 @@\n"
        " phantom\n"
        "-nope\n"
        "+bad\n"
        " end\n"
    )
    # One file chunk: first hunk applies at base, second does not.
    chunks = [ch for _a, _b, ch in _iter_diff_chunks(strip_mailbox_to_unified(good_patch))]
    assert len(chunks) == 1
    filtered = _filter_patch_to_applying_hunks(good_patch, repo)
    ok, _ = validate_git_patch(filtered, repo)
    assert ok
    assert "changed" in filtered
    assert "phantom" not in filtered
