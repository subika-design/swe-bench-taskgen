from __future__ import annotations

import json
import re
import sys
from typing import Any

from .llm_client import chat_completions, extract_json_object, load_prompt

_BAD = re.compile(
    r"(?i)(^|\s)(rm\s+-rf\s+/|curl\s+[^|]*\|\s*sh|wget\s+[^|]*\|\s*sh|sudo\s+rm|mkfs\.|"
    r":\(\)\{|:&;|:>\s*/dev/|>\s*/etc/passwd)"
)


def _sanitize_commands(raw: list[Any], *, max_lines: int = 40) -> list[str]:
    out: list[str] = []
    for x in raw:
        if not isinstance(x, str):
            continue
        line = " ".join(x.strip().split())
        if not line or line.startswith("#"):
            continue
        if _BAD.search(line):
            print(f"# skip blocked remediation line: {line[:120]!r}", file=sys.stderr)
            continue
        out.append(line)
        if len(out) >= max_lines:
            break
    return out


def suggest_docker_remediation_commands(
    *,
    install_config: dict[str, Any],
    diagnostics_text: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    prior_commands: str = "",
) -> str:
    """
    Ask the LLM for extra shell lines to run in Docker before pytest (cumulative script body).

    Returns newline-separated shell commands (no shebang).
    """
    tpl = load_prompt("docker_remediation.txt")
    user = tpl.replace("{{diagnostics}}", diagnostics_text[:120_000])
    extra = ""
    if prior_commands.strip():
        extra = "\n\nCommands already applied in earlier attempts (do not repeat unless fixing a mistake):\n" + prior_commands[:50_000]
    user = user + extra + "\n\nCurrent install_config:\n" + json.dumps(install_config, indent=2)[:80_000]
    raw = chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system="You output only JSON as specified in the user message.",
        user=user,
        timeout_s=timeout_s,
        json_object=True,
    )
    obj = extract_json_object(raw)
    cmds = obj.get("commands")
    if not isinstance(cmds, list):
        return ""
    lines = _sanitize_commands(cmds)
    return "\n".join(lines) + ("\n" if lines else "")
