"""Stacked patch apply-check and base-aligned diff helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from swe_rebench_pr.patch_validate import (
    PatchSplitUnrecoverableError,
    _summarize_git_apply_error,
    build_patches_from_git_at_base,
    diff_at_base,
    ensure_patch_commits_fetched,
    ensure_patches_for_base,
    recover_test_patch_paths_from_git,
    strip_mailbox_to_unified,
    validate_git_patch_stack,
)


def _init_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "foo.txt").write_text("base\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_x():\n    assert 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    (repo / "foo.txt").write_text("head\n", encoding="utf-8")
    (repo / "tests" / "test_a.py").write_text(
        "def test_x():\n    assert 2\n\ndef test_y():\n    assert 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "head"], cwd=repo, check=True, capture_output=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    subprocess.run(["git", "checkout", base], cwd=repo, check=True, capture_output=True)
    return repo, base, head


def test_diff_at_base_matches_git_diff(tmp_path: Path):
    repo, base, head = _init_repo(tmp_path)
    raw = diff_at_base(repo, base, head)
    assert "foo.txt" in raw
    assert "test_a.py" in raw


def test_validate_git_patch_stack_test_then_impl(tmp_path: Path):
    repo, base, head = _init_repo(tmp_path)
    raw = diff_at_base(repo, base, head)
    test_patch = (
        "diff --git a/tests/test_a.py b/tests/test_a.py\n"
        "--- a/tests/test_a.py\n"
        "+++ b/tests/test_a.py\n"
        "@@ -1,2 +1,5 @@\n"
        " def test_x():\n"
        "-    assert 1\n"
        "+    assert 2\n"
        "+\n"
        "+def test_y():\n"
        "+    assert 1\n"
    )
    impl_patch = (
        "diff --git a/foo.txt b/foo.txt\n"
        "--- a/foo.txt\n"
        "+++ b/foo.txt\n"
        "@@ -1 +1 @@\n"
        "-base\n"
        "+head\n"
    )
    ok, err = validate_git_patch_stack(test_patch, impl_patch, repo)
    assert ok, err


def test_recover_test_patch_paths_from_git_per_file(tmp_path: Path):
    repo, base, head = _init_repo(tmp_path)
    recovered = recover_test_patch_paths_from_git(
        repo, base, head, ["tests/test_a.py"]
    )
    assert recovered is not None
    assert "test_y" in recovered


def test_ensure_patch_commits_fetched_after_shallow_clone(tmp_path: Path):
    repo, base, head = _init_repo(tmp_path)
    subprocess.run(["git", "checkout", base], cwd=repo, check=True, capture_output=True)
    assert ensure_patch_commits_fetched(repo, base, head)


def test_per_file_test_patch_subset_applied_paths(tmp_path: Path):
    from swe_rebench_pr.patch_validate import _per_file_test_patch

    repo, base, head = _init_repo(tmp_path)
    body, applied = _per_file_test_patch(
        repo,
        base,
        head,
        ["tests/test_a.py", "does_not_exist.py"],
        verify_apply=True,
    )
    assert body is not None
    assert applied == ["tests/test_a.py"]


def test_summarize_git_apply_error_prefers_failure_line():
    raw = (
        "Applied patch to '.github/workflows/test.yml' cleanly.\n"
        "error: patch failed: command.go:42\n"
        "error: command.go: patch does not apply\n"
    )
    assert "command.go" in _summarize_git_apply_error(raw)
    assert "cleanly" not in _summarize_git_apply_error(raw)


def test_build_patches_excludes_infra_from_impl(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    wf = repo / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "test.yml").write_text("on: push\n", encoding="utf-8")
    (repo / "command.go").write_text("package main\n", encoding="utf-8")
    (repo / "command_test.go").write_text("package main\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    base = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    (wf / "test.yml").write_text("on: pull_request\n", encoding="utf-8")
    (repo / "command.go").write_text("package main\n// impl\n", encoding="utf-8")
    (repo / "command_test.go").write_text("package main\n// test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "head"], cwd=repo, check=True, capture_output=True)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    subprocess.run(["git", "checkout", base], cwd=repo, check=True, capture_output=True)

    built, err = build_patches_from_git_at_base(
        repo, base_commit=base, head_sha=head, language="go"
    )
    assert err == "", err
    assert built is not None
    impl, test = built
    assert "command_test.go" in test
    assert "command.go" in impl
    assert ".github/workflows/test.yml" not in impl
    ok, stack_err = validate_git_patch_stack(test, impl, repo)
    assert ok, stack_err


def test_ensure_patches_for_base_recovers_from_git_diff(tmp_path: Path):
    from swe_rebench_pr.gh_pr import ParsedPR

    repo, base, head = _init_repo(tmp_path)
    pr = ParsedPR("o", "r", 1)
    corrupt = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n+b\n"
    impl, test = ensure_patches_for_base(
        pr,
        repo,
        base_commit=base,
        head_sha=head,
        patch=corrupt,
        test_patch=corrupt,
        diff="",
        llm_split_used=False,
    )
    body = strip_mailbox_to_unified(test)
    assert "test_a.py" in body or "test_y" in body
