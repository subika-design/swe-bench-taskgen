"""Patch apply-check recovery for mailbox / LLM-split PR diffs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from swe_rebench_pr.gh_pr import ParsedPR, strip_mailbox_to_unified
from swe_rebench_pr.patch_validate import (
    ensure_valid_patch_split,
    recover_patches_heuristic,
    validate_git_patch,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PHP_FFMPEG_BASE = "368def99a2cb95e3c78775b0a8e05ad1b4895886"


def _clone_php_ffmpeg_at_base(tmp_path: Path) -> Path:
    repo = tmp_path / "php-ffmpeg"
    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/PHP-FFMpeg/PHP-FFMpeg.git", str(repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "fetch", "--depth", "1", "origin", PHP_FFMPEG_BASE],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "checkout", PHP_FFMPEG_BASE], cwd=repo, check=True, capture_output=True)
    return repo


def test_strip_mailbox_flattens_multi_commit_patch():
    raw = (FIXTURES / "php_ffmpeg_855_mailbox.patch").read_text(encoding="utf-8")
    flat = strip_mailbox_to_unified(raw)
    assert "From c97dae8" not in flat
    assert flat.count("diff --git") == raw.count("diff --git")


def test_recover_patches_heuristic_php_ffmpeg_855(tmp_path: Path):
    repo = _clone_php_ffmpeg_at_base(tmp_path)
    pr = ParsedPR("PHP-FFMpeg", "PHP-FFMpeg", 855)
    diff = strip_mailbox_to_unified((FIXTURES / "php_ffmpeg_855_mailbox.patch").read_text(encoding="utf-8"))
    corrupt = (
        "diff --git a/tests/FFMpeg/Unit/Media/ConcatTest.php "
        "b/tests/FFMpeg/Unit/Media/ConcatTest.php\n"
        "index 1111111..2222222 100644\n"
        "--- a/tests/FFMpeg/Unit/Media/ConcatTest.php\n"
        "+++ b/tests/FFMpeg/Unit/Media/ConcatTest.php\n"
        "@@ -1,999 +1,999 @@\n"
        "+// corrupt hunk\n"
    )
    ok, err = validate_git_patch(corrupt, repo)
    assert not ok

    recovered = recover_patches_heuristic(pr, repo, diff=diff)
    assert recovered is not None
    impl, test = recovered
    ok_test, _ = validate_git_patch(strip_mailbox_to_unified(test), repo)
    ok_impl, _ = validate_git_patch(strip_mailbox_to_unified(impl), repo)
    assert ok_test and ok_impl


def test_ensure_valid_patch_split_falls_back_from_corrupt_test_patch(tmp_path: Path):
    repo = _clone_php_ffmpeg_at_base(tmp_path)
    pr = ParsedPR("PHP-FFMpeg", "PHP-FFMpeg", 855)
    diff = strip_mailbox_to_unified((FIXTURES / "php_ffmpeg_855_api.diff").read_text(encoding="utf-8"))
    corrupt_test = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n+b\n"
    impl, test = ensure_valid_patch_split(
        pr,
        repo,
        diff,
        patch="",
        test_patch=corrupt_test,
        llm_split_used=True,
    )
    ok, _ = validate_git_patch(strip_mailbox_to_unified(test), repo)
    assert ok
    assert "tests/FFMpeg/Unit/Media" in test


def test_fetch_pr_diff_prefers_flat_api_diff():
    from swe_rebench_pr.gh_pr import fetch_pr_diff

    api_body = (FIXTURES / "php_ffmpeg_855_api.diff").read_text(encoding="utf-8")
    mailbox_body = (FIXTURES / "php_ffmpeg_855_mailbox.patch").read_text(encoding="utf-8")

    with patch("swe_rebench_pr.gh_pr.run_gh") as mock_gh:
        mock_gh.side_effect = [api_body, mailbox_body]
        out = fetch_pr_diff("PHP-FFMpeg", "PHP-FFMpeg", 855)
    assert "From " not in out
    assert "diff --git a/tests/FFMpeg/Unit/Media/ConcatTest.php" in out
