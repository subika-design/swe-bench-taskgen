"""Resolve Gradle project paths for Java test files (settings parse + LLM + fallback)."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .llm_client import chat_completions, extract_json_object, load_prompt

_INCLUDE_RE = re.compile(
    r"""include(?:\s*\(|\s+)['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_PROJECT_DIR_RE = re.compile(
    r"""project\s*\(\s*['"]:?([^'"]+)['"]\s*\)\s*\.projectDir\s*=\s*file\s*\(\s*['"]([^'"]+)['"]\s*\)""",
    re.MULTILINE,
)
_WRAPPER_DIRS = frozenset(
    {"module", "modules", "spring-boot-project", "core", "documentation", "build-plugin"}
)


@dataclass(frozen=True)
class GradleProjectIndex:
    """Known Gradle projects and optional filesystem directory roots."""

    projects: frozenset[str]
    dir_to_project: frozenset[tuple[str, str]]

    def project_list(self) -> list[str]:
        return sorted(self.projects)


def _normalize_gradle_project(raw: str) -> str:
    s = raw.strip().replace("\\", "/").strip("/")
    if not s:
        return ""
    if not s.startswith(":"):
        s = ":" + s
    return s


def _include_to_project(include_path: str) -> str:
    parts = [p for p in include_path.replace("\\", "/").split("/") if p]
    if not parts:
        return ""
    return _normalize_gradle_project(":".join(parts))


def discover_gradle_projects_from_settings(repo: Path) -> GradleProjectIndex:
    """Parse ``settings.gradle`` / ``settings.gradle.kts`` for ``include`` and ``projectDir``."""
    projects: set[str] = set()
    dir_pairs: set[tuple[str, str]] = set()
    for name in ("settings.gradle.kts", "settings.gradle"):
        path = repo / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:80_000]
        except OSError:
            continue
        for m in _INCLUDE_RE.finditer(text):
            proj = _include_to_project(m.group(1))
            if proj:
                projects.add(proj)
        for m in _PROJECT_DIR_RE.finditer(text):
            proj = _normalize_gradle_project(m.group(1))
            dir_rel = m.group(2).replace("\\", "/").strip("/")
            if proj and dir_rel:
                projects.add(proj)
                dir_pairs.add((dir_rel, proj))
    return GradleProjectIndex(frozenset(projects), frozenset(dir_pairs))


def _format_gradle_projects_block(index: GradleProjectIndex) -> str:
    lines = ["Known Gradle project paths:"]
    for p in index.project_list()[:80]:
        lines.append(f"- {p}")
    if index.dir_to_project:
        lines.append("")
        lines.append("projectDir mappings:")
        for dir_rel, proj in sorted(index.dir_to_project)[:80]:
            lines.append(f"- {dir_rel} -> {proj}")
    if not index.projects:
        lines.append("(none parsed — infer from test paths and typical Gradle layouts)")
    return "\n".join(lines)


def coerce_gradle_project(gp: str, index: GradleProjectIndex) -> str:
    """Map LLM/heuristic guesses onto projects declared in settings.gradle."""
    norm = _normalize_gradle_project(gp)
    if not norm:
        return norm
    if norm in index.projects:
        return norm
    if norm.startswith(":module:"):
        alt = _normalize_gradle_project(norm[len(":module:") :])
        if alt in index.projects:
            return alt
    tail = norm.lstrip(":").split(":")[-1]
    for proj in index.projects:
        if proj.lstrip(":").split(":")[-1] == tail:
            return proj
    return norm


def _fallback_map_test_path(path: str, index: GradleProjectIndex) -> str | None:
    p = path.replace("\\", "/").strip()
    if "/src/test/" not in p:
        return None
    prefix = p.split("/src/test/", 1)[0].strip("/")
    if not prefix:
        return None

    for dir_rel, proj in index.dir_to_project:
        d = dir_rel.strip("/")
        if prefix == d or prefix.endswith("/" + d):
            return proj

    parts = [x for x in prefix.split("/") if x]
    candidates: list[str] = []
    if parts:
        candidates.append(parts[-1])
    if len(parts) >= 2 and parts[0] in _WRAPPER_DIRS:
        candidates.append(parts[1])
    if len(parts) >= 2:
        candidates.append(":".join(parts[-2:]))
        candidates.append(f"{parts[-2]}:{parts[-1]}")

    name_to_proj: dict[str, str] = {}
    for proj in index.projects:
        tail = proj.lstrip(":").split(":")[-1]
        name_to_proj[tail] = proj

    for cand in candidates:
        if not cand:
            continue
        proj = name_to_proj.get(cand)
        if proj:
            return proj
        norm = _normalize_gradle_project(cand)
        if norm in index.projects:
            return norm

    return None


