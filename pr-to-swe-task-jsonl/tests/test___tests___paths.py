"""Regression tests for ``__tests__/`` layout (e.g. isomorphic-git)."""

from unittest.mock import patch

from swe_rebench_pr.diff_split import (
    collect_heuristic_test_paths_from_patch,
    split_impl_and_test_patch,
)


def test_split_puts___tests___in_test_patch():
    diff = (
        "diff --git a/src/api/status.js b/src/api/status.js\n"
        "--- a/src/api/status.js\n+++ b/src/api/status.js\n"
        "diff --git a/__tests__/test-status.js b/__tests__/test-status.js\n"
        "--- a/__tests__/test-status.js\n+++ b/__tests__/test-status.js\n"
    )
    impl, test = split_impl_and_test_patch(diff, llm=None)
    assert "__tests__/test-status.js" in test
    assert "src/api/status.js" in impl
    assert collect_heuristic_test_paths_from_patch(test) == ["__tests__/test-status.js"]


def test_llm_both_impl_labels_still_route___tests___to_test_patch():
    diff = "diff --git a/__tests__/test-status.js b/__tests__/test-status.js\n---\n+++ b\n"
    roles = {"__tests__/test-status.js": "impl"}
    with patch("swe_rebench_pr.diff_split._llm_classify_patch_paths", return_value=roles):
        impl, test = split_impl_and_test_patch(diff, repo_id="x", llm=("k", "u", "m", 30))
    assert "__tests__/test-status.js" in test
    assert "__tests__" not in impl
