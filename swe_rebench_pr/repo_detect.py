"""Detect repo-specific build/test behavior from checkout artifacts, not GitHub slugs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_JEST_CONFIG_NAMES = (
    ".config/jest.js",
    "jest.config.js",
    "jest.config.cjs",
    "jest.config.mjs",
    "jest.config.ts",
)

_NPS_JEST_TEST_RE = re.compile(
    r"^(?:.*/)?(?:server-only\.)?test-[^/]+\.js$",
    re.IGNORECASE,
)


def _read_repo_text(path: Path, *, max_bytes: int = 500_000) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except OSError:
        return ""


def repo_has_django_runtests(repo: Path) -> bool:
    """True when the tree uses Django's upstream ``tests/runtests.py`` harness."""
    return (repo / "tests" / "runtests.py").is_file()


def _django_repo_id_fallback(repo_id: str) -> bool:
    """Last resort when no clone exists (e.g. ``language=auto`` before checkout)."""
    repo_l = str(repo_id or "").lower().replace("__", "/")
    return bool(
        re.match(r"^django/django(-|$)", repo_l)
        or repo_l.endswith("/django")
        or repo_l == "django/django"
    )


def uses_django_runtests(*, repo: Path | None = None, repo_id: str | None = None) -> bool:
    if repo is not None:
        return repo_has_django_runtests(repo)
    return _django_repo_id_fallback(repo_id or "")


def repo_uses_meson_python_backend(repo: Path) -> bool:
    """True when the project builds via meson-python (e.g. pandas), not plain setuptools."""
    ppt = repo / "pyproject.toml"
    if ppt.is_file():
        text = _read_repo_text(ppt).lower()
        if "meson-python" in text or "mesonpython" in text or 'build-backend = "meson' in text:
            return True
    return (repo / "meson.build").is_file()


def repo_needs_dateutil_zoneinfo(repo: Path) -> bool:
    """True when the tree ships ``updatezinfo.py`` and zoneinfo metadata (dateutil-style)."""
    return (repo / "updatezinfo.py").is_file() and (
        (repo / "zonefile_metadata.json").is_file()
        or (repo / "zoneinfo_metadata.json").is_file()
    )


def _jest_config_texts(repo: Path) -> list[str]:
    texts: list[str] = []
    for name in _JEST_CONFIG_NAMES:
        p = repo / name
        if p.is_file():
            texts.append(_read_repo_text(p))
    return texts


def jest_config_references_http_node(repo: Path) -> bool:
    for text in _jest_config_texts(repo):
        if "http/node" in text or "http\\node" in text.replace("<rootDir>/", ""):
            return True
    return False


def repo_needs_jest_http_rollup_build(repo: Path) -> bool:
    """True when Jest maps ``http/node`` but rollup output is not built yet."""
    http_node = repo / "http" / "node"
    if (http_node / "index.cjs").is_file() or (http_node / "index.js").is_file():
        return False
    return jest_config_references_http_node(repo)


def jest_http_node_build_shell_prefix(*, repo_dir: str = "/testbed") -> str:
    """Shell guard: build ``http/node`` when Jest config expects it (artifact-based)."""
    return (
        f"(cd {repo_dir} && "
        "if [[ ! -f http/node/index.cjs ]] && [[ ! -f http/node/index.js ]]; then "
        "for cfg in .config/jest.js jest.config.js jest.config.cjs jest.config.mjs; do "
        '  if [[ -f "$cfg" ]] && grep -qE "http/node|http\\\\/node" "$cfg" 2>/dev/null; then '
        '    echo "[docker] jest http/node rollup build" >&2; '
        "    (npx nps build.rollup 2>/dev/null || npx rollup -c 2>/dev/null || true); "
        "    break; "
        "  fi; "
        "done; "
        "fi; fi) && "
    )


def nps_jest_uses_test_dash_regex(repo: Path) -> bool:
    """True when Jest is configured for ``test-*.js`` under ``__tests__/`` (NPS monorepos)."""
    for text in _jest_config_texts(repo):
        if "testRegex" in text and "test-" in text:
            return True
        if "__tests__" in text and "test-" in text:
            return True
    tests_dir = repo / "__tests__"
    if tests_dir.is_dir():
        for p in tests_dir.rglob("test-*.js"):
            if p.is_file():
                return True
    return False


