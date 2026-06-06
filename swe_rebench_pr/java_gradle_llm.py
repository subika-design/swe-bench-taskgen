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
_PRIMARY_TEST_MODULE_RE = re.compile(r"-tests-java\d", re.I)
_JDK8_TEST_MODULE_RE = re.compile(r"-tests-java8(?:$|[-:])", re.I)
_JDK9PLUS_TEST_MODULE_RE = re.compile(
    r"-tests-java9plus|-tests-java1[0-9](?:plus|$|[-:])",
    re.I,
)
_PACKAGE_DECL_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)


def _module_dir_from_test_rel(rel_path: str) -> str:
    rel = rel_path.replace("\\", "/").strip().lstrip("/")
    if "/src/test/" in rel:
        return rel.split("/src/test/", 1)[0].strip("/")
    if "/src/intTest/" in rel:
        return rel.split("/src/intTest/", 1)[0].strip("/")
    return ""


def is_repo_root_java_test_rel(rel_path: str) -> bool:
    """True when a test path lives under repo-root ``src/test/`` or ``src/intTest/``."""
    rel = rel_path.replace("\\", "/").strip().lstrip("/")
    return rel.startswith("src/test/") or rel.startswith("src/intTest/")


def _is_codegen_test_module_dir(module_dir: str) -> bool:
    name = module_dir.rsplit("/", 1)[-1].lower()
    return "codegen" in name and "test" in name


def _is_primary_test_module_dir(module_dir: str) -> bool:
    name = module_dir.rsplit("/", 1)[-1]
    if _is_codegen_test_module_dir(module_dir):
        return False
    return bool(
        _PRIMARY_TEST_MODULE_RE.search(name) or _JDK9PLUS_TEST_MODULE_RE.search(name)
    )


def _is_jdk8_test_module_dir(module_dir: str) -> bool:
    return bool(_JDK8_TEST_MODULE_RE.search(module_dir.rsplit("/", 1)[-1]))


def _is_jdk9plus_test_module_dir(module_dir: str) -> bool:
    return bool(_JDK9PLUS_TEST_MODULE_RE.search(module_dir.rsplit("/", 1)[-1]))


def _package_needs_jdk9plus_module(pkg: str) -> bool:
    """True when the package name suggests JDK9+ only tests."""
    low = (pkg or "").lower()
    return any(tok in low for tok in ("java9", "java10", "java11", "module", "jpms"))


def _jdk_flavor_module_score(module_dir: str, pkg: str = "") -> int:
    """
    Prefer ``*-tests-java8`` over ``*-tests-java9plus`` for generic packages.

    Picocli splits the main suite (java8) from JDK9+ API tests (java9plus).
    """
    if _is_jdk8_test_module_dir(module_dir):
        return 4
    if _is_jdk9plus_test_module_dir(module_dir):
        if pkg and not _package_needs_jdk9plus_module(pkg):
            return -4
        return 1
    return 0


