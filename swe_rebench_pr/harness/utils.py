"""Lightweight harness utilities (no datasets/dotenv/swebench package deps)."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def run_sequential(func, payloads):
    succeeded, failed = [], []
    for payload in payloads:
        try:
            func(*payload)
            succeeded.append(payload)
        except Exception:
            failed.append(payload)
    return succeeded, failed


def run_threadpool(func, payloads, max_workers):
    if max_workers <= 0:
        return run_sequential(func, payloads)
    succeeded, failed = [], []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(func, *payload): payload for payload in payloads}
        for future in as_completed(futures):
            payload = futures[future]
            try:
                future.result()
                succeeded.append(payload)
            except Exception:
                failed.append(payload)
    return succeeded, failed


def ansi_escape(text: str) -> str:
    return re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])").sub("", text)


def get_modified_files(patch: str) -> list[str]:
    try:
        from unidiff import PatchSet

        out: list[str] = []
        for file in PatchSet(patch):
            if file.source_file != "/dev/null":
                path = file.source_file
                if path.startswith("a/"):
                    path = path[2:]
                out.append(path)
        return out
    except ImportError:
        return [
            m[2:] if m.startswith("a/") else m
            for m in re.findall(r"^diff --git a/(\S+)", patch, re.MULTILINE)
        ]


def get_new_files(patch: str) -> list[str]:
    try:
        from unidiff import PatchSet

        out: list[str] = []
        for file in PatchSet(patch):
            if file.source_file == "/dev/null":
                target = file.target_file
                if target.startswith("b/"):
                    target = target[2:]
                out.append(target)
        return out
    except ImportError:
        return re.findall(r"^diff --git a/dev/null b/(\S+)", patch, re.MULTILINE)


def load_cached_environment_yml(instance_id: str) -> str | None:
    """Optional per-instance conda cache (not used by default in this pipeline)."""
    try:
        repo, _number = instance_id.rsplit("-", 1)
    except ValueError:
        return None
    cache_dir = Path(__file__).resolve().parents[2] / "logs" / "env_cache"
    for candidate in (
        cache_dir / f"{instance_id}.yml",
        cache_dir / f"{repo.replace('/', '__')}.yml",
    ):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return None
