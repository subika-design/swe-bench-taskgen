"""JavaScript / Node install and test heuristics for Docker discover."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

DEFAULT_NODE_VERSION = "22.12.0"
NPM_ALIAS_MIN_NODE = "16.14.0"
YARN_INSTALL_CMD = "yarn install --frozen-lockfile || yarn install --network-timeout 600000"
YARN_ENSURE_PRE = "command -v yarn >/dev/null 2>&1 || npm install -g yarn@1.22.22"
# Last NVM-published release per major (avoids ``nvm install 12.7.2`` from ``@types/node``).
_NVM_LTS_BY_MAJOR: dict[int, str] = {
    12: "12.22.12",
    14: "14.21.3",
    16: "16.20.2",
}
JUNIT_OUT_PLACEHOLDER = "__JUNIT_OUT__"
MOCHA_JUNIT_REPORTER = "__MOCHA_JUNIT_REPORTER__"
MOCHA_JUNIT_REPORTER_MODULE = "mocha-junit-reporter"
MOCHA_JUNIT_TOOLS_DIR = "/w/mocha-junit-reporter"
MOCHA_JUNIT_INSTALL_CMD = (
    "npm install --no-save --no-fund --no-audit --legacy-peer-deps "
    f"{MOCHA_JUNIT_REPORTER_MODULE}@2"
)
JsTestRunner = Literal["jest", "vitest", "mocha"]

# Strip patterns that hide real test failures but still yield empty JUnit.
_EXIT_ZERO_SUFFIX_RE = re.compile(
    r"\s*(?:;\s*)?(?:\|\|\s*true\s*)?(?:;\s*)?exit\s+0\s*$",
    re.I,
)
# SWE-bench eval scripts append ``End Test Output`` after test_cmd; ``exit $status``
# inside test_cmd prevents that marker from being written.
_EXIT_STATUS_SUFFIX_RE = re.compile(r";\s*exit\s+\$status\s*$", re.I)
_NPS_STATUS_BEFORE_TEARDOWN_RE = re.compile(
    r";\s*status=\$\?;\s*(?=\(npx nps test\.teardown \|\| true\))",
    re.I,
)
_NPS_TEARDOWN = "(npx nps test.teardown || true)"


def _strip_exit_status_suffix(cmd: str) -> str:
    """Drop ``exit $status`` so harness eval scripts reach the End Test Output marker."""
    out = (cmd or "").strip()
    out = _NPS_STATUS_BEFORE_TEARDOWN_RE.sub("; ", out)
    out = _EXIT_STATUS_SUFFIX_RE.sub("", out)
    return out.strip()


def _sh_escape_double(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def load_package_json(repo: Path) -> dict[str, Any]:
    pkg = repo / "package.json"
    if not pkg.is_file():
        return {}
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _semver_tuple(version: str) -> tuple[int, int, int]:
    parts = version.strip().split(".")
    try:
        major = int(parts[0]) if parts else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return (0, 0, 0)
    return (major, minor, patch)


def _semver_tuple_to_str(t: tuple[int, int, int]) -> str:
    return f"{t[0]}.{t[1]}.{t[2]}"


def normalize_nvm_installable_version(version: str) -> str:
    """
    Map dependency-derived versions to an NVM-installable release.

    ``@types/node@12.7.2`` is not a Node runtime; ``>=12`` floors use the last 12.x LTS.
    """
    major, minor, patch = _semver_tuple(version)
    if major <= 0:
        return DEFAULT_NODE_VERSION
    lts = _NVM_LTS_BY_MAJOR.get(major)
    if lts and (
        (minor, patch) == (0, 0)
        or (major, minor, patch) == (12, 7, 2)  # common @types/node pin, not a Node release
    ):
        return lts
    return _semver_tuple_to_str((major, minor, patch))


def sanitize_js_docker_specs(specs: dict[str, Any]) -> dict[str, Any]:
    """Normalize harness ``docker_specs`` fields set by heuristics or LLM remediation."""
    out = dict(specs)
    nv = str(out.get("node_version") or "").strip()
    if nv:
        out["node_version"] = normalize_nvm_installable_version(nv)
    py = str(out.get("python_version") or "").strip()
    if not py or not re.match(r"^\d+\.\d+", py):
        out["python_version"] = "3.9"
    return out


def _semver_min_from_range(spec: str) -> tuple[int, int, int] | None:
    """Best-effort minimum (major, minor, patch) from an npm version range."""
    raw = (spec or "").strip()
    if not raw:
        return None
    for pat in (
        r">=\s*(\d+)\.(\d+)\.(\d+)",
        r"\^\s*(\d+)\.(\d+)\.(\d+)",
        r"(\d+)\.(\d+)\.(\d+)",
        r">=\s*(\d+)\.(\d+)",
        r"\^\s*(\d+)\.(\d+)",
        r"(\d+)\.(\d+)",
    ):
        m = re.search(pat, raw)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        if len(g) >= 3:
            return (g[0], g[1], g[2])
        if len(g) == 2:
            return (g[0], g[1], 0)
        if len(g) == 1:
            return (g[0], 0, 0)
    return None


def infer_node_version_from_deps(data: dict[str, Any]) -> str | None:
    """
    Raise the Node floor when ``devDependencies`` imply a newer runtime than
    ``engines.node`` (e.g. ``engines.node: '>=14.17'`` with ``jest@30``).
    """
    deps: dict[str, str] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        for name, spec in block.items():
            if isinstance(name, str) and isinstance(spec, str):
                deps[name] = spec

    mins: list[tuple[int, int, int]] = []

    for pkg in ("jest", "@jest/core"):
        jt = _semver_min_from_range(deps.get(pkg, ""))
        if jt is None:
            continue
        if jt[0] >= 30:
            mins.append((18, 14, 0))
        elif jt[0] >= 29:
            mins.append((18, 0, 0))

    vt = _semver_min_from_range(deps.get("vitest", ""))
    if vt and vt[0] >= 2:
        mins.append((18, 0, 0))

    jpt = _semver_min_from_range(deps.get("jest-puppeteer", ""))
    if jpt and jpt[0] >= 11:
        mins.append((18, 0, 0))

  # @types/node tracks API typings, not the NVM patch to install (12.7.2 != node v12.7.2).
    tnt = _semver_min_from_range(deps.get("@types/node", ""))
    if tnt and tnt[0] >= 18:
        mins.append((tnt[0], 0, 0))

    if not mins:
        return None
    return normalize_nvm_installable_version(_semver_tuple_to_str(max(mins)))


def resolve_node_version_for_repo(repo: Path) -> str:
    """``max(engines.node floor, devDependency-implied minimum)`` or harness default."""
    candidates: list[tuple[int, int, int]] = []
    from_engines = parse_engines_node(repo / "package.json")
    if from_engines:
        candidates.append(_semver_tuple(from_engines))
    data = load_package_json(repo)
    if data:
        from_deps = infer_node_version_from_deps(data)
        if from_deps:
            candidates.append(_semver_tuple(from_deps))
    if package_json_uses_npm_alias_protocol(repo):
        candidates.append(_semver_tuple(NPM_ALIAS_MIN_NODE))
    if not candidates:
        return DEFAULT_NODE_VERSION
    return normalize_nvm_installable_version(_semver_tuple_to_str(max(candidates)))


def package_json_uses_npm_alias_protocol(repo: Path) -> bool:
    """True when ``package.json`` uses ``npm:`` alias specs (requires npm 7+ / Node 16+)."""
    pkg = repo / "package.json"
    if not pkg.is_file():
        return False
    try:
        text = pkg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if "npm:" in text:
        return True
    data = load_package_json(repo)
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        block = data.get(key)
        if not isinstance(block, dict):
            continue
        for spec in block.values():
            if isinstance(spec, str) and spec.strip().startswith("npm:"):
                return True
    return False


def repo_uses_yarn_lock(repo: Path) -> bool:
    return (repo / "yarn.lock").is_file()


def parse_engines_node(package_json: Path) -> str | None:
    """Pick an NVM-installable Node version from ``package.json`` ``engines.node``."""
    if not package_json.is_file():
        return None
    try:
        data = json.loads(package_json.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None
    engines = data.get("engines") or {}
    raw = str(engines.get("node") or "").strip()
    if not raw:
        return None
    # Prefer explicit majors: >=22.12.0, ^20.19.0, >=12, etc.
    for pat in (
        r">=\s*(\d+)\.(\d+)\.(\d+)",
        r"\^\s*(\d+)\.(\d+)\.(\d+)",
        r"(\d+)\.(\d+)\.(\d+)",
        r">=\s*(\d+)\.(\d+)",
        r"\^\s*(\d+)\.(\d+)",
        r"(\d+)\.(\d+)",
        r">=\s*(\d+)\s*$",
        r">=\s*(\d+)(?:\s|,)",
    ):
        m = re.search(pat, raw)
        if m:
            groups = [g for g in m.groups() if g is not None]
            if len(groups) >= 3:
                return f"{groups[0]}.{groups[1]}.{groups[2]}"
            if len(groups) == 2:
                major, minor = int(groups[0]), int(groups[1])
                return f"{major}.{minor}.0"
            if len(groups) == 1:
                return normalize_nvm_installable_version(f"{groups[0]}.0.0")
    return None


def uses_nps_test_script(repo: Path) -> bool:
    """True when ``package.json`` ``scripts.test`` delegates to ``nps`` (e.g. isomorphic-git)."""
    data = load_package_json(repo)
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    ts = str(scripts.get("test") or "").strip().lower()
    return ts in ("nps", "nps test") or ts.startswith("nps ")


def isomorphic_git_repo(repo: Path) -> bool:
    """Deprecated: use ``should_apply_nps_jest_target_filter`` (artifact-based)."""
    from .repo_detect import should_apply_nps_jest_target_filter

    return should_apply_nps_jest_target_filter(repo)


def isomorphic_git_needs_http_build(repo: Path) -> bool:
    """Deprecated: use ``repo_needs_jest_http_rollup_build`` from ``repo_detect``."""
    from .repo_detect import repo_needs_jest_http_rollup_build

    return repo_needs_jest_http_rollup_build(repo)


def isomorphic_git_http_build_shell_prefix(*, repo_dir: str = "/testbed") -> str:
    """Deprecated: use ``jest_http_node_build_shell_prefix`` from ``repo_detect``."""
    from .repo_detect import jest_http_node_build_shell_prefix

    return jest_http_node_build_shell_prefix(repo_dir=repo_dir)


def _filter_isomorphic_git_targets(repo: Path, paths: list[str]) -> list[str]:
    """Drop NPS/Jest targets that break scoped discover (submodule, huge-repo, non-Jest)."""
    from .repo_detect import filter_nps_jest_test_targets

    return filter_nps_jest_test_targets(repo, paths)


def _nps_scripts_text(repo: Path) -> str:
    parts: list[str] = []
    data = load_package_json(repo)
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    if isinstance(scripts, dict):
        parts.extend(str(v) for v in scripts.values() if isinstance(v, str))
    for name in ("package-scripts.cjs", "package-scripts.js", "package-scripts.mjs"):
        p = repo / name
        if not p.is_file():
            continue
        try:
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(parts)


def nps_resolve_jest_subset_script(repo: Path) -> str | None:
    """
    NPS script for node/Jest discover runs (``test.node`` or legacy ``test.jest``).

    Returns ``None`` when package-scripts does not define either (``npx nps test.node`` fails).
    """
    text = _nps_scripts_text(repo)
    for name in ("test.node", "test.jest"):
        if any(tok in text for tok in (f"'{name}'", f'"{name}"', f"{name},")):
            return name
    m = re.search(r"\btest:\s*\{", text)
    if m:
        block = text[m.start() : m.start() + 3000]
        if re.search(r"\bnode\s*:", block):
            return "test.node"
        if re.search(r"\bjest\s*:", block):
            return "test.jest"
    return None


def nps_test_node_uses_unsupported_node_options(repo: Path) -> bool:
    """
    True when NPS/Jest scripts include Node options unsupported by current runtime.

    Example: ``--max-old-space-size-percentage`` in older Node builds.
    """
    text = _nps_scripts_text(repo)
    return "--max-old-space-size-percentage" in text


def _nps_use_direct_jest_for_discover(repo: Path, paths: list[str]) -> bool:
    """
    True when discover should run scoped ``npx jest`` instead of ``npx nps test.node``.

    NPS subset scripts do not accept per-file Jest args; older commits use ``test.jest`` only.
    """
    if nps_test_node_uses_unsupported_node_options(repo):
        return True
    script = nps_resolve_jest_subset_script(repo)
    if script is None:
        return bool(paths)
    if script != "test.node":
        return True
    return bool(paths)


def jest_use_experimental_vm_modules(repo: Path) -> bool:
    """
    Only newer NPS ``test.node`` scripts need ``--experimental-vm-modules``.

    Legacy isomorphic-git tests mix ``import`` + ``require()``; forcing ESM breaks them.
    """
    return nps_test_node_uses_unsupported_node_options(repo)


def _nps_safe_jest_cmd(
    *,
    jest_cfg: str = "",
    paths: list[str] | None = None,
    experimental_vm_modules: bool = False,
    test_timeout_ms: int | None = None,
) -> str:
    scoped = " ".join(f'"{p}"' for p in (paths or [])[:40])
    node_opts = (
        'NODE_OPTIONS="--experimental-vm-modules" ' if experimental_vm_modules else ""
    )
    timeout_flag = f" --testTimeout={test_timeout_ms}" if test_timeout_ms else ""
    jest = (
        f"{node_opts}"
        f"npx jest --ci --forceExit --coverage{jest_cfg}{timeout_flag} "
        f"--reporters=default --reporters=jest-junit --outputFile={JUNIT_OUT_PLACEHOLDER}"
    )
    if scoped:
        jest = f"{jest} {scoped}"
    return (
        "(npx nps test.setup || true) && "
        f"(({jest}) || ({jest}) || ({jest}))"
    )


def npm_run_test_cmd(
    *,
    repo_dir: str = "/testbed",
    nps_subset: bool = False,
    nps_script: str = "test.node",
) -> str:
    """
    Run the repo's test script, or a narrower NPS subset for discover.

    ``nps_subset=True`` avoids heavy chains like lint/build/browser suites and focuses
    on setup + node Jest + teardown.
    """
    if nps_subset:
        return (
            f"cd {repo_dir} && "
            "(npx nps proxy.stop || true) && "
            "(npx nps gitserver.stop || true) && "
            "(npx nps test.setup || true) && "
            f"(npx nps {nps_script}); "
            f"{_NPS_TEARDOWN}"
        )
    return f"cd {repo_dir} && npm run test"


def repo_uses_electron(repo: Path) -> bool:
    data = load_package_json(repo)
    if not data:
        return False
    deps: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        block = data.get(key)
        if isinstance(block, dict):
            deps.update(block)
    names = " ".join(deps.keys()).lower()
    return "electron" in names or "electron-builder" in data.get("scripts", {}).get("postinstall", "").lower()


def _package_scripts(repo: Path) -> dict[str, str]:
    data = load_package_json(repo)
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    return {str(k): str(v) for k, v in scripts.items() if isinstance(v, str)}


def _package_deps(repo: Path) -> dict[str, Any]:
    data = load_package_json(repo)
    deps: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        block = data.get(key)
        if isinstance(block, dict):
            deps.update(block)
    return deps


def _path_likely_mocha(path: str) -> bool:
    """Node-side Mocha targets (not browser snapshot artifacts)."""
    from .languages import is_javascript_snapshot_artifact

    if is_javascript_snapshot_artifact(path):
        return False
    p = path.replace("\\", "/").lower()
    return any(
        marker in p
        for marker in ("__tests__/", "__node_tests__/", "__integration__/", "/tests/", "/test/")
    ) and (".test." in p or ".spec." in p)


def _npm_test_invokes_jest(repo: Path) -> bool:
    scripts = _package_scripts(repo)
    test_script = scripts.get("test", "").lower()
    return "jest" in test_script and "vitest" not in test_script.split("&&")[0].split(";")[0]


def _repo_has_jest_config(repo: Path, test_paths: list[str] | None = None) -> str:
    from .repo_detect import discover_jest_config_path

    return discover_jest_config_path(repo, test_paths) or ""


def ci_test_cmd_uses_jest(test_cmd: str) -> bool:
    """True when ``test_cmd`` runs Jest (not merely a repo that lists jest in devDeps)."""
    low = (test_cmd or "").lower()
    if "jest" not in low:
        return False
    return bool(
        re.search(r"\bnpx\s+jest\b", low)
        or re.search(r"\bjest\s+--", low)
        or ("npm test" in low and "jest" in low)
    )


def ci_test_cmd_uses_mocha_or_makefile(test_cmd: str) -> bool:
    """True for ``node Makefile mocha``, ``npx mocha``, etc."""
    low = (test_cmd or "").lower()
    if re.search(r"\bnode\s+Makefile(?:\.js)?\b", low) and "mocha" in low:
        return True
    if re.search(r"\bnpx\s+mocha\b|\b_mocha\b", low) and "jest" not in low.split("&&")[0]:
        return True
    return False


def log_indicates_jest_haste_map_failure(log_tail: str) -> bool:
    """Jest died in haste-map before tests (common in repos with broken fixture package.json)."""
    low = (log_tail or "").lower()
    return (
        "jest-haste-map" in low
        or "haste module naming collision" in low
        or ("cannot parse" in low and "package.json as json" in low)
    )


def makefile_ci_test_cmd(repo: Path, *, repo_dir: str = "/testbed") -> str:
    """CI-style ``node Makefile.js mocha`` when the repo uses shelljs make targets."""
    from .repo_detect import repo_makefile_path

    mf = repo_makefile_path(repo)
    if mf is None:
        return ""
    return f"cd {repo_dir} && node {mf.name} mocha"


def makefile_mocha_test_cmd_from_targets(
    test_paths: list[str],
    *,
    repo_dir: str = "/testbed",
    repo: Path | None = None,
) -> str:
    """Scoped Mocha (+ optional c8) aligned with Makefile.js conventions (eslint class)."""
    from .repo_detect import (
        makefile_text,
        makefile_uses_c8_with_mocha,
        repo_makefile_path,
    )

    paths = _filter_js_targets(test_paths)
    if not paths:
        return makefile_ci_test_cmd(repo, repo_dir=repo_dir) if repo else ""

    timeout = "10000"
    if repo is not None:
        text = makefile_text(repo)
        m = re.search(
            r"MOCHA_TIMEOUT\s*=\s*parseInt\([^,]+,\s*10\)\s*\|\|\s*(\d+)",
            text,
        )
        if m:
            timeout = m.group(1)

    require_hook = resolve_mocha_require_hook(repo) if repo is not None else ""
    junit = (
        f"--reporter {MOCHA_JUNIT_REPORTER} "
        f"--reporter-options mochaFile={JUNIT_OUT_PLACEHOLDER}"
    )
    quoted = " ".join(f'"{p}"' for p in paths[:40])
    inner = (
        f"npx mocha{require_hook} --forbid-only -t {timeout} {junit} {quoted}"
    ).strip()
    cmd = f"cd {repo_dir} && "
    if repo is not None and makefile_uses_c8_with_mocha(repo):
        cmd += f"npx c8 -- {inner}"
    else:
        cmd += inner
    if repo is not None and not repo_makefile_path(repo):
        return mocha_test_cmd_from_targets(paths, repo_dir=repo_dir, repo=repo)
    return cmd


def should_scope_with_jest(
    cfg: dict[str, Any],
    repo: Path,
    test_paths: list[str],
    *,
    runner: JsTestRunner,
) -> bool:
    """Only replace CI ``test_cmd`` with scoped ``npx jest`` when Jest is the real runner."""
    if runner != "jest":
        return False
    tc = str(cfg.get("test_cmd") or "").strip()
    if tc and ci_test_cmd_uses_mocha_or_makefile(tc) and not ci_test_cmd_uses_jest(tc):
        return False
    from .repo_detect import repo_has_jest_haste_fixture_risk

    if repo_has_jest_haste_fixture_risk(repo) and not _repo_has_jest_config(
        repo, test_paths
    ):
        return False
    if tc and not ci_test_cmd_uses_jest(tc) and not _repo_has_jest_config(repo, test_paths):
        return False
    return True


def detect_js_test_runner(repo: Path, test_paths: list[str] | None = None) -> JsTestRunner:
    """
    Detect whether the repo's primary test runner is Vitest, Jest, or Mocha.

    Vitest repos (e.g. axios) expose ``vitest`` in devDependencies and route
    ``npm test`` through ``vitest run``; Jest repos use ``jest`` / jest-junit.
    Mocha repos (e.g. style-dictionary, eslint Makefile.js) use Mocha or ``test:node``.
    """
    from .repo_detect import (
        makefile_has_mocha_target,
        repo_has_jest_haste_fixture_risk,
        repo_uses_makefile_test,
    )

    if repo_uses_makefile_test(repo) and makefile_has_mocha_target(repo):
        return "mocha"

    data = load_package_json(repo)
    if not data:
        return "jest"
    scripts = _package_scripts(repo)
    deps = _package_deps(repo)
    has_vitest = "vitest" in deps
    has_jest = "jest" in deps or "@jest/core" in deps
    has_mocha = "mocha" in deps

    test_script = str(scripts.get("test") or "").lower()
    script_blob = " ".join(str(v).lower() for v in scripts.values())
    vitest_in_scripts = "vitest" in script_blob
    vitest_in_test = "vitest" in test_script
    jest_in_test = "jest" in test_script and "vitest" not in test_script
    mocha_in_scripts = "mocha" in script_blob

    paths = [p for p in (test_paths or []) if isinstance(p, str) and p.strip()]
    if paths and has_mocha and mocha_in_scripts and not has_jest:
        if all(_path_likely_mocha(p) for p in paths):
            return "mocha"
    if has_mocha and mocha_in_scripts and not has_jest:
        test_node = str(scripts.get("test:node") or "").lower()
        if "mocha" in test_node and (not paths or any(_path_likely_mocha(p) for p in paths)):
            return "mocha"

    if vitest_in_test:
        return "vitest"
    if jest_in_test:
        return "jest"
    if has_vitest and vitest_in_scripts and not has_jest:
        return "vitest"
    if has_vitest and vitest_in_scripts and has_jest:
        return "vitest" if vitest_in_test or not jest_in_test else "jest"
    if has_vitest and not has_jest:
        return "vitest"
    if has_mocha and not has_jest and repo_has_jest_haste_fixture_risk(repo):
        return "mocha"
    if has_mocha and not has_jest and re.search(
        r"\bnode\s+Makefile(?:\.js)?\b", test_script, re.I
    ):
        return "mocha"
    return "jest"


def runner_from_install_config(cfg: dict[str, Any], repo: Path | None = None) -> JsTestRunner:
    explicit = str(cfg.get("js_test_runner") or "").strip().lower()
    if explicit in ("vitest", "jest", "mocha"):
        return explicit  # type: ignore[return-value]
    tc = str(cfg.get("test_cmd") or "").lower()
    if "vitest" in tc and "jest" not in tc.split("&&")[0]:
        return "vitest"
    if "mocha" in tc and "jest" not in tc.split("&&")[0]:
        return "mocha"
    if repo is not None:
        return detect_js_test_runner(repo)
    return "jest"


def ensure_js_docker_specs(
    cfg: dict[str, Any],
    *,
    repo: Path | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """Set ``docker_specs.node_version`` for harness env image builds."""
    lang = str(language or cfg.get("language") or "").lower()
    if lang not in ("", "javascript", "js", "node", "typescript", "ts"):
        return cfg
    out = dict(cfg)
    specs = dict(out.get("docker_specs") or {}) if isinstance(out.get("docker_specs"), dict) else {}
    if not specs.get("node_version"):
        nv: str | None = None
        if repo is not None:
            nv = resolve_node_version_for_repo(repo)
        specs["node_version"] = nv or DEFAULT_NODE_VERSION
    out["docker_specs"] = sanitize_js_docker_specs(specs)
    return out


def _normalize_output_file_placeholder(cmd: str) -> str:
    cmd = re.sub(r"--outputFile=\S+", f"--outputFile={JUNIT_OUT_PLACEHOLDER}", cmd)
    cmd = re.sub(r"/w/junit-(?:base|patch)\.xml", JUNIT_OUT_PLACEHOLDER, cmd)
    return cmd


def _ensure_jest_force_exit_and_timeout(cmd: str) -> str:
    """Avoid Jest hanging in SWE-bench eval (open handles, slow integration tests)."""
    out = (cmd or "").strip()
    if not out or "jest" not in out.lower():
        return out
    if "forceexit" not in out.lower():
        out = re.sub(r"\bnpx jest\b", "npx jest --forceExit", out, count=1)
    if "testtimeout" not in out.lower().replace(" ", ""):
        out = re.sub(r"\bnpx jest\b", "npx jest --testTimeout=120000", out, count=1)
    return out


def normalize_jest_test_cmd(test_cmd: str) -> str:
    """Normalize Jest command for Docker (JUnit placeholder, no ``exit 0`` masking)."""
    cmd = _ensure_jest_force_exit_and_timeout(
        _strip_exit_status_suffix(_EXIT_ZERO_SUFFIX_RE.sub("", (test_cmd or "").strip()))
    )
    cmd = _normalize_output_file_placeholder(cmd)
    if not cmd or "jest" not in cmd.lower() or "jest-junit" in cmd:
        return cmd.strip()
    junit = f"--reporters=jest-junit --outputFile={JUNIT_OUT_PLACEHOLDER}"
    # Compound nps/shell wrappers; inject junit before teardown, not after it.
    if "npx nps test.setup" in cmd:
        if "--reporters=default" in cmd:
            return cmd.replace(
                "--reporters=default",
                f"--reporters=default {junit}",
            ).strip()
        return re.sub(
            r"(npx jest\b[^|;)\"]*(?:\"[^\"]*\")*)",
            rf"\1 --reporters=default {junit}",
            cmd,
            count=1,
        ).strip()
    return _strip_exit_status_suffix(f"{cmd} --reporters=default {junit}".strip())


def normalize_vitest_test_cmd(test_cmd: str) -> str:
    """Normalize Vitest command (built-in JUnit reporter, ``__JUNIT_OUT__`` placeholder)."""
    cmd = _strip_exit_status_suffix(_EXIT_ZERO_SUFFIX_RE.sub("", (test_cmd or "").strip()))
    cmd = _normalize_output_file_placeholder(cmd)
    low = cmd.lower()
    if cmd and "vitest" in low:
        if "reporter=junit" not in low and "--reporter junit" not in low:
            cmd = f"{cmd} --reporter=junit"
        if "outputfile" not in low.replace(" ", ""):
            cmd = f"{cmd} --outputFile={JUNIT_OUT_PLACEHOLDER}"
    return cmd.strip()


def normalize_mocha_test_cmd(test_cmd: str) -> str:
    """Normalize Mocha command (mocha-junit-reporter, ``__JUNIT_OUT__`` placeholder)."""
    cmd = _strip_exit_status_suffix(_EXIT_ZERO_SUFFIX_RE.sub("", (test_cmd or "").strip()))
    cmd = _normalize_output_file_placeholder(cmd)
    low = cmd.lower()
    if not cmd or "mocha" not in low:
        return cmd.strip()
    if MOCHA_JUNIT_REPORTER not in cmd and "mocha-junit-reporter" not in low:
        cmd = f"{cmd} --reporter {MOCHA_JUNIT_REPORTER}"
    elif "mocha-junit-reporter" in low and MOCHA_JUNIT_REPORTER not in cmd:
        for old in (
            "--reporter node_modules/mocha-junit-reporter",
            "--reporter mocha-junit-reporter",
        ):
            cmd = cmd.replace(old, f"--reporter {MOCHA_JUNIT_REPORTER}")
    if "mochafile" not in low.replace(" ", ""):
        cmd = f"{cmd} --reporter-options mochaFile={JUNIT_OUT_PLACEHOLDER}"
    return cmd.strip()


def normalize_js_test_cmd(test_cmd: str, *, runner: JsTestRunner | None = None) -> str:
    r = runner
    if r is None:
        low = (test_cmd or "").lower()
        if "vitest" in low and "jest" not in low.split("npx")[0]:
            r = "vitest"
        elif "mocha" in low and "jest" not in low.split("npx")[0]:
            r = "mocha"
        else:
            r = "jest"
    if r == "vitest":
        return normalize_vitest_test_cmd(test_cmd)
    if r == "mocha":
        return normalize_mocha_test_cmd(test_cmd)
    return normalize_jest_test_cmd(test_cmd)


def _with_jest_http_rollup_build(
    cmd: str,
    repo: Path | None,
    *,
    repo_dir: str = "/testbed",
) -> str:
    from .repo_detect import jest_http_node_build_shell_prefix, repo_needs_jest_http_rollup_build

    if repo is not None and repo_needs_jest_http_rollup_build(repo):
        return f"{jest_http_node_build_shell_prefix(repo_dir=repo_dir)}{cmd}"
    return cmd


def _filter_js_targets(test_paths: list[str]) -> list[str]:
    from .languages import filter_javascript_test_targets

    return filter_javascript_test_targets(
        [p.strip() for p in test_paths if isinstance(p, str) and p.strip()]
    )


def resolve_mocha_require_hook(repo: Path) -> str:
    """Return ``-r <hook>`` flag when repo defines Mocha setup (e.g. style-dictionary)."""
    for name in ("mocha-hooks.mjs", "mocha-hooks.js", "test/mocha-setup.js", "test/setup.js"):
        if (repo / name).is_file():
            return f" -r {name}"
    return ""


def mocha_test_cmd_from_targets(
    test_paths: list[str],
    *,
    repo_dir: str = "/testbed",
    repo: Path | None = None,
) -> str:
    """Scoped ``mocha`` on diff / test_patch paths with JUnit output."""
    if repo is not None:
        from .repo_detect import makefile_has_mocha_target, repo_uses_makefile_test

        if repo_uses_makefile_test(repo) and makefile_has_mocha_target(repo):
            return makefile_mocha_test_cmd_from_targets(
                test_paths, repo_dir=repo_dir, repo=repo
            )
    paths = _filter_js_targets(test_paths)
    require_hook = resolve_mocha_require_hook(repo) if repo is not None else ""
    junit = (
        f"--reporter {MOCHA_JUNIT_REPORTER} "
        f"--reporter-options mochaFile={JUNIT_OUT_PLACEHOLDER}"
    )
    base = f"cd {repo_dir} && npx mocha{require_hook} {junit}"
    if paths:
        quoted = " ".join(f'"{p}"' for p in paths[:40])
        return f"{base} {quoted}"
    if repo is not None and "test:node" in _package_scripts(repo):
        return (
            f"cd {repo_dir} && npx mocha{require_hook} {junit} "
            '"./__integration__/**/*.test.js" "./__tests__/**/*.test.js" '
            '"./__node_tests__/**/*.test.js"'
        )
    return base


def jest_test_cmd_from_targets(
    test_paths: list[str],
    *,
    repo_dir: str = "/testbed",
    repo: Path | None = None,
) -> str:
    """Jest or full ``npm run test`` (nps) for diff / test_patch paths."""
    paths = _filter_js_targets(test_paths)
    if repo is not None:
        from .repo_detect import repo_has_jest_haste_fixture_risk

        if repo_has_jest_haste_fixture_risk(repo) and not _repo_has_jest_config(
            repo, paths or test_paths
        ):
            return mocha_test_cmd_from_targets(paths, repo_dir=repo_dir, repo=repo)
    jest_cfg = ""
    if repo is not None:
        cfg_name = _repo_has_jest_config(repo, paths or test_paths)
        if cfg_name:
            jest_cfg = f" --config={cfg_name}"
    if repo is not None and uses_nps_test_script(repo):
        if _nps_use_direct_jest_for_discover(repo, paths):
            scoped_paths = _filter_isomorphic_git_targets(repo, paths)
            vm_modules = jest_use_experimental_vm_modules(repo)
            from .repo_detect import should_apply_nps_jest_target_filter

            jest_timeout = 120_000 if should_apply_nps_jest_target_filter(repo) else None
            return _with_jest_http_rollup_build(
                (
                    f"cd {repo_dir} && "
                    "(npx nps proxy.stop || true) && "
                    "(npx nps gitserver.stop || true) && "
                    f"{_nps_safe_jest_cmd(jest_cfg=jest_cfg, paths=scoped_paths, experimental_vm_modules=vm_modules, test_timeout_ms=jest_timeout)}; "
                    f"{_NPS_TEARDOWN}"
                ),
                repo,
                repo_dir=repo_dir,
            )
        nps_script = nps_resolve_jest_subset_script(repo) or "test.node"
        return _with_jest_http_rollup_build(
            npm_run_test_cmd(repo_dir=repo_dir, nps_subset=True, nps_script=nps_script),
            repo,
            repo_dir=repo_dir,
        )
    base = (
        f"cd {repo_dir} && npx jest --ci --forceExit --testTimeout=120000{jest_cfg} "
        f"--reporters=default --reporters=jest-junit --outputFile={JUNIT_OUT_PLACEHOLDER}"
    )
    if paths:
        quoted = " ".join(f'"{p}"' for p in paths[:40])
        if repo is not None and jest_cfg and _npm_test_invokes_jest(repo):
            return _with_jest_http_rollup_build(
                (
                    f"cd {repo_dir} && npm test -- --ci --forceExit --testTimeout=120000 "
                    f"--reporters=default --reporters=jest-junit "
                    f"--outputFile={JUNIT_OUT_PLACEHOLDER} {quoted}"
                ),
                repo,
                repo_dir=repo_dir,
            )
        return _with_jest_http_rollup_build(f"{base} {quoted}", repo, repo_dir=repo_dir)
    return _with_jest_http_rollup_build(
        (
            f"cd {repo_dir} && npm test -- --ci --reporters=default "
            f"--reporters=jest-junit --outputFile={JUNIT_OUT_PLACEHOLDER}"
        ),
        repo,
        repo_dir=repo_dir,
    )


def vitest_test_cmd_from_targets(
    test_paths: list[str],
    *,
    repo_dir: str = "/testbed",
    repo: Path | None = None,
) -> str:
    """Scoped ``vitest run`` on diff / test_patch paths with JUnit output."""
    paths = _filter_js_targets(test_paths)
    base = (
        f"cd {repo_dir} && npx vitest run --reporter=junit "
        f"--outputFile={JUNIT_OUT_PLACEHOLDER}"
    )
    if paths:
        quoted = " ".join(f'"{p}"' for p in paths[:40])
        return f"{base} {quoted}"
    if repo is not None:
        data = load_package_json(repo)
        test_script = str((data.get("scripts") or {}).get("test") or "")
        if "vitest" in test_script.lower():
            return (
                f"cd {repo_dir} && npm test -- --reporter=junit "
                f"--outputFile={JUNIT_OUT_PLACEHOLDER}"
            )
    return base


def js_test_cmd_from_targets(
    test_paths: list[str],
    *,
    repo_dir: str = "/testbed",
    repo: Path | None = None,
    runner: JsTestRunner = "jest",
) -> str:
    """Default ``test_cmd`` scoped to test paths for the given runner."""
    if runner == "vitest":
        return vitest_test_cmd_from_targets(test_paths, repo_dir=repo_dir, repo=repo)
    if runner == "mocha":
        return mocha_test_cmd_from_targets(test_paths, repo_dir=repo_dir, repo=repo)
    return jest_test_cmd_from_targets(test_paths, repo_dir=repo_dir, repo=repo)


def ensure_js_post_install(cfg: dict[str, Any], runner: JsTestRunner) -> dict[str, Any]:
    out = dict(cfg)
    post = list(out.get("post_install") or [])
    if runner == "vitest":
        post = [ln for ln in post if "jest-junit" not in str(ln) and "mocha-junit-reporter" not in str(ln)]
    post_text = "\n".join(post)
    if runner == "jest" and "jest-junit" not in post_text:
        post.append(
            "npm install --save-dev jest-junit 2>/dev/null || "
            "npm install --no-save jest-junit 2>/dev/null || true"
        )
    if runner == "mocha" and "mocha-junit-reporter" not in post_text:
        post.append(MOCHA_JUNIT_INSTALL_CMD)
    if "node_modules/.bin" not in post_text and 'export PATH="$(pwd)/node_modules/.bin' not in post_text:
        post.insert(0, 'export PATH="$(pwd)/node_modules/.bin:$PATH"')
    out["post_install"] = post
    return out


# Backward-compatible alias
def ensure_jest_junit_post_install(cfg: dict[str, Any]) -> dict[str, Any]:
    runner = runner_from_install_config(cfg)
    return ensure_js_post_install(cfg, runner)


def js_install_config_for_repo(
    repo: Path,
    base: dict[str, Any] | None = None,
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Heuristic ``install_config`` for JavaScript repos."""
    from .languages import get_language_spec

    runner = detect_js_test_runner(repo, test_paths)
    cfg = dict(base or get_language_spec("javascript").default_install_config)
    cfg["js_test_runner"] = runner
    cfg = ensure_js_docker_specs(cfg, repo=repo, language="javascript")
    cfg = ensure_js_post_install(cfg, runner)

    if repo_uses_electron(repo):
        cfg["install"] = "npm ci --ignore-scripts || npm install --ignore-scripts"
    elif repo_uses_yarn_lock(repo):
        cfg["install"] = YARN_INSTALL_CMD
    elif (repo / "package-lock.json").is_file():
        cfg["install"] = (
            "npm ci || npm install || "
            "(npm ci --ignore-scripts || npm install --ignore-scripts)"
        )
    else:
        cfg["install"] = "npm install || npm install --ignore-scripts"

    pre = list(cfg.get("pre_install") or [])
    if repo_uses_yarn_lock(repo) and not any("yarn" in ln for ln in pre):
        pre.append(YARN_ENSURE_PRE)
    if not any("ELECTRON_SKIP_BINARY_DOWNLOAD" in ln for ln in pre):
        pre.append("export ELECTRON_SKIP_BINARY_DOWNLOAD=1")
    cfg["pre_install"] = pre

    paths = list(test_paths or [])
    tc = str(cfg.get("test_cmd") or "").strip()
    if paths:
        cfg["test_cmd"] = js_test_cmd_from_targets(paths, repo=repo, runner=runner)
    elif not tc or tc.startswith("pytest"):
        cfg["test_cmd"] = js_test_cmd_from_targets([], repo=repo, runner=runner)
    else:
        cfg["test_cmd"] = normalize_js_test_cmd(tc, runner=runner)
    cfg["language"] = "javascript"
    return cfg


