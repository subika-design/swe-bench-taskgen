"""Django ``runtests.py`` helpers aligned with SWE-bench harness (not pytest)."""

from __future__ import annotations

import re
from pathlib import Path

from .test_log_parsers import parse_django_runtests_log


def is_django_repo(repo_id: str) -> bool:
    """Deprecated: use ``uses_django_runtests`` from ``repo_detect`` when possible."""
    from .repo_detect import uses_django_runtests

    return uses_django_runtests(repo_id=repo_id)


def paths_to_runtests_labels(paths: list[str]) -> list[str]:
    """
    Convert test file paths to ``runtests.py`` labels (same rules as SWE-bench
    ``get_test_directives`` for ``django/django``).
    """
    labels: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        d = raw.replace("\\", "/").strip()
        if not d.endswith(".py"):
            continue
        d = d[: -len(".py")]
        if d.startswith("tests/"):
            d = d[len("tests/") :]
        label = d.replace("/", ".")
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def _labels_from_test_patch_paths(paths: list[str]) -> list[str]:
    return paths_to_runtests_labels(paths)


def _case_map_key_matches_paths(key: str, labels: list[str]) -> bool:
    if not labels:
        return True
    for label in labels:
        if label in key:
            return True
        # Module-only label vs ``test_x (pkg.mod.Class)``.
        if f"({label}." in key or key.endswith(f"({label})"):
            return True
    return False


def django_outcome_counts_for_paths(
    case_map: dict[str, str],
    paths: list[str],
) -> tuple[int, int, int, int, int]:
    """Count outcomes for tests whose runtests log key matches ``paths``."""
    labels = _labels_from_test_patch_paths(paths)
    passed = failure = error = skipped = 0
    for key, outcome in case_map.items():
        if not _case_map_key_matches_paths(key, labels):
            continue
        o = (outcome or "").upper()
        if o in ("PASSED", "OK"):
            passed += 1
        elif o in ("FAILED", "FAILURE"):
            failure += 1
        elif o == "ERROR":
            error += 1
        elif o == "SKIPPED":
            skipped += 1
        else:
            passed += 1
    total = passed + failure + error + skipped
    return passed, failure, error, skipped, total


def django_fail_error_skip_messages_for_paths(
    log_path: Path,
    paths: list[str],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Collect failure/error/skip messages from a runtests log for ``paths``."""
    failures: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    skips: list[tuple[str, str]] = []
    if not log_path.is_file() or not paths:
        return failures, errors, skips
    labels = _labels_from_test_patch_paths(paths)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    status_map = parse_django_runtests_log(text)
    for key, outcome in status_map.items():
        if not _case_map_key_matches_paths(key, labels):
            continue
        o = (outcome or "").upper()
        if o in ("FAILED", "FAILURE"):
            failures.append((key, _reason_from_log_line(text, key, "FAIL")))
        elif o == "ERROR":
            errors.append((key, _reason_from_log_line(text, key, "ERROR")))
        elif o == "SKIPPED":
            skips.append((key, _reason_from_log_line(text, key, "skipped")))
    return failures, errors, skips


def _reason_from_log_line(log: str, test_key: str, kind: str) -> str:
    """Return the ERROR/FAIL line plus following traceback lines from a runtests log."""
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if test_key not in line:
            continue
        if kind.lower() not in line.lower() and "... ERROR" not in line and "... FAIL" not in line:
            continue
        chunk = [line.rstrip()]
        for j in range(i + 1, min(i + 40, len(lines))):
            nxt = lines[j]
            if re.match(
                r"^test_\w+.*\(\S+\).*\.\.\.\s+(OK|FAIL|ERROR|skipped)",
                nxt,
                re.IGNORECASE,
            ):
                break
            if nxt.startswith("=" * 5):
                break
            chunk.append(nxt.rstrip())
        return "\n".join(chunk)[:4000]
    return f"runtests {kind}: {test_key[:200]}"
