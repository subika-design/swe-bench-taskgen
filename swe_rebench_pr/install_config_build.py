"""Repo-first install_config: CI + manifests + heuristics (+ optional LLM refine)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .ci_extract import CiExtractDraft, extract_ci_draft, merge_ci_draft_into_config
from .install_cache import load_cached_install_config, save_cached_install_config
from .install_llm import (
    default_install_config_heuristic,
    normalize_install_config,
    refine_install_config_llm,
    sanitize_install_config_for_docker,
)
from .manifest_extract import merge_manifest_into_config


def _build_repo_base_config(
    repo: Path,
    language: str,
    repo_id: str,
    *,
    use_cache: bool,
) -> tuple[dict[str, Any], CiExtractDraft]:
    """CI + heuristics + manifests (cacheable per repo, not per PR)."""
    if use_cache:
        cached = load_cached_install_config(repo_id, repo)
        if cached:
            excerpt = str(cached.get("_ci_excerpt") or "")
            draft = CiExtractDraft(ci_excerpt=excerpt)
            return dict(cached), draft

    ci_draft = extract_ci_draft(repo)
    cfg = default_install_config_heuristic(repo, language)
    cfg = merge_ci_draft_into_config(cfg, ci_draft, language=language)
    cfg = merge_manifest_into_config(cfg, repo, language)
    cfg = sanitize_install_config_for_docker(cfg, repo_id, repo=repo)
    if ci_draft.ci_excerpt:
        cfg["_ci_excerpt"] = ci_draft.ci_excerpt

    if use_cache:
        save_cached_install_config(repo_id, repo, cfg)
    return cfg, ci_draft


def build_install_config_for_repo(
    repo: Path,
    language: str,
    repo_id: str,
    *,
    test_paths: list[str] | None = None,
    llm_install: tuple[str, str, str, int] | None = None,
    use_cache: bool = True,
    patch: str = "",
    test_patch: str = "",
    instance_id: str = "",
) -> dict[str, Any]:
    """
    Build first-pass ``install_config`` from repo artifacts.

    Order: cache (repo base) → per-PR java/js scope → LLM refine → sanitize.
    """
    cfg, ci_draft = _build_repo_base_config(
        repo, language, repo_id, use_cache=use_cache
    )

    lang = str(cfg.get("language") or language).lower()
    if lang == "java":
        from .java_build import merge_java_build_into_config
        from .languages import collect_test_targets

        paths = test_paths or collect_test_targets(language, patch, test_patch)
        cfg = merge_java_build_into_config(
            cfg,
            repo,
            paths,
            llm=llm_install,
            repo_id=repo_id,
            instance_id=instance_id,
        )
    elif lang == "javascript" and test_paths:
        from .js_build import merge_js_build_into_config

        cfg = merge_js_build_into_config(cfg, repo, test_paths)
    elif lang == "c" and repo is not None:
        from .integration_build import (
            apply_native_build_if_integration,
            merge_hybrid_c_integration_paths,
        )

        detection, _runner = merge_hybrid_c_integration_paths(
            patch, test_patch, language=language, test_paths=test_paths
        )
        cfg = apply_native_build_if_integration(
            cfg,
            repo,
            test_paths=detection,
            test_patch=test_patch,
            patch=patch,
        )

    if llm_install is not None:
        api_key, base_url, model, to = llm_install
        try:
            cfg = refine_install_config_llm(
                cfg,
                repo,
                repo_id,
                language=lang,
                ci_draft=ci_draft,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_s=to,
            )
        except Exception as e:
            print(
                f"  {instance_id or repo_id}: install LLM refine failed ({e}); keeping draft",
                file=sys.stderr,
            )

    cfg = sanitize_install_config_for_docker(cfg, repo_id, repo=repo)
    if not cfg.get("_ci_excerpt") and ci_draft.ci_excerpt:
        cfg["_ci_excerpt"] = ci_draft.ci_excerpt

    return cfg


def get_ci_excerpt_from_config(cfg: dict[str, Any]) -> str:
    return str(cfg.get("_ci_excerpt") or "").strip()