def merge_js_build_into_config(
    cfg: dict[str, Any],
    repo: Path,
    test_paths: list[str],
    *,
    repo_dir: str = "/testbed",
) -> dict[str, Any]:
    """Apply JS heuristics before Docker discover / image build."""
    runner = detect_js_test_runner(repo, test_paths)
    tc_in = str(cfg.get("test_cmd") or "").strip()
    if tc_in and ci_test_cmd_uses_mocha_or_makefile(tc_in) and not ci_test_cmd_uses_jest(tc_in):
        runner = "mocha"
    base = js_install_config_for_repo(repo, base=cfg, test_paths=test_paths)
    out = dict(cfg)
    out["js_test_runner"] = runner
    for key in (
        "install",
        "test_cmd",
        "post_install",
        "pre_install",
        "docker_specs",
    ):
        if key in base and base[key]:
            out[key] = base[key]
    scoped_paths = _filter_js_targets([p for p in test_paths if isinstance(p, str) and p.strip()])
    preserved_ci = (
        tc_in
        and ci_test_cmd_uses_mocha_or_makefile(tc_in)
        and not ci_test_cmd_uses_jest(tc_in)
        and not scoped_paths
    )
    if scoped_paths:
        if should_scope_with_jest(out, repo, scoped_paths, runner=runner):
            out["test_cmd"] = js_test_cmd_from_targets(
                scoped_paths, repo_dir=repo_dir, repo=repo, runner=runner
            )
        else:
            out["test_cmd"] = js_test_cmd_from_targets(
                scoped_paths, repo_dir=repo_dir, repo=repo, runner=runner
            )
    elif preserved_ci:
        out["test_cmd"] = normalize_js_test_cmd(tc_in, runner=runner)
    elif not str(out.get("test_cmd") or "").strip() or str(out.get("test_cmd", "")).startswith(
        "pytest"
    ):
        out["test_cmd"] = js_test_cmd_from_targets([], repo_dir=repo_dir, repo=repo, runner=runner)
    elif tc_in and runner == "mocha" and not ci_test_cmd_uses_jest(tc_in):
        out["test_cmd"] = normalize_js_test_cmd(tc_in, runner=runner)
    else:
        out["test_cmd"] = normalize_js_test_cmd(str(out.get("test_cmd") or tc_in), runner=runner)
    out["js_test_runner"] = runner
    return ensure_js_docker_specs(
        ensure_js_post_install(out, runner),
        repo=repo,
        language="javascript",
    )


