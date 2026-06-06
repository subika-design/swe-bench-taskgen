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


def parse_pytest_log(log: str) -> dict[str, str]:
    """
    Parse pytest summary / per-test lines into ``path::node`` keys.

    Handles ``file.py::test_name PASSED`` and ``FAILED path::test`` styles.
    """
    out: dict[str, str] = {}
    status_line = re.compile(
        r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(\S+?::\S+)$",
        re.I,
    )
    trailing = re.compile(
        r"^(\S+?::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s*$",
        re.I,
    )
    for line in log.splitlines():
        stripped = line.strip()
        m = status_line.match(stripped)
        if m:
            status, key = m.group(1).upper(), m.group(2)
            out[key] = PASSED if status in ("PASSED", "XPASS") else FAILED
            continue
        m2 = trailing.match(stripped)
        if m2:
            key, status = m2.group(1), m2.group(2).upper()
            if key not in out:
                out[key] = PASSED if status in ("PASSED", "XPASS") else FAILED
    return out


def parse_rspec_log(log: str) -> dict[str, str]:
    """
    Parse RSpec-style lines into ``path::example`` keys matching discover JUnit node ids.

    Handles explicit ``PASSED|FAILED path::name`` lines and classic RSpec failure summaries.
    """
    out: dict[str, str] = {}
    explicit = re.compile(r"^(PASSED|FAILED|ERROR)\s+(\S+?::.+)$", re.I)
    for line in log.splitlines():
        m = explicit.match(line.strip())
        if m:
            status, key = m.group(1).upper(), m.group(2)
            if status == "PASSED":
                out[key] = PASSED
            elif status in ("FAILED", "ERROR"):
                out[key] = FAILED
            continue
        if " Failure" in line or line.strip().startswith("Failed examples:"):
            continue
        # ``rspec ./spec/foo_spec.rb:12 # Group example name``
        m2 = re.search(
            r"rspec\s+(\./)?(\S+_spec\.rb):\d+\s+#\s+(.+)$",
            line.strip(),
            re.I,
        )
        if m2:
            rel = m2.group(2).lstrip("./")
            name = m2.group(3).strip()
            key = f"{rel}::{name}"
            if key not in out:
                out[key] = FAILED
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


def parse_runtests_log(log: str) -> dict[str, str]:
    """
    Parse curl-style ``runtests.pl`` output into ``testNNNN`` keys.

    Supports automake lines (``PASS/FAIL: N - name``) and verbose `` OK `` / ``FAILED`` tails.
    """
    out: dict[str, str] = {}
    automake = re.compile(r"^(PASS|FAIL):\s*(\d+)\b", re.IGNORECASE)
    verbose_ok = re.compile(r"\bOK\b\s*\((\d+)\s", re.IGNORECASE)
    verbose_fail = re.compile(r"\bFAILED\b", re.IGNORECASE)
    test_header = re.compile(r"^Test\s+0*(\d+)\b", re.IGNORECASE)
    ignored_line = re.compile(r"^(\d+):\s*IGNORED:", re.IGNORECASE)
    missing_test = re.compile(
        r"(?:no such test|test\s+not found|unknown test|missing test).*?\b0*(\d+)\b",
        re.IGNORECASE,
    )
    missing_test_alt = re.compile(
        r"\btest\s+0*(\d+)\b.*(?:not found|does not exist|missing|unknown)",
        re.IGNORECASE,
    )
    pending: str | None = None
    for line in log.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = automake.match(stripped)
        if m:
            status, num = m.group(1).upper(), m.group(2)
            key = f"test{int(num)}"
            out[key] = PASSED if status == "PASS" else FAILED
            pending = None
            continue
        ig = ignored_line.match(stripped)
        if ig:
            out[f"test{int(ig.group(1))}"] = FAILED
            pending = None
            continue
        for miss_pat in (missing_test, missing_test_alt):
            mm = miss_pat.search(stripped)
            if mm:
                out[f"test{int(mm.group(1))}"] = FAILED
                pending = None
                break
        else:
            mm = None
        if mm is not None:
            continue
        hm = test_header.match(stripped)
        if hm:
            pending = f"test{int(hm.group(1))}"
            continue
        if pending:
            if verbose_fail.search(stripped):
                out[pending] = FAILED
                pending = None
                continue
            vm = verbose_ok.search(stripped)
            if vm:
                out[pending] = PASSED
                pending = None
                continue
        vm2 = verbose_ok.search(stripped)
        if vm2:
            key = f"test{int(vm2.group(1))}"
            out[key] = PASSED
        elif verbose_fail.search(stripped) and re.search(r"\b\d+\b", stripped):
            mnum = re.search(r"\((\d+)\s", stripped)
            if mnum:
                key = f"test{int(mnum.group(1))}"
                out[key] = FAILED
    return out


def parse_ctest_log(log: str) -> dict[str, str]:
    """Parse ``ctest --output-on-failure`` summary lines into test name keys."""
    out: dict[str, str] = {}
    line_re = re.compile(
        r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(\S+)\s+.*?"
        r"(\*\*\*Failed|\bPassed\b|\bNot Run\b|\bSkipped\b)",
        re.IGNORECASE,
    )
    for line in log.splitlines():
        m = line_re.match(line.strip())
        if not m:
            continue
        name, status = m.group(1), m.group(2)
        if "fail" in status.lower():
            out[name] = FAILED
        elif "skip" in status.lower() or "not run" in status.lower():
            out[name] = SKIPPED
        else:
            out[name] = PASSED
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
    if result_format == "rspec_log":
        return parse_rspec_log(log)
    if result_format == "runtests_log":
        return parse_runtests_log(log)
    if result_format == "ctest_log":
        return parse_ctest_log(log)
    return {}
