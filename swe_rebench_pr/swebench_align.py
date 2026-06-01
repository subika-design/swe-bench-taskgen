"""Align task ``install_config`` with SWE-bench harness specs (no Django-only hardcoding)."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Used when ``SWE-bench`` is not on disk (same strings as ``swebench.harness.constants.python``).
_FALLBACK_TEST_DJANGO = "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1"
_FALLBACK_MAP_REPO_TO_REQS_PATHS: dict[str, list[str]] = {
    "django/django": ["tests/requirements/py3.txt"],
}
_FALLBACK_SPECS_DJANGO_EVAL: list[str] = [
    "export LANG=en_US.UTF-8",
    "export LC_ALL=en_US.UTF-8",
    "export PYTHONIOENCODING=utf8",
    "export LANGUAGE=en_US:en",
]

# Keys SWE-bench ``harness_specs_from_install_config_only`` copies into specs.
HARNESS_INSTALL_CONFIG_KEYS: tuple[str, ...] = (
    "python",
    "packages",
    "install",
    "test_cmd",
    "pre_install",
    "pip_packages",
    "post_install",
    "reqs_path",
    "eval_commands",
    "docker_specs",
    "no_use_env",
    "apt-pkgs",
)

# Internal-only keys used during Docker discovery; never written to JSONL for eval.
INTERNAL_INSTALL_KEYS: frozenset[str] = frozenset(
    {
        "django_runtests",
        "django_pytest",
        "pytest_plugins",
        "pytest_extra_args",
        "test_env",
        "language",
        "java_build_system",
        "gradle_junit_roots",
        "docker_image",
        "env_yml_path",
        "cargo_features",
        "c_build_system",
        "result_format",
        "premake_test_cmd_base",
    }
)
_INTERNAL_INSTALL_KEYS = INTERNAL_INSTALL_KEYS


def internal_install_keys(cfg: dict[str, Any]) -> dict[str, Any]:
    """Copy discover-only ``install_config`` keys that must survive harness export."""
    return {
        k: cfg[k]
        for k in INTERNAL_INSTALL_KEYS
        if k in cfg and cfg[k] is not None
    }


def merge_internal_install_keys(
    cfg: dict[str, Any],
    internal: dict[str, Any] | None,
) -> dict[str, Any]:
    """Restore internal keys dropped by ``export_install_config_for_harness``."""
    if not internal:
        return cfg
    out = dict(cfg)
    for key, val in internal.items():
        if val is not None:
            out[key] = val
    return out


# Pip requirement names (substring) -> Debian packages for source builds in Docker discover.
_PIP_NAME_DEBIAN_APT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pylibmc", ("libmemcached-dev",)),
    ("mysqlclient", ("pkg-config", "libmariadb-dev")),
    ("psycopg2", ("libpq-dev",)),
    ("psycopg", ("libpq-dev",)),
    ("Pillow", ("libjpeg-dev", "zlib1g-dev")),
    ("pillow", ("libjpeg-dev", "zlib1g-dev")),
)

_REQS_PATH_DEBIAN_APT: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"(^|/)mysql\.txt$", re.I), ("pkg-config", "libmariadb-dev")),
    (re.compile(r"(^|/)postgres\.txt$", re.I), ("libpq-dev",)),
)


def find_swebench_root() -> Path | None:
    env = os.environ.get("SWEBENCH_PATH") or os.environ.get("SWE_BENCH_PATH")
    if env:
        p = Path(env).expanduser().resolve()
        if (p / "swebench" / "harness").is_dir():
            return p
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "SWE-bench"
        if (candidate / "swebench" / "harness").is_dir():
            return candidate
    return None


def _load_module_from_path(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_swebench_python_constants() -> tuple[str, dict[str, list[str]], dict[str, Any]]:
    root = find_swebench_root()
    if root is None:
        return (
            _FALLBACK_TEST_DJANGO,
            _FALLBACK_MAP_REPO_TO_REQS_PATHS,
            {"5.2": {"python": "3.11", "eval_commands": _FALLBACK_SPECS_DJANGO_EVAL, "pre_install": []}},
        )
    mod_path = root / "swebench" / "harness" / "constants" / "python.py"
    if not mod_path.is_file():
        return (
            _FALLBACK_TEST_DJANGO,
            _FALLBACK_MAP_REPO_TO_REQS_PATHS,
            {"5.2": {"python": "3.11", "eval_commands": _FALLBACK_SPECS_DJANGO_EVAL, "pre_install": []}},
        )
    mod = _load_module_from_path("swebench_harness_constants_python", mod_path)
    return mod.TEST_DJANGO, mod.MAP_REPO_TO_REQS_PATHS, mod.SPECS_DJANGO


def is_django_repo_id(repo_id: str) -> bool:
    """Prefer ``uses_django_runtests(repo=...)`` when a checkout exists."""
    from .repo_detect import uses_django_runtests

    return uses_django_runtests(repo_id=repo_id)


def uses_runtests_test_cmd(install_config: dict[str, Any]) -> bool:
    tc = str(install_config.get("test_cmd") or "")
    return "runtests.py" in tc


def python_version_from_repo(repo: Path) -> str | None:
    """Read ``requires-python`` from ``pyproject.toml`` (PEP 621)."""
    path = repo / "pyproject.toml"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"requires-python\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not m:
        return None
    spec = m.group(1)
    m2 = re.search(r">=\s*(\d+\.\d+)", spec)
    if m2:
        return m2.group(1)
    m3 = re.search(r"(\d+\.\d+)", spec)
    return m3.group(1) if m3 else None


def apt_debian_packages_for_requirements_text(text: str) -> list[str]:
    low = text.lower()
    seen: set[str] = set()
    out: list[str] = []
    for needle, packages in _PIP_NAME_DEBIAN_APT:
        if needle.lower() in low:
            for pkg in packages:
                if pkg not in seen:
                    seen.add(pkg)
                    out.append(pkg)
    return out


def apt_debian_packages_for_reqs_path(
    reqs_path: list[str],
    *,
    repo: Path | None = None,
) -> list[str]:
    """Map requirement file paths (and optional repo contents) to apt package names."""
    seen: set[str] = set()
    out: list[str] = []
    for rel in reqs_path:
        norm = rel.replace("\\", "/").strip()
        if not norm:
            continue
        for pattern, packages in _REQS_PATH_DEBIAN_APT:
            if pattern.search(norm):
                for pkg in packages:
                    if pkg not in seen:
                        seen.add(pkg)
                        out.append(pkg)
        if repo is not None:
            fpath = repo / norm
            if fpath.is_file():
                for pkg in apt_debian_packages_for_requirements_text(
                    fpath.read_text(encoding="utf-8", errors="replace")
                ):
                    if pkg not in seen:
                        seen.add(pkg)
                        out.append(pkg)
    return out


def django_baseline_install_config(repo: Path | None = None) -> dict[str, Any]:
    """
    Baseline ``install_config`` fields for ``django/django`` from SWE-bench constants
    plus repo ``requires-python`` and requirement-file-derived apt deps.
    """
    test_django, map_reqs, specs_django = _import_swebench_python_constants()
    reqs = list(map_reqs.get("django/django") or ["tests/requirements/py3.txt"])
    # Newest SPECS_DJANGO entry with eval_commands / pre_install (5.x line).
    spec_row = specs_django.get("5.2") or specs_django.get("5.1") or next(iter(specs_django.values()))
    eval_commands = list(spec_row.get("eval_commands") or [])
    if not eval_commands:
        for vk in ("4.2", "4.1", "3.2", "2.2", "2.1", "1.11"):
            row = specs_django.get(vk) or {}
            if row.get("eval_commands"):
                eval_commands = list(row["eval_commands"])
                break
    if not eval_commands:
        eval_commands = list(_FALLBACK_SPECS_DJANGO_EVAL)
    pre_install = list(spec_row.get("pre_install") or [])
    python = (python_version_from_repo(repo) if repo is not None else None) or str(
        spec_row.get("python") or "3.11"
    )
    cfg: dict[str, Any] = {
        "python": python,
        "install": "python -m pip install -e .",
        "test_cmd": test_django,
        "reqs_path": reqs,
        "pip_packages": ["pip", "wheel", "setuptools"],
        "eval_commands": eval_commands,
        "pre_install": pre_install,
        "post_install": [],
    }
    deb = apt_debian_packages_for_reqs_path(reqs, repo=repo)
    if deb:
        from .install_llm import merge_pre_install_debian_packages

        cfg["pre_install"] = merge_pre_install_debian_packages(
            list(cfg.get("pre_install") or []),
            ["git", "build-essential", *deb],
        )
    return cfg


def merge_install_config_with_swebench_baseline(
    cfg: dict[str, Any],
    repo_id: str,
    *,
    repo: Path | None = None,
) -> dict[str, Any]:
    """
    For repos with ``tests/runtests.py``, overlay SWE-bench harness fields onto *cfg*.

    Harness-visible keys come from the baseline; *cfg* cannot replace ``test_cmd`` with
    pytest or add meson/post_install pytest recipes.
    """
    from .repo_detect import uses_django_runtests

    if not uses_django_runtests(repo=repo, repo_id=repo_id):
        return cfg
    baseline = django_baseline_install_config(repo)
    out = dict(cfg)
    for key in HARNESS_INSTALL_CONFIG_KEYS:
        if key in baseline:
            out[key] = baseline[key]
    # Bookworm discover image uses plain ``pip install -e .`` (equivalent to SWE-bench).
    out["install"] = "pip install -e ."
    out.pop("pytest_plugins", None)
    out.pop("pytest_extra_args", None)
    out.pop("test_env", None)
    out.pop("django_pytest", None)
    out["django_runtests"] = True  # internal: docker discover only
    return out


def export_install_config_for_harness(
    cfg: dict[str, Any],
    *,
    language: str | None = None,
) -> dict[str, Any]:
    """Strip keys SWE-bench does not consume so JSONL matches ``run_evaluation``."""
    from .c_build import ensure_c_install_config
    from .go_build import ensure_go_docker_specs
    from .java_build import ensure_java_docker_specs, repair_gradle_install_config_for_harness
    from .js_build import ensure_js_docker_specs
    from .php_build import ensure_php_docker_specs
    from .ruby_build import ensure_ruby_docker_specs
    from .rust_build import ensure_rust_docker_specs

    lang = str(language or cfg.get("language") or "").lower()
    cfg = ensure_java_docker_specs(
        repair_gradle_install_config_for_harness(dict(cfg)),
        language=lang or None,
    )
    cfg = ensure_js_docker_specs(cfg, language=lang or None)
    cfg = ensure_go_docker_specs(cfg, language=lang or None)
    cfg = ensure_ruby_docker_specs(cfg, language=lang or None)
    cfg = ensure_rust_docker_specs(cfg, language=lang or None)
    cfg = ensure_php_docker_specs(cfg, language=lang or None)
    cfg = ensure_c_install_config(cfg)
    out: dict[str, Any] = {}
    for key in HARNESS_INSTALL_CONFIG_KEYS:
        if key not in cfg:
            continue
        val = cfg[key]
        if val is None:
            continue
        if key in ("pre_install", "post_install", "reqs_path", "pip_packages", "eval_commands"):
            if not isinstance(val, list) or not val:
                continue
        out[key] = val
    return out


_DJANGO_STATUS_RANK: dict[str, int] = {
    "PASSED": 3,
    "OK": 3,
    "SKIPPED": 2,
    "XFAIL": 2,
    "FAILED": 1,
    "FAILURE": 1,
    "ERROR": 0,
}


def normalize_django_runtests_test_key(test: str) -> str:
    """
    Strip migration noise glued to the first test line in runtests output.

    Example::

        Applying sites.0002_alter_domain_unique...test_editable (pkg.Class) ...
        -> test_editable (pkg.Class)
    """
    t = (test or "").strip()
    if not t:
        return t
    if "Applying " in t and "..." in t:
        rest = t.split("...", 1)[1].strip()
        if rest:
            return rest
    return t


def _django_status_rank(status: str | None) -> int:
    return _DJANGO_STATUS_RANK.get((status or "").upper(), 0)


def normalize_django_runtests_status_map(raw: dict[str, str]) -> dict[str, str]:
    """Merge duplicate keys after normalization; prefer stronger outcomes."""
    out: dict[str, str] = {}
    for key, status in raw.items():
        nk = normalize_django_runtests_test_key(key)
        if not nk or "Applying " in nk:
            continue
        if nk not in out or _django_status_rank(status) > _django_status_rank(out[nk]):
            out[nk] = status
    return out


def django_runtests_gradable_nodeid(nodeid: str) -> bool:
    """True when a runtests log key can be matched by SWE-bench ``parse_log_django``."""
    nid = normalize_django_runtests_test_key(nodeid)
    return bool(nid) and "Applying " not in nid


def _is_junit_display_name_label(case: str) -> bool:
    if " > " not in case:
        return False
    _, method = case.split(" > ", 1)
    method = method.strip()
    return bool(method) and not method.endswith("()") and "[" not in method


def _build_java_display_name_to_gradle_aliases(
    labels: list[str],
    gradle_map: dict[str, str],
) -> dict[str, str]:
    """Map JUnit XML display names to Gradle harness log keys (same class, ordered [n])."""
    import re
    from collections import defaultdict

    param_re = re.compile(r"^(.+)\[(\d+)\]$")
    display_by_class: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    for case in labels:
        if not _is_junit_display_name_label(case) or case in seen:
            continue
        cls, _ = case.split(" > ", 1)
        display_by_class[cls.strip()].append(case)
        seen.add(case)

    aliases: dict[str, str] = {}
    for cls, display_cases in display_by_class.items():
        prefix = f"{cls} > "
        groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for key in gradle_map:
            if not key.startswith(prefix):
                continue
            method = key[len(prefix) :]
            m = param_re.match(method)
            if not m or "(" not in m.group(1):
                continue
            base = m.group(1)
            groups[base].append((int(m.group(2)), key))
        for items in groups.values():
            if len(items) != len(display_cases):
                continue
            items.sort(key=lambda x: x[0])
            for display_case, (_, gradle_key) in zip(display_cases, items):
                aliases[display_case] = gradle_key
            break
    return aliases


def canonicalize_java_gradle_test_maps(
    junit_map: dict[str, str],
    gradle_log: str,
) -> dict[str, str]:
    """
    Prefer Gradle harness log keys (what SWE-bench grades) over JUnit XML display names.
    """
    from .test_log_parsers import parse_gradle_harness_log

    gradle_map = parse_gradle_harness_log(gradle_log)
    if not gradle_map:
        return junit_map
    aliases = _build_java_display_name_to_gradle_aliases(
        list(junit_map.keys()), gradle_map
    )
    out = dict(gradle_map)
    for junit_key, status in junit_map.items():
        gradle_key = aliases.get(junit_key) or junit_key
        if " > " in gradle_key:
            cls, method = gradle_key.split(" > ", 1)
            gradle_key = f"{cls.strip()} > {method.strip().rstrip('()')}"
        if gradle_key not in out:
            out[gradle_key] = status
    return out


def repair_java_fail_pass_lists(
    fail_to_pass: list[str],
    pass_to_pass: list[str],
) -> tuple[list[str], list[str]]:
    """Gradle ``fqcn > method`` labels for SWE-bench ``parse_log_gradle_custom`` grading."""
    from .diff_split import filter_swebench_gradable_nodeids, pytest_style_nodeid_to_gradle_test_key

    def _dedupe_ordered(labels: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for label in labels:
            key = pytest_style_nodeid_to_gradle_test_key(label)
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    f2p, _ = filter_swebench_gradable_nodeids(fail_to_pass, language="java")
    p2p, _ = filter_swebench_gradable_nodeids(pass_to_pass, language="java")
    return _dedupe_ordered(f2p), _dedupe_ordered(p2p)


def repair_django_fail_pass_lists(
    fail_to_pass: list[str],
    pass_to_pass: list[str],
) -> tuple[list[str], list[str]]:
    """Normalize and dedupe Django runtests labels for JSONL / SWE-bench grading."""
    from .diff_split import filter_swebench_gradable_nodeids

    def _dedupe_ordered(labels: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for label in labels:
            if label not in seen:
                seen.add(label)
                out.append(label)
        return out

    f2p, _ = filter_swebench_gradable_nodeids(fail_to_pass, django_runtests=True)
    p2p, _ = filter_swebench_gradable_nodeids(pass_to_pass, django_runtests=True)
    return _dedupe_ordered(f2p), _dedupe_ordered(p2p)


def repair_jsonl_row_install_config(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize ``install_config`` for SWE-bench harness (docker_specs, Gradle flags)."""
    out = dict(row)
    raw_ic = out.get("install_config")
    if raw_ic is None:
        return out
    if isinstance(raw_ic, str):
        try:
            ic = json.loads(raw_ic)
        except json.JSONDecodeError:
            return out
    elif isinstance(raw_ic, dict):
        ic = dict(raw_ic)
    else:
        return out
    lang = str(out.get("language") or ic.get("language") or "python").lower()
    ic["language"] = lang
    out["install_config"] = export_install_config_for_harness(ic, language=lang)
    return out


