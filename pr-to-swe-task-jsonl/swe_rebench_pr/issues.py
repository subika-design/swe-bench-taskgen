from __future__ import annotations

import json
from typing import Optional

from .gh_pr import run_gh


def fetch_issue_title_body(owner: str, repo: str, issue_number: int) -> tuple[str, str]:
    raw = run_gh(["api", f"repos/{owner}/{repo}/issues/{issue_number}", "-q", ".title,.body"])
    # gh -q doesn't work that way for two fields - use json
    raw = run_gh(["api", f"repos/{owner}/{repo}/issues/{issue_number}"])
    data = json.loads(raw)
    return (data.get("title") or "", data.get("body") or "")


def fetch_hints_before_commit(
    owner: str,
    repo: str,
    issue_number: int,
    cutoff_iso: str,
) -> str:
    """Concatenate issue comments strictly before the PR first commit (ISO time)."""
    raw = run_gh(
        [
            "api",
            f"repos/{owner}/{repo}/issues/{issue_number}/comments?per_page=100",
        ]
    )
    try:
        comments = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(comments, list):
        return ""
    lines: list[str] = []
    for c in comments:
        if not isinstance(c, dict):
            continue
        created = c.get("created_at") or ""
        if cutoff_iso and created >= cutoff_iso:
            continue
        user = (c.get("user") or {}).get("login") if isinstance(c.get("user"), dict) else ""
        body = (c.get("body") or "").strip()
        if not body:
            continue
        who = user or "unknown"
        lines.append(f"--- {who} ({created}) ---\n{body}")
    return "\n\n".join(lines).strip()


def build_problem_and_hints(
    pr_owner: str,
    pr_repo: str,
    *,
    pr_title: str,
    pr_body: str,
    closing_issue_numbers: list[int],
    first_commit_date: str,
) -> tuple[str, str]:
    if closing_issue_numbers:
        n = closing_issue_numbers[0]
        ititle, ibody = fetch_issue_title_body(pr_owner, pr_repo, n)
        problem = f"{ititle}\n\n{ibody}".strip()
        cutoff = first_commit_date or ""
        hints = fetch_hints_before_commit(pr_owner, pr_repo, n, cutoff)
        return problem, hints
    return f"{pr_title}\n\n{pr_body}".strip(), ""
