"""Strip junk paths (e.g. macOS .DS_Store) from unified PR diffs."""

from __future__ import annotations

import re

_JUNK_BASENAMES = frozenset(
    {
        ".ds_store",
        "thumbs.db",
        "desktop.ini",
    }
)

_JUNK_PREFIXES = (
    "__macosx/",
)


def is_junk_patch_path(path: str) -> bool:
    """True if this path should not appear in impl/test patches."""
    p = path.replace("\\", "/").strip()
    if not p:
        return True
    low = p.lower()
    base = low.rsplit("/", 1)[-1]
    if base in _JUNK_BASENAMES:
        return True
    return any(low.startswith(pref) for pref in _JUNK_PREFIXES)


def filter_junk_from_unified_diff(diff: str) -> str:
    """Drop per-file diff chunks whose paths are junk (binary .DS_Store hunks, etc.)."""
    if not diff.strip():
        return diff
    chunks = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    kept: list[str] = []
    for ch in chunks:
        s = ch.strip("\n")
        if not s.startswith("diff --git "):
            if ch and not kept:
                kept.append(ch)
            continue
        m = re.search(r"^diff --git a/(\S+) b/(\S+)$", s, re.MULTILINE)
        if not m:
            continue
        if is_junk_patch_path(m.group(1)) or is_junk_patch_path(m.group(2)):
            continue
        kept.append(ch if ch.endswith("\n") else ch + "\n")
    return "".join(kept)
