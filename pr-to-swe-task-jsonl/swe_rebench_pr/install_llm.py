from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .gh_pr import git_ls_candidate_files, read_files_budget
from .llm_client import chat_completions, extract_json_array, extract_json_object, load_prompt

_DOCKER_SKIP_PKG = re.compile(r"(?i)pyqt|pyside|pyobjc|qtpy")
_BAD_PIP_EXTRA = re.compile(r"(?i)\[(all|clipboard|complete)\]")
_APT_INSTALL_LINE = re.compile(
    r"^apt-get install -y(?:\s+--no-install-recommends)?\s+(.*)$",
    re.IGNORECASE,
)
_APT_UPDATE_INSTALL_LINE = re.compile(
    r"^(apt-get update(?:\s+-qq)?)\s*&&\s*apt-get install -y(?:\s+--no-install-recommends)?\s+(.*)$",
    re.IGNORECASE,
)
from .swebench_align import (
    apt_debian_packages_for_reqs_path as apt_debian_packages_for_reqs_path,
    export_install_config_for_harness,
    merge_install_config_with_swebench_baseline,
    uses_runtests_test_cmd,
)


def merge_pre_install_debian_packages(pre_install: list[str], deb_packages: list[str]) -> list[str]:
    """Ensure ``pre_install`` installs *deb_packages* via apt (merge into existing install line)."""
    if not deb_packages:
        return pre_install
    from .apt_from_log import (
        _extract_apt_packages_from_pre_install,
        _is_apt_install_shell_line,
        resilient_apt_install_shell_lines,
        sanitize_apt_package_names,
    )
    from .integration_build import filter_http3_apt_for_harness

    deb_packages = filter_http3_apt_for_harness(sanitize_apt_package_names(deb_packages))
    if not deb_packages:
        return pre_install

    pre = list(pre_install)
    want: list[str] = []
    seen: set[str] = set()
    for pkg in ("git", "build-essential", *_extract_apt_packages_from_pre_install(pre), *deb_packages):
        if pkg not in seen:
            seen.add(pkg)
            want.append(pkg)

    non_apt = [ln for ln in pre if not _is_apt_install_shell_line(ln)]
    has_update = any("apt-get update" in str(ln).lower() for ln in non_apt)
    apt_lines = resilient_apt_install_shell_lines(want, include_update=not has_update)
    return non_apt + apt_lines

# Mirrors ``tests/runtests.py`` ``ALWAYS_INSTALLED_APPS`` + per-target test modules.
_DJANGO_CONTRIB_INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sites",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.admin.apps.SimpleAdminConfig",
    "django.contrib.staticfiles",
]

# Subset of ``tests/runtests.py`` ``CONTRIB_TESTS_TO_APPS``.
_DJANGO_CONTRIB_TEST_EXTRA_APPS: dict[str, list[str]] = {
    "deprecation": ["django.contrib.flatpages", "django.contrib.redirects"],
    "flatpages_tests": ["django.contrib.flatpages"],
    "redirects_tests": ["django.contrib.redirects"],
}


def django_test_module_from_path(rel_path: str) -> str | None:
    """``tests/admin_views/test_foo.py`` -> ``admin_views``; ``tests/view_tests/tests/test_x.py`` -> ``view_tests.tests``."""
    p = rel_path.replace("\\", "/").strip()
    if not p.startswith("tests/"):
        return None
    p = p[len("tests/") :]
    if p.endswith(".py"):
        p = p[:-3]
    parts = [x for x in p.split("/") if x]
    if not parts:
        return None
    if parts[-1].startswith("test_"):
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


def django_test_apps_from_targets(targets: list[str]) -> list[str]:
    """Test package labels to add to ``INSTALLED_APPS`` (same as ``runtests.get_apps_to_install``)."""
    apps: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            apps.append(name)

    for rel in targets:
        mod = django_test_module_from_path(rel)
        if not mod:
            continue
        for extra in _DJANGO_CONTRIB_TEST_EXTRA_APPS.get(mod, []):
            add(extra)
        # Nested labels like ``view_tests.tests`` need the root package in INSTALLED_APPS
        # for models (``view_tests.models``) and i18n catalogs (``view_tests``).
        if "." in mod:
            add(mod.split(".", 1)[0])
        add(mod)
    return apps


