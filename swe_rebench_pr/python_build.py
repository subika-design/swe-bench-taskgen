"""Python repo-specific install/test helpers for Docker discover."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .repo_detect import repo_needs_dateutil_zoneinfo

DATEUTIL_UPDATEZINFO_CMD = "python3 updatezinfo.py zonefile_metadata.json"


def needs_dateutil_zoneinfo(*, repo: Path | None = None) -> bool:
    """True when checkout artifacts require a zoneinfo tarball build step."""
    return repo is not None and repo_needs_dateutil_zoneinfo(repo)


def _has_updatezinfo_command(lines: list[Any]) -> bool:
    return any(
        "updatezinfo.py" in str(ln) and not str(ln).strip().startswith("#")
        for ln in lines
    )


def augment_python_install_config(
    cfg: dict[str, Any],
    *,
    repo: Path | None = None,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """Add build steps required by some Python repos (e.g. dateutil zoneinfo tarball)."""
    del repo_id  # artifact-only; kept for call-site compatibility
    out = dict(cfg)
    if not needs_dateutil_zoneinfo(repo=repo):
        return out

    post = list(out.get("post_install") or [])
    eval_cmds = list(out.get("eval_commands") or [])
    if _has_updatezinfo_command(post) or _has_updatezinfo_command(eval_cmds):
        return out

    # ``updatezinfo`` imports ``dateutil.zoneinfo.rebuild``; run after editable install.
    post.append(DATEUTIL_UPDATEZINFO_CMD)
    eval_cmds.append(DATEUTIL_UPDATEZINFO_CMD)
    out["post_install"] = post
    out["eval_commands"] = eval_cmds
    return out


def slice_failures_are_dateutil_zoneinfo(
    failures: list[tuple[str, str]],
    errors: list[tuple[str, str]],
) -> bool:
    """True when every failure/error is the missing zoneinfo tarball (env, not test logic)."""
    msgs = [msg for _, msg in failures + errors if msg.strip()]
    if not msgs:
        return False
    needle = "dateutil-zoneinfo.tar.gz"
    return all(needle in msg for msg in msgs)
