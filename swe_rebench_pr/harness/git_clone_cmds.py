"""Shared git clone/checkout commands for harness setup scripts."""

from __future__ import annotations

from swe_rebench_pr.harness.constants import REPO_BASE_COMMIT_BRANCH


def git_clone_branch_arg(repo: str, base_commit: str) -> str:
    branch = REPO_BASE_COMMIT_BRANCH.get(repo, {}).get(base_commit, "")
    return f"--branch {branch}" if branch else ""


def git_fetch_and_reset_commands(base_commit: str) -> list[str]:
    """
    Ensure *base_commit* is reachable, then hard-reset.

    Mirrors host ``clone_repo_at`` fetch-before-checkout for SHAs absent from a
    default-branch ``--single-branch`` clone.
    """
    bc = base_commit.strip()
    return [
        f"if ! git cat-file -e {bc}^{{commit}} 2>/dev/null; then",
        f"  git fetch origin {bc} --depth=1 2>/dev/null || git fetch origin {bc} 2>/dev/null || true",
        "fi",
        f"git reset --hard {bc}",
    ]


def git_post_reset_hygiene_commands(base_commit: str) -> list[str]:
    """SWE-bench-style history trimming after checkout (Python env images)."""
    bc = base_commit.strip()
    return [
        "git remote remove origin",
        f"TARGET_TIMESTAMP=$(git show -s --format=%ci {bc})",
        'git tag -l | while read tag; do TAG_COMMIT=$(git rev-list -n 1 "$tag"); TAG_TIME=$(git show -s --format=%ci "$TAG_COMMIT"); if [[ "$TAG_TIME" > "$TARGET_TIMESTAMP" ]]; then git tag -d "$tag"; fi; done',
        "git reflog expire --expire=now --all",
        "git gc --prune=now --aggressive",
        'AFTER_TIMESTAMP=$(date -d "$TARGET_TIMESTAMP + 1 second" \'+%Y-%m-%d %H:%M:%S\')',
        'COMMIT_COUNT=$(git log --oneline --all --since="$AFTER_TIMESTAMP" | wc -l)',
        '[ "$COMMIT_COUNT" -eq 0 ] || exit 1',
    ]


def python_conda_activate_commands(env_name: str) -> list[str]:
    return [
        "source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
        'echo "Current environment: $CONDA_DEFAULT_ENV"',
    ]


def python_swebench_marker_commands() -> list[str]:
    return [
        "git config --global user.email setup@swebench.config",
        "git config --global user.name SWE-bench",
        "git commit --allow-empty -am SWE-bench",
    ]