def render_django_pytest_settings(targets: list[str]) -> str:
    """Settings module for pytest on individual Django test files."""
    test_apps = django_test_apps_from_targets(targets)
    all_apps = _DJANGO_CONTRIB_INSTALLED_APPS + test_apps
    apps_lines = ",\n    ".join(repr(a) for a in all_apps)
    return f'''\
"""Pytest settings for swe_rebench_pr (django/django)."""
import os

from test_sqlite import *  # noqa: F403, F401

INSTALLED_APPS = [
    {apps_lines},
]

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "urls"
SITE_ID = 1
STATIC_URL = "static/"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = [
    {{
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(_TESTS_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {{
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        }},
    }}
]

MIGRATION_MODULES = {{
    "auth": None,
    "contenttypes": None,
    "sessions": None,
}}
'''


def _parse_path_list(raw: str) -> list[str]:
    raw = raw.strip()
    try:
        data = extract_json_array(raw)
        if isinstance(data, list):
            return [str(x) for x in data if isinstance(x, str)]
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r"\[[\s\S]*?\]", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return [str(x) for x in data if isinstance(x, str)]
        except json.JSONDecodeError:
            pass
    return []


def _strip_pytest_quiet_flags(test_cmd: str) -> str:
    """SWE-bench ``parse_log_pytest`` needs non-quiet output (e.g. ``PASSED ...`` lines)."""
    parts = test_cmd.split()
    out_parts: list[str] = []
    for p in parts:
        if p in ("-q", "--quiet"):
            continue
        if p.startswith("--"):
            out_parts.append(p)
            continue
        # Combined short flags, e.g. ``-rq`` → ``-r``
        if len(p) > 2 and p.startswith("-") and "q" in p[1:]:
            collapsed = "-" + "".join(c for c in p[1:] if c != "q")
            if collapsed != "-":
                out_parts.append(collapsed)
            continue
        out_parts.append(p)
    return " ".join(out_parts).strip() or "pytest -rA"


