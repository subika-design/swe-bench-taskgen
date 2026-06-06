"""Validate unified diffs before Docker apply."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .diff_split import _iter_diff_chunks
from .gh_pr import strip_mailbox_to_unified
from .patch_paths import (
    collect_gradable_test_paths_from_diff,
    collect_impl_paths_from_diff,
    is_non_test_infrastructure_path,
)


class PatchSplitUnrecoverableError(RuntimeError):
    """Raised when impl/test patches cannot apply at base (solo or stacked)."""


def _git_apply_flags(*, check_only: bool, three_way: bool) -> list[str]:
    flags = ["git", "apply", "--whitespace=nowarn"]
    if three_way:
        flags.append("-3")
    if check_only:
        flags.append("--check")
    return flags


def _summarize_git_apply_error(err: str) -> str:
    """Prefer real failure lines over leading 'Applied patch … cleanly' noise."""
    lines = [ln.strip() for ln in (err or "").splitlines() if ln.strip()]
    if not lines:
        return "git apply failed"
    for ln in lines:
        low = ln.lower()
        if low.startswith("error:") or "patch failed:" in low:
            return ln[:500]
    return lines[-1][:500]


def _run_git_apply(
    patch_file: Path,
    repo: Path,
    *,
    check_only: bool = False,
    three_way: bool = False,
    timeout_s: int = 120,
) -> tuple[bool, str]:
    flags = _git_apply_flags(check_only=check_only, three_way=three_way)
    flags.append(str(patch_file))
    try:
        r = subprocess.run(
            flags,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            return False, _summarize_git_apply_error(err)
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "git apply timed out"


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
        ok, err = _run_git_apply(
            patch_file, repo, check_only=True, three_way=False, timeout_s=timeout_s
        )
        if ok:
            return True, ""
        ok3, err3 = _run_git_apply(
            patch_file, repo, check_only=True, three_way=True, timeout_s=timeout_s
        )
        if ok3:
            return True, ""
        return False, err3 or err
    finally:
        patch_file.unlink(missing_ok=True)


def _git_reset_hard(repo: Path) -> None:
    subprocess.run(
        ["git", "reset", "--hard", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )
    subprocess.run(
        ["git", "clean", "-ffdx"],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )


def _git_apply(patch: str, repo: Path, *, check_only: bool = False) -> tuple[bool, str]:
    body = strip_mailbox_to_unified(patch or "")
    if not body.strip():
        return True, ""
    patch_file = repo.parent / ".patch_apply_stack.tmp"
    try:
        patch_file.write_text(body, encoding="utf-8")
        ok, err = _run_git_apply(patch_file, repo, check_only=check_only, three_way=False)
        if ok:
            return True, ""
        ok3, err3 = _run_git_apply(patch_file, repo, check_only=check_only, three_way=True)
        if ok3:
            return True, ""
        return False, err3 or err
    finally:
        patch_file.unlink(missing_ok=True)


def validate_git_patch_stack(
    test_patch: str,
    impl_patch: str,
    repo: Path,
) -> tuple[bool, str]:
    """
    Validate Docker two-phase apply order: ``test_patch`` then ``impl_patch``.

    Assumes *repo* is a clean checkout at ``base_commit``.
    """
    test_body = strip_mailbox_to_unified(test_patch or "")
    impl_body = strip_mailbox_to_unified(impl_patch or "")
    if not test_body.strip():
        return False, "empty test_patch"
    _git_reset_hard(repo)
    ok, err = _git_apply(test_body, repo, check_only=True)
    if not ok:
        return False, f"test_patch apply-check: {err}"
    ok, err = _git_apply(test_body, repo, check_only=False)
    if not ok:
        _git_reset_hard(repo)
        return False, f"test_patch apply: {err}"
    if impl_body.strip():
        ok, err = _git_apply(impl_body, repo, check_only=True)
        if not ok:
            _git_reset_hard(repo)
            return False, f"impl_patch apply-check after test_patch: {err}"
        ok, err = _git_apply(impl_body, repo, check_only=False)
        if not ok:
            _git_reset_hard(repo)
            return False, f"impl_patch apply after test_patch: {err}"
    _git_reset_hard(repo)
    return True, ""


def ensure_patch_commits_fetched(
    repo: Path,
    base_commit: str,
    head_sha: str,
    *,
    timeout_s: int = 120,
) -> bool:
    """Ensure *base_commit* and *head_sha* resolve for per-file patch diffs."""
    base = base_commit.strip()
    head = head_sha.strip()
    if not base or not head:
        return False

    def _has_commit(sha: str) -> bool:
        r = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(repo),
            capture_output=True,
            timeout=timeout_s,
        )
        return r.returncode == 0

    for sha in (base, head):
        if _has_commit(sha):
            continue
        subprocess.run(
            ["git", "fetch", "origin", sha, "--depth=1"],
            cwd=str(repo),
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        if not _has_commit(sha):
            subprocess.run(
                ["git", "fetch", "origin", sha],
                cwd=str(repo),
                capture_output=True,
                timeout=timeout_s,
                check=False,
            )
    return _has_commit(base) and _has_commit(head)


def diff_at_base(repo: Path, base_commit: str, head_sha: str) -> str:
    """``git diff base head`` — patches aligned with harness ``base_commit``."""
    if not base_commit.strip() or not head_sha.strip():
        return ""
    ensure_patch_commits_fetched(repo, base_commit, head_sha)
    base = base_commit.strip()
    head = head_sha.strip()
    r = subprocess.run(
        ["git", "diff", base, head],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return r.stdout if r.returncode == 0 else ""


def _git_diff_file(
    repo: Path,
    base_commit: str,
    head_sha: str,
    rel_path: str,
) -> str:
    rel = rel_path.replace("\\", "/").strip().lstrip("/")
    if not rel:
        return ""
    r = subprocess.run(
        [
            "git",
            "diff",
            base_commit.strip(),
            head_sha.strip(),
            "--",
            rel,
        ],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if r.returncode == 0 and r.stdout.strip() and "diff --git" in r.stdout:
        return r.stdout
    return ""


def _impl_patch_from_raw_excluding(
    raw: str,
    exclude_paths: set[str],
) -> str:
    chunks: list[str] = []
    for path_a, path_b, ch in _iter_diff_chunks(raw):
        if path_a in exclude_paths or path_b in exclude_paths:
            continue
        if is_non_test_infrastructure_path(path_a) or is_non_test_infrastructure_path(
            path_b
        ):
            continue
        chunks.append(ch)
    return "".join(chunks)


def _split_diff_into_hunk_patches(chunk: str) -> list[str]:
    """Split a single-file unified diff into one mini-patch per ``@@`` hunk."""
    if not chunk.strip():
        return []
    lines = chunk.splitlines(keepends=True)
    header: list[str] = []
    i = 0
    while i < len(lines) and not lines[i].startswith("@@"):
        header.append(lines[i])
        i += 1
    if i >= len(lines):
        return [chunk]
    hunks: list[str] = []
    current: list[str] = []
    for line in lines[i:]:
        if line.startswith("@@") and current:
            hunks.append("".join(header + current))
            current = [line]
        else:
            current.append(line)
    if current:
        hunks.append("".join(header + current))
    return hunks or [chunk]


def _merge_hunk_patches(mini_patches: list[str]) -> str:
    """Combine applying per-hunk mini-patches under one ``diff --git`` header."""
    if not mini_patches:
        return ""
    if len(mini_patches) == 1:
        return mini_patches[0]
    first = mini_patches[0]
    lines = first.splitlines(keepends=True)
    header_end = 0
    for i, ln in enumerate(lines):
        if ln.startswith("@@"):
            header_end = i
            break
    header = lines[:header_end]
    body_parts: list[str] = []
    for mp in mini_patches:
        started = False
        for ln in mp.splitlines(keepends=True):
            if ln.startswith("@@"):
                started = True
            if started:
                body_parts.append(ln)
    return "".join(header + body_parts)


def _filter_patch_to_applying_hunks(patch: str, repo: Path) -> str:
    """Keep only ``@@`` hunks from *patch* that pass solo ``git apply --check`` at base."""
    body = strip_mailbox_to_unified(patch or "")
    if not body.strip() or "diff --git" not in body:
        return body
    out_chunks: list[str] = []
    for _pa, _pb, chunk in _iter_diff_chunks(body):
        if not chunk.strip():
            continue
        mini_hunks = _split_diff_into_hunk_patches(chunk)
        if len(mini_hunks) <= 1:
            ok, _ = validate_git_patch(chunk, repo)
            if ok:
                out_chunks.append(chunk)
            continue
        applying: list[str] = []
        for mini in mini_hunks:
            ok, _ = validate_git_patch(mini, repo)
            if ok:
                applying.append(mini)
        if not applying:
            continue
        if len(applying) == len(mini_hunks):
            out_chunks.append(chunk)
            continue
        merged = _merge_hunk_patches(applying)
        if merged.strip():
            ok, _ = validate_git_patch(merged, repo)
            if ok:
                out_chunks.append(merged)
    return "".join(out_chunks)


def _per_file_patch(
    repo: Path,
    base_commit: str,
    head_sha: str,
    paths: list[str],
    *,
    verify_apply: bool = True,
) -> tuple[str, list[str]]:
    """Per-file ``git diff`` hunks that pass solo apply-check."""
    chunks: list[str] = []
    applied: list[str] = []
    for raw in paths:
        rel = raw.replace("\\", "/").strip().lstrip("/")
        if not rel:
            continue
        body = _git_diff_file(repo, base_commit, head_sha, rel)
        if not body:
            continue
        if verify_apply:
            ok, _ = validate_git_patch(body, repo)
            if not ok:
                recovered = _recover_single_file_patch(repo, base_commit, head_sha, rel)
                if recovered:
                    body = recovered
                    ok, _ = validate_git_patch(body, repo)
            if not ok and body.strip():
                filtered = _filter_patch_to_applying_hunks(body, repo)
                if filtered.strip():
                    body = filtered
                    ok, _ = validate_git_patch(body, repo)
            if not body.strip() or not ok:
                continue
        chunks.append(body)
        applied.append(rel)
    return "".join(chunks), applied


def _per_file_test_patch(
    repo: Path,
    base_commit: str,
    head_sha: str,
    test_paths: list[str],
    *,
    verify_apply: bool = True,
) -> tuple[str | None, list[str]]:
    """Build ``test_patch`` from per-file ``git diff base head -- path``.

    Returns ``(merged_patch, applied_paths)`` — only hunks that pass solo apply-check.
    """
    if not test_paths:
        return None, []
    merged, applied = _per_file_patch(
        repo, base_commit, head_sha, test_paths, verify_apply=verify_apply
    )
    if not merged.strip():
        return None, []
    return merged, applied


def _per_file_impl_patch(
    repo: Path,
    base_commit: str,
    head_sha: str,
    impl_paths: list[str],
    *,
    verify_apply: bool = True,
) -> tuple[str, list[str]]:
    """Build ``impl_patch`` from per-file diffs (subset of applying source paths)."""
    if not impl_paths:
        return "", []
    return _per_file_patch(
        repo, base_commit, head_sha, impl_paths, verify_apply=verify_apply
    )


def _recover_single_file_patch(
    repo: Path,
    base_commit: str,
    head_sha: str,
    rel_path: str,
) -> str:
    """``git checkout base -- path`` then verify per-file diff applies."""
    rel = rel_path.replace("\\", "/").strip().lstrip("/")
    body = _git_diff_file(repo, base_commit, head_sha, rel)
    if not body:
        return ""
    _git_reset_hard(repo)
    subprocess.run(
        ["git", "checkout", base_commit.strip(), "--", rel],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )
    ok, _ = _git_apply(body, repo, check_only=True)
    _git_reset_hard(repo)
    return body if ok else ""


def _failed_paths_from_apply_error(err: str, test_paths: list[str]) -> list[str]:
    m = re.search(r"patch failed:\s*([^:\n]+):\d+", err or "")
    if m:
        hit = m.group(1).replace("\\", "/").strip()
        if hit in test_paths:
            return [hit]
    return list(test_paths)


def build_patches_from_git_at_base(
    repo: Path,
    *,
    base_commit: str,
    head_sha: str,
    language: str = "",
) -> tuple[tuple[str, str] | None, str]:
    """
    Primary patch builder: per-file ``test_patch`` + ``impl_patch`` from ``base..head``.

    Returns ``((impl, test) | None, stack_error)``.
    """
    if not ensure_patch_commits_fetched(repo, base_commit, head_sha):
        return None, "head/base commits unreachable"

    raw = diff_at_base(repo, base_commit, head_sha)
    if not raw.strip():
        return None, "empty git diff at base"

    test_paths = collect_gradable_test_paths_from_diff(raw, language)
    if not test_paths:
        return None, "no gradable test paths in diff"

    test_patch, applied_paths = _per_file_test_patch(
        repo, base_commit, head_sha, test_paths, verify_apply=True
    )
    if not test_patch or not applied_paths:
        return None, "no per-file test_patch hunks apply at base"

    test_set = set(applied_paths)
    impl_paths = collect_impl_paths_from_diff(raw, test_set, language)
    impl_patch, _impl_applied = _per_file_impl_patch(
        repo, base_commit, head_sha, impl_paths, verify_apply=True
    )
    test_body = strip_mailbox_to_unified(test_patch)
    impl_body = strip_mailbox_to_unified(impl_patch)

    ok_stack, stack_err = validate_git_patch_stack(test_body, impl_body, repo)
    if ok_stack:
        return (impl_patch, test_patch), ""

    failed = _failed_paths_from_apply_error(stack_err, list(applied_paths))
    recovered_chunks: list[str] = []
    recovered_paths: list[str] = []
    for path in failed:
        chunk = (
            _recover_single_file_patch(repo, base_commit, head_sha, path)
            or _git_diff_file(repo, base_commit, head_sha, path)
        )
        if chunk:
            ok, _ = validate_git_patch(chunk, repo)
            if not ok:
                chunk = _filter_patch_to_applying_hunks(chunk, repo)
            ok, _ = validate_git_patch(chunk, repo)
            if ok:
                recovered_chunks.append(chunk)
                recovered_paths.append(path)
    if recovered_chunks:
        test_patch = "".join(recovered_chunks)
        test_body = strip_mailbox_to_unified(test_patch)
        test_set = set(recovered_paths)
        impl_paths = collect_impl_paths_from_diff(raw, test_set, language)
        impl_patch, _impl_applied = _per_file_impl_patch(
            repo, base_commit, head_sha, impl_paths, verify_apply=True
        )
        impl_body = strip_mailbox_to_unified(impl_patch)
        ok_stack, stack_err = validate_git_patch_stack(test_body, impl_body, repo)
        if ok_stack:
            return (impl_patch, test_patch), ""

    return None, stack_err


def recover_test_patch_paths_from_git(
    repo: Path,
    base_commit: str,
    head_sha: str,
    test_paths: list[str],
) -> str | None:
    """Per-file ``git diff base head -- path`` for reliable test hunks."""
    ensure_patch_commits_fetched(repo, base_commit, head_sha)
    body, _applied = _per_file_test_patch(
        repo, base_commit, head_sha, test_paths, verify_apply=True
    )
    if not body:
        return None
    ok, _ = validate_git_patch(body, repo)
    return body if ok else None


def recover_patches_from_base(
    pr,
    repo: Path,
    *,
    base_commit: str,
    head_sha: str,
    language: str = "",
    llm: tuple[str, str, str, int] | None = None,
) -> tuple[str, str] | None:
    """Build patches from ``git diff base..head`` with per-file test hunks."""
    del llm
    built, _git_err = build_patches_from_git_at_base(
        repo,
        base_commit=base_commit,
        head_sha=head_sha,
        language=language,
    )
    if built is not None:
        return built

    from .diff_split import split_impl_and_test_patch

    raw = diff_at_base(repo, base_commit, head_sha)
    if not raw.strip():
        return None
    impl, test = split_impl_and_test_patch(raw, repo_id=pr.repo_id, llm=None)
    impl_body = strip_mailbox_to_unified(impl)
    test_body = strip_mailbox_to_unified(test)
    if not test_body.strip():
        return None
    ok_stack, _ = validate_git_patch_stack(test_body, impl_body, repo)
    if ok_stack:
        return impl, test
    return None


def recover_patches_heuristic(
    pr,
    repo: Path,
    *,
    diff: str | None = None,
    base_commit: str = "",
    head_sha: str = "",
    language: str = "",
) -> tuple[str, str] | None:
    """
    Re-split when both patches pass stacked ``git apply`` at ``base_commit``.

    Prefers per-file ``git diff base..head`` when *base_commit* and *head_sha* are set.
    """
    if base_commit.strip() and head_sha.strip():
        from_base = recover_patches_from_base(
            pr,
            repo,
            base_commit=base_commit,
            head_sha=head_sha,
            language=language,
        )
        if from_base is not None:
            return from_base

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
    ok_stack, _ = validate_git_patch_stack(test_body, impl_body, repo)
    if ok_stack:
        return impl, test
    return None


def ensure_patches_for_base(
    pr,
    repo: Path,
    *,
    base_commit: str,
    head_sha: str,
    patch: str,
    test_patch: str,
    diff: str,
    llm_split_used: bool,
    language: str = "",
) -> tuple[str, str]:
    """
    Return ``(impl_patch, test_patch)`` aligned with harness ``base_commit``.

    Raises ``PatchSplitUnrecoverableError`` when recovery and stacked apply fail.
    """
    test_body = strip_mailbox_to_unified(test_patch)
    impl_body = strip_mailbox_to_unified(patch)
    stack_err = "empty patches"

    if test_body.strip() or impl_body.strip():
        ok_stack, stack_err = validate_git_patch_stack(test_body, impl_body, repo)
        if ok_stack:
            return patch, test_patch

    built, git_stack_err = build_patches_from_git_at_base(
        repo,
        base_commit=base_commit,
        head_sha=head_sha,
        language=language,
    )
    if built is not None:
        impl, test = built
        print(
            f"  {pr.instance_id}: patches aligned from per-file git diff "
            f"{base_commit[:12]}..{head_sha[:12]}",
            file=sys.stderr,
        )
        return impl, test

    recovered = recover_patches_heuristic(
        pr,
        repo,
        diff=diff,
        base_commit=base_commit,
        head_sha=head_sha,
        language=language,
    )
    if recovered is not None:
        impl, test = recovered
        if llm_split_used:
            print(
                f"  {pr.instance_id}: patch split recovered from base..head diff "
                f"(stacked apply-check OK)",
                file=sys.stderr,
            )
        return impl, test

    if git_stack_err:
        reason = git_stack_err
    elif test_body.strip() or impl_body.strip():
        reason = stack_err
    else:
        reason = "no gradable test_patch"
    print(
        f"  {pr.instance_id}: patch split unrecoverable at base "
        f"{base_commit[:12]} ({reason})",
        file=sys.stderr,
    )
    raise PatchSplitUnrecoverableError(
        f"{pr.instance_id}: patches do not apply at base_commit "
        f"(stacked test→impl failed: {reason})"
    )


def ensure_valid_patch_split(
    pr,
    repo: Path,
    diff: str,
    patch: str,
    test_patch: str,
    *,
    base_commit: str = "",
    head_sha: str = "",
    llm_split_used: bool,
    language: str = "",
) -> tuple[str, str]:
    """Validate patch split at base commit; align to ``base..head`` when needed."""
    if base_commit.strip() and head_sha.strip():
        return ensure_patches_for_base(
            pr,
            repo,
            base_commit=base_commit,
            head_sha=head_sha,
            patch=patch,
            test_patch=test_patch,
            diff=diff,
            llm_split_used=llm_split_used,
            language=language,
        )

    test_body = strip_mailbox_to_unified(test_patch)
    impl_body = strip_mailbox_to_unified(patch)
    ok_stack, _ = validate_git_patch_stack(test_body, impl_body, repo)
    if ok_stack:
        return patch, test_patch

    recovered = recover_patches_heuristic(pr, repo, diff=diff, language=language)
    if recovered is not None:
        if llm_split_used:
            print(
                f"  {pr.instance_id}: LLM patch split failed apply-check; "
                f"recovered via heuristic re-split",
                file=sys.stderr,
            )
        return recovered

    raise PatchSplitUnrecoverableError(
        f"{pr.instance_id}: patches do not apply at base_commit (no head_sha for realignment)"
    )
