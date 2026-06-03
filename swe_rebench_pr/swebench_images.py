"""Build SWE-bench-style Docker images (base / env / instance) for custom JSONL tasks."""

from __future__ import annotations

import json
import sys
from typing import Any

from .swebench_align import (
    HARNESS_INSTALL_CONFIG_KEYS,
    apt_debian_packages_for_reqs_path,
    export_install_config_for_harness,
)

# Task language (CLI) -> harness MAP_REPO_TO_EXT value
_LANGUAGE_TO_EXT: dict[str, str] = {
    "python": "py",
    "javascript": "js",
    "java": "java",
    "go": "go",
    "c": "c",
    "php": "php",
    "ruby": "rb",
    "rust": "rs",
}


class SwebenchHarnessUnavailable(RuntimeError):
    """Bundled harness or docker SDK unavailable."""


def language_to_harness_ext(language: str) -> str:
    from .languages import normalize_language

    lang = normalize_language(language)
    ext = _LANGUAGE_TO_EXT.get(lang)
    if not ext:
        raise ValueError(f"Unsupported language for harness images: {language!r}")
    return ext


def install_config_to_harness_specs(
    install_config: dict[str, Any],
    *,
    language: str | None = None,
) -> dict[str, Any]:
    """Map task ``install_config`` to harness ``MAP_REPO_VERSION_TO_SPECS`` entry."""
    lang = language or install_config.get("language")
    ic = export_install_config_for_harness(install_config, language=lang)
    specs: dict[str, Any] = {}
    for key in HARNESS_INSTALL_CONFIG_KEYS:
        if key in ic and ic[key] is not None:
            specs[key] = ic[key]

    reqs = ic.get("reqs_path")
    if reqs and not specs.get("packages"):
        specs["packages"] = "requirements.txt"

    post = ic.get("post_install") or []
    if isinstance(post, list) and post:
        install = str(specs.get("install") or "true").strip()
        extra = [ln.strip() for ln in post if isinstance(ln, str) and ln.strip()]
        if extra:
            specs["install"] = (
                " && ".join([install, *extra])
                if install and install != "true"
                else " && ".join(extra)
            )

    if not specs.get("install"):
        specs["install"] = "true"
    if not specs.get("test_cmd"):
        lang_norm = str(lang or "").lower()
        if lang_norm in ("javascript", "js", "node", "typescript", "ts"):
            specs["test_cmd"] = (
                "npx jest --ci --forceExit --reporters=default --reporters=jest-junit "
                "--outputFile=__JUNIT_OUT__"
            )
        else:
            specs["test_cmd"] = "pytest -rA"

    if not specs.get("apt-pkgs"):
        reqs_paths = ic.get("reqs_path")
        if isinstance(reqs_paths, list) and reqs_paths:
            deb = apt_debian_packages_for_reqs_path(
                [str(p) for p in reqs_paths if isinstance(p, str) and p.strip()]
            )
            if deb:
                specs["apt-pkgs"] = deb

    optional = install_config.get("apt-pkgs-optional")
    if isinstance(optional, list) and optional:
        specs["apt-pkgs-optional"] = [str(p) for p in optional if str(p).strip()]

    return specs


def _import_harness():
    try:
        import docker  # noqa: F401
    except ImportError as e:
        raise SwebenchHarnessUnavailable(
            "Python package 'docker' is required for harness image builds. "
            "Install with: pip install docker"
        ) from e

    from swe_rebench_pr.harness.constants import (
        MAP_REPO_TO_EXT,
        MAP_REPO_TO_REQS_PATHS,
        MAP_REPO_VERSION_TO_SPECS,
    )
    from swe_rebench_pr.harness.docker_build import (
        build_env_images,
        build_instance_images,
    )
    from swe_rebench_pr.harness.test_spec.create_scripts import make_repo_clone_script_list
    from swe_rebench_pr.harness.test_spec.test_spec import make_test_spec

    return {
        "MAP_REPO_TO_EXT": MAP_REPO_TO_EXT,
        "MAP_REPO_TO_REQS_PATHS": MAP_REPO_TO_REQS_PATHS,
        "MAP_REPO_VERSION_TO_SPECS": MAP_REPO_VERSION_TO_SPECS,
        "build_env_images": build_env_images,
        "build_instance_images": build_instance_images,
        "make_repo_clone_script_list": make_repo_clone_script_list,
        "make_test_spec": make_test_spec,
    }