def normalize_install_config(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["python"] = str(raw.get("python") or "3.10")
    out["install"] = str(raw.get("install") or "pip install -e .")
    default_test = "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider"
    tc = str(raw.get("test_cmd") or default_test)
    if "pytest" in tc:
        tc = _strip_pytest_quiet_flags(tc)
    out["test_cmd"] = tc
    if raw.get("packages") is not None:
        out["packages"] = str(raw["packages"])
    if raw.get("pre_install") is not None and isinstance(raw["pre_install"], list):
        out["pre_install"] = [str(x) for x in raw["pre_install"]]
    if raw.get("reqs_path") is not None and isinstance(raw["reqs_path"], list):
        out["reqs_path"] = [str(x) for x in raw["reqs_path"]]
    if raw.get("env_yml_path") is not None and isinstance(raw["env_yml_path"], list):
        out["env_yml_path"] = [str(x) for x in raw["env_yml_path"]]
    if raw.get("pip_packages") is not None and isinstance(raw["pip_packages"], list):
        out["pip_packages"] = [str(x) for x in raw["pip_packages"]]
    if raw.get("post_install") is not None and isinstance(raw["post_install"], list):
        out["post_install"] = [str(x) for x in raw["post_install"]]
    if raw.get("pytest_plugins") is not None and isinstance(raw["pytest_plugins"], list):
        out["pytest_plugins"] = [str(x) for x in raw["pytest_plugins"]]
    if raw.get("test_env") is not None and isinstance(raw["test_env"], dict):
        out["test_env"] = {str(k): str(v) for k, v in raw["test_env"].items() if str(k).strip()}
    if raw.get("pytest_extra_args") is not None and isinstance(raw["pytest_extra_args"], list):
        out["pytest_extra_args"] = [str(x).strip() for x in raw["pytest_extra_args"] if str(x).strip()]
    if raw.get("eval_commands") is not None and isinstance(raw["eval_commands"], list):
        out["eval_commands"] = [str(x) for x in raw["eval_commands"]]
    if raw.get("apt-pkgs") is not None and isinstance(raw["apt-pkgs"], list):
        out["apt-pkgs"] = [str(x) for x in raw["apt-pkgs"] if str(x).strip()]
    if raw.get("language") is not None:
        out["language"] = str(raw["language"]).strip().lower()
    if raw.get("django_pytest"):
        out["django_pytest"] = True
    if raw.get("django_runtests"):
        out["django_runtests"] = True
    if raw.get("java_build_system") is not None:
        out["java_build_system"] = str(raw["java_build_system"]).strip().lower()
    if raw.get("docker_image") is not None:
        out["docker_image"] = str(raw["docker_image"]).strip()
    if raw.get("docker_specs") is not None and isinstance(raw["docker_specs"], dict):
        out["docker_specs"] = dict(raw["docker_specs"])
    if raw.get("gradle_junit_roots") is not None and isinstance(raw["gradle_junit_roots"], list):
        out["gradle_junit_roots"] = [str(x) for x in raw["gradle_junit_roots"]]
    return out


def sanitize_install_config_for_docker(
    cfg: dict[str, Any],
    repo_id: str,
    *,
    repo: Path | None = None,
) -> dict[str, Any]:
    """
    Deterministic guardrails so ``install_config`` replays in headless ``python:*-bookworm``.

    Strips PyQt/PySide lines and rewrites known-bad ``install`` extras (e.g. ``.[all]``).
  """
    out: dict[str, Any] = dict(cfg)

    def bad(s: str) -> bool:
        return bool(_DOCKER_SKIP_PKG.search(s))

    for key in ("pip_packages", "pre_install", "post_install", "reqs_path"):
        val = out.get(key)
        if not isinstance(val, list):
            continue
        out[key] = [str(x).strip() for x in val if isinstance(x, str) and x.strip() and not bad(x)]

    install = str(out.get("install") or "pip install -e .").strip()
    from .c_build import is_meson_repo
    from .repo_detect import repo_uses_meson_python_backend

    meson_backend = (
        repo is not None
        and repo_uses_meson_python_backend(repo)
        and not is_meson_repo(repo)
    )
    if bad(install) or _BAD_PIP_EXTRA.search(install):
        if meson_backend:
            install = 'pip install -e ".[test,pyarrow]" --no-build-isolation'
        else:
            install = re.sub(r"\[[^\]]+\]", "", install).strip() or "pip install -e ."
        out["install"] = install

    if meson_backend:
        post = list(out.get("post_install") or [])
        post_text = " ".join(post).lower()
        if "meson-python" not in post_text and "meson_python" not in post_text:
            post = [
                "python3 -m pip install -q --upgrade pip wheel setuptools",
                'python3 -m pip install -q meson-python meson ninja versioneer "cython>=3.0.5" '
                "numpy python-dateutil pytz tzdata",
                "meson subprojects download --force-redownload 2>/dev/null || meson subprojects download",
                'python3 -m pip install -q -e ".[test,pyarrow]" --no-build-isolation',
                "python3 -m pip uninstall -y pytest-httpserver 2>/dev/null || true",
                "python3 -m pip install -q pytest-localserver",
            ] + post
            out["install"] = "# see post_install: pandas editable install with meson"
        plugins = out.get("pytest_plugins")
        if not isinstance(plugins, list) or not plugins:
            out["pytest_plugins"] = ["pytest_localserver"]
        pre = list(out.get("pre_install") or [])
        if not any("build-essential" in ln for ln in pre):
            pre = [
                "apt-get update -qq",
                "apt-get install -y --no-install-recommends git build-essential",
            ] + pre
            out["pre_install"] = pre

    from .repo_detect import uses_django_runtests

    if uses_django_runtests(repo=repo, repo_id=repo_id):
        out = merge_install_config_with_swebench_baseline(out, repo_id, repo=repo)

    from .repo_detect import apply_repo_overrides

    out = apply_repo_overrides(out, repo_id, repo=repo)

    if repo is not None:
        from .java_build import detect_java_build_system, install_cmd_is_noop

        if detect_java_build_system(repo) == "gradle":
            from .java_build import (
                detect_required_java_major_version,
                eclipse_temurin_docker_image,
            )

            if install_cmd_is_noop(str(out.get("install") or "")):
                out["install"] = "chmod +x ./gradlew 2>/dev/null || true"
            if "maven:" in str(out.get("docker_image") or "") or "temurin" in str(
                out.get("docker_image") or ""
            ):
                java_major = detect_required_java_major_version(repo)
                out["docker_image"] = eclipse_temurin_docker_image(java_major)
                specs = dict(out.get("docker_specs") or {})
                specs["java_version"] = str(java_major)
                out["docker_specs"] = specs
            if str(out.get("java_build_system") or "") not in ("gradle", "maven"):
                out["java_build_system"] = "gradle"

    reqs = out.get("reqs_path")
    if isinstance(reqs, list) and reqs:
        deb = apt_debian_packages_for_reqs_path(
            [str(r) for r in reqs if isinstance(r, str)],
            repo=repo,
        )
        if deb:
            pre = list(out.get("pre_install") or [])
            out["pre_install"] = merge_pre_install_debian_packages(pre, deb)

    out = normalize_install_config(out)
    specs = out.get("docker_specs")
    if isinstance(specs, dict):
        from .js_build import sanitize_js_docker_specs

        out["docker_specs"] = sanitize_js_docker_specs(specs)

    from .c_build import ensure_c_install_config

    out = ensure_c_install_config(out, repo=repo)
    if repo is not None:
        from .integration_build import apply_native_build_if_integration, language_supports_native_integration

        if language_supports_native_integration(str(out.get("language") or "")):
            out = apply_native_build_if_integration(out, repo)

    lang = str(out.get("language") or "python").strip().lower()
    if lang in ("python", "py"):
        from .ci_install_normalize import docker_safe_python_install, normalize_ci_test_command

        out["install"] = docker_safe_python_install(str(out.get("install") or ""))
        for key in ("pre_install", "post_install"):
            lines = out.get(key)
            if not isinstance(lines, list):
                continue
            out[key] = [
                docker_safe_python_install(ln) if re.search(r"\buv\b", ln, re.IGNORECASE) else ln
                for ln in lines
                if isinstance(ln, str) and ln.strip()
            ]
        tc = str(out.get("test_cmd") or "").strip()
        if tc and re.search(r"\buv\s+run\b", tc, re.IGNORECASE):
            out["test_cmd"] = normalize_ci_test_command(tc, language="python")

    return out


def llm_pick_install_files(
    repo: Path,
    repo_id: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
) -> list[str]:
    candidates = git_ls_candidate_files(repo, max_files=400)
    tpl = load_prompt("list_install_files.txt")
    user = tpl.replace("{{repo_name}}", repo_id).replace(
        "{{list_of_files}}", json.dumps(candidates, indent=2)
    )
    raw = chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system="You return only a JSON array of file paths as instructed.",
        user=user,
        timeout_s=timeout_s,
        json_object=False,
    )
    picked = _parse_path_list(raw)
    exist = [p for p in picked if (repo / p).is_file()]
    if not exist:
        return [p for p in candidates[:15] if (repo / p).is_file()]
    return exist


