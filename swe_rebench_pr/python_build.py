"""Python repo-specific install/test helpers for Docker discover."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .repo_detect import repo_needs_dateutil_zoneinfo

DATEUTIL_UPDATEZINFO_CMD = "python3 updatezinfo.py zonefile_metadata.json"

_TOX_INI_PYTEST_PACKAGES: tuple[str, ...] = (
    "pytest",
    "pytest-cov",
    "pytest-randomly",
    "wcag-contrast-ratio",
)

_DEFAULT_PYTEST_CMD = (
    "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider"
)

_TEST_EXTRA_GROUP_NAMES = ("tests", "test", "testing", "dev", "devel", "development")
_PLAIN_INSTALL_RE = re.compile(
    r"^(?:python3?\s+-m\s+)?pip\s+install(?:\s+-e)?\s+\.?\s*$",
    re.IGNORECASE,
)
_UV_NOT_FOUND = re.compile(r"\buv:\s*command not found\b", re.IGNORECASE)
_BUILD_BACKEND_PIP: dict[str, str] = {
    "pdm.backend": "pdm-backend",
    "pdm_backend": "pdm-backend",
    "hatchling": "hatchling",
    "flit_core": "flit-core",
    "flit.core": "flit-core",
    "poetry.core": "poetry-core",
    "poetry_core": "poetry-core",
    "maturin": "maturin",
    "setuptools": "setuptools",
    "wheel": "wheel",
}
_TEST_REQUIREMENTS_GLOBS = (
    "requirements-test.txt",
    "requirements/test.txt",
    "requirements/tests.txt",
    "requirements/dev.txt",
    "requirements/devel.txt",
    "test-requirements.txt",
    "tests-requirements.txt",
    "dev-requirements.txt",
    "requirements_dev.txt",
    "requirements-dev.txt",
)


def needs_dateutil_zoneinfo(*, repo: Path | None = None) -> bool:
    """True when checkout artifacts require a zoneinfo tarball build step."""
    return repo is not None and repo_needs_dateutil_zoneinfo(repo)


def _read_repo_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _install_has_extras(install: str) -> bool:
    return bool(re.search(r"pip\s+install[^\n;|&]*\[[^\]]+\]", install, re.IGNORECASE))


def _is_plain_editable_install(install: str) -> bool:
    s = install.strip()
    if not s or s.startswith("#"):
        return False
    if _install_has_extras(s):
        return False
    return bool(_PLAIN_INSTALL_RE.match(s)) or s in ("pip install -e .", "pip install .")


def _pyproject_section_lines(text: str, section_marker: str) -> list[str]:
    """Lines belonging to a TOML table whose header contains *section_marker*."""
    lines: list[str] = []
    active = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            header = stripped[1:-1].strip().lower()
            active = section_marker.lower() in header
            continue
        if active:
            lines.append(raw)
    return lines


def _parse_toml_table_group_names(lines: list[str]) -> list[str]:
    groups: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        m = re.match(r'^\s*["\']?([A-Za-z0-9_-]+)["\']?\s*=', raw)
        if not m:
            continue
        name = m.group(1).lower()
        if name in _TEST_EXTRA_GROUP_NAMES and name not in seen:
            seen.add(name)
            groups.append(name)
    return groups


def _parse_dependency_group_packages(text: str, group_name: str) -> list[str]:
    """Extract pip requirement strings from a PEP 735 ``[dependency-groups]`` entry."""
    lines = _pyproject_section_lines(text, "dependency-groups")
    pkgs: list[str] = []
    in_group = False
    for raw in lines:
        if re.match(rf'^\s*["\']?{re.escape(group_name)}["\']?\s*=\s*\[', raw, re.IGNORECASE):
            in_group = True
            continue
        if not in_group:
            continue
        if re.match(r"^\s*\]", raw):
            break
        if "include-group" in raw.lower():
            continue
        for item in re.findall(r'["\']([^"\']+)["\']', raw):
            if item.strip() and "include-group" not in item.lower():
                pkgs.append(item.strip())
    return pkgs


def _pyproject_optional_extra_groups(text: str) -> list[str]:
    groups: list[str] = []
    seen: set[str] = set()
    for marker in ("optional-dependencies", "options.extras_require"):
        for name in _parse_toml_table_group_names(_pyproject_section_lines(text, marker)):
            if name not in seen:
                seen.add(name)
                groups.append(name)
    return _order_test_extra_groups(groups)


def _pyproject_dependency_group_packages(text: str) -> list[str]:
    """Packages declared under test/dev ``[dependency-groups]`` (PEP 735 / PDM)."""
    pkgs: list[str] = []
    seen: set[str] = set()
    for group in _order_test_extra_groups(
        _parse_toml_table_group_names(_pyproject_section_lines(text, "dependency-groups"))
    ):
        for req in _parse_dependency_group_packages(text, group):
            base = req.split("==")[0].split("[")[0].strip().lower()
            if base and base not in seen:
                seen.add(base)
                pkgs.append(req)
    return pkgs


def _pyproject_test_extra_groups(text: str) -> list[str]:
    return _pyproject_optional_extra_groups(text)


def _order_test_extra_groups(groups: list[str]) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()
    for preferred in _TEST_EXTRA_GROUP_NAMES:
        if preferred in groups and preferred not in seen:
            seen.add(preferred)
            order.append(preferred)
    for name in groups:
        if name not in seen:
            seen.add(name)
            order.append(name)
    return order


def _pyproject_build_backend_packages(text: str) -> list[str]:
    pkgs: list[str] = []
    seen: set[str] = set()
    block = "\n".join(_pyproject_section_lines(text, "build-system"))
    m = re.search(r"requires\s*=\s*\[(.*?)\]", block, re.DOTALL | re.IGNORECASE)
    if not m:
        return pkgs
    for item in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
        base = re.split(r"[<>=!~;]", item.strip())[0].strip()
        if not base:
            continue
        mapped = _BUILD_BACKEND_PIP.get(base.lower().replace("-", "_"), base)
        low = mapped.lower()
        if low not in seen and low not in ("setuptools", "wheel"):
            seen.add(low)
            pkgs.append(mapped)
    return pkgs


def _pyproject_pytest_minversion(text: str) -> str:
    for marker in ("tool.pytest", "tool:pytest"):
        block = "\n".join(_pyproject_section_lines(text, marker))
        m = re.search(r'minversion\s*=\s*["\']([^"\']+)["\']', block, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    m = re.search(r'minversion\s*=\s*["\']([^"\']+)["\']', text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _setup_cfg_test_extra_groups(text: str) -> list[str]:
    lines = _pyproject_section_lines(text, "options.extras_require")
    return _order_test_extra_groups(_parse_toml_table_group_names(lines))


def _setup_py_test_extra_groups(text: str) -> list[str]:
    groups: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(
        r'["\'](test|tests|testing|dev|devel|development)["\']\s*:',
        text,
        re.IGNORECASE,
    ):
        name = m.group(1).lower()
        if name not in seen:
            seen.add(name)
            groups.append(name)
    return _order_test_extra_groups(groups)


def _find_test_requirements_files(repo: Path) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for rel in _TEST_REQUIREMENTS_GLOBS:
        path = repo / rel
        if path.is_file():
            norm = rel.replace("\\", "/")
            if norm not in seen:
                seen.add(norm)
                out.append(norm)
    for pattern in ("requirements/*test*.txt", "requirements/*dev*.txt"):
        for path in sorted(repo.glob(pattern)):
            if not path.is_file():
                continue
            rel = path.relative_to(repo).as_posix()
            if rel not in seen:
                seen.add(rel)
                out.append(rel)
    return out


def infer_python_test_install_signals(repo: Path) -> dict[str, Any]:
    """
    Artifact-driven hints for pytest/dev install (pyproject, setup, requirements).

    Used to augment plain ``pip install -e .`` recipes before Docker discover.
    """
    extra_groups: list[str] = []
    build_backends: list[str] = []
    pytest_minversion = ""
    reqs_files: list[str] = []
    dependency_group_pkgs: list[str] = []

    ppt = repo / "pyproject.toml"
    if ppt.is_file():
        text = _read_repo_text(ppt)
        extra_groups = _pyproject_test_extra_groups(text)
        build_backends = _pyproject_build_backend_packages(text)
        pytest_minversion = _pyproject_pytest_minversion(text)
        dependency_group_pkgs = _pyproject_dependency_group_packages(text)

    setup_cfg = repo / "setup.cfg"
    if setup_cfg.is_file() and not extra_groups:
        extra_groups = _setup_cfg_test_extra_groups(_read_repo_text(setup_cfg))

    setup_py = repo / "setup.py"
    if setup_py.is_file() and not extra_groups:
        extra_groups = _setup_py_test_extra_groups(_read_repo_text(setup_py))

    reqs_files = _find_test_requirements_files(repo)

    pip_packages: list[str] = list(dependency_group_pkgs)
    if pytest_minversion:
        pip_packages.append(f"pytest>={pytest_minversion}")
    elif extra_groups or dependency_group_pkgs or reqs_files or (repo / "tests").is_dir() or list(repo.glob("test_*.py")):
        if not any(p.lower().startswith("pytest") for p in pip_packages):
            pip_packages.append("pytest")

    return {
        "extra_groups": extra_groups,
        "build_backends": build_backends,
        "pytest_minversion": pytest_minversion,
        "reqs_files": reqs_files,
        "pip_packages": pip_packages,
        "dependency_group_pkgs": dependency_group_pkgs,
    }


def editable_install_with_test_extras(extra_groups: list[str]) -> str:
    """Shell ``pip install -e`` with fallbacks across common extra group names."""
    ordered = _order_test_extra_groups(extra_groups)
    if not ordered:
        return "pip install -e ."
    attempts = [f'pip install -e ".[{name}]"' for name in ordered]
    attempts.append("pip install -e .")
    return " || ".join(attempts)


def _merge_pip_packages(existing: list[Any], new_pkgs: list[str]) -> list[str]:
    out = [str(x).strip() for x in existing if isinstance(x, str) and str(x).strip()]
    seen = {p.split("==")[0].split("[")[0].strip().lower() for p in out}
    for pkg in new_pkgs:
        base = pkg.split("==")[0].split("[")[0].strip().lower()
        if not base or base in seen:
            continue
        seen.add(base)
        out.append(pkg)
    return out


def _should_augment_python_install(cfg: dict[str, Any]) -> bool:
    install = str(cfg.get("install") or "").strip()
    if not install or install == "true":
        return True
    if _install_has_extras(install):
        return False
    return _is_plain_editable_install(install)


def merge_python_test_install_into_config(
    cfg: dict[str, Any],
    repo: Path | None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Merge pytest/dev/build-backend hints from repo artifacts into *cfg*."""
    if repo is None or not repo.is_dir():
        return dict(cfg)

    signals = infer_python_test_install_signals(repo)
    out = dict(cfg)

    pip_add = list(signals.get("build_backends") or []) + list(signals.get("pip_packages") or [])
    if pip_add:
        out["pip_packages"] = _merge_pip_packages(list(out.get("pip_packages") or []), pip_add)

    reqs_add = list(signals.get("reqs_files") or [])
    if reqs_add:
        reqs = list(out.get("reqs_path") or [])
        seen = {str(r).strip() for r in reqs}
        for rel in reqs_add:
            if rel not in seen:
                seen.add(rel)
                reqs.append(rel)
        out["reqs_path"] = reqs

    extra_groups = list(signals.get("extra_groups") or [])
    if extra_groups and (force or _should_augment_python_install(out)):
        out["install"] = editable_install_with_test_extras(extra_groups)

    return out