def is_nps_jest_test_path(path: str) -> bool:
    norm = path.replace("\\", "/").strip()
    return bool(_NPS_JEST_TEST_RE.match(norm))


def should_apply_nps_jest_target_filter(repo: Path) -> bool:
    from .js_build import uses_nps_test_script

    return uses_nps_test_script(repo) and nps_jest_uses_test_dash_regex(repo)


def filter_nps_jest_test_targets(repo: Path, paths: list[str]) -> list[str]:
    """Drop NPS/Jest paths that break scoped discover (submodules, huge-repo, non-Jest)."""
    if not should_apply_nps_jest_target_filter(repo):
        return paths
    filtered = [
        p
        for p in paths
        if "-in-submodule.js" not in p
        and "FixtureFSSubmodule" not in p
        and "-checkout-huge-repo" not in p
        and is_nps_jest_test_path(p)
    ]
    return filtered or paths


def repo_is_react_native_app(repo: Path) -> bool:
    """True when ``package.json`` declares a React Native app (mobile, not Node CI)."""
    pkg = repo / "package.json"
    if not pkg.is_file():
        return False
    text = _read_repo_text(pkg).lower()
    return '"react-native"' in text or "'react-native'" in text


# Writable snapshots for Jest/Mocha in Docker (all nested ``**/__snapshots__`` trees).
JAVASCRIPT_SNAPSHOT_CHMOD_CMD = (
    'find . -type d -name __snapshots__ -not -path "*/node_modules/*" '
    '-exec chmod -R u+w {} + 2>/dev/null || true'
)


def _path_under_node_modules(p: Path) -> bool:
    return "node_modules" in p.parts


def discover_javascript_snapshot_dirs(repo: Path) -> list[str]:
    """Repo-relative ``__snapshots__`` directories (excluding ``node_modules``)."""
    found: list[str] = []
    seen: set[str] = set()
    for base_name in ("__tests__", "__integration__", "__node_tests__", "test", "tests"):
        base = repo / base_name
        if not base.is_dir():
            continue
        try:
            for p in base.rglob("__snapshots__"):
                if not p.is_dir() or _path_under_node_modules(p):
                    continue
                rel = str(p.relative_to(repo)).replace("\\", "/")
                if rel not in seen:
                    seen.add(rel)
                    found.append(rel)
        except OSError:
            continue
    if not found:
        try:
            for p in repo.rglob("__snapshots__"):
                if not p.is_dir() or _path_under_node_modules(p):
                    continue
                rel = str(p.relative_to(repo)).replace("\\", "/")
                if rel not in seen:
                    seen.add(rel)
                    found.append(rel)
        except OSError:
            pass
    return sorted(found)


def repo_has_javascript_snapshots(repo: Path) -> bool:
    """True when any ``__snapshots__`` tree exists (Jest/Mocha/web-test-runner)."""
    return bool(discover_javascript_snapshot_dirs(repo))


_MAKEFILE_NAMES = ("Makefile.js", "Makefile")


def repo_makefile_path(repo: Path) -> Path | None:
    """Return ``Makefile.js`` or ``Makefile`` when the repo uses shelljs/make targets."""
    for name in _MAKEFILE_NAMES:
        p = repo / name
        if p.is_file():
            return p
    return None


def _package_json_test_script(repo: Path) -> str:
    pkg = repo / "package.json"
    if not pkg.is_file():
        return ""
    try:
        data = json.loads(_read_repo_text(pkg, max_bytes=200_000))
    except (json.JSONDecodeError, TypeError):
        return ""
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    return str(scripts.get("test") or "")


def repo_uses_makefile_test(repo: Path) -> bool:
    """True when ``npm test`` / CI invokes ``node Makefile[.js]`` (eslint, etc.)."""
    test = _package_json_test_script(repo)
    if re.search(r"\bnode\s+Makefile(?:\.js)?\b", test, re.I):
        return True
    return repo_makefile_path(repo) is not None and bool(test.strip())


def makefile_text(repo: Path) -> str:
    path = repo_makefile_path(repo)
    return _read_repo_text(path) if path else ""


def makefile_has_mocha_target(repo: Path) -> bool:
    """True when the Makefile defines a Mocha unit-test target (``target.mocha``)."""
    text = makefile_text(repo)
    if not text:
        return False
    if re.search(r"target\.mocha\s*=", text):
        return True
    return "MOCHA" in text and ("_mocha" in text or "mocha/bin" in text)


