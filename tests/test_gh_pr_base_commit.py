from unittest.mock import patch

from swe_rebench_pr.gh_pr import ParsedPR, _fetch_base_commit_sha


def test_fetch_base_commit_uses_merge_parent_when_merged():
    pr = ParsedPR("fastapi", "typer", 1)
    with patch("swe_rebench_pr.gh_pr._fetch_pull_refs") as refs:
        refs.return_value = ("base_tip", "head", "merge_abc", True)
        with patch("swe_rebench_pr.gh_pr._fetch_merge_commit_first_parent") as parent:
            parent.return_value = "parent_sha"
            assert _fetch_base_commit_sha(pr) == "parent_sha"
            parent.assert_called_once_with(pr, "merge_abc")


def test_fetch_base_commit_uses_compare_merge_base_when_open():
    pr = ParsedPR("fastapi", "typer", 2)
    with patch("swe_rebench_pr.gh_pr._fetch_pull_refs") as refs:
        refs.return_value = ("base_tip", "head_sha", None, False)
        with patch("swe_rebench_pr.gh_pr._fetch_compare_merge_base") as mb:
            mb.return_value = "merge_base_sha"
            assert _fetch_base_commit_sha(pr) == "merge_base_sha"
            mb.assert_called_once_with(pr, "base_tip", "head_sha")