def js_test_cmd_for_docker_entry(install_config: dict[str, Any]) -> str:
    """Value embedded in ``docker_entry.sh`` as ``JS_TEST_CMD`` (may be empty)."""
    tc = str(install_config.get("test_cmd") or "").strip()
    if not tc or tc.startswith("pytest"):
        return ""
    runner = runner_from_install_config(install_config)
    return normalize_js_test_cmd(tc, runner=runner)


def install_config_remediation_unchanged_js(before: dict[str, Any], after: dict[str, Any]) -> bool:
    keys = (
        "install",
        "test_cmd",
        "post_install",
        "pre_install",
        "docker_specs",
        "js_test_runner",
    )
    return all(before.get(k) == after.get(k) for k in keys)


def log_indicates_vitest_ran(log_tail: str) -> bool:
    low = (log_tail or "").lower()
    return "vitest" in low and ("run tests" in low or "test files" in low or "tests " in low)


def slice_failures_are_snapshot_permissions(
    failures: list[tuple[str, str]],
    errors: list[tuple[str, str]],
) -> bool:
    """True when test failures/errors are EACCES on snapshot dirs (env, not test_patch)."""
    for _nid, msg in list(failures or []) + list(errors or []):
        low = (msg or "").lower()
        if "eacces" in low and ("snapshot" in low or "__snapshots__" in low):
            return True
        if "permission denied" in low and "__snapshots__" in low:
            return True
    return False