def makefile_uses_c8_with_mocha(repo: Path) -> bool:
    text = makefile_text(repo)
    return bool(text) and "c8" in text and "MOCHA" in text


def repo_has_jest_haste_fixture_risk(repo: Path, *, min_fixture_pkg_json: int = 3) -> bool:
    """
    Repos like eslint keep intentionally invalid ``package.json`` under ``tests/fixtures``.

    Scoped ``npx jest`` still runs haste-map over the tree and fails before tests run.
    """
    for root_name in ("tests/fixtures", "test/fixtures"):
        fixtures = repo.joinpath(*root_name.split("/"))
        if not fixtures.is_dir():
            continue
        count = 0
        try:
            for p in fixtures.rglob("package.json"):
                if p.is_file():
                    count += 1
                    if count >= min_fixture_pkg_json:
                        return True
        except OSError:
            continue
    return False


def repo_uses_mocha_with_snapshots(repo: Path) -> bool:
    """True when Mocha is used and snapshot dirs exist (may need writable snapshots)."""
    if not repo_has_javascript_snapshots(repo):
        return False
    pkg = repo / "package.json"
    if not pkg.is_file():
        return True
    text = _read_repo_text(pkg).lower()
    return '"mocha"' in text or "mocha " in text


def javascript_snapshot_post_install(repo: Path) -> list[str]:
    """``post_install`` lines to make all snapshot trees writable in Docker."""
    if not repo_has_javascript_snapshots(repo):
        return []
    return [JAVASCRIPT_SNAPSHOT_CHMOD_CMD]


def mocha_snapshot_post_install(repo: Path) -> list[str]:
    """Backward-compatible alias for :func:`javascript_snapshot_post_install`."""
    return javascript_snapshot_post_install(repo)


def _jest_config_at_dir(directory: Path) -> Path | None:
    for name in _JEST_CONFIG_NAMES:
        p = directory / name
        if p.is_file():
            return p
    return None


def _jest_config_nearest_to_paths(repo: Path, test_paths: list[str]) -> str | None:
    """Pick the shallowest Jest config on the path from each test file up to repo root."""
    best_depth = 10_000
    best_rel: str | None = None
    root = repo.resolve()
    for raw in test_paths:
        rel = raw.replace("\\", "/").strip().lstrip("/")
        if not rel:
            continue
        cur = (repo / rel).parent
        depth_from_test = len(Path(rel).parts)
        for _ in range(24):
            try:
                cur.resolve().relative_to(root)
            except ValueError:
                break
            cfg = _jest_config_at_dir(cur)
            if cfg is not None:
                rel_cfg = str(cfg.relative_to(repo)).replace("\\", "/")
                score = depth_from_test + len(cfg.relative_to(repo).parts)
                if score < best_depth:
                    best_depth = score
                    best_rel = rel_cfg
                break
            if cur == repo or cur.parent == cur:
                break
            cur = cur.parent
    return best_rel


def discover_jest_config_path(
    repo: Path,
    test_paths: list[str] | None = None,
) -> str | None:
    """
    Relative path to the nearest Jest config.

    When *test_paths* are given, walks up from each file's directory first, then
    falls back to root, common monorepo subdirs, and a bounded ``rglob``.
    """
    if test_paths:
        nearest = _jest_config_nearest_to_paths(repo, test_paths)
        if nearest:
            return nearest

    candidates: list[Path] = []
    for name in _JEST_CONFIG_NAMES:
        candidates.append(repo / name)
    for sub in ("examples", "packages", "apps", "lib", "src"):
        for name in _JEST_CONFIG_NAMES:
            candidates.append(repo / sub / name)
        subdir = repo / sub
        if subdir.is_dir():
            try:
                for child in subdir.iterdir():
                    if child.is_dir():
                        for name in _JEST_CONFIG_NAMES:
                            candidates.append(child / name)
            except OSError:
                pass

    for p in candidates:
        if p.is_file():
            return str(p.relative_to(repo)).replace("\\", "/")

    best_depth = 10_000
    best: Path | None = None
    try:
        for p in repo.rglob("jest.js"):
            if (
                p.is_file()
                and p.parent.name == ".config"
                and not _path_under_node_modules(p)
            ):
                try:
                    depth = len(p.relative_to(repo).parts)
                except ValueError:
                    continue
                if depth < best_depth:
                    best_depth = depth
                    best = p
        for pattern in ("jest.config.js", "jest.config.cjs", "jest.config.mjs", "jest.config.ts"):
            for p in repo.rglob(pattern):
                if not p.is_file() or _path_under_node_modules(p):
                    continue
                try:
                    depth = len(p.relative_to(repo).parts)
                except ValueError:
                    continue
                if depth < best_depth:
                    best_depth = depth
                    best = p
    except OSError:
        pass
    if best is not None:
        return str(best.relative_to(repo)).replace("\\", "/")
    return None