def register_task_harness_specs(
    repo: str,
    version: str,
    install_config: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Register dynamic repo/version specs so ``make_test_spec`` can build images."""
    mods = _import_harness()
    specs = install_config_to_harness_specs(install_config, language=language)
    ext = language_to_harness_ext(language)

    repo_specs = mods["MAP_REPO_VERSION_TO_SPECS"].setdefault(repo, {})
    repo_specs[version] = specs
    mods["MAP_REPO_TO_EXT"][repo] = ext

    reqs = install_config.get("reqs_path")
    if isinstance(reqs, list) and reqs:
        mods["MAP_REPO_TO_REQS_PATHS"][repo] = [
            str(p) for p in reqs if isinstance(p, str) and p.strip()
        ]

    return specs


def row_to_swebench_instance(row: dict[str, Any]) -> dict[str, Any]:
    """Minimal instance dict for ``make_test_spec`` / image build."""
    repo = str(row.get("repo") or "")
    if "__" in repo and "/" not in repo:
        repo = repo.replace("__", "/", 1)

    def _loads(key: str) -> list:
        raw = row.get(key)
        if raw is None:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                val = json.loads(raw)
                return val if isinstance(val, list) else []
            except json.JSONDecodeError:
                return []
        return []

    return {
        "instance_id": str(row["instance_id"]),
        "repo": repo,
        "version": str(row.get("version") or "0.0"),
        "base_commit": str(row["base_commit"]),
        "patch": str(row.get("patch") or ""),
        "test_patch": str(row.get("test_patch") or ""),
        "problem_statement": str(row.get("problem_statement") or ""),
        "hints_text": str(row.get("hints_text") or ""),
        "created_at": str(row.get("created_at") or ""),
        "environment_setup_commit": str(
            row.get("environment_setup_commit") or row["base_commit"]
        ),
        "FAIL_TO_PASS": _loads("FAIL_TO_PASS"),
        "PASS_TO_PASS": _loads("PASS_TO_PASS"),
    }


def _harness_build_log_tail(row: dict[str, Any], install_config: dict[str, Any], language: str) -> str:
    """Tail env/instance harness build logs for LLM or heuristic remediation."""
    try:
        mods = _import_harness()
        from swe_rebench_pr.harness.constants import ENV_IMAGE_BUILD_DIR

        instance = row_to_swebench_instance(row)
        register_task_harness_specs(
            instance["repo"], instance["version"], install_config, language
        )
        from swe_rebench_pr.harness.constants import LATEST

        test_spec = mods["make_test_spec"](
            instance,
            namespace=None,
            env_image_tag=LATEST,
            instance_image_tag=LATEST,
        )
        parts: list[str] = []
        labels = [("env", test_spec.env_image_key)]
        for label, key in labels:
            log_dir = ENV_IMAGE_BUILD_DIR / key.replace(":", "__")
            for name in ("build_image.log", "prepare_image.log"):
                path = log_dir / name
                if path.is_file():
                    text = path.read_text(encoding="utf-8", errors="replace")
                    parts.append(f"=== harness {label} {name} ({key}) ===\n{text[-80_000:]}")
        return "\n\n".join(parts)
    except Exception:
        return ""


def heuristic_fix_install_config_from_harness_build(
    install_config: dict[str, Any],
    build_log: str,
    *,
    repo_id: str = "",
) -> dict[str, Any]:
    """Apply known fixes for common harness env/instance build failures."""
    from .install_llm import merge_pre_install_debian_packages, sanitize_install_config_for_docker

    low = (build_log or "").lower()
    deb: list[str] = []
    if "pylibmc" in low or "libmemcached" in low:
        deb.append("libmemcached-dev")
    if ("pillow" in low or "pil " in low) and ("jpeg" in low or "zlib" in low):
        deb.extend(["libjpeg-dev", "zlib1g-dev"])
    if "mysqlclient" in low or "mariadb" in low:
        deb.extend(["pkg-config", "libmariadb-dev"])
    if "psycopg2" in low or "psycopg" in low:
        deb.append("libpq-dev")

    from .apt_from_log import apt_packages_from_build_log

    deb.extend(apt_packages_from_build_log(build_log))

    if not deb:
        return install_config

    cfg = sanitize_install_config_for_docker(dict(install_config), repo_id or "")
    # Apply after sanitize (django baseline merge can reset pre_install).
    pre = list(cfg.get("pre_install") or [])
    cfg["pre_install"] = merge_pre_install_debian_packages(pre, deb)
    apt = list(cfg.get("apt-pkgs") or [])
    seen = set(apt)
    for pkg in deb:
        if pkg not in seen:
            seen.add(pkg)
            apt.append(pkg)
    cfg["apt-pkgs"] = apt
    return cfg


def write_harness_setup_repo_script(
    work: Path,
    row: dict[str, Any],
    install_config: dict[str, Any],
    language: str,
) -> None:
    """Write clone/checkout script for env-only discover (no per-task instance image)."""
    mods = _import_harness()
    instance = row_to_swebench_instance(row)
    register_task_harness_specs(
        instance["repo"], instance["version"], install_config, language
    )
    specs = mods["MAP_REPO_VERSION_TO_SPECS"][instance["repo"]][instance["version"]]
    cmds = mods["make_repo_clone_script_list"](
        specs,
        instance["repo"],
        "/testbed",
        instance["base_commit"],
        "testbed",
    )
    script = work / "setup_repo.sh"
    script.write_text(
        "#!/bin/bash\nset -euxo pipefail\n" + "\n".join(cmds) + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def build_env_images_for_row(
    row: dict[str, Any],
    install_config: dict[str, Any],
    language: str,
    *,
    force_rebuild: bool = False,
    max_workers: int = 1,
) -> str:
    """
    Build base → env images for one task row (no per-task instance image).

    Returns the Docker image tag (``TestSpec.env_image_key``).
    """
    mods = _import_harness()
    from swe_rebench_pr.harness.constants import LATEST

    instance = row_to_swebench_instance(row)
    repo = instance["repo"]
    version = instance["version"]
    register_task_harness_specs(repo, version, install_config, language)

    import docker

    test_spec = mods["make_test_spec"](
        instance,
        namespace=None,
        env_image_tag=LATEST,
        instance_image_tag=LATEST,
    )
    client = docker.from_env()
    _, env_failed = mods["build_env_images"](
        client,
        [instance],
        force_rebuild=force_rebuild,
        max_workers=max_workers,
        env_image_tag=LATEST,
        instance_image_tag=LATEST,
    )
    if env_failed:
        raise RuntimeError(
            f"Harness env image build failed for {instance['instance_id']}: {env_failed}"
        )
    return test_spec.env_image_key


def build_harness_images_for_row(
    row: dict[str, Any],
    install_config: dict[str, Any],
    language: str,
    *,
    force_rebuild: bool = False,
    max_workers: int = 1,
) -> str:
    """
    Build base → env → instance images for one task row.

    Returns the Docker image tag (``TestSpec.instance_image_key``).
    """
    mods = _import_harness()
    from swe_rebench_pr.harness.constants import LATEST

    instance = row_to_swebench_instance(row)
    repo = instance["repo"]
    version = instance["version"]
    register_task_harness_specs(repo, version, install_config, language)

    import docker

    test_spec = mods["make_test_spec"](
        instance,
        namespace=None,
        env_image_tag=LATEST,
        instance_image_tag=LATEST,
    )
    client = docker.from_env()
    # build_instance_images re-calls make_test_spec; must pass tags (defaults were None).
    successful, failed = mods["build_instance_images"](
        client,
        [instance],
        force_rebuild=force_rebuild,
        max_workers=max_workers,
        env_image_tag=LATEST,
        tag=LATEST,
    )
    if failed:
        raise RuntimeError(
            f"Harness instance image build failed for {instance['instance_id']}: {failed}"
        )
    if not successful:
        raise RuntimeError(
            f"Harness instance image build produced no image for {instance['instance_id']}"
        )
    return test_spec.instance_image_key


def harness_images_available() -> bool:
    try:
        _import_harness()
        return True
    except SwebenchHarnessUnavailable:
        return False


def build_discover_image(
    row: dict[str, Any],
    install_config: dict[str, Any],
    language: str,
    *,
    force_rebuild: bool = False,
    llm_remediate: tuple[str, str, str, int] | None = None,
    remediation_max_rounds: int = 3,
    repo_id: str = "",
    build_instance_images: bool = False,
) -> tuple[str, dict[str, Any]]:
    """
    Build or reuse harness Docker image(s) for Docker discover.

  By default only base + env images are built; clone/install run at container start.
  Set ``build_instance_images=True`` to also bake a per-task instance image (legacy).

    On env image build failure, applies heuristics and optional LLM fixes to
    ``install_config`` (same remediation family as docker discover install failures).
    """
    max_r = max(2, remediation_max_rounds) if llm_remediate else 1
    cfg = dict(install_config)
    instance_id = str(row.get("instance_id") or "")
    last_err = ""
    build_fn = (
        build_harness_images_for_row
        if build_instance_images
        else build_env_images_for_row
    )

    for attempt in range(1, max_r + 1):
        try:
            tag = build_fn(
                row,
                cfg,
                language,
                force_rebuild=force_rebuild or attempt > 1,
            )
            if attempt > 1:
                print(
                    f"  {instance_id}: harness image build ok on attempt {attempt}/{max_r}",
                    file=sys.stderr,
                )
            return tag, export_install_config_for_harness(cfg)
        except RuntimeError as e:
            last_err = str(e)
            log = _harness_build_log_tail(row, cfg, language)
            if not log:
                log = last_err
            layer = (
                "Env + instance image build"
                if build_instance_images
                else "Env image build"
            )
            log_blob = (
                f"HARNESS_DOCKER_BUILD_FAILED=1 ({layer})\n"
                "Env layer: conda create + apt-pkgs + pip install -r requirements.txt\n"
                + (
                    "Instance layer: git clone + pre_install + install\n"
                    if build_instance_images
                    else "Discover: git clone + install_config scripts at container start\n"
                )
                + f"instance_id: {instance_id}\n\n{log}"
            )

            if attempt >= max_r:
                raise RuntimeError(last_err) from e

            cfg = heuristic_fix_install_config_from_harness_build(
                cfg, log_blob, repo_id=repo_id or str(row.get("repo") or "")
            )
            cfg = export_install_config_for_harness(cfg)

            if llm_remediate is None:
                print(
                    f"  {instance_id}: harness build failed (attempt {attempt}); "
                    f"heuristic install_config applied, no LLM key — retrying build",
                    file=sys.stderr,
                )
                continue

            api_key, base_url, model, to = llm_remediate
            print(
                f"  {instance_id}: harness build failed — LLM will update install_config; "
                f"build attempt {attempt + 1}/{max_r}",
                file=sys.stderr,
            )
            try:
                from .install_llm import llm_fix_recipe, sanitize_install_config_for_docker
                from .java_build import merge_java_harness_fields_after_llm

                prev_cfg = dict(cfg)
                cfg = llm_fix_recipe(
                    cfg,
                    log_blob,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout_s=to,
                )
                cfg = merge_java_harness_fields_after_llm(prev_cfg, cfg)
                cfg = sanitize_install_config_for_docker(
                    cfg, repo_id or str(row.get("repo") or "")
                )
                cfg = export_install_config_for_harness(cfg, language=language)
            except Exception as ex:
                print(
                    f"  {instance_id}: harness build remediation LLM failed: {ex}",
                    file=sys.stderr,
                )
                raise RuntimeError(last_err) from e

    raise RuntimeError(last_err or "harness image build failed")