def augment_javascript_snapshot_permissions(cfg: dict[str, Any]) -> dict[str, Any]:
    """Ensure generic ``find … __snapshots__`` chmod runs in Docker post_install."""
    from .repo_detect import JAVASCRIPT_SNAPSHOT_CHMOD_CMD

    out = dict(cfg)
    post = list(out.get("post_install") or [])
    if JAVASCRIPT_SNAPSHOT_CHMOD_CMD not in post:
        post.append(JAVASCRIPT_SNAPSHOT_CHMOD_CMD)
    out["post_install"] = post
    return out


def remediate_js_jest_haste_to_mocha(
    cfg: dict[str, Any],
    repo: Path,
    test_paths: list[str] | None = None,
    *,
    repo_dir: str = "/testbed",
) -> dict[str, Any]:
    """Switch from scoped Jest to Mocha when haste-map dies on fixture package.json trees."""
    from .repo_detect import makefile_has_mocha_target, repo_uses_makefile_test

    out = dict(cfg)
    out["js_test_runner"] = "mocha"
    paths = [p for p in (test_paths or []) if isinstance(p, str) and p.strip()]
    if paths:
        if repo_uses_makefile_test(repo) and makefile_has_mocha_target(repo):
            out["test_cmd"] = makefile_mocha_test_cmd_from_targets(
                paths, repo_dir=repo_dir, repo=repo
            )
        else:
            out["test_cmd"] = mocha_test_cmd_from_targets(
                paths, repo_dir=repo_dir, repo=repo
            )
    else:
        cmd = makefile_ci_test_cmd(repo, repo_dir=repo_dir)
        out["test_cmd"] = cmd or mocha_test_cmd_from_targets([], repo_dir=repo_dir, repo=repo)
    out["test_cmd"] = normalize_js_test_cmd(str(out["test_cmd"]), runner="mocha")
    return ensure_js_docker_specs(
        ensure_js_post_install(out, "mocha"),
        repo=repo,
        language="javascript",
    )


