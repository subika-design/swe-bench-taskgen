from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .patch_sanitize import filter_junk_from_unified_diff


PR_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<num>\d+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedPR:
    owner: str
    repo: str
    number: int

    @property
    def repo_id(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def instance_id(self) -> str:
        return f"{self.owner}__{self.repo}-{self.number}"


class BaseCommitUnreachableError(RuntimeError):
    """Raised when ``base_commit`` is not present in a cloned repository."""

    def __init__(self, commit: str, repo_id: str, *, detail: str = "") -> None:
        self.commit = commit
        self.repo_id = repo_id
        self.detail = detail.strip()
        msg = f"base_commit {commit[:12]} unreachable in {repo_id}"
        if self.detail:
            msg = f"{msg}: {self.detail}"
        super().__init__(msg)


def parse_pr_url(url: str) -> ParsedPR:
    m = PR_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"Not a GitHub PR URL: {url!r}")
    return ParsedPR(m.group("owner"), m.group("repo"), int(m.group("num")))


def run_gh(args: list[str], *, strip: bool = True) -> str:
    p = subprocess.run(["gh", *args], capture_output=True)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"gh failed: {args}")
    out = p.stdout.decode("utf-8", errors="replace")
    return out.strip() if strip else out.replace("\r\n", "\n")


