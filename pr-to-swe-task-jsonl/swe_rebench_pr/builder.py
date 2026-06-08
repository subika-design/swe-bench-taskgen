from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from .diff_split import split_impl_and_test_patch
from .docker_discover import discover_fail_to_pass_pass_to_pass_docker
from .gh_pr import ParsedPR, clone_repo_at, fetch_pr_diff, fetch_pr_metadata, validate_base_commit_reachable
from .install_config_build import build_install_config_for_repo
from .install_llm import llm_fix_recipe
from .env_setup import try_pip_install_and_freeze
from .issues import build_problem_and_hints
from .schema import OUTPUT_KEYS
from .swebench_align import repair_jsonl_row_for_harness
from .task_type import TASK_TYPE_SKIP
from .languages import (
    detect_language_from_changed_paths,
    detect_language_from_patches,
    detect_language_from_repo,
    detect_language_from_repo_build_markers,
    normalize_language,
)
from .versioning import harness_version_for_instance, normalized_install_version


def _builder_skip_row(
    pr: ParsedPR,
    meta,
    *,
    patch: str,
    test_patch: str,
    problem: str,
    hints: str,
    version: str,
    task_language: str,
    install_cfg: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "instance_id": pr.instance_id,
        "patch": patch,
        "repo": pr.repo_id,
        "base_commit": meta.base_commit,
        "head_commit": meta.head_commit,
        "hints_text": hints,
        "created_at": meta.created_at,
        "test_patch": test_patch,
        "problem_statement": problem,
        "version": version,
        "environment_setup_commit": meta.base_commit,
        "FAIL_TO_PASS": json.dumps([]),
        "PASS_TO_PASS": json.dumps([]),
        "task_type": TASK_TYPE_SKIP,
        "language": task_language,
        "install_config": install_cfg,
        "requirements": "",
        "environment": "",
    }
    return repair_jsonl_row_for_harness(row)


def resolve_task_language(
    language: str,
    *,
    repo: Path | None = None,
    patch: str = "",
    test_patch: str = "",
    repo_id: str = "",
) -> str:
    raw = language.strip().lower()
    if raw == "auto":
        from .repo_detect import uses_django_runtests

        if uses_django_runtests(repo=repo, repo_id=repo_id):
            return "python"
        if repo is not None:
            from .integration_build import resolve_integration_task_language

            integration_lang = resolve_integration_task_language(
                repo, patch=patch, test_patch=test_patch
            )
            if integration_lang:
                return integration_lang
        # Prefer test_patch signal over implementation paths in hybrid repos.
        from_test_patch = detect_language_from_patches("", test_patch)
        if from_test_patch:
            return from_test_patch
        from_test_changed = detect_language_from_changed_paths("", test_patch)
        if from_test_changed:
            return from_test_changed
        if repo is not None:
            from_build = detect_language_from_repo_build_markers(repo)
            if from_build:
                return from_build
        from_patches = detect_language_from_patches(patch, test_patch)
        if from_patches:
            return from_patches
        from_changed = detect_language_from_changed_paths(patch, test_patch)
        if from_changed:
            return from_changed
        if repo is not None:
            from_repo = detect_language_from_repo(repo)
            if from_repo:
                return from_repo
        return "python"
    return normalize_language(raw)


