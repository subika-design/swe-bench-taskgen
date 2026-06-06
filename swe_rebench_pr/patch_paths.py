"""Gradable test path classification for patch split / recovery."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .languages import get_language_spec, is_test_path, normalize_language
from .patch_sanitize import is_junk_patch_path

_NON_TEST_INFRA_PREFIXES: tuple[str, ...] = (
    ".github/",
    ".gitlab/",
    ".circleci/",
    ".travis/",
)

_NON_TEST_INFRA_BASENAMES: frozenset[str] = frozenset(
    {
        "cargo.lock",
        "jenkinsfile",
        ".golangci.yml",
        "go.sum",
    }
)

_DOC_BASENAME_MARKERS: tuple[str, ...] = (
    "guide",
    "changelog",
    "readme",
    "history",
    "authors",
    "contributing",
    "license",
)


def is_non_test_infrastructure_path(path: str) -> bool:
    """CI, lockfiles, lint config, and docs — not harness-gradable test targets."""
    low = path.replace("\\", "/").lower().lstrip("./")
    if not low:
        return True
    if any(low.startswith(p) for p in _NON_TEST_INFRA_PREFIXES):
        return True
    base = PurePosixPath(low).name
    if base in _NON_TEST_INFRA_BASENAMES:
        return True
    if base.endswith("golangci.yml") or base.endswith("golangci.yaml"):
        return True
    if low.endswith((".yml", ".yaml")) and (
        "workflow" in low or "/ci/" in low or low.startswith(".github")
    ):
        return True
    if low.endswith(".md"):
        stem = base.removesuffix(".md")
        if any(m in stem for m in _DOC_BASENAME_MARKERS):
            return True
        if not (
            low.startswith(("tests/", "test/"))
            or "/tests/" in low
            or stem.startswith("test_")
            or stem.endswith("_test")
        ):
            return True
    if low.endswith(".lock"):
        return True
    return False


def is_gradable_test_path(path: str, language: str = "") -> bool:
    """True when *path* is a runnable test file suitable for ``test_patch``."""
    if is_junk_patch_path(path) or is_non_test_infrastructure_path(path):
        return False
    lang = normalize_language(language) if language else "python"
    spec = get_language_spec(lang)
    if is_test_path(path, spec):
        return True
    low_path = path.replace("\\", "/").lower()
    if low_path.startswith(("tests/", "test/")):
        return True
    if (
        "/tests/" in low_path
        or "/test/" in low_path
        or "/spec/" in low_path
        or "/specs/" in low_path
        or "__tests__" in low_path
    ):
        return True
    if low_path.endswith(".go") and "_test.go" in low_path:
        return True
    if low_path.endswith(".rs") and ("/tests/" in low_path or low_path.startswith("tests/")):
        return True
    if low_path.endswith(".php") and ("test" in low_path or "spec" in low_path):
        return True
    return False


def has_runnable_python_tests(test_patch: str, language: str = "") -> bool:
    """
    True when *test_patch* contains at least one pytest-collectible ``test_*.py`` module.

    Filters doc-only / non-test Python PRs that would yield 0 pytest cases in discover.
    """
    from .languages import normalize_language

    lang = normalize_language(language) if language else "python"
    if lang not in ("python", "py"):
        return True
    paths = collect_gradable_test_paths_from_patch(test_patch, lang)
    for raw in paths:
        p = raw.replace("\\", "/").strip().lower()
        if not p.endswith(".py"):
            continue
        base = PurePosixPath(p).name
        if base.startswith("test_") or base == "conftest.py":
            return True
        if p.startswith(("tests/", "test/")) and "test" in base:
            return True
    return False


def collect_gradable_test_paths_from_diff(
    diff: str,
    language: str = "",
) -> list[str]:
    """Unique gradable test paths from ``diff --git`` headers."""
    seen: set[str] = set()
    out: list[str] = []
    for m in re.finditer(r"^diff --git a/(\S+) b/\1$", diff or "", re.MULTILINE):
        rel = m.group(1).replace("\\", "/").strip()
        if not rel or rel in seen:
            continue
        if is_gradable_test_path(rel, language):
            seen.add(rel)
            out.append(rel)
    return sorted(out)


def collect_gradable_test_paths_from_patch(
    patch: str,
    language: str = "",
) -> list[str]:
    return collect_gradable_test_paths_from_diff(patch, language)


def collect_impl_paths_from_diff(
    diff: str,
    exclude_paths: set[str],
    language: str = "",
) -> list[str]:
    """Non-test, non-infra paths from a unified diff (harness ``impl_patch`` candidates)."""
    seen: set[str] = set()
    out: list[str] = []
    for m in re.finditer(r"^diff --git a/(\S+) b/\1$", diff or "", re.MULTILINE):
        rel = m.group(1).replace("\\", "/").strip()
        if not rel or rel in seen or rel in exclude_paths:
            continue
        if is_non_test_infrastructure_path(rel) or is_gradable_test_path(rel, language):
            continue
        seen.add(rel)
        out.append(rel)
    return sorted(out)