def llm_extract_recipe(
    repo: Path,
    repo_id: str,
    rels: list[str],
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
) -> dict[str, Any]:
    rendered = read_files_budget(repo, rels, max_chars=100_000)
    tpl = load_prompt("extract_install_recipe.txt")
    user = tpl.replace("{{repo_name}}", repo_id).replace("{{rendered}}", rendered)
    raw = chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system="Return only a single valid JSON object matching the schema. No markdown.",
        user=user,
        timeout_s=timeout_s,
        json_object=True,
    )
    obj = extract_json_object(raw)
    if not isinstance(obj, dict):
        raise ValueError("Recipe model output is not a JSON object")
    return sanitize_install_config_for_docker(normalize_install_config(obj), repo_id)


def llm_fix_recipe_from_docker_tests(
    install_config: dict[str, Any],
    diagnostics_text: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    ci_context: str = "",
) -> dict[str, Any]:
    """Update ``install_config`` from pytest JUnit diagnostics (fail / error / import skips)."""
    from .harness_guards import extract_structured_failure_log

    structured = extract_structured_failure_log(
        diagnostics_text, language=str(install_config.get("language") or "")
    )
    tpl = load_prompt("fix_install_from_tests.txt")
    user = (
        tpl.replace("{{install_config}}", json.dumps(install_config, indent=2))
        .replace("{{cut_logs}}", structured[-120_000:])
        .replace("{{ci_context}}", (ci_context or "(none)")[:8000])
    )
    raw = chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system="Return only a single valid JSON object for install_config. No markdown, no trailing commas.",
        user=user,
        timeout_s=timeout_s,
        json_object=True,
    )
    obj = extract_json_object(raw)
    if not isinstance(obj, dict):
        raise ValueError("Fix-from-tests model output is not a JSON object")
    repo_hint = ""
    m = re.search(r"instance_id:\s*(\S+)", diagnostics_text)
    if m:
        repo_hint = m.group(1)
    from .c_build import merge_c_harness_fields_after_llm
    from .java_build import merge_java_harness_fields_after_llm
    from .php_build import merge_php_harness_fields_after_llm
    from .ruby_build import merge_ruby_harness_fields_after_llm
    from .rust_build import merge_rust_harness_fields_after_llm

    merged = merge_java_harness_fields_after_llm(install_config, obj)
    merged = merge_c_harness_fields_after_llm(install_config, merged)
    merged = merge_ruby_harness_fields_after_llm(install_config, merged)
    merged = merge_rust_harness_fields_after_llm(install_config, merged)
    merged = merge_php_harness_fields_after_llm(install_config, merged)
    merged = merge_refined_with_draft(
        install_config,
        merged,
        language=str(install_config.get("language") or "python"),
    )
    return sanitize_install_config_for_docker(normalize_install_config(merged), repo_hint)