def build_row(
    pr: ParsedPR,
    *,
    llm_patch_split: Optional[tuple[str, str, str, int]],
    llm_install: Optional[tuple[str, str, str, int]],
    clone_depth: int,
    clone_timeout: int,
    run_install_freeze: bool,
    install_timeout: int,
    discover_tests_docker: bool = True,
    discover_work_parent: Path,
    discover_clone_depth: int,
    docker_timeout: int = 7200,
    llm_docker_remediate: Optional[tuple[str, str, str, int]] = None,
    docker_remediation_rounds: int = 3,
    test_patch_apply_attempts: int = 5,
    docker_pip_freeze_after: bool = True,
    docker_remediate_skips: bool = False,
    language: str = "python",
    force_rebuild_harness_images: bool = False,
    build_instance_harness_images: bool = False,
) -> dict[str, Any]:
    meta = fetch_pr_metadata(pr)
    diff = fetch_pr_diff(pr.owner, pr.repo, pr.number)
    patch, test_patch = split_impl_and_test_patch(diff, repo_id=pr.repo_id, llm=llm_patch_split)

    problem, hints = build_problem_and_hints(
        pr.owner,
        pr.repo,
        pr_title=meta.title,
        pr_body=meta.body,
        closing_issue_numbers=meta.closing_issue_numbers,
        first_commit_date=meta.first_commit_date or meta.created_at,
    )

    version = f"0.0-{meta.base_commit[:8]}"
    from .languages import get_language_spec

    task_language = resolve_task_language(
        language, patch=patch, test_patch=test_patch, repo_id=pr.repo_id
    )
    install_cfg: dict[str, Any] = dict(get_language_spec(task_language).default_install_config)
    requirements = ""
    environment = ""

    work = Path(tempfile.mkdtemp(prefix="swe_rebench_row_", dir=str(discover_work_parent)))
    try:
        repo = work / "repo"
        clone_repo_at(pr, repo, meta.base_commit, depth=clone_depth, timeout=clone_timeout)
        validate_base_commit_reachable(repo, meta.base_commit)
        from .patch_validate import (
            PatchSplitUnrecoverableError,
            ensure_patch_commits_fetched,
            ensure_valid_patch_split,
        )

        ensure_patch_commits_fetched(repo, meta.base_commit, meta.head_commit)
        try:
            patch, test_patch = ensure_valid_patch_split(
                pr,
                repo,
                diff,
                patch,
                test_patch,
                base_commit=meta.base_commit,
                head_sha=meta.head_commit,
                llm_split_used=llm_patch_split is not None,
                language=task_language,
            )
        except PatchSplitUnrecoverableError as e:
            print(f"  {pr.instance_id}: skip — {e}", file=sys.stderr)
            return _builder_skip_row(
                pr,
                meta,
                patch=patch,
                test_patch=test_patch,
                problem=problem,
                hints=hints,
                version=version,
                task_language=task_language,
                install_cfg=install_cfg,
            )
        task_language = resolve_task_language(
            language, repo=repo, patch=patch, test_patch=test_patch, repo_id=pr.repo_id
        )
        from .patch_paths import collect_gradable_test_paths_from_patch

        gradable_paths = collect_gradable_test_paths_from_patch(
            test_patch, task_language
        )
        if not gradable_paths:
            print(
                f"  {pr.instance_id}: skip — no gradable test paths in test_patch "
                f"(language={task_language})",
                file=sys.stderr,
            )
            return _builder_skip_row(
                pr,
                meta,
                patch=patch,
                test_patch=test_patch,
                problem=problem,
                hints=hints,
                version=version,
                task_language=task_language,
                install_cfg=install_cfg,
            )
        from .patch_paths import has_runnable_python_tests

        if task_language == "python" and not has_runnable_python_tests(
            test_patch, task_language
        ):
            print(
                f"  {pr.instance_id}: skip — no runnable pytest modules in test_patch "
                f"(docs-only or non-test paths)",
                file=sys.stderr,
            )
            return _builder_skip_row(
                pr,
                meta,
                patch=patch,
                test_patch=test_patch,
                problem=problem,
                hints=hints,
                version=version,
                task_language=task_language,
                install_cfg=install_cfg,
            )
        from .languages import collect_test_targets

        if task_language == "c":
            from .integration_build import merge_hybrid_c_integration_paths

            test_paths, _runner = merge_hybrid_c_integration_paths(
                patch, test_patch, language=task_language
            )
        else:
            test_paths = collect_test_targets(task_language, patch, test_patch)
        version = normalized_install_version(repo, meta.base_commit)
        install_cfg = build_install_config_for_repo(
            repo,
            task_language,
            pr.repo_id,
            test_paths=test_paths,
            llm_install=llm_install,
            patch=patch,
            test_patch=test_patch,
            instance_id=pr.instance_id,
        )
        version = harness_version_for_instance(pr.instance_id, task_language, version)
        from .swebench_align import export_install_config_for_harness

        install_cfg = export_install_config_for_harness(install_cfg)
        install_cfg["language"] = task_language

        if run_install_freeze:
            req, env, log = try_pip_install_and_freeze(
                repo, work, install_cfg, timeout_s=install_timeout
            )
            if not req and llm_install is not None:
                try:
                    install_cfg = llm_fix_recipe(
                        install_cfg,
                        log,
                        api_key=llm_install[0],
                        base_url=llm_install[1],
                        model=llm_install[2],
                        timeout_s=llm_install[3],
                    )
                    shutil.rmtree(work / ".install_venv", ignore_errors=True)
                    req, env, log2 = try_pip_install_and_freeze(
                        repo, work, install_cfg, timeout_s=install_timeout
                    )
                    log = log + "\n" + log2
                except Exception as e:
                    print(f"  {pr.instance_id}: install fix LLM failed: {e}", file=sys.stderr)
            requirements, environment = req, env
            if not requirements:
                print(f"  {pr.instance_id}: pip freeze empty (install may have failed)", file=sys.stderr)

    finally:
        shutil.rmtree(work, ignore_errors=True)

    row: dict[str, Any] = {
        "instance_id": pr.instance_id,
        "patch": patch,
        "repo": pr.repo_id,
        "base_commit": meta.base_commit,
        "head_commit": meta.head_commit,
        "hints_text": hints,
        "created_at": meta.created_at,
        "test_patch": test_patch,
        "problem_statement": problem,
        "version": version,
        "environment_setup_commit": meta.base_commit,
        "FAIL_TO_PASS": json.dumps([]),
        "PASS_TO_PASS": json.dumps([]),
        "task_type": "",
        "language": task_language,
        "install_config": install_cfg,
        "requirements": requirements,
        "environment": environment,
    }

    if discover_tests_docker:
        print(f"  {pr.instance_id}: language={task_language}", file=sys.stderr)
        f2p, p2p, task_type = discover_fail_to_pass_pass_to_pass_docker(
            row,
            pr,
            install_cfg,
            task_language,
            work_parent=discover_work_parent,
            clone_timeout=clone_timeout,
            clone_depth=discover_clone_depth,
            docker_timeout=docker_timeout,
            llm_remediate=llm_docker_remediate,
            remediation_max_rounds=docker_remediation_rounds,
            test_patch_apply_attempts=test_patch_apply_attempts,
            docker_pip_freeze_after=docker_pip_freeze_after,
            remediate_skips=docker_remediate_skips,
            force_rebuild_harness_images=force_rebuild_harness_images,
            build_instance_harness_images=build_instance_harness_images,
        )
        row["FAIL_TO_PASS"] = json.dumps(f2p)
        row["PASS_TO_PASS"] = json.dumps(p2p)
        row["task_type"] = task_type

    row = repair_jsonl_row_for_harness(row)
    # Stable key order
    return {k: row[k] for k in OUTPUT_KEYS if k in row}


def row_to_jsonl_line(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False) + "\n"
