"""Cheap pre-Docker checks: skip PRs unlikely to produce valid SWE-bench tasks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .diff_split import split_impl_and_test_patch
from .gh_pr import ParsedPR, fetch_pr_diff, run_gh
from .languages import (
    collect_test_targets_from_test_patch,
    detect_language_from_patches,
    get_language_spec,
    normalize_language,
)

# Repo root markers that usually need non-Docker CI (tier F).
_HARD_REPO_MARKERS: tuple[tuple[str, str], ...] = (
    ("react-native", "React Native (mobile toolchain)"),
    ("@react-native/", "React Native dependency"),
    ("playwright", "Playwright browser tests"),
    ("puppeteer", "Puppeteer browser tests"),
    ("electron", "Electron desktop app"),
    ("docker-compose", "docker-compose services"),
    ("services:", "docker-compose services block"),
    ("ffmpeg", "FFmpeg native stack"),
    ("PHP-FFMpeg", "PHP-FFMpeg extension stack"),
)

# Filenames whose presence strongly suggests full Rails app (tier F).
_RAILS_MARKERS = ("config/application.rb", "config/database.yml", "bin/rails")


@dataclass
class PreflightResult:
    pr: ParsedPR
    passed: bool
    language: str | None = None
    test_paths_in_diff: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _repo_root_names(owner: str, repo: str) -> set[str]:
    try:
        raw = run_gh(["api", f"repos/{owner}/{repo}/contents/"], strip=False)
        items = json.loads(raw)
        if isinstance(items, list):
            return {str(x.get("name") or "") for x in items if isinstance(x, dict)}
    except (RuntimeError, json.JSONDecodeError):
        pass
    return set()


def _repo_has_path(owner: str, repo: str, rel: str) -> bool:
    try:
        run_gh(["api", f"repos/{owner}/{repo}/contents/{rel}"])
        return True
    except RuntimeError:
        return False


def _scan_text_for_hard_markers(text: str) -> list[str]:
    low = text.lower()
    hits: list[str] = []
    for needle, label in _HARD_REPO_MARKERS:
        if needle.lower() in low:
            hits.append(label)
    return hits


def preflight_pr(
    pr: ParsedPR,
    *,
    language: str = "auto",
    require_test_paths_in_diff: bool = True,
    allow_llm_test_patch: bool = False,
) -> PreflightResult:
    """
    Fast gate before Docker discover.

    Does not clone; uses ``gh pr diff`` and GitHub API for root markers.
    """
    blockers: list[str] = []
    warnings: list[str] = []

    try:
        diff = fetch_pr_diff(pr.owner, pr.repo, pr.number)
    except RuntimeError as e:
        return PreflightResult(pr=pr, passed=False, blockers=[f"Could not fetch PR diff: {e}"])

    patch, test_patch = split_impl_and_test_patch(diff, repo_id=pr.repo_id)
    root_names = _repo_root_names(pr.owner, pr.repo)
    lang = language.strip().lower()
    if lang == "auto":
        lang = (
            detect_language_from_patches("", test_patch)
            or detect_language_from_patches(patch, test_patch)
            or ("c" if "CMakeLists.txt" in root_names else "python")
        )
    else:
        lang = normalize_language(lang)

    from .patch_paths import collect_gradable_test_paths_from_patch

    gradable_paths = collect_gradable_test_paths_from_patch(test_patch, lang)
    test_paths = collect_test_targets_from_test_patch(lang, test_patch)
    if require_test_paths_in_diff and not gradable_paths and not allow_llm_test_patch:
        blockers.append("No gradable test file paths in PR diff (test_patch empty)")
    if lang in ("python", "py") and test_patch.strip() and not gradable_paths:
        blockers.append(
            "test_patch has no Python test modules (test_*.py / *_test.py)"
        )

    # Scan diff + a few root files for hard markers
    marker_hits = _scan_text_for_hard_markers(diff)
    if "package.json" in root_names:
        try:
            import base64

            raw = run_gh(["api", f"repos/{pr.owner}/{pr.repo}/contents/package.json"], strip=False)
            payload = json.loads(raw)
            content = payload.get("content") or ""
            if payload.get("encoding") == "base64" and content:
                text = base64.b64decode(content).decode("utf-8", errors="replace")
                marker_hits.extend(_scan_text_for_hard_markers(text))
        except Exception:
            pass

    if "Gemfile" in root_names:
        for rel in _RAILS_MARKERS:
            if _repo_has_path(pr.owner, pr.repo, rel):
                blockers.append(f"Rails app marker: {rel}")
                break

    for hit in sorted(set(marker_hits)):
        if "React Native" in hit or "Playwright" in hit or "FFmpeg" in hit:
            blockers.append(hit)
        else:
            warnings.append(hit)

    # Language-specific sanity (artifact: react-native in package.json)
    spec = get_language_spec(lang)
    if lang == "javascript" and "package.json" in root_names:
        try:
            import base64

            raw = run_gh(["api", f"repos/{pr.owner}/{pr.repo}/contents/package.json"], strip=False)
            payload = json.loads(raw)
            content = payload.get("content") or ""
            if payload.get("encoding") == "base64" and content:
                pkg_text = base64.b64decode(content).decode("utf-8", errors="replace")
                low = pkg_text.lower()
                if '"react-native"' in low or "'react-native'" in low:
                    blockers.append("React Native app (mobile CI not supported)")
        except Exception:
            pass

    passed = not blockers
    return PreflightResult(
        pr=pr,
        passed=passed,
        language=lang,
        test_paths_in_diff=test_paths,
        blockers=blockers,
        warnings=warnings,
    )


def preflight_urls(
    urls: list[str],
    *,
    language: str = "auto",
    require_test_paths_in_diff: bool = True,
    allow_llm_test_patch: bool = False,
) -> list[PreflightResult]:
    from .gh_pr import parse_pr_url

    out: list[PreflightResult] = []
    for url in urls:
        out.append(
            preflight_pr(
                parse_pr_url(url),
                language=language,
                require_test_paths_in_diff=require_test_paths_in_diff,
                allow_llm_test_patch=allow_llm_test_patch,
            )
        )
    return out


def write_preflight_report(results: list[PreflightResult], path: Path) -> None:
    lines: list[str] = []
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(f"{status}\t{r.pr.instance_id}\tlang={r.language or '?'}")
        for b in r.blockers:
            lines.append(f"  BLOCKER: {b}")
        for w in r.warnings:
            lines.append(f"  warn: {w}")
        if r.test_paths_in_diff:
            lines.append(f"  tests: {', '.join(r.test_paths_in_diff[:5])}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
