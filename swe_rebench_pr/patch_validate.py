"""Validate unified diffs before Docker apply."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .gh_pr import strip_mailbox_to_unified


def validate_git_patch(patch: str, repo: Path, *, timeout_s: int = 60) -> tuple[bool, str]:
    """Return (ok, reason). Uses ``git apply --check`` on a clean tree."""
    body = strip_mailbox_to_unified(patch or "")
    if not body.strip():
        return False, "empty patch"
    if "diff --git" not in body:
        return False, "not a unified diff (missing diff --git)"
    patch_file = repo.parent / ".patch_validate.tmp"
    try:
        patch_file.write_text(body, encoding="utf-8")
        r = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn", str(patch_file)],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            first = err.splitlines()[0] if err else "git apply --check failed"
            return False, first[:500]
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "git apply --check timed out"
    finally:
        patch_file.unlink(missing_ok=True)


def recover_patches_heuristic(
    pr,
    repo: Path,
    *,
    diff: str | None = None,
) -> tuple[str, str] | None:
    """
    Re-split a PR diff with heuristics only when both patches pass ``git apply --check``.

    Re-fetches the diff when ``diff`` is omitted (e.g. LLM patch split produced a corrupt test_patch).
    """
    from .diff_split import split_impl_and_test_patch
    from .gh_pr import fetch_pr_diff

    source = diff if diff is not None else ""
    if not source.strip():
        try:
            source = fetch_pr_diff(pr.owner, pr.repo, pr.number)
        except RuntimeError:
            return None
    source = strip_mailbox_to_unified(source) or source
    impl, test = split_impl_and_test_patch(source, repo_id=pr.repo_id, llm=None)
    impl_body = strip_mailbox_to_unified(impl)
    test_body = strip_mailbox_to_unified(test)
    if not test_body.strip():
        return None
    ok_test, _ = validate_git_patch(test_body, repo)
    if not ok_test:
        return None
    if impl_body.strip():
        ok_impl, _ = validate_git_patch(impl_body, repo)
        if not ok_impl:
            return None
    return impl, test


def ensure_valid_patch_split(
    pr,
    repo: Path,
    diff: str,
    patch: str,
    test_patch: str,
    *,
    llm_split_used: bool,
) -> tuple[str, str]:
    """Validate patch split at base commit; fall back to heuristic-only re-split when needed."""
    test_body = strip_mailbox_to_unified(test_patch)
    impl_body = strip_mailbox_to_unified(patch)
    ok_test = not test_body.strip() or validate_git_patch(test_body, repo)[0]
    ok_impl = not impl_body.strip() or validate_git_patch(impl_body, repo)[0]
    if ok_test and ok_impl:
        return patch, test_patch

    recovered = recover_patches_heuristic(pr, repo, diff=diff)
    if recovered is not None:
        if llm_split_used:
            print(
                f"  {pr.instance_id}: LLM patch split failed apply-check; "
                f"recovered via heuristic re-split",
                file=sys.stderr,
            )
        return recovered

    if llm_split_used:
        print(
            f"  {pr.instance_id}: LLM patch split failed apply-check; heuristic-only re-split",
            file=sys.stderr,
        )
        from .diff_split import split_impl_and_test_patch

        return split_impl_and_test_patch(diff, repo_id=pr.repo_id, llm=None)
    return patch, test_patch