def _parse_llm_mappings(raw: str, test_paths: list[str]) -> dict[str, str]:
    obj = extract_json_object(raw)
    if not isinstance(obj, dict):
        raise ValueError("Gradle resolve output is not a JSON object")
    mappings = obj.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("Gradle resolve output missing mappings array")
    want = {p.replace("\\", "/") for p in test_paths}
    out: dict[str, str] = {}
    for item in mappings:
        if not isinstance(item, dict):
            continue
        tp = str(item.get("test_path") or "").replace("\\", "/").strip()
        gp = _normalize_gradle_project(str(item.get("gradle_project") or ""))
        if tp in want and gp:
            out[tp] = gp
    return out


def _apply_coerced_mappings(
    raw: dict[str, str], index: GradleProjectIndex
) -> dict[str, str]:
    return {tp: coerce_gradle_project(gp, index) for tp, gp in raw.items()}


def llm_resolve_gradle_projects_for_test_paths(
    repo: Path,
    test_paths: list[str],
    index: GradleProjectIndex,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    repo_id: str = "",
) -> dict[str, str]:
    paths = [p.replace("\\", "/") for p in test_paths if p.strip()]
    if not paths:
        return {}
    tpl = load_prompt("resolve_gradle_test_paths.txt")
    user = (
        tpl.replace("{{repo}}", repo_id or "unknown")
        .replace("{{gradle_projects_block}}", _format_gradle_projects_block(index))
        .replace("{{test_paths_block}}", "\n".join(f"- {p}" for p in paths[:30]))
    )
    raw = chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=(
            "You map Java test file paths to Gradle project paths. "
            "Return only valid JSON."
        ),
        user=user,
        timeout_s=timeout_s,
        json_object=True,
    )
    return _parse_llm_mappings(raw, paths)


def resolve_gradle_projects_for_test_paths(
    repo: Path,
    test_paths: list[str],
    *,
    api_key: str | None = None,
    base_url: str = "",
    model: str = "",
    timeout_s: int = 120,
    repo_id: str = "",
    instance_id: str = "",
) -> dict[str, str]:
    """
    Map each test path to a Gradle project path (``:foo`` or ``:a:b``).

    Uses LLM when ``api_key`` is set; fills gaps with settings-based fallback.
    """
    paths = [p.replace("\\", "/") for p in test_paths if p.strip()]
    if not paths:
        return {}

    index = discover_gradle_projects_from_settings(repo)
    out: dict[str, str] = {}

    for p in paths:
        fb = _fallback_map_test_path(p, index)
        if fb:
            out[p] = coerce_gradle_project(fb, index)

    unresolved = [p for p in paths if p not in out]
    if api_key and api_key.strip() and unresolved:
        try:
            llm_out = llm_resolve_gradle_projects_for_test_paths(
                repo,
                unresolved,
                index,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_s=timeout_s,
                repo_id=repo_id,
            )
            for tp, gp in _apply_coerced_mappings(llm_out, index).items():
                out[tp] = gp
        except Exception as ex:
            if instance_id:
                print(
                    f"  {instance_id}: Gradle project LLM failed ({ex}); using fallback",
                    file=sys.stderr,
                )

    for p in paths:
        if p in out:
            continue
        fb = _fallback_map_test_path(p, index)
        if fb:
            out[p] = coerce_gradle_project(fb, index)

    if instance_id:
        for p in paths[:8]:
            gp = out.get(p)
            if gp:
                print(
                    f"  {instance_id}: gradle project for test path -> {gp} ({p.split('/')[-1]})",
                    file=sys.stderr,
                )

    return out
