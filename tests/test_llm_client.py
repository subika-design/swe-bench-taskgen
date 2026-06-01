from __future__ import annotations

import pytest

from swe_rebench_pr.llm_client import extract_json_object


def test_extract_json_object_trailing_comma():
    raw = '{"test_patch": "diff --git a/foo\\n",\n}'
    obj = extract_json_object(raw)
    assert "test_patch" in obj


def test_extract_json_object_single_quoted_keys():
    raw = "{'commands': ['echo ok']}"
    obj = extract_json_object(raw)
    assert obj["commands"] == ["echo ok"]


def test_extract_json_object_from_markdown_fence():
    raw = 'Here:\n```json\n{"a": 1}\n```\n'
    assert extract_json_object(raw) == {"a": 1}