def log_indicates_python_pytest_env_failure(log: str, *, docker_exit: int = 0) -> bool:
    low = (log or "").lower()
    if docker_exit in (4, 5):
        return True
    needles = (
        "no module named pytest",
        "modulenotfounderror: no module named pytest",
        "pytest: error:",
        "pytest command line usage error",
        "could not find a version that satisfies the requirement pytest",
        "requires pytest>=",
        "requires pytest >=",
        "minversion",
        "pdm-backend",
        "hatchling",
        "metadata-generation-failed",
        "backend unavailable",
        "build backend",
    )
    if any(n in low for n in needles):
        if "minversion" in low:
            return "pytest" in low
        return True
    return False


def remediate_python_install_from_log(
    cfg: dict[str, Any],
    log: str,
    *,
    repo: Path | None = None,
    docker_exit: int = 0,
) -> dict[str, Any]:
    """Deterministic Python install fixes for pytest/build-backend/test-deps failures."""
    from .apt_from_log import remediate_apt_install_from_log

    out = remediate_apt_install_from_log(dict(cfg), log)
    if _UV_NOT_FOUND.search(log):
        from .ci_install_normalize import docker_safe_python_install

        out["install"] = docker_safe_python_install(str(out.get("install") or ""))
    if repo is None:
        return out

    force = log_indicates_python_pytest_env_failure(log, docker_exit=docker_exit)
    out = merge_python_test_install_into_config(out, repo, force=force or _should_augment_python_install(out))

    if force:
        pip = list(out.get("pip_packages") or [])
        pip = _merge_pip_packages(pip, ["pip", "wheel", "setuptools"])
        out["pip_packages"] = pip
    return out