def repair_jsonl_row_for_harness(row: dict[str, Any]) -> dict[str, Any]:
    """Apply install_config + FAIL_TO_PASS / PASS_TO_PASS repairs for SWE-bench eval."""
    return repair_jsonl_row_test_labels(repair_jsonl_row_install_config(row))


def repair_jsonl_file(in_path: Path, out_path: Path) -> int:
    """Rewrite JSONL rows with harness-aligned install_config and test labels."""
    count = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with in_path.open(encoding="utf-8") as fin, out_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            row = repair_jsonl_row_for_harness(json.loads(line))
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def repair_jsonl_row_test_labels(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``row`` with repaired ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` strings."""
    out = dict(row)
    try:
        f2p = json.loads(out.get("FAIL_TO_PASS") or "[]")
    except json.JSONDecodeError:
        f2p = []
    try:
        p2p = json.loads(out.get("PASS_TO_PASS") or "[]")
    except json.JSONDecodeError:
        p2p = []
    if not isinstance(f2p, list):
        f2p = []
    if not isinstance(p2p, list):
        p2p = []
    lang = str(out.get("language") or "python").lower()
    if lang == "java":
        f2p, p2p = repair_java_fail_pass_lists(f2p, p2p)
    else:
        f2p, p2p = repair_django_fail_pass_lists(f2p, p2p)
    out["FAIL_TO_PASS"] = json.dumps(f2p)
    out["PASS_TO_PASS"] = json.dumps(p2p)
    return out


def parse_django_log_like_swebench(log: str) -> dict[str, str]:
    """Use SWE-bench ``parse_log_django`` when available; same test-name keys as grading."""
    root = find_swebench_root()
    if root is not None:
        mod_path = root / "swebench" / "harness" / "log_parsers" / "python.py"
        if mod_path.is_file():
            try:
                mod = _load_module_from_path("swebench_harness_log_parsers_python", mod_path)
                raw = mod.parse_log_django(log, None)  # type: ignore[arg-type]
                return normalize_django_runtests_status_map(raw)
            except Exception:
                pass

    return _parse_django_log_local(log)


def _parse_django_log_local(log: str) -> dict[str, str]:
    """Local copy of SWE-bench ``parse_log_django`` (no ``swebench`` package import)."""
    test_status_map: dict[str, str] = {}
    lines = log.split("\n")
    prev_test: str | None = None
    for line in lines:
        line = line.strip()
        if "--version is equivalent to version" in line:
            test_status_map["--version is equivalent to version"] = "PASSED"
        if " ... " in line:
            prev_test = normalize_django_runtests_test_key(line.split(" ... ")[0])
        for suffix in (" ... ok", " ... OK", " ...  OK"):
            if line.endswith(suffix):
                test = normalize_django_runtests_test_key(line.rsplit(suffix, 1)[0])
                test_status_map[test] = "PASSED"
                break
        if " ... skipped" in line:
            test = normalize_django_runtests_test_key(line.split(" ... skipped")[0])
            test_status_map[test] = "SKIPPED"
        if line.endswith(" ... FAIL"):
            test = normalize_django_runtests_test_key(line.split(" ... FAIL")[0])
            test_status_map[test] = "FAILED"
        if line.startswith("FAIL:"):
            test = line.split()[1].strip()
            test_status_map[test] = "FAILED"
        if line.endswith(" ... ERROR"):
            test = normalize_django_runtests_test_key(line.split(" ... ERROR")[0])
            test_status_map[test] = "ERROR"
        if line.startswith("ERROR:"):
            test = line.split()[1].strip()
            test_status_map[test] = "ERROR"
        if line.lstrip().startswith("ok") and prev_test is not None:
            test_status_map[prev_test] = "PASSED"
    patterns = [
        r"^(.*?)\s\.\.\.\sTesting\ against\ Django\ installed\ in\ ((?s:.*?))\ silenced\)\.\nok$",
        r"^(.*?)\s\.\.\.\sInternal\ Server\ Error:\ \/(.*)\/\nok$",
        r"^(.*?)\s\.\.\.\sSystem check identified no issues \(0 silenced\)\nok$",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, log, re.MULTILINE):
            test_status_map[normalize_django_runtests_test_key(match.group(1))] = "PASSED"
    return normalize_django_runtests_status_map(test_status_map)


def outcome_passed(status: str | None) -> bool:
    if not status:
        return False
    return status.upper() in ("PASSED", "OK", "XFAIL")


def outcome_failed(status: str | None) -> bool:
    if not status:
        return True
    return status.upper() in ("FAILED", "FAILURE", "ERROR")