def llm_fix_recipe(
    install_config: dict[str, Any],
    cut_logs: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
) -> dict[str, Any]:
    tpl = load_prompt("fix_install_recipe.txt")
    from .harness_guards import extract_structured_failure_log

    structured = extract_structured_failure_log(
        cut_logs, language=str(install_config.get("language") or "")
    )
    user = tpl.replace("{{install_config}}", json.dumps(install_config, indent=2)).replace(
        "{{cut_logs}}", structured[-12_000:]
    )
    raw = chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system="Return only a single valid JSON object for install_config. No markdown, no trailing commas.",
        user=user,
        timeout_s=timeout_s,
        json_object=True,
    )
    obj = extract_json_object(raw)
    if not isinstance(obj, dict):
        raise ValueError("Fix recipe model output is not a JSON object")
    from .c_build import merge_c_harness_fields_after_llm
    from .java_build import merge_java_harness_fields_after_llm
    from .php_build import merge_php_harness_fields_after_llm
    from .ruby_build import merge_ruby_harness_fields_after_llm
    from .rust_build import merge_rust_harness_fields_after_llm

    merged = merge_java_harness_fields_after_llm(install_config, obj)
    merged = merge_c_harness_fields_after_llm(install_config, merged)
    merged = merge_ruby_harness_fields_after_llm(install_config, merged)
    merged = merge_rust_harness_fields_after_llm(install_config, merged)
    merged = merge_php_harness_fields_after_llm(install_config, merged)
    return normalize_install_config(merged)


def build_install_config_llm(
    repo: Path,
    repo_id: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
) -> dict[str, Any]:
    rels = llm_pick_install_files(
        repo, repo_id, api_key=api_key, base_url=base_url, model=model, timeout_s=timeout_s
    )
    return llm_extract_recipe(
        repo, repo_id, rels, api_key=api_key, base_url=base_url, model=model, timeout_s=timeout_s
    )


def _test_cmd_weakened(before: str, after: str) -> bool:
    b, a = before.strip(), after.strip()
    if not a:
        return True
    if "|| true" in a.lower() and "|| true" not in b.lower():
        return True
    if "; exit 0" in a.lower() and "; exit 0" not in b.lower():
        return True
    return False


def merge_refined_with_draft(
    draft: dict[str, Any],
    refined: dict[str, Any],
    *,
    language: str,
) -> dict[str, Any]:
    """Keep CI/heuristic install & test_cmd when LLM output weakens them."""
    out = normalize_install_config({**draft, **refined})
    lang = language.strip().lower()
    for key in (
        "install",
        "test_cmd",
        "pre_install",
        "post_install",
        "pip_packages",
        "reqs_path",
        "native_integration_setup",
        "native_integration_build",
        "native_integration_pytest_root",
        "native_integration_repo_dir",
    ):
        if draft.get(key) is not None and not refined.get(key):
            out[key] = draft[key]
    tc_before = str(draft.get("test_cmd") or "")
    tc_after = str(out.get("test_cmd") or "")
    if tc_before and _test_cmd_weakened(tc_before, tc_after):
        out["test_cmd"] = tc_before
    if lang == "javascript" and "pytest" in tc_after.lower() and "jest" in tc_before.lower():
        out["test_cmd"] = tc_before
    if lang == "javascript" and "pytest" in tc_after.lower() and "vitest" in tc_before.lower():
        out["test_cmd"] = tc_before
    specs_d = draft.get("docker_specs") if isinstance(draft.get("docker_specs"), dict) else {}
    specs_r = out.get("docker_specs") if isinstance(out.get("docker_specs"), dict) else {}
    merged_specs = {**specs_r, **{k: v for k, v in specs_d.items() if v}}
    if merged_specs:
        out["docker_specs"] = merged_specs
    if draft.get("_ci_excerpt"):
        out["_ci_excerpt"] = draft["_ci_excerpt"]
    return out


