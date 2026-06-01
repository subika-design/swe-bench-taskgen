from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_LLM_MODEL = "claude-opus-4-6"

# Legacy CLI/env names → current Anthropic Messages API model ids.
_ANTHROPIC_LEGACY_TO_MESSAGES_MODEL = {
    "claude-3-opus-20240229": "claude-opus-4-6",
    "claude-3-sonnet-20240229": "claude-sonnet-4-6",
    "claude-3-haiku-20240307": "claude-haiku-4-5-20251001",
}


def is_anthropic_model(model: str) -> bool:
    return model.strip().lower().startswith("claude")


def anthropic_messages_model_id(model: str) -> str:
    """Resolve env/CLI model name to a live Anthropic Messages API model id."""
    override = (os.environ.get("ANTHROPIC_MESSAGES_MODEL_ID") or "").strip()
    if override:
        return override
    name = model.strip()
    return _ANTHROPIC_LEGACY_TO_MESSAGES_MODEL.get(name, name)


def anthropic_omit_sampling_params(api_model: str) -> bool:
    """Claude 4.x Messages API models reject temperature/top_p."""
    prefixes = ("claude-opus-4-", "claude-sonnet-4-", "claude-haiku-4-")
    return any(api_model.startswith(p) for p in prefixes)


def resolve_llm_api_key(model: str, explicit_key: str = "") -> str:
    """Pick API key for ``model`` (Anthropic for Claude, else OpenAI-compatible)."""
    if explicit_key.strip():
        return explicit_key.strip()
    if is_anthropic_model(model):
        return (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY") or "").strip()
    return (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def chat_completions(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout_s: int,
    json_object: bool = False,
) -> str:
    key = resolve_llm_api_key(model, api_key)
    if is_anthropic_model(model):
        anthropic_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        if key.startswith("sk-ant-"):
            anthropic_key = key
        elif key and not anthropic_key:
            anthropic_key = key
        if anthropic_key:
            return _anthropic_messages(
                api_key=anthropic_key,
                model=anthropic_messages_model_id(model),
                system=system,
                user=user,
                timeout_s=timeout_s,
                json_object=json_object,
            )
    return _openai_chat_completions(
        api_key=key,
        base_url=base_url,
        model=model,
        system=system,
        user=user,
        timeout_s=timeout_s,
        json_object=json_object,
    )


def _openai_chat_completions(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout_s: int,
    json_object: bool,
) -> str:
    payload: dict = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_object:
        payload["response_format"] = {"type": "json_object"}
    url = base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def _anthropic_messages(
    *,
    api_key: str,
    model: str,
    system: str,
    user: str,
    timeout_s: int,
    json_object: bool,
) -> str:
    system_text = system
    if json_object:
        system_text += "\n\nRespond with a single valid JSON object only. No markdown fences or commentary."
    payload: dict = {
        "model": model,
        "max_tokens": 16384,
        "system": system_text,
        "messages": [{"role": "user", "content": user}],
    }
    if not anthropic_omit_sampling_params(model):
        payload["temperature"] = 0
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    blocks = body.get("content") or []
    parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    return "".join(parts)


def _loads_json_lenient(text: str) -> Any:
    """Parse JSON from LLM output; tolerate trailing commas and minor formatting issues."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Trailing commas before } or ]
    fixed = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # Single-quoted keys/strings (common LLM mistake)
    fixed2 = re.sub(r"'([^'\\]*)'", r'"\1"', fixed)
    return json.loads(fixed2)


def extract_json_array(text: str) -> list:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    data = _loads_json_lenient(text)
    if not isinstance(data, list):
        raise ValueError("Expected JSON array from model output")
    return data


def extract_json_object(text: str) -> dict:
    text = text.strip()
    candidates: list[str] = []
    if "```" in text:
        for m in re.finditer(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE):
            candidates.append(m.group(1))
    if text.startswith("{"):
        candidates.insert(0, text)
    else:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            candidates.append(m.group(0))
    if not candidates:
        raise ValueError("No JSON object found in model output")
    last_err: json.JSONDecodeError | None = None
    for blob in candidates:
        try:
            data = _loads_json_lenient(blob)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as ex:
            last_err = ex
            continue
    if last_err is not None:
        raise last_err
    raise ValueError("No JSON object found in model output")


def load_prompt(name: str) -> str:
    root = Path(__file__).resolve().parent.parent / "prompts"
    return (root / name).read_text(encoding="utf-8")