def read_java_package_from_source(path: Path) -> str | None:
    """Read ``package`` declaration from a ``.java`` source file."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:8_000]
    except OSError:
        return None
    m = _PACKAGE_DECL_RE.search(text)
    return m.group(1).strip() if m else None


def java_fqcn_from_source_file(path: Path) -> str | None:
    """FQCN from on-disk ``package`` + public class filename."""
    if not path.is_file() or not path.name.endswith(".java"):
        return None
    pkg = read_java_package_from_source(path)
    simple = path.stem
    if pkg:
        return f"{pkg}.{simple}"
    return None


def _java_package_prefix_from_test_rel(rel_path: str) -> str:
    rel = rel_path.replace("\\", "/")
    for marker in ("src/test/java/", "src/intTest/java/"):
        if marker in rel and rel.endswith(".java"):
            pkg_path = rel.split(marker, 1)[1][:-5]
            return pkg_path.replace("/", ".")
    return ""


def _score_test_file_candidate(
    rel_path: str,
    requested_path: str,
    *,
    pkg_override: str | None = None,
) -> tuple[int, int, int, int, str]:
    """
    Rank on-disk test file paths when basename search returns multiple modules.

    Higher is better: suffix match, PR dir prefix, primary ``*-tests-java*`` module.
    """
    rel = rel_path.replace("\\", "/").strip().lstrip("/")
    req = requested_path.replace("\\", "/").strip().lstrip("/")
    module_dir = _module_dir_from_test_rel(rel)

    suffix_score = 0
    if req and "src/test/java/" in req:
        want_suffix = req.split("src/test/java/", 1)[1]
        if rel.endswith("src/test/java/" + want_suffix):
            suffix_score = 4
        elif want_suffix in rel:
            suffix_score = 2

    prefix_score = 0
    from .java_build import module_prefix_before_java_test

    req_prefix = module_prefix_before_java_test(req)
    if req_prefix is not None and module_dir:
        if module_dir == req_prefix or module_dir.endswith("/" + req_prefix):
            prefix_score = 3
        elif req_prefix in module_dir or module_dir.endswith(req_prefix):
            prefix_score = 1

    module_score = 0
    if _is_primary_test_module_dir(module_dir):
        module_score = 5
    elif _is_codegen_test_module_dir(module_dir):
        module_score = -5
    elif any(tok in module_dir.lower() for tok in ("example", "demo", "sample")):
        module_score = -2

    pkg = pkg_override or _java_package_prefix_from_test_rel(rel)
    if pkg and ".codegen." in pkg:
        module_score -= 3

    if is_repo_root_java_test_rel(req):
        if not module_dir:
            module_score += 20
        elif _is_primary_test_module_dir(module_dir) or _is_jdk8_test_module_dir(
            module_dir
        ) or _is_jdk9plus_test_module_dir(module_dir):
            module_score -= 15
    else:
        module_score += _jdk_flavor_module_score(module_dir, pkg)

    fqcn_match_score = 0
    if "src/test/java/" in req and req.endswith(".java"):
        want_class = req.rsplit("/", 1)[-1][:-5]
        expected_fqcn = _java_package_prefix_from_test_rel(req)
        if pkg and expected_fqcn:
            hit_fqcn = f"{pkg}.{want_class}"
            if hit_fqcn == expected_fqcn:
                fqcn_match_score = 3

    return (
        suffix_score + fqcn_match_score,
        prefix_score,
        module_score,
        len(module_dir),
        module_dir,
    )


def _score_gradle_project_for_test(
    proj: str,
    rel_path: str,
    requested_path: str,
) -> tuple[int, int, int, int, str]:
    """Rank Gradle project owners for a resolved on-disk test file."""
    tail = _project_tail(proj)
    module_dir = _module_dir_from_test_rel(rel_path)
    file_score = _score_test_file_candidate(rel_path, requested_path)

    project_score = 0
    if _is_primary_test_module_dir(tail):
        project_score = 5
    elif _is_codegen_test_module_dir(tail):
        project_score = -5

    return (
        file_score[0],
        file_score[1],
        file_score[2] + project_score,
        len(module_dir),
        tail,
    )


def _pick_best_test_file_hit(
    hits: list[Path],
    repo: Path,
    requested_norm: str,
) -> Path:
    if len(hits) == 1:
        return hits[0]
    scored = [
        (
            _score_test_file_candidate(
                h.relative_to(repo).as_posix(),
                requested_norm,
                pkg_override=read_java_package_from_source(h),
            ),
            h,
        )
        for h in hits
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


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
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("include") and "include(" not in stripped:
                continue
            for m in re.finditer(r"""['"]([^'"]+)['"]""", line):
                proj = _include_to_project(m.group(1))
                if proj:
                    projects.add(proj)
        if not projects:
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


def coerce_gradle_project(
    gp: str,
    index: GradleProjectIndex,
    repo: Path | None = None,
) -> str:
    """Map LLM/heuristic guesses onto projects declared in settings.gradle."""
    norm = _normalize_gradle_project(gp)
    if not norm:
        return norm
    if norm in index.projects:
        return norm
    if repo is not None:
        root = _root_gradle_project(index, repo)
        if norm == root:
            return root
    if norm.startswith(":module:"):
        alt = _normalize_gradle_project(norm[len(":module:") :])
        if alt in index.projects:
            return alt
    tail = norm.lstrip(":").split(":")[-1]
    if repo is not None:
        matches = gradle_projects_matching_short_name(tail, index, repo)
        if matches:
            return matches[0]
    for proj in sorted(index.projects, key=lambda p: -len(p.lstrip(":"))):
        if proj.lstrip(":").split(":")[-1] == tail:
            return proj
    return norm


_ROOT_PROJECT_NAME_RE = re.compile(
    r"""rootProject\.name\s*=\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def parse_root_project_name(repo: Path) -> str | None:
    """Gradle root project name from ``settings.gradle*``."""
    for name in ("settings.gradle.kts", "settings.gradle"):
        path = repo / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:80_000]
        except OSError:
            continue
        m = _ROOT_PROJECT_NAME_RE.search(text)
        if m:
            return m.group(1).strip()
    return None


def _root_gradle_project(index: GradleProjectIndex, repo: Path) -> str:
    """Gradle path for tests under repo-root ``src/test/``."""
    root_name = parse_root_project_name(repo)
    if root_name:
        return _normalize_gradle_project(root_name)
    return ":"


def _project_tail(project: str) -> str:
    return _normalize_gradle_project(project).lstrip(":").split(":")[-1]