def refine_install_config_llm(
    draft: dict[str, Any],
    repo: Path,
    repo_id: str,
    *,
    language: str,
    ci_draft: Any = None,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
) -> dict[str, Any]:
    """LLM refines an existing draft (all languages); prefers CI over invention."""
    from .ci_extract import CiExtractDraft, ci_excerpt_for_remediation

    rels = llm_pick_install_files(
        repo, repo_id, api_key=api_key, base_url=base_url, model=model, timeout_s=timeout_s
    )
    rendered = read_files_budget(repo, rels, max_chars=80_000)
    excerpt = ""
    if isinstance(ci_draft, CiExtractDraft):
        excerpt = ci_excerpt_for_remediation(ci_draft)
    elif draft.get("_ci_excerpt"):
        excerpt = str(draft["_ci_excerpt"])[:8000]

    tpl = load_prompt("refine_install_recipe.txt")
    user = (
        tpl.replace("{{repo_name}}", repo_id)
        .replace("{{language}}", language)
        .replace("{{draft_config}}", json.dumps(draft, indent=2))
        .replace("{{ci_excerpt}}", excerpt or "(none)")
        .replace("{{rendered}}", rendered)
    )
    raw = chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system="Return only a single valid JSON object matching the draft schema. No markdown.",
        user=user,
        timeout_s=timeout_s,
        json_object=True,
    )
    obj = extract_json_object(raw)
    if not isinstance(obj, dict):
        raise ValueError("Refine recipe model output is not a JSON object")
    merged = merge_refined_with_draft(draft, obj, language=language)
    return sanitize_install_config_for_docker(merged, repo_id, repo=repo)


def default_install_config_heuristic(repo: Path, language: str = "python") -> dict[str, Any]:
    """Minimal recipe when no LLM key is available (Docker-replayable)."""
    from .languages import detect_language_from_repo, get_language_spec, normalize_language

    raw = language.strip().lower()
    if raw == "auto":
        lang = detect_language_from_repo(repo) or "python"
    else:
        lang = normalize_language(raw)
    spec = get_language_spec(lang)
    cfg = dict(spec.default_install_config)
    if lang == "python":
        has_py = (repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file()
        cfg["install"] = "pip install -e ." if has_py else "pip install ."
        from .integration_build import apply_native_build_if_integration
        from .python_build import merge_python_test_install_into_config

        cfg = apply_native_build_if_integration(cfg, repo)
        cfg = merge_python_test_install_into_config(cfg, repo)
    elif lang == "java":
        from .java_build import java_install_config_for_repo

        return normalize_install_config(java_install_config_for_repo(repo, base=cfg))
    elif lang == "javascript":
        from .js_build import js_install_config_for_repo

        return normalize_install_config(js_install_config_for_repo(repo, base=cfg))
    elif lang == "c":
        from .c_build import ensure_c_install_config, is_meson_repo, meson_install_config_for_repo

        if is_meson_repo(repo):
            return normalize_install_config(meson_install_config_for_repo(repo, base=cfg))
        if not (repo / "CMakeLists.txt").is_file():
            cfg["install"] = "true"
        else:
            cfg = ensure_c_install_config(cfg, repo=repo)
            if repo is not None:
                from .integration_build import apply_native_build_if_integration

                cfg = apply_native_build_if_integration(cfg, repo)
                from .apt_from_log import sanitize_native_integration_apt_config

                cfg = sanitize_native_integration_apt_config(cfg)
    elif lang == "ruby":
        from .ruby_build import ruby_install_config_for_repo

        return normalize_install_config(ruby_install_config_for_repo(repo, base=cfg))
    elif lang == "rust":
        from .rust_build import rust_install_config_for_repo

        return normalize_install_config(rust_install_config_for_repo(repo, base=cfg))
    elif lang == "php":
        from .php_build import php_install_config_for_repo

        return normalize_install_config(php_install_config_for_repo(repo, base=cfg))
    elif lang == "go":
        from .go_build import go_install_config_for_repo

        return normalize_install_config(go_install_config_for_repo(repo, base=cfg))
    return normalize_install_config(cfg)