def repo_uses_trybuild_or_compile_fail(repo: Path) -> bool:
    """True when tests use trybuild / compile-fail UI tests (not plain ``cargo test`` junit)."""
    cargo = repo / "Cargo.toml"
    if cargo.is_file():
        text = _read_repo_text(cargo).lower()
        if "trybuild" in text or "compile_fail" in text or "compile-fail" in text:
            return True
    cf = repo / "compatibility-tests" / "compile-fail"
    return cf.is_dir() and (cf / "tests").is_dir()


def repo_uses_artisan_phpunit(repo: Path) -> bool:
    """Laravel-style: ``artisan`` + ``phpunit.xml`` at repo root."""
    return (repo / "artisan").is_file() and (repo / "phpunit.xml").is_file()


def _overrides_file() -> Path:
    return Path(__file__).resolve().parent.parent / "repo_overrides.yaml"


def _parse_repo_overrides_yaml(text: str) -> dict[str, dict[str, Any]]:
    """Parse the small subset used by ``repo_overrides.yaml`` (no PyYAML required)."""
    out: dict[str, dict[str, Any]] = {}
    current_repo: str | None = None
    current_key: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        m_repo = re.match(r'^["\']?([^"\']+/[^"\']+)["\']?\s*:\s*$', line.strip())
        if m_repo:
            current_repo = m_repo.group(1).strip()
            out.setdefault(current_repo, {})
            current_key = None
            continue
        m_key = re.match(r"^\s+(post_install|pre_install|eval_commands|test_cmd)\s*:\s*$", line)
        if m_key and current_repo:
            current_key = m_key.group(1)
            if current_key != "test_cmd":
                out[current_repo].setdefault(current_key, [])
            continue
        m_item = re.match(r"^\s+-\s+(.+)$", line)
        if m_item and current_repo and current_key and current_key != "test_cmd":
            val = m_item.group(1).strip().strip('"').strip("'")
            lst = out[current_repo].setdefault(current_key, [])
            if isinstance(lst, list):
                lst.append(val)
            continue
        m_scalar = re.match(r"^\s+test_cmd\s*:\s*(.+)$", line)
        if m_scalar and current_repo:
            out[current_repo]["test_cmd"] = m_scalar.group(1).strip().strip('"').strip("'")
    return out


def load_repo_overrides() -> dict[str, dict[str, Any]]:
    """Parse optional ``repo_overrides.yaml`` (empty dict if missing)."""
    path = _overrides_file()
    if not path.is_file():
        return {}
    try:
        return _parse_repo_overrides_yaml(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def apply_repo_overrides(
    cfg: dict[str, Any],
    repo_id: str,
    *,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Merge ``repo_overrides.yaml`` + artifact ``post_install`` hints into *cfg*."""
    out = dict(cfg)
    slug = str(repo_id or "").replace("__", "/")
    overrides = load_repo_overrides().get(slug) or {}
    for key in ("post_install", "pre_install", "eval_commands", "pip_packages"):
        extra = overrides.get(key)
        if isinstance(extra, list) and extra:
            merged = list(out.get(key) or [])
            for line in extra:
                if isinstance(line, str) and line.strip() and line not in merged:
                    merged.append(line.strip())
            out[key] = merged
    if overrides.get("test_cmd") and not out.get("test_cmd"):
        out["test_cmd"] = str(overrides["test_cmd"])
    if repo is not None:
        for line in repo_post_install_hints(repo):
            post = list(out.get("post_install") or [])
            if line not in post:
                post.append(line)
            out["post_install"] = post
    return out


def repo_post_install_hints(repo: Path) -> list[str]:
    """Artifact-based ``post_install`` lines (snapshots, etc.) — no slug lookup."""
    hints: list[str] = []
    hints.extend(javascript_snapshot_post_install(repo))
    return hints