def gradle_projects_matching_short_name(short: str, index: GradleProjectIndex, repo: Path) -> list[str]:
    """
    Gradle projects whose name equals ``short`` or shares a ``short-*`` prefix.

    When the root is named ``picocli`` and children include ``picocli-tests-java8``,
    ``:picocli:test`` is ambiguous — callers must use a full child path or root ``:test``.
    """
    name = short.lstrip(":").strip()
    if not name:
        return [":"]
    hits: set[str] = set()
    root = _root_gradle_project(index, repo)
    if _project_tail(root) == name:
        hits.add(root)
    for proj in index.projects:
        tail = _project_tail(proj)
        if tail == name or tail.startswith(name + "-"):
            hits.add(proj)

    def _rank(proj: str) -> tuple[int, int, int, int, str]:
        tail = _project_tail(proj)
        if proj == root:
            base = repo
        elif tail:
            base = repo / tail.replace(":", "/")
        else:
            base = repo
        has_tests = (base / "src/test/java").is_dir()
        exact = 10 if tail == name else 0
        child = 1 if tail.startswith(name + "-") else 0
        module_score = 0
        if _is_primary_test_module_dir(tail):
            module_score = 3
        elif _is_codegen_test_module_dir(tail):
            module_score = -3
        if exact == 0:
            module_score += _jdk_flavor_module_score(tail)
        return (exact, child, 1 if has_tests else 0, module_score, len(tail), tail)

    return sorted(hits, key=_rank, reverse=True)


def is_gradle_short_name_ambiguous(short: str, index: GradleProjectIndex, repo: Path) -> bool:
    return len(gradle_projects_matching_short_name(short, index, repo)) > 1


def gradle_task_for_project(
    project: str,
    task: str,
    index: GradleProjectIndex,
    repo: Path,
) -> str:
    """
    Qualified Gradle task (``:test``, ``:compileTestJava``, ``:sub:test``, …).

    Root-project tasks use a leading colon only (``:test``) so Gradle does not expand
    ambiguous names like ``picocli`` to every ``picocli-*`` subproject.
    """
    proj = _normalize_gradle_project(project) if project else ":"
    root = _root_gradle_project(index, repo)
    if proj == ":" or proj == root or _project_tail(proj) == _project_tail(root):
        return f":{task}"
    return f"{proj}:{task}"


def gradle_test_task_for_project(project: str, index: GradleProjectIndex, repo: Path) -> str:
    """Gradle test task name (``:test`` or ``:subproject:test``) avoiding ambiguous abbreviations."""
    return gradle_task_for_project(project, "test", index, repo)


def _iter_project_directory_pairs(
    index: GradleProjectIndex, repo: Path
) -> list[tuple[str, str]]:
    """``(relative_dir, gradle_project_path)`` sorted longest-dir first."""
    pairs: dict[str, str] = {}
    root_proj = _root_gradle_project(index, repo)
    pairs[""] = root_proj
    for dir_rel, proj in index.dir_to_project:
        d = dir_rel.strip("/")
        if d:
            pairs[d] = proj
    for proj in index.projects:
        tail = proj.lstrip(":").replace(":", "/")
        if tail and (repo / tail).is_dir():
            pairs.setdefault(tail, proj)
    return sorted(pairs.items(), key=lambda x: (-len(x[0]), x[0]))


def _find_test_file_on_disk(repo: Path, test_path: str) -> Path | None:
    """Resolve a PR test path to an on-disk file (direct path or unique ``**/src/test/java/**``)."""
    norm = test_path.replace("\\", "/").strip().lstrip("/")
    direct = repo / norm
    if direct.is_file():
        return direct
    base = Path(norm).name
    if not base.endswith(".java"):
        return None
    hits = sorted(repo.glob(f"**/src/test/java/**/{base}"))
    hits = [p for p in hits if p.is_file()]
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    want_suffix = norm.split("src/test/java/", 1)[-1] if "src/test/java/" in norm else ""
    if want_suffix:
        suffix_hits = [
            h
            for h in hits
            if h.relative_to(repo).as_posix().endswith("src/test/java/" + want_suffix)
        ]
        if len(suffix_hits) == 1:
            return suffix_hits[0]
        if suffix_hits:
            return _pick_best_test_file_hit(suffix_hits, repo, norm)
    return _pick_best_test_file_hit(hits, repo, norm)


