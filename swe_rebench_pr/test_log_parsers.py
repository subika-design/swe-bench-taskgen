"""Test log parsers aligned with SWE-bench harness (no TestSpec dependency)."""

from __future__ import annotations

import re

PASSED = "PASSED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"
ERROR = "ERROR"


def parse_gotest_log(log: str) -> dict[str, str]:
    out: dict[str, str] = {}
    pattern = r"^--- (PASS|FAIL|SKIP): (.+) \((.+)\)$"
    for line in log.split("\n"):
        match = re.match(pattern, line.strip())
        if not match:
            continue
        status, test_name, _duration = match.groups()
        if status == "PASS":
            out[test_name] = PASSED
        elif status == "FAIL":
            out[test_name] = FAILED
        elif status == "SKIP":
            out[test_name] = SKIPPED
    return out


def parse_cargo_log(log: str) -> dict[str, str]:
    out: dict[str, str] = {}
    pattern = r"^test\s+(\S+)\s+\.\.\.\s+(\w+)$"
    for line in log.split("\n"):
        match = re.match(pattern, line.strip())
        if not match:
            continue
        test_name, outcome = match.groups()
        if outcome == "ok":
            out[test_name] = PASSED
        elif outcome == "FAILED":
            out[test_name] = FAILED
    return out


def parse_django_runtests_log(log: str) -> dict[str, str]:
    """
    Parse Django ``runtests.py`` output via SWE-bench ``parse_log_django`` when available.

    Map keys are the full test description before `` ... ok`` / `` ... FAIL``, etc.
    Status values match SWE-bench grading (``PASSED``, ``FAILED``, …).
    """
    from .swebench_align import parse_django_log_like_swebench

    return parse_django_log_like_swebench(log)


def _normalize_gradle_harness_test_key(name: str) -> str:
    """``fqcn > method()`` -> ``fqcn > method`` (SWE-bench ``parse_log_gradle_custom``)."""
    key = (name or "").strip()
    if " > " not in key:
        return key
    cls, method = key.split(" > ", 1)
    return f"{cls.strip()} > {method.strip().rstrip('()')}"


def parse_gradle_harness_log(log: str) -> dict[str, str]:
    """
    Parse Gradle test lines from the harness logging init script output.

    Matches SWE-bench ``parse_log_gradle_custom`` / grading keys.
    """
    out: dict[str, str] = {}
    full_pattern = re.compile(r"^([^>].+)\s+(PASSED|FAILED)$")
    test_name_pattern = re.compile(r"^([^>]\S*\s+>\s+\S+)$")
    status_only_pattern = re.compile(r"^(PASSED|FAILED)$")
    pending: str | None = None
    for line in log.split("\n"):
        stripped = line.strip()
        match = full_pattern.match(stripped)
        if match:
            key = _normalize_gradle_harness_test_key(match.group(1))
            out[key] = match.group(2)
            pending = None
            continue
        test_name_match = test_name_pattern.match(stripped)
        if test_name_match:
            pending = _normalize_gradle_harness_test_key(test_name_match.group(1))
            continue
        if pending:
            status_match = status_only_pattern.match(stripped)
            if status_match:
                out[pending] = status_match.group(1)
                pending = None
    return out


def parse_googletest_log(log: str) -> dict[str, str]:
    """Parse GoogleTest-style lines (Premake self-test, fmtlib, etc.)."""
    out: dict[str, str] = {}
    pattern = re.compile(r"^\s*\[\s*(OK|FAILED)\s*\]\s(.+?)\s\(.+\)\s*$")
    for line in log.split("\n"):
        match = pattern.match(line.strip())
        if not match:
            continue
        status, test_name = match.groups()
        if status == "OK":
            out[test_name] = PASSED
        elif status == "FAILED":
            out[test_name] = FAILED
    return out


def parse_log_for_language(log: str, result_format: str) -> dict[str, str]:
    if result_format == "gotest_log":
        return parse_gotest_log(log)
    if result_format == "cargo_log":
        return parse_cargo_log(log)
    if result_format == "django_log":
        return parse_django_runtests_log(log)
    if result_format == "gradle_harness_log":
        return parse_gradle_harness_log(log)
    if result_format == "googletest_log":
        return parse_googletest_log(log)
    return {}