def _has_updatezinfo_command(lines: list[Any]) -> bool:
    return any(
        "updatezinfo.py" in str(ln) and not str(ln).strip().startswith("#")
        for ln in lines
    )


def augment_python_install_config(
    cfg: dict[str, Any],
    *,
    repo: Path | None = None,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """Add build steps required by some Python repos (e.g. dateutil zoneinfo tarball)."""
    del repo_id  # artifact-only; kept for call-site compatibility
    out = merge_python_test_install_into_config(dict(cfg), repo)
    if not needs_dateutil_zoneinfo(repo=repo):
        return out

    post = list(out.get("post_install") or [])
    eval_cmds = list(out.get("eval_commands") or [])
    if _has_updatezinfo_command(post) or _has_updatezinfo_command(eval_cmds):
        return out

    # ``updatezinfo`` imports ``dateutil.zoneinfo.rebuild``; run after editable install.
    post.append(DATEUTIL_UPDATEZINFO_CMD)
    eval_cmds.append(DATEUTIL_UPDATEZINFO_CMD)
    out["post_install"] = post
    out["eval_commands"] = eval_cmds
    return out


def merge_tox_ini_into_config(cfg: dict[str, Any], repo: Path | None) -> dict[str, Any]:
    """Add common tox.ini pytest plugin deps to ``pip_packages``."""
    if repo is None or not (repo / "tox.ini").is_file():
        return dict(cfg)
    out = dict(cfg)
    pkgs = list(out.get("pip_packages") or [])
    pkgs_low = {p.split("==")[0].split("[")[0].strip().lower() for p in pkgs}
    for name in _TOX_INI_PYTEST_PACKAGES:
        if name.lower() not in pkgs_low:
            pkgs.append(name)
    out["pip_packages"] = pkgs
    return out


def apply_python_version_from_repo(cfg: dict[str, Any], repo: Path | None) -> dict[str, Any]:
    """Set ``python`` from ``pyproject.toml`` ``requires-python`` when present."""
    if repo is None:
        return dict(cfg)
    from .swebench_align import python_version_from_repo

    pv = python_version_from_repo(repo)
    if not pv:
        return dict(cfg)
    out = dict(cfg)
    out["python"] = pv
    return out


def finalize_python_install_config(
    cfg: dict[str, Any],
    repo: Path | None,
    *,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """
    Shared repo-first + Docker-effective Python install steps.

    Ensures JSONL ``install_config`` matches what Docker discover runs.
    """
    out = merge_tox_ini_into_config(dict(cfg), repo)
    out = apply_python_version_from_repo(out, repo)
    out = augment_python_install_config(out, repo=repo, repo_id=repo_id)
    return out


def pytest_test_cmd_from_targets(targets: list[str], base_cmd: str = "") -> str:
    """Build a scoped pytest command from ``test_patch`` file paths."""
    paths = expand_pytest_discover_targets(targets, include_parent_dir=False)
    base = str(base_cmd or "").strip()
    if not base or "pytest" not in base.lower():
        base = _DEFAULT_PYTEST_CMD
    if not paths:
        return base
    flags = python_pytest_cmd_without_collection_paths(base)
    return f"{flags} {' '.join(paths)}"


def python_pytest_cmd_without_collection_paths(cmd: str) -> str:
    """Keep pytest flags / junit options; drop directory-only collection args."""
    skip = frozenset(
        {
            "pytest",
            "python3",
            "python",
            "-m",
            "||",
            "&&",
            "tests",
            "tests/",
            "test",
            "test/",
        }
    )
    parts: list[str] = []
    for token in str(cmd or "").split():
        if not token:
            continue
        if token.startswith("-"):
            parts.append(token)
            continue
        low = token.lower()
        if low in skip:
            continue
        if token.endswith(".py"):
            continue
        if "/" in token and not token.endswith(".py"):
            continue
        if low not in skip:
            parts.append(token)
    out = " ".join(parts).strip()
    return out or _DEFAULT_PYTEST_CMD


def merge_python_build_into_config(
    cfg: dict[str, Any],
    repo: Path,
    test_paths: list[str],
    *,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """Apply per-PR pytest scoping and repo artifact install hints before Docker."""
    from .ci_fidelity import should_preserve_ci_test_cmd, test_cmd_needs_explicit_pytest_paths

    out = dict(cfg)
    scoped = [p for p in test_paths if isinstance(p, str) and p.strip()]
    tc_in = str(out.get("test_cmd") or "").strip()
    needs_paths = test_cmd_needs_explicit_pytest_paths(tc_in, scoped)
    if scoped and needs_paths:
        out["test_cmd"] = pytest_test_cmd_from_targets(scoped, tc_in)
    elif scoped and should_preserve_ci_test_cmd(out) and needs_paths:
        out["test_cmd"] = pytest_test_cmd_from_targets(scoped, tc_in)
    return finalize_python_install_config(out, repo, repo_id=repo_id)


def expand_pytest_discover_targets(
    paths: list[str],
    *,
    include_parent_dir: bool = True,
) -> list[str]:
    """
    Normalize pytest path targets from ``test_patch`` for Docker ``targets.txt``.

    Keeps file paths, adds ``tests/`` when all targets live under one test tree, and
    drops non-``.py`` paths that are not pytest collection targets.
    """
    out: list[str] = []
    seen: set[str] = set()
    py_paths: list[str] = []
    for raw in paths:
        p = raw.replace("\\", "/").strip().lstrip("/")
        if not p:
            continue
        if not p.endswith(".py"):
            continue
        if p not in seen:
            seen.add(p)
            py_paths.append(p)
            out.append(p)
    if not py_paths or not include_parent_dir:
        return out
    parents = {str(Path(p).parent).strip("/") for p in py_paths}
    if len(parents) == 1:
        parent = next(iter(parents))
        if parent and parent.startswith("tests") and parent not in seen:
            seen.add(parent)
            out.insert(0, parent)
    return out


def pytest_log_indicates_tests_ran(log: str) -> bool:
    low = (log or "").lower()
    return bool(
        re.search(r"\b\d+\s+passed\b", low)
        or re.search(r"\b\d+\s+failed\b", low)
        or " passed in " in low
        or re.search(r"^=+\s*\d+\s+passed", low, re.M)
    )


def pytest_log_indicates_all_passed(log: str) -> bool:
    low = (log or "").lower()
    m = re.search(r"(\d+)\s+passed", low)
    if m and int(m.group(1)) > 0:
        if re.search(r"\d+\s+failed", low) or re.search(r"\d+\s+error", low):
            return False
        return True
    return " passed in " in low and " failed" not in low and " error" not in low


def filter_pytest_map_to_test_patch_paths(
    case_map: dict[str, str],
    test_patch_paths: list[str],
    *,
    test_patch: str = "",
) -> dict[str, str]:
    from .diff_split import junit_outcome_counts_for_paths

    if not case_map or not test_patch_paths:
        return dict(case_map)
    out: dict[str, str] = {}
    for k, v in case_map.items():
        pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(
            {k: v},
            test_patch_paths,
            language="python",
            test_patch=test_patch,
        )
        if tot > 0:
            out[k] = v
    return out


def refine_python_junit_maps_for_discover(
    base_map: dict[str, str],
    patch_map: dict[str, str],
    *,
    test_patch_paths: list[str],
    work_dir: Path,
    test_patch: str = "",
) -> tuple[dict[str, str], dict[str, str]]:
    """Scope pytest JUnit to ``test_patch`` paths; log fallback when XML empty."""
    from .test_log_parsers import parse_pytest_log

    scoped_base = filter_pytest_map_to_test_patch_paths(
        base_map, test_patch_paths, test_patch=test_patch
    )
    scoped_patch = filter_pytest_map_to_test_patch_paths(
        patch_map, test_patch_paths, test_patch=test_patch
    )

    if not scoped_patch:
        patch_log = work_dir / "test-patch.log"
        if patch_log.is_file() and pytest_log_indicates_tests_ran(
            patch_log.read_text(encoding="utf-8", errors="replace")
        ):
            log_map = parse_pytest_log(
                patch_log.read_text(encoding="utf-8", errors="replace")
            )
            scoped_patch = filter_pytest_map_to_test_patch_paths(
                log_map, test_patch_paths, test_patch=test_patch
            )
            if (
                not scoped_patch
                and scoped_base
                and pytest_log_indicates_all_passed(
                    patch_log.read_text(encoding="utf-8", errors="replace")
                )
            ):
                scoped_patch = {k: "passed" for k in scoped_base}

    if not scoped_base:
        base_log = work_dir / "test-base.log"
        if base_log.is_file():
            log_map = parse_pytest_log(
                base_log.read_text(encoding="utf-8", errors="replace")
            )
            scoped_base = filter_pytest_map_to_test_patch_paths(
                log_map, test_patch_paths, test_patch=test_patch
            )

    return scoped_base, scoped_patch


def python_docker_test_cmd_for_entry(cfg: dict[str, Any]) -> str:
    """
    ``test_cmd`` for Docker when it is a pytest invocation (CI or heuristic).

    Inserts ``--junitxml=__JUNIT_OUT__`` when missing; shell substitutes the path.
    Returns flag-only cmd when ``test_cmd`` already lists concrete ``.py`` paths
    (Docker ``T[@]`` supplies collection paths).
    """
    tc = str(cfg.get("test_cmd") or "").strip()
    if not tc or "pytest" not in tc.lower():
        return ""
    import shlex

    has_file_paths = any(
        t.endswith(".py") for t in shlex.split(tc) if not t.startswith("-")
    )
    if has_file_paths:
        tc = python_pytest_cmd_without_collection_paths(tc)
    if "__JUNIT_OUT__" in tc:
        return tc
    low = tc.lower()
    if "junitxml" in low or "junit-xml" in low:
        return tc
    return f"{tc} --junitxml=__JUNIT_OUT__ -o junit_family=xunit2"


def pytest_junit_nodeid_in_test_patch_paths(
    nodeid: str,
    paths: list[str],
    *,
    test_patch: str = "",
) -> bool:
    """Extra pytest JUnit ↔ ``test_patch`` matching (module classnames, patch hints)."""
    if not nodeid or not paths:
        return False
    from .diff_split import (
        _nodeid_in_test_patch_paths,
        _nodeid_leading_relpath,
        _path_filter_sets,
        _test_path_aliases,
    )

    path_set, dotted, java_fqcns = _path_filter_sets(paths)
    if _nodeid_in_test_patch_paths(
        nodeid,
        path_set,
        dotted,
        java_fqcns,
        test_patch_paths=paths,
        test_patch=test_patch,
        language="",
    ):
        return True
    head = _nodeid_leading_relpath(nodeid)
    for raw in paths:
        for alias in _test_path_aliases(raw):
            if not alias.endswith(".py"):
                continue
            mod = alias[:-3].replace("/", ".")
            if head == mod or head.startswith(mod + ".") or head.startswith(mod + "::"):
                return True
            base = Path(alias).name.removesuffix(".py")
            if base and (head.endswith(base) or f".{base}" in head or f"/{base}" in head):
                return True
    if len(paths) == 1 and "::" in nodeid and test_patch:
        func = nodeid.rsplit("::", 1)[-1].split("[", 1)[0].strip()
        if func and re.search(rf"def\s+{re.escape(func)}\s*\(", test_patch):
            stem = Path(paths[0]).stem
            if stem in nodeid or paths[0] in nodeid:
                return True
    return False


def slice_failures_are_dateutil_zoneinfo(
    failures: list[tuple[str, str]],
    errors: list[tuple[str, str]],
) -> bool:
    """True when every failure/error is the missing zoneinfo tarball (env, not test logic)."""
    msgs = [msg for _, msg in failures + errors if msg.strip()]
    if not msgs:
        return False
    needle = "dateutil-zoneinfo.tar.gz"
    return all(needle in msg for msg in msgs)