def gradle_projects_owning_relative_path(
    rel_path: str,
    index: GradleProjectIndex,
    repo: Path,
    *,
    requested_path: str = "",
) -> list[str]:
    """Gradle projects whose directory contains ``rel_path`` (best match first)."""
    rel = rel_path.replace("\\", "/").strip().lstrip("/")
    owners: list[str] = []
    for dir_rel, proj in _iter_project_directory_pairs(index, repo):
        if dir_rel:
            if rel == dir_rel or rel.startswith(dir_rel + "/"):
                owners.append(proj)
        elif rel.startswith("src/test/") or rel.startswith("src/intTest/"):
            owners.append(proj)
    if not owners:
        return []
    req = requested_path or rel_path
    ranked = sorted(
        owners,
        key=lambda proj: _score_gradle_project_for_test(proj, rel, req),
        reverse=True,
    )
    seen: set[str] = set()
    out: list[str] = []
    for proj in ranked:
        if proj not in seen:
            seen.add(proj)
            out.append(proj)
    return out


def resolve_gradle_project_for_test_path(
    repo: Path,
    test_path: str,
    index: GradleProjectIndex,
) -> str | None:
    """
    Pick an unambiguous Gradle project for a Java test file path.

    Prefer filesystem ownership (settings ``include`` / ``projectDir``), then longest
    matching project name. Never return a short name that Gradle would treat as ambiguous.
    """
    norm = test_path.replace("\\", "/").strip()
    norm_l = norm.lstrip("/")
    if is_repo_root_java_test_rel(norm_l):
        return _root_gradle_project(index, repo)

    disk = _find_test_file_on_disk(repo, norm)
    rel = disk.relative_to(repo).as_posix() if disk is not None else norm_l

    owners = gradle_projects_owning_relative_path(
        rel, index, repo, requested_path=norm
    )
    if owners:
        return owners[0]

    from .java_build import module_prefix_before_java_test

    prefix = module_prefix_before_java_test(norm)
    if prefix is None:
        return None
    if prefix == "":
        return _root_gradle_project(index, repo)

    for dir_rel, proj in _iter_project_directory_pairs(index, repo):
        if prefix == dir_rel or prefix.endswith("/" + dir_rel):
            return proj

    matches = gradle_projects_matching_short_name(prefix.split("/")[-1], index, repo)
    if matches:
        return matches[0]

    return _fallback_map_test_path(norm, index, repo)


def _mapping_matches_test_path(
    path: str,
    project: str,
    index: GradleProjectIndex,
    repo: Path,
) -> bool:
    """Reject LLM guesses that do not own the test file path."""
    proj = _normalize_gradle_project(project)
    disk = _find_test_file_on_disk(repo, path)
    if disk is not None:
        owners = gradle_projects_owning_relative_path(
            disk.relative_to(repo).as_posix(),
            index,
            repo,
            requested_path=path,
        )
        if owners:
            return proj == owners[0] or proj in owners
    resolved = resolve_gradle_project_for_test_path(repo, path, index)
    return resolved is not None and proj == _normalize_gradle_project(resolved)


def _fallback_map_test_path(path: str, index: GradleProjectIndex, repo: Path | None = None) -> str | None:
    from .java_build import module_prefix_before_java_test

    prefix = module_prefix_before_java_test(path)
    if prefix is None:
        return None
    if prefix == "":
        if repo is not None:
            return _root_gradle_project(index, repo)
        return ":"

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

    if repo is not None:
        for cand in reversed(candidates):
            if not cand:
                continue
            matches = gradle_projects_matching_short_name(cand, index, repo)
            if matches:
                return matches[0]

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
    raw: dict[str, str],
    index: GradleProjectIndex,
    repo: Path | None = None,
) -> dict[str, str]:
    return {tp: coerce_gradle_project(gp, index, repo) for tp, gp in raw.items()}


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
        resolved = resolve_gradle_project_for_test_path(repo, p, index)
        if resolved:
            out[p] = coerce_gradle_project(resolved, index, repo)

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
            for tp, gp in _apply_coerced_mappings(llm_out, index, repo).items():
                if _mapping_matches_test_path(tp, gp, index, repo):
                    out[tp] = gp
                elif instance_id:
                    print(
                        f"  {instance_id}: reject Gradle map {gp} for {tp.split('/')[-1]} "
                        f"(path/module mismatch)",
                        file=sys.stderr,
                    )
        except Exception as ex:
            if instance_id:
                print(
                    f"  {instance_id}: Gradle project LLM failed ({ex}); using fallback",
                    file=sys.stderr,
                )

    for p in paths:
        if p in out:
            continue
        resolved = resolve_gradle_project_for_test_path(repo, p, index)
        if resolved:
            out[p] = coerce_gradle_project(resolved, index, repo)

    if instance_id:
        for p in paths[:8]:
            gp = out.get(p)
            if gp:
                print(
                    f"  {instance_id}: gradle project for test path -> {gp} ({p.split('/')[-1]})",
                    file=sys.stderr,
                )

    return out
