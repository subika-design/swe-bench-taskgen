"""Extra diagnostics for test-patch LLM remediation (source excerpts, hints)."""

from __future__ import annotations

from pathlib import Path


def is_pytest_argv_child_args_mismatch(message: str) -> bool:
    """True when failure looks like get_child_arguments() under ``python -m pytest``."""
    m = message.lower()
    if "lists differ" not in m and "assertionerror" not in m:
        return False
    return "-m" in m and "pytest" in m


def _func_from_nodeid(nodeid: str) -> str | None:
    parts = nodeid.split("::")
    if len(parts) < 2:
        return None
    name = parts[-1]
    return name if name.startswith("test_") else None


def _file_from_nodeid(nodeid: str) -> str | None:
    parts = nodeid.split("::")
    if not parts or not parts[0].endswith(".py"):
        return None
    return parts[0]


def build_failure_source_context(
    repo_root: Path,
    failures: list[tuple[str, str]],
    errors: list[tuple[str, str]],
    *,
    max_files: int = 3,
    context_lines: int = 12,
) -> str:
    """Read failing test sources from the patched repo for the LLM prompt."""
    blocks: list[str] = []
    seen_files: set[str] = set()
    for nid, _msg in failures + errors:
        rel = _file_from_nodeid(nid)
        if not rel or rel in seen_files:
            continue
        seen_files.add(rel)
        if len(seen_files) > max_files:
            break
        fpath = repo_root / rel
        if not fpath.is_file():
            continue
        lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
        func = _func_from_nodeid(nid)
        start = 0
        end = len(lines)
        if func:
            for i, line in enumerate(lines):
                if line.strip().startswith(f"def {func}("):
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 8)
                    break
        excerpt = "\n".join(f"{start + j + 1:5d}| {lines[j]}" for j in range(start, end))
        blocks.append(f"### {rel} (around {func or 'file'})\n{excerpt}")
    if not blocks:
        return ""
    return "\n\n".join(blocks)


def pytest_argv_mismatch_hint(failures: list[tuple[str, str]]) -> str:
    if not any(is_pytest_argv_child_args_mismatch(msg) for _, msg in failures):
        return ""
    return (
        "Pytest argv mismatch: tests mock sys.argv as ``[__file__, 'runserver']`` but "
        "``get_child_arguments()`` sees ``python -m pytest`` because ``__main__.__spec__`` "
        "is set. Fix: add ``@mock.patch('__main__.__spec__', None)`` on the failing test "
        "(see ``test_warnoptions`` in the same class). Do not change expected argv to include "
        "``-m pytest`` unless the test explicitly targets module execution."
    )
