from __future__ import annotations

import re
import subprocess
from pathlib import Path


def normalized_install_version(repo: Path, commit: str) -> str:
    """SWE-rebench-style ``major.minor`` from tags at ``commit``, else unique ``0.0-<sha>``."""
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-fq", commit],
        check=True,
        timeout=120,
    )
    r = subprocess.run(
        ["git", "-C", str(repo), "tag", "--points-at", commit],
        capture_output=True,
        text=True,
        check=False,
    )
    tags = [t.strip() for t in (r.stdout or "").splitlines() if t.strip()]
    best: tuple[int, int] | None = None
    best_s = ""
    for t in tags:
        m = re.match(r"^v?(\d+)\.(\d+)", t)
        if not m:
            continue
        ma, mi = int(m.group(1)), int(m.group(2))
        cand = (ma, mi)
        if best is None or cand > best:
            best = cand
            best_s = f"{ma}.{mi}"
    if best_s:
        return best_s

    r2 = subprocess.run(
        ["git", "-C", str(repo), "describe", "--tags", "--abbrev=0"],
        capture_output=True,
        text=True,
        check=False,
    )
    t = (r2.stdout or "").strip()
    if t:
        m = re.match(r"^v?(\d+)\.(\d+)", t)
        if m:
            return f"{m.group(1)}.{m.group(2)}"

    return f"0.0-{commit[:8]}"


def harness_version_for_instance(
    instance_id: str,
    language: str,
    fallback_version: str,
) -> str:
    """
    SWE-bench harness keys specs by ``(repo, version)``.

    Use the GitHub PR number suffix from ``instance_id`` (e.g. ``...-2313`` → ``2313``)
    so each task gets a unique harness spec entry (avoids semver tag collisions).
    """
    m = re.search(r"-(\d+)$", instance_id)
    if m:
        return m.group(1)
    return fallback_version