def remediate_js_install_from_log(
    cfg: dict[str, Any],
    log_tail: str,
    *,
    repo: Path | None = None,
    test_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Heuristic install fixes for npm/yarn failures seen in Docker discover logs."""
    out = dict(cfg)
    low = (log_tail or "").lower()
    if repo is not None and log_indicates_jest_haste_map_failure(log_tail):
        from .repo_detect import makefile_has_mocha_target

        if makefile_has_mocha_target(repo) or str(out.get("js_test_runner") or "") == "jest":
            return remediate_js_jest_haste_to_mocha(
                out, repo, test_paths=test_paths
            )
    if "eacces" in low and ("snapshot" in low or "__snapshots__" in low):
        out = augment_javascript_snapshot_permissions(out)
    specs = dict(out.get("docker_specs") or {}) if isinstance(out.get("docker_specs"), dict) else {}

    if "eunsupportedprotocol" in low or 'unsupported url type "npm:"' in low:
        cur = _semver_tuple(str(specs.get("node_version") or "0.0.0"))
        floor = _semver_tuple(NPM_ALIAS_MIN_NODE)
        if cur < floor:
            specs["node_version"] = NPM_ALIAS_MIN_NODE
        out["docker_specs"] = specs
        if repo is not None and repo_uses_yarn_lock(repo):
            out["install"] = YARN_INSTALL_CMD
        else:
            out["install"] = "npm install --legacy-peer-deps || npm install"

    if "yarn: command not found" in low or (
        "command not found" in low and "yarn install" in str(out.get("install") or "").lower()
    ):
        pre = list(out.get("pre_install") or [])
        if YARN_ENSURE_PRE not in pre:
            pre.append(YARN_ENSURE_PRE)
        out["pre_install"] = pre
        if repo is not None and repo_uses_yarn_lock(repo):
            out["install"] = YARN_INSTALL_CMD

    return ensure_js_docker_specs(out, repo=repo, language="javascript")
