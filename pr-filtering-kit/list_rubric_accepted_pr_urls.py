#!/usr/bin/env python3
"""Print full GitHub PR URLs by rubric_accepted status (from repo_evaluator JSON)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RUBRIC_STATUS_ACCEPTED = "accepted"
RUBRIC_STATUS_PARTIALLY_ACCEPTED = "partially_accepted"
RUBRIC_STATUS_REJECTED = "rejected"


def _normalize_status(value) -> str:
    if value is True:
        return RUBRIC_STATUS_ACCEPTED
    if value is False:
        return RUBRIC_STATUS_REJECTED
    if value in (
        RUBRIC_STATUS_ACCEPTED,
        RUBRIC_STATUS_PARTIALLY_ACCEPTED,
        RUBRIC_STATUS_REJECTED,
    ):
        return value
    return RUBRIC_STATUS_REJECTED


def pr_url_from_row(entry: dict, owner_repo: str | None) -> str:
    if url := (entry.get("url") or "").strip():
        return url
    num = entry.get("number")
    if owner_repo and num is not None:
        return f"https://github.com/{owner_repo}/pull/{num}"
    raise ValueError(f"No url and cannot build URL: {entry}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="List PR URLs filtered by rubric_accepted status from evaluator JSON."
    )
    ap.add_argument(
        "json_path",
        nargs="?",
        default=Path(__file__).resolve().parent / "output" / "pandas-dev__pandas.json",
        type=Path,
        help="Path to repo_evaluator --json output",
    )
    ap.add_argument(
        "--repo",
        default=None,
        help="owner/repo if URLs must be built (e.g. pandas-dev/pandas); optional if each row has url",
    )
    ap.add_argument(
        "--status",
        choices=(
            "accepted",
            "partially_accepted",
            "rejected",
            "goal",
            "all",
        ),
        default="accepted",
        help=(
            "Filter: accepted (default), partially_accepted, rejected, "
            "goal (accepted+partial), or all"
        ),
    )
    args = ap.parse_args()

    if not args.json_path.exists():
        print(f"File not found: {args.json_path}", file=sys.stderr)
        return 1

    data = json.loads(args.json_path.read_text(encoding="utf-8"))
    rows = data.get("pr_rubrics") or []

    if args.status == "all":
        matched = rows
    elif args.status == "goal":
        matched = [
            r
            for r in rows
            if _normalize_status(r.get("rubric_accepted"))
            in (RUBRIC_STATUS_ACCEPTED, RUBRIC_STATUS_PARTIALLY_ACCEPTED)
        ]
    else:
        matched = [
            r
            for r in rows
            if _normalize_status(r.get("rubric_accepted")) == args.status
        ]

    for entry in matched:
        try:
            print(pr_url_from_row(entry, args.repo))
        except ValueError as e:
            print(f"# skip: {e}", file=sys.stderr)

    print(f"# status={args.status} count={len(matched)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
