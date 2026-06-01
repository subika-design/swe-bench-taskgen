"""Bash-safe quoting for pip requirements and shell tokens."""

from __future__ import annotations

import re


def shell_quote_token(s: str) -> str:
    """Quote a token for bash if it contains shell metacharacters (``<``, ``>``, etc.)."""
    token = str(s or "").strip()
    if not token:
        return "''"
    if re.match(r"^[a-zA-Z0-9@%_+=:,./-]+$", token):
        return token
    return "'" + token.replace("'", "'\"'\"'") + "'"


def shell_join_pip_requirements(packages: list[str]) -> str:
    """Join pip requirement strings for ``python -m pip install ...``."""
    return " ".join(shell_quote_token(p) for p in packages if str(p or "").strip())