def normalize_patch(raw: str) -> str:
    text = raw.replace("\r\n", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def fetch_pr_diff(owner: str, repo: str, number: int) -> str:
    flag = f"{owner}/{repo}"
    # Prefer REST squashed diff (no git-format-patch ``From`` headers) for ``git apply --check``.
    for args in (
        [
            "api",
            f"repos/{owner}/{repo}/pulls/{number}",
            "-H",
            "Accept: application/vnd.github.diff",
        ],
        ["pr", "diff", str(number), "--repo", flag, "--color", "never"],
        ["pr", "diff", str(number), "--repo", flag, "--patch", "--color", "never"],
    ):
        try:
            raw = run_gh(args, strip=False)
            if raw.strip():
                flat = strip_mailbox_to_unified(raw)
                body = flat if flat.strip() else raw
                return normalize_patch(filter_junk_from_unified_diff(body))
        except RuntimeError:
            continue
    raise RuntimeError(f"Could not fetch diff for {flag}#{number}")


def strip_mailbox_to_unified(patch: str) -> str:
    text = (patch or "").replace("\r\n", "\n")
    # Drop git-format-patch commit headers embedded in multi-commit PR diffs.
    text = re.sub(
        r"(?ms)^From [0-9a-f]{40} Mon Sep 17 00:00:00 2001\n.*?(?=^diff --git |\Z)",
        "",
        text,
    )
    parts = re.split(r"(?=^diff --git )", text, flags=re.MULTILINE)
    return "".join(p for p in parts if p.startswith("diff --git "))


@dataclass
class PRMetadata:
    base_commit: str
    head_commit: str
    title: str
    body: str
    created_at: str
    first_commit_oid: str
    first_commit_date: str
    closing_issue_numbers: list[int]


def _fetch_pull_refs(pr: ParsedPR) -> tuple[str, str, str | None, bool]:
    """``(base_sha, head_sha, merge_commit_sha|None, merged)`` from GitHub REST."""
    raw = run_gh(
        [
            "api",
            f"repos/{pr.owner}/{pr.repo}/pulls/{pr.number}",
            "-q",
            "{base: .base.sha, head: .head.sha, merge: .merge_commit_sha, merged: .merged}",
        ]
    )
    data: dict[str, Any] = json.loads(raw)
    base_sha = str(data.get("base") or "").strip()
    head_sha = str(data.get("head") or "").strip()
    merge_sha = str(data.get("merge") or "").strip() or None
    merged = bool(data.get("merged"))
    if not base_sha:
        raise RuntimeError(f"Empty base.sha for {pr.repo_id}#{pr.number}")
    return base_sha, head_sha, merge_sha, merged


def _fetch_merge_commit_first_parent(pr: ParsedPR, merge_commit_sha: str) -> str:
    parent = run_gh(
        [
            "api",
            f"repos/{pr.owner}/{pr.repo}/commits/{merge_commit_sha}",
            "-q",
            ".parents[0].sha",
        ]
    ).strip()
    if not parent:
        raise RuntimeError(
            f"No parent for merge commit {merge_commit_sha[:12]} on {pr.repo_id}#{pr.number}"
        )
    return parent


def _fetch_compare_merge_base(pr: ParsedPR, base_sha: str, head_sha: str) -> str:
    compare = f"{base_sha}...{head_sha}"
    mb = run_gh(
        [
            "api",
            f"repos/{pr.owner}/{pr.repo}/compare/{compare}",
            "-q",
            ".merge_base_commit.sha",
        ]
    ).strip()
    if not mb:
        raise RuntimeError(f"Empty merge_base_commit for {pr.repo_id}#{pr.number}")
    return mb


def _fetch_head_commit_sha(pr: ParsedPR) -> str:
    """Post-PR tree tip: merge commit when merged, else PR head."""
    _base_sha, head_sha, merge_sha, merged = _fetch_pull_refs(pr)
    if merged and merge_sha:
        return merge_sha
    return head_sha


def _fetch_base_commit_sha(pr: ParsedPR) -> str:
    """
    Pre-PR base commit for SWE-bench replay.

    Merged PRs: first parent of the merge commit (state on target branch before merge).
    Open/closed unmerged: ``merge_base_commit`` between PR base and head (not current ``base.sha`` tip).
    Falls back to ``base.sha`` if compare/merge metadata is unavailable.
    """
    base_sha, head_sha, merge_sha, merged = _fetch_pull_refs(pr)
    if merged and merge_sha:
        try:
            return _fetch_merge_commit_first_parent(pr, merge_sha)
        except RuntimeError:
            pass
    if head_sha:
        try:
            return _fetch_compare_merge_base(pr, base_sha, head_sha)
        except RuntimeError:
            pass
    return base_sha


def _fetch_closing_issue_numbers(pr: ParsedPR) -> list[int]:
    """Closing issues linked on the PR (GraphQL); empty if unavailable."""
    query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          closingIssuesReferences(first: 20) {
            nodes { number }
          }
        }
      }
    }
    """
    try:
        raw = run_gh(
            [
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={pr.owner}",
                "-f",
                f"name={pr.repo}",
                "-F",
                f"number={pr.number}",
            ]
        )
        payload = json.loads(raw)
        nodes = (
            payload.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("closingIssuesReferences", {})
            .get("nodes", [])
        )
    except (RuntimeError, json.JSONDecodeError, AttributeError, TypeError):
        return []

    nums: list[int] = []
    for node in nodes or []:
        if isinstance(node, dict) and "number" in node:
            try:
                nums.append(int(node["number"]))
            except (TypeError, ValueError):
                continue
    return nums


def fetch_pr_metadata(pr: ParsedPR) -> PRMetadata:
    raw = run_gh(
        [
            "pr",
            "view",
            str(pr.number),
            "--repo",
            pr.repo_id,
            "--json",
            "title,body,createdAt,commits",
        ]
    )
    data: dict[str, Any] = json.loads(raw)
    commits = data.get("commits") or []
    first_oid = ""
    first_date = ""
    if commits:
        c0 = commits[0] if isinstance(commits[0], dict) else {}
        first_oid = (c0.get("oid") or "") if isinstance(c0, dict) else ""
        first_date = (c0.get("authoredDate") or c0.get("committedDate") or "") if isinstance(
            c0, dict
        ) else ""

    return PRMetadata(
        base_commit=_fetch_base_commit_sha(pr),
        head_commit=_fetch_head_commit_sha(pr),
        title=data.get("title") or "",
        body=data.get("body") or "",
        created_at=data.get("createdAt") or "",
        first_commit_oid=first_oid,
        first_commit_date=first_date,
        closing_issue_numbers=_fetch_closing_issue_numbers(pr),
    )


def clone_repo_at(
    pr: ParsedPR,
    dest: Path,
    commit: str,
    *,
    depth: int,
    timeout: int,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{pr.owner}/{pr.repo}.git"
    if dest.exists():
        subprocess.run(["rm", "-rf", str(dest)], check=False)
    if depth > 0:
        subprocess.run(
            ["git", "clone", "--depth", str(depth), url, str(dest)],
            check=True,
            timeout=timeout,
        )
    else:
        subprocess.run(["git", "clone", url, str(dest)], check=True, timeout=timeout)
    checkout = subprocess.run(
        ["git", "-C", str(dest), "checkout", "-f", commit],
        capture_output=True,
        timeout=min(timeout, 600),
    )
    if checkout.returncode == 0:
        return
    # Commit may be unreachable after shallow clone or history rewrite — fetch then retry.
    subprocess.run(
        ["git", "-C", str(dest), "fetch", "origin", commit],
        capture_output=True,
        timeout=min(timeout, 600),
        check=False,
    )
    subprocess.run(
        ["git", "-C", str(dest), "checkout", "-f", commit],
        check=True,
        timeout=min(timeout, 600),
    )


def validate_base_commit_reachable(repo: Path, commit: str) -> None:
    """
    Verify *commit* resolves in *repo* after ``clone_repo_at``.

    Raises ``BaseCommitUnreachableError`` when the object is missing.
    """
    sha = str(commit or "").strip()
    if not sha:
        raise BaseCommitUnreachableError(sha, str(repo), detail="empty commit sha")
    probe = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip()
        repo_id = ""
        try:
            origin = subprocess.run(
                ["git", "-C", str(repo), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if origin.returncode == 0:
                m = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/.]+)", origin.stdout)
                if m:
                    repo_id = f"{m.group('owner')}/{m.group('name')}"
        except (OSError, subprocess.SubprocessError):
            pass
        raise BaseCommitUnreachableError(sha, repo_id or str(repo), detail=detail)


def git_ls_candidate_files(repo: Path, *, max_files: int = 400) -> list[str]:
    p = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
    score: list[tuple[int, str]] = []
    pri = (
        "readme",
        "license",
        "setup.py",
        "setup.cfg",
        "pyproject",
        "requirements",
        "environment",
        "dockerfile",
        "makefile",
        "tox.ini",
        "conftest",
        "pytest",
        "contributing",
        "develop",
    )
    exts = {".md", ".rst", ".txt", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".in"}
    for rel in lines:
        low = rel.lower()
        s = 0
        if "/" not in rel:
            s += 5
        for i, k in enumerate(pri):
            if k in low:
                s += 20 - i
        if Path(rel).suffix.lower() in exts:
            s += 2
        if low.startswith("docs/") and "install" in low:
            s += 8
        score.append((s, rel))
    score.sort(key=lambda x: (-x[0], x[1]))
    out = [rel for _, rel in score[:max_files]]
    if len(out) < max_files:
        for rel in lines:
            if rel not in out:
                out.append(rel)
            if len(out) >= max_files:
                break
    return out


def read_files_budget(repo: Path, rels: list[str], *, max_chars: int) -> str:
    parts: list[str] = []
    used = 0
    for rel in rels:
        path = repo / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        block = f"\n\n===== File: {rel} =====\n\n{text}"
        if used + len(block) > max_chars:
            remain = max_chars - used - 200
            if remain < 500:
                break
            block = block[:remain] + "\n\n[... truncated ...]\n"
        parts.append(block)
        used += len(block)
        if used >= max_chars:
            break
    return "".join(parts)
