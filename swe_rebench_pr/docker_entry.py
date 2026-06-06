"""Generate Docker entry scripts per language."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .js_build import _sh_escape_double, js_test_cmd_for_docker_entry
from .languages import get_language_spec


def _sh_quote(s: str) -> str:
    import re

    if re.match(r"^[a-zA-Z0-9@%_+=:,./-]+$", s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _test_env_block(install_config: dict[str, Any]) -> str:
    """Export ``install_config['test_env']`` before pytest (e.g. Django settings)."""
    env = install_config.get("test_env")
    if not isinstance(env, dict) or not env:
        return ""
    lines: list[str] = []
    for key, val in env.items():
        if not isinstance(key, str) or not str(val).strip():
            continue
        lines.append(f"export {key.strip()}={_sh_quote(str(val).strip())}")
    return "\n".join(lines) + "\n" if lines else ""


def _pytest_extra_args_block(install_config: dict[str, Any]) -> str:
    args = install_config.get("pytest_extra_args") or []
    if not isinstance(args, list):
        return ""
    parts: list[str] = []
    for arg in args:
        if isinstance(arg, str) and arg.strip():
            parts.append(f"PYT_EXTRA+=({_sh_quote(arg.strip())})")
    return "\n".join(parts) + "\n" if parts else ""


def _python_native_integration_block(
    install_config: dict[str, Any], *, repo_dir: str
) -> str:
    """``cd`` into integration pytest root and rebase target paths (CMake + pytest suites)."""
    if not install_config.get("native_integration_build"):
        return ""
    root = str(install_config.get("native_integration_pytest_root") or "").strip().strip("/")
    if not root:
        return ""
    qroot = _sh_quote(root)
    qrepo = _sh_quote(repo_dir)
    return f"""NATIVE_PYTEST_ROOT={qroot}
if [[ -n "${{NATIVE_PYTEST_ROOT}}" ]]; then
  cd {qrepo}/"${{NATIVE_PYTEST_ROOT}}"
  declare -a _TNEW=()
  for _p in "${{T[@]}}"; do
    if [[ "$_p" == "${{NATIVE_PYTEST_ROOT}}"/* ]]; then
      _TNEW+=("${{_p#${{NATIVE_PYTEST_ROOT}}/}}")
    elif [[ "$_p" == "./${{NATIVE_PYTEST_ROOT}}"/* ]]; then
      _TNEW+=("${{_p#./${{NATIVE_PYTEST_ROOT}}/}}")
    else
      _TNEW+=("$_p")
    fi
  done
  if [[ ${{#_TNEW[@]}} -gt 0 ]]; then
    T=("${{_TNEW[@]}}")
  fi
fi
"""


def _python_integration_test_cmd_block(install_config: dict[str, Any]) -> str:
    if not install_config.get("native_integration_build"):
        return 'PY_INTEGRATION_TEST_CMD=""\n'
    tc = str(install_config.get("test_cmd") or "").strip()
    if not tc:
        return 'PY_INTEGRATION_TEST_CMD=""\n'
    return f'PY_INTEGRATION_TEST_CMD="{_sh_escape_double(tc)}"\n'


def _native_integration_setup_block(
    install_config: dict[str, Any],
    *,
    repo_dir: str = "/w/repo",
) -> str:
    """Re-run setup from repo root (config.ini, cmake targets) after reset/clean."""
    if not install_config.get("native_integration_build"):
        return ""
    lines = install_config.get("native_integration_setup")
    if not isinstance(lines, list) or not lines:
        return ""
    from .integration_build import native_integration_repo_dir

    setup_repo = native_integration_repo_dir(install_config)
    if repo_dir.rstrip("/") not in ("", "/w/repo", "/"):
        setup_repo = repo_dir.rstrip("/")
    qrepo = _sh_quote(setup_repo)
    body: list[str] = ['echo "[docker] native integration setup" >&2']
    for raw in lines:
        if not isinstance(raw, str) or not raw.strip():
            continue
        body.append(f"(cd {qrepo} && {raw.strip()}) || true")
    return "\n".join(body) + "\n"


def _run_python_integration_tests_fn(setup_block: str = "") -> str:
    setup = setup_block.strip()
    if setup and not setup.endswith("\n"):
        setup += "\n"
    return f"""
_run_python_integration_tests() {{
  local junit_out="$1"
  local log="$2"
  local cmd=""
{setup}  if [[ -n "${{PY_INTEGRATION_TEST_CMD:-}}" ]]; then
    cmd="${{PY_INTEGRATION_TEST_CMD//__JUNIT_OUT__/$junit_out}}"
    cmd="${{cmd//__TARGETS__/}}"
    echo "[docker] integration pytest (${{#T[@]}} path(s))" >&2
    eval "$cmd" "${{T[@]}}" 2>&1 | tee "$log" || true
  else
    python3 -m pytest "${{PYT_EXTRA[@]}}" "${{T[@]}}" \\
      --junitxml="$junit_out" -o junit_family=xunit2 --tb=short -rA 2>&1 | tee "$log" || true
  fi
  python3 /w/empty_junit_if_missing.py "$junit_out" "$log" || true
}}
"""


def _python_pytest_report_flags(install_config: dict[str, Any]) -> str:
    """JUnit is primary; use ``-rA`` for native integration (SWE-bench log parsers need non-quiet)."""
    if install_config.get("native_integration_build"):
        return "-rA"
    return "-q"


def _python_test_cmd_block(install_config: dict[str, Any]) -> str:
    from .python_build import (
        python_docker_test_cmd_for_entry,
        python_pytest_cmd_without_collection_paths,
    )

    tc = python_docker_test_cmd_for_entry(install_config)
    if not tc:
        return 'PY_TEST_CMD=""\nPY_TEST_CMD_FLAGS=""\n'
    flags_src = python_pytest_cmd_without_collection_paths(
        str(install_config.get("test_cmd") or tc)
    )
    flags = python_docker_test_cmd_for_entry(
        {**install_config, "test_cmd": flags_src}
    )
    return (
        f'PY_TEST_CMD="{_sh_escape_double(tc)}"\n'
        f'PY_TEST_CMD_FLAGS="{_sh_escape_double(flags)}"\n'
    )


def _python_pytest_run_fn(report_flags: str) -> str:
    return rf"""
_run_pytest_phase() {{
  local junit_out="$1"
  local log="$2"
  if [[ -n "${{PY_TEST_CMD:-}}" ]]; then
    local cmd="${{PY_TEST_CMD//__JUNIT_OUT__/$junit_out}}"
    if [[ ${{#T[@]}} -gt 0 && -n "${{PY_TEST_CMD_FLAGS:-}}" ]]; then
      cmd="${{PY_TEST_CMD_FLAGS//__JUNIT_OUT__/$junit_out}}"
      echo "[docker] pytest flags+${{#T[@]}} target(s)" >&2
      eval "$cmd" "${{T[@]}}" 2>&1 | tee "$log" || true
    else
      echo "[docker] pytest test_cmd=$cmd" >&2
      if [[ ${{#T[@]}} -gt 0 ]]; then
        eval "$cmd" "${{T[@]}}" 2>&1 | tee "$log" || true
      else
        eval "$cmd" 2>&1 | tee "$log" || true
      fi
    fi
  else
    python3 -m pytest "${{PYT_EXTRA[@]}}" "${{T[@]}}" \\
      --junitxml="$junit_out" -o junit_family=xunit2 --tb=no {report_flags} 2>&1 | tee "$log" || true
  fi
  python3 /w/empty_junit_if_missing.py "$junit_out" "$log" || true
}}
"""


def _pytest_plugin_block(install_config: dict[str, Any]) -> str:
    plugins = install_config.get("pytest_plugins") or []
    if not isinstance(plugins, list):
        return ""
    parts: list[str] = []
    for p in plugins:
        if isinstance(p, str) and p.strip():
            parts.append(f'PYT_EXTRA+=(-p {_sh_quote(p.strip())})')
    return "\n".join(parts)


def _common_header(
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
    install_config: dict[str, Any] | None = None,
    language: str = "",
) -> str:
    harness_env_block = ""
    setup_repo_block = ""
    reset_block = ""
    install_block = ""

    if tests_only:
        if harness_conda:
            harness_env_block = """
source /opt/miniconda3/etc/profile.d/conda.sh
conda activate testbed
"""
        reset_block = f"""
git reset --hard "${{SWEBENCH_BASE_COMMIT:-HEAD}}"
{_js_git_clean_excludes()}
"""
        restore_block = ""
        if install_config and language:
            from .runtime_deps import runtime_deps_restore_shell

            restore_block = runtime_deps_restore_shell(
                language, install_config, repo_dir=repo_dir
            )
        return f"""#!/bin/bash
set -euo pipefail
REPO_DIR={_sh_quote(repo_dir)}
{harness_env_block}cd "$REPO_DIR"
git config --global --add safe.directory "$REPO_DIR" || true
{reset_block}
{restore_block}
mapfile -t T < /w/targets.txt || true
"""

    if not skip_install:
        install_block = """
bash /w/pre_install.sh
bash /w/project_install.sh
bash /w/post_install.sh
"""
    elif harness_env_only:
        setup_repo_block = """
cd /
bash /w/setup_repo.sh
"""
        if harness_conda:
            harness_env_block = """
source /opt/miniconda3/etc/profile.d/conda.sh
conda activate testbed
"""
            install_block = """
bash /w/pre_install.sh
bash /w/pip_packages.sh
bash /w/reqs_path.sh
bash /w/project_install.sh
bash /w/post_install.sh
"""
        else:
            install_block = """
bash /w/pre_install.sh
bash /w/project_install.sh
bash /w/post_install.sh
"""
    else:
        reset_block = """
git reset --hard HEAD
git clean -fdx
"""
        if harness_conda:
            harness_env_block = """
source /opt/miniconda3/etc/profile.d/conda.sh
conda activate testbed
"""
            install_block = """
bash /w/pre_install.sh
bash /w/pip_packages.sh
bash /w/reqs_path.sh
bash /w/project_install.sh
bash /w/post_install.sh
"""
        else:
            install_block = """
bash /w/pre_install.sh
bash /w/project_install.sh
bash /w/post_install.sh
"""

    return f"""#!/bin/bash
set -euo pipefail
{harness_env_block}{setup_repo_block}cd {_sh_quote(repo_dir)}
git config --global --add safe.directory {_sh_quote(repo_dir)} || true
{reset_block}{install_block}
mapfile -t T < /w/targets.txt || true
"""


def _apply_one_fn() -> str:
    return r"""
_apply_one() {
  local f="$1"
  if [[ ! -s "$f" ]] || ! grep -q '^diff --git' "$f" 2>/dev/null; then
    return 0
  fi
  if ! git apply --check --whitespace=nowarn "$f" 2>/dev/null; then
    echo "[docker] patch apply check failed: $f" >&2
    git apply --check --whitespace=nowarn "$f" 2>&1 | tail -5 >&2 || true
    return 1
  fi
  echo "[docker] applying patch: $f" >&2
  if ! git apply --whitespace=nowarn "$f"; then
    echo "[docker] patch apply failed: $f" >&2
    return 1
  fi
}
"""


def _apply_patches_block() -> str:
    return (
        _apply_one_fn()
        + r"""
_apply_one /w/impl.patch || exit 1
_apply_one /w/test.patch || exit 1
"""
    )


def _empty_junit_both() -> str:
    return r"""
if [[ ${#T[@]} -eq 0 ]]; then
  cat > /w/junit-base.xml <<'XEOF'
<?xml version="1.0" ?>
<testsuites><testsuite name="empty" tests="0"></testsuite></testsuites>
XEOF
  cp /w/junit-base.xml /w/junit-patch.xml
  cp /w/junit-base.xml /w/test-base.log 2>/dev/null || true
  cp /w/junit-base.xml /w/test-patch.log 2>/dev/null || true
  exit 0
fi
"""


def _pip_freeze_block() -> str:
    return r"""
if [[ "${RUN_PIP_FREEZE:-0}" == "1" ]]; then
  echo "[docker] pip freeze -> /w/pip-freeze.txt" >&2
  python3 -m pip freeze > /w/pip-freeze.txt 2>/dev/null || true
fi
"""


def _django_pytest_settings_block(install_config: dict[str, Any]) -> str:
    if not install_config.get("django_pytest"):
        return ""
    return r"""
if [[ -f /w/django_pytest_settings.py ]]; then
  mkdir -p tests
  cp /w/django_pytest_settings.py /w/repo/tests/swe_rebench_pytest_settings.py
fi
"""


def _eval_commands_block(install_config: dict[str, Any]) -> str:
    cmds = install_config.get("eval_commands") or []
    if install_config.get("native_integration_build"):
        from .integration_build import native_integration_eval_commands

        cmds = native_integration_eval_commands(install_config)
    if not isinstance(cmds, list):
        return ""
    lines: list[str] = []
    for ln in cmds:
        if isinstance(ln, str) and ln.strip():
            lines.append(ln.strip())
    return "\n".join(lines) + "\n" if lines else ""


def _install_prelude(*, skip_install: bool) -> str:
    if skip_install:
        return ""
    return "bash /w/pip_packages.sh\nbash /w/reqs_path.sh\n"


def _django_runtests_body(
    install_config: dict[str, Any],
    run_pip_freeze: bool,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    env_block = _eval_commands_block(install_config)
    return (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
        )
        + _install_prelude(skip_install=skip_install)
        + env_block
        + r"""mapfile -t T < /w/targets.txt || true
_run_dj() {
  local log="$1"
  shift
  ./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1 "$@" >"$log" 2>&1 || true
}
if [[ ${#T[@]} -eq 0 ]]; then
  : > /w/test-base.log
  : > /w/test-patch.log
else
  echo "[docker] runtests ${#T[@]} label(s) (before patch)" >&2
  _run_dj /w/test-base.log "${T[@]}"
fi
"""
        + _apply_patches_block()
        + r"""if [[ ${#T[@]} -eq 0 ]]; then
  :
else
  echo "[docker] runtests ${#T[@]} label(s) (after patch)" >&2
  _run_dj /w/test-patch.log "${T[@]}"
fi
"""
        + (_pip_freeze_block() if run_pip_freeze else "")
    )


def _python_git_clean_after_reset(install_config: dict[str, Any] | None = None) -> str:
    """Remove untracked files left by ``test_patch`` (new tests) after ``git reset --hard``."""
    excludes = [
        "-e subprojects ",
        "-e src/dateutil/zoneinfo/dateutil-zoneinfo.tar.gz ",
    ]
    if install_config and install_config.get("native_integration_build"):
        excludes.append("-e build ")
        root = str(install_config.get("native_integration_pytest_root") or "").strip().strip("/")
        if root:
            excludes.append(f"-e {root}/config.ini ")
            excludes.append(f"-e {root}/gen ")
    exclude_args = "".join(excludes)
    return (
        f"git clean -ffdx {exclude_args}"
        "2>/dev/null || git clean -ffdx "
        f"{exclude_args}"
        "2>/dev/null || git clean -ffdx"
    )


def _python_body(
    install_config: dict[str, Any],
    run_pip_freeze: bool,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    plugin_block = _pytest_plugin_block(install_config)
    extra_args_block = _pytest_extra_args_block(install_config)
    env_block = _test_env_block(install_config)
    django_settings_block = _django_pytest_settings_block(install_config)
    native_block = _python_native_integration_block(install_config, repo_dir=repo_dir)
    integration_cmd_block = _python_integration_test_cmd_block(install_config)
    use_integration_runner = bool(install_config.get("native_integration_build"))
    restore_repo = f"cd {_sh_quote(repo_dir)}\n" if native_block else ""
    report_flags = _python_pytest_report_flags(install_config)
    if use_integration_runner:
        setup_block = _native_integration_setup_block(install_config, repo_dir=repo_dir)
        run_tests_fn = _run_python_integration_tests_fn(setup_block)
        pytest_base = (
            native_block
            + '_run_python_integration_tests /w/junit-base.xml /w/test-base.log\n'
            + restore_repo
        )
        pytest_patch = native_block + (
            '_run_python_integration_tests /w/junit-patch.xml /w/test-patch.log\n'
            + restore_repo
        )
    else:
        run_tests_fn = _python_pytest_run_fn(report_flags)
        pytest_base = native_block + "_run_pytest_phase /w/junit-base.xml /w/test-base.log\n" + restore_repo
        pytest_patch = (
            native_block + "_run_pytest_phase /w/junit-patch.xml /w/test-patch.log\n" + restore_repo
        )
    py_test_cmd_block = _python_test_cmd_block(install_config)
    body = (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
        )
        + _install_prelude(skip_install=skip_install)
        + _empty_junit_both()
        + django_settings_block
        + env_block
        + _eval_commands_block(install_config)
        + integration_cmd_block
        + py_test_cmd_block
        + "PYT_EXTRA=()\n"
        + (plugin_block + "\n" if plugin_block else "")
        + extra_args_block
        + _apply_one_fn()
        + run_tests_fn
        + rf"""echo "[docker] pytest ${{#T[@]}} path(s) (base + test_patch only)" >&2
_apply_one /w/test.patch || exit 1
"""
        + pytest_base
        + """echo "[docker] reset to base_commit" >&2
git reset --hard "${SWEBENCH_BASE_COMMIT:-HEAD}"
"""
        + _python_git_clean_after_reset(install_config)
        + "\n"
        + _eval_commands_block(install_config)
        + rf"""echo "[docker] pytest ${{#T[@]}} path(s) (test_patch + impl.patch)" >&2
_apply_one /w/test.patch || exit 1
_apply_one /w/impl.patch || exit 1
"""
        + pytest_patch
        + (_pip_freeze_block() if run_pip_freeze else "")
    )
    return body


def _go_packages_from_targets(targets: list[str]) -> list[str]:
    from .go_build import go_packages_from_test_paths

    return go_packages_from_test_paths(targets)


def _go_body(
    targets: list[str],
    run_pip_freeze: bool,
    install_config: dict[str, Any] | None = None,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    from .go_build import resolve_go_test_invocation

    cfg = install_config if isinstance(install_config, dict) else {}
    go_test_cmd = resolve_go_test_invocation(cfg.get("test_cmd"), targets)
    return (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
        )
        + _empty_junit_both()
        + f'GO_TEST_CMD="{_sh_escape_double(go_test_cmd)}"\n'
        + _apply_one_fn()
        + r"""echo "[docker] go test (base + test_patch only) $GO_TEST_CMD" >&2
_apply_one /w/test.patch || exit 1
eval "$GO_TEST_CMD" > /w/test-base.log 2>&1 || true
echo "[docker] reset to base_commit" >&2
git reset --hard "${SWEBENCH_BASE_COMMIT:-HEAD}"
git clean -ffdx 2>/dev/null || true
echo "[docker] go test (test_patch + impl.patch) $GO_TEST_CMD" >&2
_apply_one /w/test.patch || exit 1
_apply_one /w/impl.patch || exit 1
eval "$GO_TEST_CMD" > /w/test-patch.log 2>&1 || true
"""
        + (_pip_freeze_block() if run_pip_freeze else "")
    )


def _rust_cargo_features_block(install_config: dict[str, Any]) -> str:
    feats = install_config.get("cargo_features") or []
    if not isinstance(feats, list):
        return "CARGO_FEAT_ARGS=()\n"
    kept = [str(f).strip() for f in feats if str(f).strip()]
    if not kept:
        return "CARGO_FEAT_ARGS=()\n"
    return f"CARGO_FEAT_ARGS=(--features {_sh_quote(','.join(kept))})\n"


def _rust_git_clean_after_reset() -> str:
    return "git clean -ffdx 2>/dev/null || true"


def _rust_body(
    install_config: dict[str, Any],
    run_pip_freeze: bool,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    feat_block = _rust_cargo_features_block(install_config)
    return (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
        )
        + _empty_junit_both()
        + feat_block
        + _apply_one_fn()
        + r"""_cargo_test_args() {
  local args=()
  local t
  local use_scoped=1
  for t in "${T[@]}"; do
    [[ -z "$t" ]] && continue
    if [[ "$t" =~ ^tests/[^/]+\.rs$ ]]; then
      args+=(--test "$(basename "$t" .rs)")
    else
      use_scoped=0
      break
    fi
  done
  if [[ $use_scoped -eq 1 && ${#args[@]} -gt 0 ]]; then
    cargo test --no-fail-fast "${CARGO_FEAT_ARGS[@]}" "${args[@]}"
  else
    cargo test --no-fail-fast "${CARGO_FEAT_ARGS[@]}"
  fi
}
echo "[docker] cargo test (base + test_patch only)" >&2
_apply_one /w/test.patch || exit 1
_cargo_test_args > /w/test-base.log 2>&1 || true
echo "[docker] reset to base_commit" >&2
git reset --hard "${SWEBENCH_BASE_COMMIT:-HEAD}"
"""
        + _rust_git_clean_after_reset()
        + "\n"
        + r"""echo "[docker] cargo test (test_patch + impl.patch)" >&2
_apply_one /w/test.patch || exit 1
_apply_one /w/impl.patch || exit 1
_cargo_test_args > /w/test-patch.log 2>&1 || true
"""
        + (_pip_freeze_block() if run_pip_freeze else "")
    )


def _maven_junit_merge_args(install_config: dict[str, Any]) -> str:
    roots = install_config.get("maven_junit_roots") or []
    if not isinstance(roots, list):
        return ""
    parts = [_sh_quote(str(r).strip()) for r in roots if isinstance(r, str) and str(r).strip()]
    return (" " + " ".join(parts)) if parts else ""


def _java_maven_body(
    install_config: dict[str, Any],
    run_pip_freeze: bool,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    test_cmd = str(install_config.get("test_cmd") or "mvn -q test -Dmaven.test.failure.ignore=true").strip()
    junit_extra = _maven_junit_merge_args(install_config)
    roots = install_config.get("maven_junit_roots") or []
    roots_log = (
        " ".join(str(r) for r in roots if isinstance(r, str) and str(r).strip())
        if isinstance(roots, list)
        else ""
    )
    return (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
        )
        + _empty_junit_both()
        + f'JAVA_TEST_CMD="{test_cmd}"\n'
        + f'REPO_DIR="{repo_dir}"\n'
        + f'echo "[docker] maven junit_roots={roots_log}" >&2\n'
        + _apply_one_fn()
        + r"""echo "[docker] mvn test (base + test_patch only)" >&2
_apply_one /w/test.patch || exit 1
eval "$JAVA_TEST_CMD" || true
python3 /w/merge_junit.py /w/junit-base.xml "$REPO_DIR" maven"""
        + junit_extra
        + r""" || true
echo "[docker] reset to base_commit" >&2
git reset --hard "${SWEBENCH_BASE_COMMIT:-HEAD}"
git clean -ffdx -e subprojects 2>/dev/null || git clean -ffdx
echo "[docker] mvn test (test_patch + impl.patch)" >&2
_apply_one /w/test.patch || exit 1
_apply_one /w/impl.patch || exit 1
eval "$JAVA_TEST_CMD" || true
python3 /w/merge_junit.py /w/junit-patch.xml "$REPO_DIR" maven"""
        + junit_extra
        + r""" || true
"""
        + (_pip_freeze_block() if run_pip_freeze else "")
    )


def _gradle_junit_merge_args(install_config: dict[str, Any]) -> str:
    roots = install_config.get("gradle_junit_roots") or []
    if not isinstance(roots, list):
        return ""
    parts = [_sh_quote(str(r).strip()) for r in roots if isinstance(r, str) and str(r).strip()]
    return (" " + " ".join(parts)) if parts else ""


def _java_gradle_body(
    install_config: dict[str, Any],
    run_pip_freeze: bool,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    test_cmd = str(
        install_config.get("test_cmd")
        or "./gradlew --no-daemon --configure-on-demand -I gradle/swebench-harness-logging.init.gradle test -Dorg.gradle.parallel=false --continue || true"
    ).strip()
    junit_extra = _gradle_junit_merge_args(install_config)
    roots = install_config.get("gradle_junit_roots") or []
    roots_log = (
        " ".join(str(r) for r in roots if isinstance(r, str) and str(r).strip())
        if isinstance(roots, list)
        else ""
    )
    return (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
        )
        + _empty_junit_both()
        + f'GRADLE_TEST_CMD="{test_cmd}"\n'
        + f'REPO_DIR="{repo_dir}"\n'
        + f'echo "[docker] gradle junit_roots={roots_log}" >&2\n'
        + _apply_one_fn()
        + r"""echo "[docker] gradle test_cmd=$GRADLE_TEST_CMD" >&2
echo "[docker] gradle test (base + test_patch only)" >&2
_apply_one /w/test.patch || exit 1
eval "$GRADLE_TEST_CMD" 2>&1 | tee /w/test-base.log || true
python3 /w/merge_junit.py /w/junit-base.xml "$REPO_DIR" gradle"""
        + junit_extra
        + r""" || true
echo "[docker] reset to base_commit" >&2
git reset --hard HEAD
git clean -ffdx -e subprojects 2>/dev/null || git clean -ffdx
echo "[docker] gradle test (test_patch + impl.patch)" >&2
_apply_one /w/test.patch || exit 1
_apply_one /w/impl.patch || exit 1
eval "$GRADLE_TEST_CMD" 2>&1 | tee /w/test-patch.log || true
python3 /w/merge_junit.py /w/junit-patch.xml "$REPO_DIR" gradle"""
        + junit_extra
        + r""" || true
"""
        + (_pip_freeze_block() if run_pip_freeze else "")
    )


def _java_body(
    install_config: dict[str, Any],
    run_pip_freeze: bool,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    if str(install_config.get("java_build_system") or "").strip().lower() == "gradle":
        return _java_gradle_body(
            install_config,
            run_pip_freeze,
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
        )
    return _java_maven_body(
        install_config,
        run_pip_freeze,
        repo_dir=repo_dir,
        skip_install=skip_install,
        harness_conda=harness_conda,
        harness_env_only=harness_env_only,
        tests_only=tests_only,
    )


def _js_git_clean_excludes() -> str:
    """``git clean`` between JS phases keeps ``node_modules`` and snapshot trees."""
    return r"""
_js_git_clean() {
  local excludes="-e node_modules -e subprojects"
  while IFS= read -r snap; do
  [[ -z "$snap" ]] && continue
  excludes="$excludes -e ${snap#./}"
  done < <(find . -type d -name __snapshots__ -not -path "*/node_modules/*" 2>/dev/null)
  git clean -ffdx $excludes 2>/dev/null || git clean -ffdx -e node_modules -e subprojects
}
_js_git_clean
"""


def _js_restore_deps_fn() -> str:
    return r"""
_js_restore_deps_if_needed() {
  if [[ -d "$REPO_DIR/node_modules" ]] && [[ -n "$(ls -A "$REPO_DIR/node_modules" 2>/dev/null)" ]]; then
    return 0
  fi
  echo "[docker] node_modules missing after reset; re-running npm install" >&2
  (cd "$REPO_DIR" && bash /w/project_install.sh && bash /w/post_install.sh) || true
  export PATH="$REPO_DIR/node_modules/.bin:${PATH}"
}

_js_ensure_mocha_junit_reporter() {
  local tools="/w/mocha-junit-reporter"
  local mod="$tools/node_modules/mocha-junit-reporter"
  if [[ -d "$REPO_DIR/node_modules/mocha-junit-reporter" ]]; then
    MOCHA_JUNIT_REPORTER="mocha-junit-reporter"
    export MOCHA_JUNIT_REPORTER
    return 0
  fi
  echo "[docker] installing mocha-junit-reporter in repo (legacy-peer-deps)" >&2
  (cd "$REPO_DIR" && npm install --no-save --no-fund --no-audit --legacy-peer-deps mocha-junit-reporter@2) \
    2>&1 | tail -5 >&2 || true
  if [[ -d "$REPO_DIR/node_modules/mocha-junit-reporter" ]]; then
    MOCHA_JUNIT_REPORTER="mocha-junit-reporter"
    export MOCHA_JUNIT_REPORTER
    return 0
  fi
  echo "[docker] repo install failed; trying isolated prefix + NODE_PATH" >&2
  npm install --prefix "$tools" --no-fund --no-audit --legacy-peer-deps mocha-junit-reporter@2 \
    2>&1 | tail -5 >&2 || true
  if [[ -f "$mod/index.js" ]] || [[ -d "$mod" ]]; then
    MOCHA_JUNIT_REPORTER="$mod"
    export MOCHA_JUNIT_REPORTER
    export NODE_PATH="$REPO_DIR/node_modules${NODE_PATH:+:$NODE_PATH}"
    return 0
  fi
  echo "[docker] mocha-junit-reporter unavailable" >&2
  return 1
}

_js_apply_mocha_junit_reporter() {
  local cmd="$1"
  _js_ensure_mocha_junit_reporter
  if [[ -z "${MOCHA_JUNIT_REPORTER:-}" ]]; then
    echo "[docker] mocha-junit-reporter unavailable" >&2
    return 1
  fi
  if [[ "$cmd" == *"__MOCHA_JUNIT_REPORTER__"* ]]; then
    cmd="${cmd//__MOCHA_JUNIT_REPORTER__/$MOCHA_JUNIT_REPORTER}"
  else
    cmd="${cmd//--reporter node_modules\/mocha-junit-reporter/--reporter $MOCHA_JUNIT_REPORTER}"
    cmd="${cmd//--reporter mocha-junit-reporter/--reporter $MOCHA_JUNIT_REPORTER}"
  fi
  printf '%s' "$cmd"
}
"""


def _js_run_tests_fn() -> str:
    return r"""
_js_ensure_jest_junit() {
  if [[ -d "$REPO_DIR/node_modules/jest-junit" ]]; then
    return 0
  fi
  echo "[docker] installing jest-junit for junit output" >&2
  (cd "$REPO_DIR" && npm install --no-save --no-fund --no-audit jest-junit 2>/dev/null) || true
}

_harvest_jest_junit_to() {
  local junit_out="$1"
  if [[ ! -d "$REPO_DIR/junit" ]]; then
    return 0
  fi
  if ! ls "$REPO_DIR"/junit/*.xml &>/dev/null; then
    echo "[docker] harvest jest junit: no *.xml under $REPO_DIR/junit" >&2
    return 1
  fi
  if python3 /w/harvest_jest_junit.py "$junit_out" "$REPO_DIR/junit"; then
    return 0
  fi
  echo "[docker] harvest jest junit: merge failed (see [harvest] lines above)" >&2
  return 1
}

_js_ensure_jest_http_node_build() {
  if [[ -f "$REPO_DIR/http/node/index.cjs" ]] || [[ -f "$REPO_DIR/http/node/index.js" ]]; then
    return 0
  fi
  local cfg
  for cfg in .config/jest.js jest.config.js jest.config.cjs jest.config.mjs; do
    if [[ -f "$REPO_DIR/$cfg" ]] && grep -qE 'http/node|http\\/node' "$REPO_DIR/$cfg" 2>/dev/null; then
      echo "[docker] jest http/node rollup build" >&2
      (cd "$REPO_DIR" && (npx nps build.rollup 2>/dev/null || npx rollup -c 2>/dev/null || true)) || true
      return 0
    fi
  done
}

_run_js_tests() {
  local junit_out="$1"
  local log="$2"
  export CI=true
  export PATH="$REPO_DIR/node_modules/.bin:${PATH}"
    if [[ -n "${JS_TEST_CMD:-}" ]]; then
    local cmd="${JS_TEST_CMD//__JUNIT_OUT__/$junit_out}"
    if [[ "$cmd" == *vitest* ]]; then
      if [[ "$cmd" != *reporter=junit* ]] && [[ "$cmd" != *"reporter junit"* ]]; then
        cmd="$cmd --reporter=junit"
      fi
      if [[ "$cmd" != *outputFile* ]]; then
        cmd="$cmd --outputFile=$junit_out"
      fi
    elif [[ "$cmd" == *mocha* ]]; then
      cmd="$(_js_apply_mocha_junit_reporter "$cmd")" || true
      if [[ "${MOCHA_JUNIT_REPORTER:-}" == /* ]]; then
        export NODE_PATH="$REPO_DIR/node_modules${NODE_PATH:+:$NODE_PATH}"
      fi
      if [[ "$cmd" != *mochaFile* ]]; then
        cmd="$cmd --reporter-options mochaFile=$junit_out"
      fi
    elif [[ "$cmd" != *jest-junit* ]] && [[ "$cmd" == *jest* ]] && [[ "$cmd" != *"npx nps test.setup"* ]]; then
      _js_ensure_jest_junit
      cmd="$cmd --reporters=default --reporters=jest-junit --outputFile=$junit_out"
    fi
    if [[ "$cmd" == *jest* ]] || [[ "$cmd" == *"npx nps test.setup"* ]]; then
      _js_ensure_jest_http_node_build
    fi
    if [[ "$cmd" == *jest* ]] || [[ "$cmd" == *"npm run test"* ]] || [[ "$cmd" == *"npm test"* ]]; then
      _js_ensure_jest_junit
    fi
    if [[ "$cmd" == *"npx nps test.setup"* ]]; then
      (cd "$REPO_DIR" && npx nps proxy.stop >/dev/null 2>&1) || true
      (cd "$REPO_DIR" && npx nps gitserver.stop >/dev/null 2>&1) || true
    fi
    export JEST_JUNIT_OUTPUT_DIR="/w"
    export JEST_JUNIT_OUTPUT_NAME="$(basename "$junit_out")"
    export JEST_JUNIT_ADD_FILE_ATTRIBUTE="true"
    export JEST_JUNIT_CLASSNAME="{filepath}"
    echo "[docker] js test_cmd=$cmd" >&2
    (cd "$REPO_DIR" && eval "$cmd") 2>&1 | tee "$log" || true
    if [[ "$cmd" == *jest* ]] || [[ "$cmd" == *"npm run test"* ]] || [[ "$cmd" == *"npm test"* ]]; then
      _harvest_jest_junit_to "$junit_out" || true
    fi
  elif [[ ${#T[@]} -gt 0 ]]; then
    if [[ "${JS_TEST_RUNNER:-jest}" == "vitest" ]]; then
      echo "[docker] npx vitest run targets (${#T[@]} path(s))" >&2
      (cd "$REPO_DIR" && npx vitest run --reporter=junit --outputFile="$junit_out" "${T[@]}") \
        2>&1 | tee "$log" || true
    elif [[ "${JS_TEST_RUNNER:-jest}" == "mocha" ]]; then
      _js_ensure_mocha_junit_reporter
      echo "[docker] npx mocha targets (${#T[@]} path(s))" >&2
      (cd "$REPO_DIR" && npx mocha --reporter "${MOCHA_JUNIT_REPORTER}" \
        --reporter-options mochaFile="$junit_out" "${T[@]}") 2>&1 | tee "$log" || true
    else
      _js_ensure_jest_junit
      echo "[docker] npx jest targets (${#T[@]} path(s))" >&2
      (cd "$REPO_DIR" && npx jest --ci --forceExit --reporters=default --reporters=jest-junit \
        --outputFile="$junit_out" "${T[@]}") 2>&1 | tee "$log" || true
      _harvest_jest_junit_to "$junit_out" || true
    fi
  elif [[ "${JS_TEST_RUNNER:-jest}" == "vitest" ]]; then
    echo "[docker] npx vitest run (fallback)" >&2
    (cd "$REPO_DIR" && npx vitest run --reporter=junit --outputFile="$junit_out") \
      2>&1 | tee "$log" || true
  elif [[ "${JS_TEST_RUNNER:-jest}" == "mocha" ]]; then
    _js_ensure_mocha_junit_reporter
    echo "[docker] npx mocha (fallback)" >&2
    (cd "$REPO_DIR" && npx mocha --reporter "${MOCHA_JUNIT_REPORTER}" \
      --reporter-options mochaFile="$junit_out") 2>&1 | tee "$log" || true
  else
    _js_ensure_jest_junit
    echo "[docker] npm test (fallback)" >&2
    (cd "$REPO_DIR" && npm test -- --ci --reporters=default --reporters=jest-junit \
      --outputFile="$junit_out") 2>&1 | tee "$log" || true
    _harvest_jest_junit_to "$junit_out" || true
  fi
  export REPO_DIR
  python3 /w/empty_junit_if_missing.py "$junit_out" "$log"
}
"""


def _js_body(
    install_config: dict[str, Any],
    run_pip_freeze: bool,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    from .js_build import runner_from_install_config

    js_cmd = js_test_cmd_for_docker_entry(install_config)
    js_runner = runner_from_install_config(install_config)
    js_cmd_line = f'JS_TEST_CMD="{_sh_escape_double(js_cmd)}"\n' if js_cmd else 'JS_TEST_CMD=""\n'
    return (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
        )
        + _empty_junit_both()
        + f'REPO_DIR="{repo_dir}"\n'
        + f'JS_TEST_RUNNER="{js_runner}"\n'
        + js_cmd_line
        + _apply_one_fn()
        + _js_restore_deps_fn()
        + _js_run_tests_fn()
        + r"""echo "[docker] js tests (base + test_patch only)" >&2
_apply_one /w/test.patch || exit 1
_run_js_tests /w/junit-base.xml /w/test-base.log
echo "[docker] reset to base_commit" >&2
git reset --hard "${SWEBENCH_BASE_COMMIT:-HEAD}"
"""
        + _js_git_clean_excludes()
        + r"""
_js_restore_deps_if_needed
echo "[docker] js tests (test_patch + impl.patch)" >&2
_apply_one /w/test.patch || exit 1
_apply_one /w/impl.patch || exit 1
_run_js_tests /w/junit-patch.xml /w/test-patch.log
"""
        + (_pip_freeze_block() if run_pip_freeze else "")
    )


def _php_body(
    install_config: dict[str, Any],
    run_pip_freeze: bool,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    test_cmd = str(install_config.get("test_cmd") or "").strip()
    runner = str(install_config.get("php_test_runner") or "").strip()
    restore = ""
    if not tests_only:
        from .runtime_deps import runtime_deps_restore_shell

        restore = runtime_deps_restore_shell("php", install_config, repo_dir=repo_dir)
    restore_mid = (restore.strip() + "\n") if restore.strip() else ""
    return (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
            install_config=install_config,
            language="php",
        )
        + _empty_junit_both()
        + f'PHP_TEST_CMD="{test_cmd}"\n'
        + f'PHP_TEST_RUNNER="{runner}"\n'
        + _apply_one_fn()
        + r"""_run_php_tests() {
  local junit_out="$1"
  local log_out="$2"
  if [[ -n "${PHP_TEST_CMD:-}" ]]; then
    local cmd="${PHP_TEST_CMD//__JUNIT_OUT__/$junit_out}"
    echo "[docker] php test_cmd=$cmd" >&2
    if [[ ${#T[@]} -gt 0 ]]; then
      eval "$cmd" "${T[@]}" 2>&1 | tee "$log_out" || true
    else
      eval "$cmd" 2>&1 | tee "$log_out" || true
    fi
    return 0
  fi
  if [[ -x vendor/bin/simple-phpunit ]]; then
    echo "[docker] php simple-phpunit ${#T[@]} path(s)" >&2
    vendor/bin/simple-phpunit --log-junit "$junit_out" "${T[@]}" 2>&1 | tee "$log_out" || true
    return 0
  fi
  if [[ -x vendor/bin/phpunit ]]; then
    echo "[docker] php phpunit ${#T[@]} path(s)" >&2
    vendor/bin/phpunit --log-junit "$junit_out" "${T[@]}" 2>&1 | tee "$log_out" || true
    return 0
  fi
  echo "[docker] php: no vendor/bin/phpunit or simple-phpunit" >&2
  phpunit --log-junit "$junit_out" 2>&1 | tee "$log_out" || true
}
echo "[docker] phpunit (base + test_patch only)" >&2
_apply_one /w/test.patch || exit 1
_run_php_tests /w/junit-base.xml /w/test-base.log
echo "[docker] reset to base_commit" >&2
git reset --hard "${SWEBENCH_BASE_COMMIT:-HEAD}"
git clean -ffdx 2>/dev/null || true
"""
        + restore_mid
        + r"""echo "[docker] phpunit (test_patch + impl.patch)" >&2
_apply_one /w/test.patch || exit 1
_apply_one /w/impl.patch || exit 1
if [[ -n "${DISCOVERY_PATCH_FULL_SUITE:-}" ]]; then
  echo "[docker] patch-phase discovery: full phpunit (no T[@] scope)" >&2
  T=()
fi
_run_php_tests /w/junit-patch.xml /w/test-patch.log
"""
        + (_pip_freeze_block() if run_pip_freeze else "")
    )


def _ruby_rspec_run_block() -> str:
    return r"""_ruby_ensure_junit_formatter() {
  if bundle exec ruby -e "require 'rspec_junit_formatter'" 2>/dev/null; then
    return 0
  fi
  echo "[docker] installing rspec_junit_formatter" >&2
  gem install rspec_junit_formatter -N 2>/dev/null || true
  bundle add --group test rspec_junit_formatter 2>/dev/null || true
}
_run_ruby_tests() {
  local junit_out="$1"
  local log_out="$2"
  _ruby_ensure_junit_formatter
  if [[ ${#T[@]} -gt 0 ]]; then
    echo "[docker] rspec ${#T[@]} path(s) -> $junit_out" >&2
    bundle exec rspec "${T[@]}" --format RspecJunitFormatter --out "$junit_out" \
      2>&1 | tee "$log_out" || true
  elif [[ -n "${RUBY_TEST_CMD:-}" ]]; then
    local cmd="${RUBY_TEST_CMD//__JUNIT_OUT__/$junit_out}"
    echo "[docker] ruby test_cmd=$cmd" >&2
    eval "$cmd" 2>&1 | tee "$log_out" || true
  else
    echo "[docker] rspec (full suite) -> $junit_out" >&2
    bundle exec rspec --format RspecJunitFormatter --out "$junit_out" \
      2>&1 | tee "$log_out" || true
  fi
  python3 /w/empty_junit_if_missing.py "$junit_out" "$log_out" || true
}
"""


def _ruby_minitest_run_block() -> str:
    return r"""_ruby_ensure_minitest_junit() {
  gem install minitest-reporters -N 2>/dev/null || true
}
_run_ruby_tests() {
  local junit_out="$1"
  local log_out="$2"
  if [[ -n "${RUBY_TEST_CMD:-}" ]]; then
    local cmd="${RUBY_TEST_CMD//__JUNIT_OUT__/$junit_out}"
    echo "[docker] ruby test_cmd=$cmd" >&2
    if [[ ${#T[@]} -gt 0 ]]; then
      eval "$cmd" "${T[@]}" 2>&1 | tee "$log_out" || true
    else
      eval "$cmd" 2>&1 | tee "$log_out" || true
    fi
    python3 /w/empty_junit_if_missing.py "$junit_out" "$log_out" || true
    return 0
  fi
  _ruby_ensure_minitest_junit
  echo "[docker] minitest ${#T[@]} path(s) -> $junit_out" >&2
  if [[ ${#T[@]} -gt 0 ]]; then
    bundle exec ruby /w/minitest_junit_runner.rb "${T[@]}" "$junit_out" \
      2>&1 | tee "$log_out" || true
  elif [[ -f Rakefile ]] && grep -qE '\btest\b' Rakefile 2>/dev/null; then
    bundle exec rake test 2>&1 | tee "$log_out" || true
    python3 /w/minitest_harvest_junit.py "$junit_out" "$log_out" || true
  else
    bundle exec ruby -Itest /w/minitest_junit_runner.rb test "$junit_out" \
      2>&1 | tee "$log_out" || true
  fi
  python3 /w/empty_junit_if_missing.py "$junit_out" "$log_out" || true
}
"""


def _ruby_two_phase_tail(
    runner_label: str,
    *,
    restore_block: str = "",
    post_patch_bundle_block: str = "",
) -> str:
    restore = (restore_block.strip() + "\n") if restore_block.strip() else ""
    post_patch = (post_patch_bundle_block.strip() + "\n") if post_patch_bundle_block.strip() else ""
    if post_patch and not post_patch.endswith("\n"):
        post_patch = post_patch + "\n"
    return rf"""echo "[docker] {runner_label} (base + test_patch only)" >&2
_apply_one /w/test.patch || exit 1
_run_ruby_tests /w/junit-base.xml /w/test-base.log
echo "[docker] reset to base_commit" >&2
git reset --hard "${{SWEBENCH_BASE_COMMIT:-HEAD}}"
git clean -ffdx 2>/dev/null || true
{restore}echo "[docker] {runner_label} (test_patch + impl.patch)" >&2
_apply_one /w/test.patch || exit 1
_apply_one /w/impl.patch || exit 1
{post_patch}if [[ -n "${{DISCOVERY_PATCH_FULL_SUITE:-}}" ]]; then
  echo "[docker] patch-phase discovery: full rspec (no T[@] scope)" >&2
  T=()
fi
_run_ruby_tests /w/junit-patch.xml /w/test-patch.log
"""


def _ruby_body(
    run_pip_freeze: bool,
    install_config: dict[str, Any] | None = None,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    from .ruby_build import (
        runner_from_install_config,
        ruby_post_patch_bundle_install_shell,
        ruby_test_cmd_for_docker_entry,
    )

    cfg = install_config if isinstance(install_config, dict) else {}
    runner = runner_from_install_config(cfg)
    ruby_cmd = ruby_test_cmd_for_docker_entry(cfg)
    ruby_cmd_line = (
        f'RUBY_TEST_CMD="{_sh_escape_double(ruby_cmd)}"\n' if ruby_cmd else 'RUBY_TEST_CMD=""\n'
    )
    run_block = _ruby_minitest_run_block() if runner == "minitest" else _ruby_rspec_run_block()
    label = "minitest" if runner == "minitest" else "rspec"
    restore = ""
    post_patch_bundle = ruby_post_patch_bundle_install_shell(cfg, repo_dir=repo_dir)
    if not tests_only:
        from .runtime_deps import runtime_deps_restore_shell

        restore = runtime_deps_restore_shell("ruby", cfg, repo_dir=repo_dir)
    return (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
            install_config=cfg,
            language="ruby",
        )
        + _empty_junit_both()
        + f'RUBY_TEST_RUNNER="{runner}"\n'
        + 'export SKIP_REPO_JUNIT_HARVEST=1\n'
        + ruby_cmd_line
        + _test_env_block(cfg)
        + _apply_one_fn()
        + run_block
        + post_patch_bundle
        + _ruby_two_phase_tail(
            label,
            restore_block=restore,
            post_patch_bundle_block="_ruby_post_patch_bundle_install",
        )
        + (_pip_freeze_block() if run_pip_freeze else "")
    )


def _cmake_runtests_invoke_block(install_config: dict[str, Any], *, repo_dir: str) -> str:
    """Scoped runtests env wrapper (no global LD_LIBRARY_PATH export)."""
    if not install_config.get("cmake_runtests_build"):
        return ""
    from .runtests_build import runtests_cmake_invoke_block

    return runtests_cmake_invoke_block(
        repo_dir=repo_dir,
        curl_tool_symlinks=bool(install_config.get("runtests_cmake_tool_symlinks")),
    )


def _cmake_runtests_verify_tools_block(
    *,
    repo_dir: str,
    layout_adapter: bool = False,
    harness_subdirs: list[Any] | None = None,
) -> str:
    root = repo_dir.rstrip("/")
    if layout_adapter:
        from .runtests_build import runtests_cmake_harness_preflight_shell

        harness_sh = runtests_cmake_harness_preflight_shell(harness_subdirs or [])
        return f"""
_runtests_verify_tools() {{
  _runtests_invoke '
    cd tests || exit 0
    for bin in ../build/src/curl ../src/curlinfo; do
      [[ -x "$bin" ]] || continue
      if ! "$bin" -V >/dev/null 2>&1; then
        echo "[docker] runtests preflight: $bin -V failed (check LD_LIBRARY_PATH)" >&2
      else
        echo "[docker] runtests preflight: $bin ok" >&2
      fi
    done
    {harness_sh}
  ' || true
}}
"""
    return f"""
_runtests_verify_tools() {{
  _runtests_invoke '
    for bin in "{root}/build/src/curl" "{root}/build/curl"; do
      [[ -x "$bin" ]] || continue
      if ! "$bin" -V >/dev/null 2>&1; then
        echo "[docker] runtests preflight: $bin -V failed (check LD_LIBRARY_PATH)" >&2
      else
        echo "[docker] runtests preflight: $bin ok" >&2
      fi
      exit 0
    done
    echo "[docker] runtests preflight: no tool binary under build/src or build/" >&2
  ' || true
}}
"""


def _cmake_runtests_setup_shell(lines: list[Any], *, repo_dir: str, log_tag: str) -> str:
    qrepo = _sh_quote(repo_dir)
    body: list[str] = []
    for ln in lines:
        if not isinstance(ln, str) or not ln.strip():
            continue
        cmd = ln.strip()
        body.append(
            f'echo "[docker] runtests setup ({log_tag}): " {_sh_quote(cmd)} >&2\n'
            f"(cd {qrepo} && {cmd}) 2>&1 | tee -a /w/runtests-setup-{log_tag}.log || true"
        )
    return "\n".join(body) + ("\n" if body else "")


def _cmake_runtests_reinstall_block() -> str:
    return r"""
_runtests_cmake_reinstall() {
  echo "[docker] re-running cmake install for runtests" >&2
  bash /w/project_install.sh 2>&1 | tee /w/runtests-reinstall.log || true
  bash /w/post_install.sh 2>&1 | tee -a /w/runtests-reinstall.log || true
}
"""


def _cmake_runtests_body(
    run_pip_freeze: bool,
    install_config: dict[str, Any] | None = None,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    cfg = install_config or {}
    test_cmd = str(cfg.get("test_cmd") or "./tests/runtests.pl -a -am -p").strip()
    test_cmd_base = str(cfg.get("runtests_test_cmd_base") or test_cmd).strip()
    setup_patch = cfg.get("runtests_setup_patch") or []
    setup_base = cfg.get("runtests_setup_base") or []
    env_block = _cmake_runtests_invoke_block(cfg, repo_dir=repo_dir)
    setup_base_sh = _cmake_runtests_setup_shell(
        setup_base if isinstance(setup_base, list) else [],
        repo_dir=repo_dir,
        log_tag="base",
    )
    setup_patch_sh = _cmake_runtests_setup_shell(
        setup_patch if isinstance(setup_patch, list) else [],
        repo_dir=repo_dir,
        log_tag="patch",
    )
    reinstall_fn = _cmake_runtests_reinstall_block()
    verify_fn = _cmake_runtests_verify_tools_block(
        repo_dir=repo_dir,
        layout_adapter=bool(cfg.get("runtests_cmake_layout_adapter")),
        harness_subdirs=cfg.get("runtests_cmake_harness_subdirs") or [],
    )
    return (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
            install_config=cfg,
            language="c",
        )
        + env_block
        + verify_fn
        + reinstall_fn
        + f'RUNTESTS_CMD="{test_cmd}"\n'
        + f'RUNTESTS_CMD_BASE="{test_cmd_base}"\n'
        + _apply_one_fn()
        + r"""echo "[docker] runtests (base + test_patch only)" >&2
_apply_one /w/test.patch || exit 1
"""
        + setup_base_sh
        + r"""_runtests_verify_tools
_runtests_invoke "$RUNTESTS_CMD_BASE" 2>&1 | tee /w/test-base.log || true
echo "[docker] reset to base_commit" >&2
git reset --hard "${SWEBENCH_BASE_COMMIT:-HEAD}"
git clean -ffdx 2>/dev/null || git clean -ffdx
echo "[docker] runtests (test_patch + impl.patch)" >&2
_apply_one /w/test.patch || exit 1
_apply_one /w/impl.patch || exit 1
_runtests_cmake_reinstall
"""
        + setup_patch_sh
        + r"""_runtests_verify_tools
_runtests_invoke "$RUNTESTS_CMD" 2>&1 | tee /w/test-patch.log || true
"""
        + (_pip_freeze_block() if run_pip_freeze else "")
    )


def _c_body(
    run_pip_freeze: bool,
    install_config: dict[str, Any] | None = None,
    *,
    repo_dir: str = "/w/repo",
    skip_install: bool = False,
    harness_conda: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> str:
    cfg = install_config or {}
    if cfg.get("cmake_runtests_build"):
        return _cmake_runtests_body(
            run_pip_freeze,
            install_config,
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
        )
    from .c_build import is_premake_config

    if is_premake_config(cfg):
        test_cmd = str(cfg.get("test_cmd") or "bin/release/premake5 test --test-all").strip()
        test_cmd_base = str(
            cfg.get("premake_test_cmd_base") or test_cmd
        ).strip()
        install_cmd = str(cfg.get("install") or "PLATFORM=x64 CONFIG=release ./Bootstrap.sh").strip()
        return (
            _common_header(
                repo_dir=repo_dir,
                skip_install=skip_install,
                harness_conda=harness_conda,
                harness_env_only=harness_env_only,
                tests_only=tests_only,
            )
            + _empty_junit_both()
            + f'PREMAKE_INSTALL_CMD="{install_cmd}"\n'
            + f'PREMAKE_TEST_CMD_BASE="{test_cmd_base}"\n'
            + f'PREMAKE_TEST_CMD="{test_cmd}"\n'
            + _apply_one_fn()
            + r"""echo "[docker] premake test (base + test_patch only)" >&2
_apply_one /w/test.patch || exit 1
bash -lc "$PREMAKE_TEST_CMD_BASE" 2>&1 | tee /w/test-base.log || true
echo "[docker] reset to base_commit" >&2
git reset --hard "${SWEBENCH_BASE_COMMIT:-HEAD}"
git clean -ffdx 2>/dev/null || git clean -ffdx
echo "[docker] premake rebuild (test_patch + impl.patch)" >&2
_apply_one /w/test.patch || exit 1
_apply_one /w/impl.patch || exit 1
bash -lc "$PREMAKE_INSTALL_CMD" 2>&1 | tee /w/premake-rebuild.log || true
echo "[docker] premake test (test_patch + impl.patch)" >&2
bash -lc "$PREMAKE_TEST_CMD" 2>&1 | tee /w/test-patch.log || true
"""
            + (_pip_freeze_block() if run_pip_freeze else "")
        )

    test_cmd = str(cfg.get("test_cmd") or "cd build && ctest --output-on-failure -j\"$(nproc)\"").strip()
    use_ctest_log = "ctest" in test_cmd and not cfg.get("native_integration_build")
    junit_block = "" if use_ctest_log else _empty_junit_both()
    ctest_junit_tail = ""
    if not use_ctest_log:
        ctest_junit_tail = r"""
python3 /w/empty_junit_if_missing.py /w/junit-base.xml /w/test-base.log
"""
    ctest_junit_tail_patch = ""
    if not use_ctest_log:
        ctest_junit_tail_patch = r"""
python3 /w/empty_junit_if_missing.py /w/junit-patch.xml /w/test-patch.log
"""
    return (
        _common_header(
            repo_dir=repo_dir,
            skip_install=skip_install,
            harness_conda=harness_conda,
            harness_env_only=harness_env_only,
            tests_only=tests_only,
        )
        + junit_block
        + f'C_TEST_CMD="{test_cmd}"\n'
        + r"""echo "[docker] c test (before patch)" >&2
if [[ -d build ]] && [[ "$C_TEST_CMD" == *ctest* ]]; then
  (cd build && ctest --output-on-failure -j"$(nproc)" 2>&1 | tee /w/test-base.log) || true
elif [[ -f Makefile ]] && [[ "$C_TEST_CMD" == *make*test* ]]; then
  make test 2>&1 | tee /w/test-base.log || true
else
  bash -lc "$C_TEST_CMD" 2>&1 | tee /w/test-base.log || true
fi
"""
        + ctest_junit_tail
        + _apply_patches_block()
        + r"""echo "[docker] c test (after patch)" >&2
if [[ -d build ]] && [[ "$C_TEST_CMD" == *ctest* ]]; then
  (cd build && ctest --output-on-failure -j"$(nproc)" 2>&1 | tee /w/test-patch.log) || true
elif [[ -f Makefile ]] && [[ "$C_TEST_CMD" == *make*test* ]]; then
  make test 2>&1 | tee /w/test-patch.log || true
else
  bash -lc "$C_TEST_CMD" 2>&1 | tee /w/test-patch.log || true
fi
"""
        + ctest_junit_tail_patch
        + (_pip_freeze_block() if run_pip_freeze else "")
    )


MERGE_JUNIT_PY = r'''
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

out = sys.argv[1]
reports_dir = Path(sys.argv[2])
mode = sys.argv[3] if len(sys.argv) > 3 else "maven"
root = ET.Element("testsuites")
paths: list[Path] = []
if mode == "gradle":
    extra_roots = sys.argv[4:] if len(sys.argv) > 4 else []
    if extra_roots:
        for rel in extra_roots:
            base = reports_dir / rel
            if base.is_dir():
                paths.extend(sorted(base.rglob("TEST-*.xml")))
        # Do not fall back to repo-wide merge when scoped roots were requested.
    elif reports_dir.is_dir():
        paths = sorted(reports_dir.rglob("build/test-results/**/TEST-*.xml"))
else:
    extra_roots = sys.argv[4:] if len(sys.argv) > 4 else []
    if extra_roots:
        for rel in extra_roots:
            base = reports_dir / rel
            if base.is_dir():
                paths.extend(sorted(base.glob("TEST-*.xml")))
    elif reports_dir.is_dir():
        paths = sorted(reports_dir.rglob("**/surefire-reports/TEST-*.xml"))
        if not paths:
            paths = sorted(reports_dir.glob("TEST-*.xml"))
for p in paths:
    try:
        t = ET.parse(p)
    except ET.ParseError:
        continue
    r = t.getroot()
    tag = r.tag.split("}")[-1]
    if tag == "testsuites":
        for child in list(r):
            root.append(child)
    elif tag == "testsuite":
        root.append(r)
if len(root) == 0:
    ET.ElementTree(ET.Element("testsuite", {"name": "empty", "tests": "0"})).write(out)
else:
    ET.ElementTree(root).write(out)
'''

HARVEST_JEST_JUNIT_PY = r'''
"""Merge jest-junit XML files from ``<repo>/junit/*.xml`` into harness ``/w/junit-*.xml``."""
import sys
from pathlib import Path
import xml.etree.ElementTree as ET


def _count_testcases(root: ET.Element) -> int:
    return sum(1 for el in root.iter() if el.tag.split("}")[-1] == "testcase")


def _append_root(dst: ET.Element, src: ET.Element) -> None:
    tag = src.tag.split("}")[-1]
    if tag == "testsuites":
        for child in list(src):
            dst.append(child)
    elif tag == "testsuite":
        dst.append(src)


def merge_junit_dir(junit_dir: Path, out: Path) -> int:
    xmls = sorted(junit_dir.glob("*.xml"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not xmls:
        return 0
    root = ET.Element("testsuites")
    used = 0
    for path in xmls:
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError) as exc:
            print(f"[harvest] skip {path.name}: {exc}", file=sys.stderr)
            continue
        _append_root(root, tree.getroot())
        used += 1
    if used == 0:
        return 0
    ET.ElementTree(root).write(out)
    n = _count_testcases(root)
    print(
        f"[harvest] merged {used} file(s) from {junit_dir} -> {out} ({n} testcase(s))",
        file=sys.stderr,
    )
    return n


def main() -> None:
    out = Path(sys.argv[1])
    junit_dir = Path(sys.argv[2])
    if not junit_dir.is_dir():
        print(f"[harvest] junit directory missing: {junit_dir}", file=sys.stderr)
        raise SystemExit(1)
    n = merge_junit_dir(junit_dir, out)
    if n <= 0 and not (out.is_file() and out.stat().st_size > 50):
        print(f"[harvest] no junit XML under {junit_dir}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
'''


EMPTY_JUNIT_PY = r'''
import os
import subprocess
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

out, log = Path(sys.argv[1]), Path(sys.argv[2])

def _has_testcases(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 50:
        return False
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return False
    return any(el.tag.split("}")[-1] == "testcase" for el in root.iter())

if _has_testcases(out):
    raise SystemExit(0)
repo = Path(os.environ.get("REPO_DIR", "/testbed"))
junit_dir = repo / "junit"
if os.environ.get("SKIP_REPO_JUNIT_HARVEST", "") != "1" and junit_dir.is_dir():
    proc = subprocess.run(
        ["python3", "/w/harvest_jest_junit.py", str(out), str(junit_dir)],
        capture_output=True,
        text=True,
    )
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode == 0 and _has_testcases(out):
        raise SystemExit(0)
root = ET.Element("testsuites")
ET.SubElement(root, "testsuite", {"name": "log", "tests": "0"})
ET.ElementTree(root).write(out)
'''


MINITEST_JUNIT_RUNNER_RB = r"""#!/usr/bin/env ruby
# Load scoped Minitest files with JUnit output (discover + harness).
require 'minitest/autorun'
require 'minitest/reporters'

junit_out = ARGV.pop
files = ARGV.reject(&:empty?)
Minitest::Reporters.use! Minitest::Reporters::JUnitReporter.new(junit_out)
files.each { |f| load f }
"""

MINITEST_HARVEST_JUNIT_PY = r'''
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

out, log = Path(sys.argv[1]), Path(sys.argv[2])
if out.is_file() and out.stat().st_size > 50:
    raise SystemExit(0)
root = ET.Element("testsuites")
ET.SubElement(root, "testsuite", {"name": "minitest", "tests": "0"})
ET.ElementTree(root).write(out)
'''


def write_helper_scripts(work: Path) -> None:
    (work / "merge_junit.py").write_text(MERGE_JUNIT_PY.strip() + "\n", encoding="utf-8")
    (work / "harvest_jest_junit.py").write_text(HARVEST_JEST_JUNIT_PY.strip() + "\n", encoding="utf-8")
    (work / "empty_junit_if_missing.py").write_text(EMPTY_JUNIT_PY.strip() + "\n", encoding="utf-8")
    (work / "minitest_junit_runner.rb").write_text(MINITEST_JUNIT_RUNNER_RB.strip() + "\n", encoding="utf-8")
    (work / "minitest_harvest_junit.py").write_text(MINITEST_HARVEST_JUNIT_PY.strip() + "\n", encoding="utf-8")


def write_entry_script(
    work: Path,
    language: str,
    targets: list[str],
    install_config: dict[str, Any],
    *,
    run_pip_freeze: bool = False,
    harness_image: bool = False,
    harness_env_only: bool = False,
    tests_only: bool = False,
) -> None:
    lang = get_language_spec(language)
    repo_dir = "/testbed" if harness_image else "/w/repo"
    skip_install = harness_image
    harness_conda = harness_image and lang.id == "python"
    use_env_only = harness_image and harness_env_only
    (work / "targets.txt").write_text("\n".join(targets) + "\n", encoding="utf-8")
    write_helper_scripts(work)
    body_kw = {
        "repo_dir": repo_dir,
        "skip_install": skip_install,
        "harness_conda": harness_conda,
        "harness_env_only": use_env_only,
        "tests_only": tests_only,
    }

    if lang.id == "python":
        if install_config.get("django_pytest"):
            from .install_llm import render_django_pytest_settings

            (work / "django_pytest_settings.py").write_text(
                render_django_pytest_settings(targets),
                encoding="utf-8",
            )
            body = _python_body(install_config, run_pip_freeze, **body_kw)
        elif install_config.get("django_runtests") or (
            "runtests.py" in str(install_config.get("test_cmd") or "")
        ):
            body = _django_runtests_body(install_config, run_pip_freeze, **body_kw)
        else:
            body = _python_body(install_config, run_pip_freeze, **body_kw)
        _write_python_install_bundle(work, install_config, repo_dir=repo_dir)
    elif lang.id == "go":
        body = _go_body(targets, run_pip_freeze, install_config, **body_kw)
        _write_generic_install_bundle(work, install_config, include_pip=False, repo_dir=repo_dir)
    elif lang.id == "rust":
        body = _rust_body(install_config, run_pip_freeze, **body_kw)
        _write_generic_install_bundle(work, install_config, include_pip=False, repo_dir=repo_dir)
    elif lang.id == "java":
        body = _java_body(install_config, run_pip_freeze, **body_kw)
        _write_generic_install_bundle(work, install_config, include_pip=False, repo_dir=repo_dir)
    elif lang.id == "javascript":
        body = _js_body(install_config, run_pip_freeze, **body_kw)
        _write_generic_install_bundle(work, install_config, include_pip=False, repo_dir=repo_dir)
    elif lang.id == "php":
        body = _php_body(install_config, run_pip_freeze, **body_kw)
        _write_generic_install_bundle(work, install_config, include_pip=False, repo_dir=repo_dir)
    elif lang.id == "ruby":
        body = _ruby_body(run_pip_freeze, install_config, **body_kw)
        _write_generic_install_bundle(work, install_config, include_pip=False, repo_dir=repo_dir)
    elif lang.id == "c":
        body = _c_body(run_pip_freeze, install_config, **body_kw)
        _write_generic_install_bundle(work, install_config, include_pip=False, repo_dir=repo_dir)
    else:
        raise ValueError(f"No docker entry for language {language}")

    script = work / "docker_entry.sh"
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    for name in (
        "pre_install.sh",
        "project_install.sh",
        "post_install.sh",
        "pip_packages.sh",
        "reqs_path.sh",
        "setup_repo.sh",
    ):
        p = work / name
        if p.is_file():
            p.chmod(0o755)


def _write_python_install_bundle(
    work: Path, install_config: dict[str, Any], *, repo_dir: str = "/w/repo"
) -> None:
    """Python install bundle (pip_packages + reqs_path)."""
    (work / "install_config.json").write_text(json.dumps(install_config, indent=2), encoding="utf-8")

    pre = install_config.get("pre_install") or []
    pre_lines = ["#!/bin/bash", "set -e", "export DEBIAN_FRONTEND=noninteractive", f"cd {_sh_quote(repo_dir)}"]
    if isinstance(pre, list):
        pre_lines.extend(ln.strip() for ln in pre if isinstance(ln, str) and ln.strip())
    (work / "pre_install.sh").write_text("\n".join(pre_lines) + "\n", encoding="utf-8")

    plines = ["#!/bin/bash", "set -e", f"cd {_sh_quote(repo_dir)}"]
    pip_pkgs = install_config.get("pip_packages") or []
    if isinstance(pip_pkgs, list):
        for p in pip_pkgs:
            if isinstance(p, str) and p.strip():
                plines.append(f"python3 -m pip install -q {_sh_quote(p.strip())}")
    (work / "pip_packages.sh").write_text("\n".join(plines) + "\n", encoding="utf-8")

    rlines = ["#!/bin/bash", "set -e", f"cd {_sh_quote(repo_dir)}"]
    reqs = install_config.get("reqs_path") or []
    if isinstance(reqs, list):
        for rel in reqs:
            if isinstance(rel, str) and rel.strip():
                q = _sh_quote(rel.strip())
                rlines.append(f"if [[ -f {q} ]]; then python3 -m pip install -q -r {q}; fi")
    (work / "reqs_path.sh").write_text("\n".join(rlines) + "\n", encoding="utf-8")

    install_cmd = str(install_config.get("install") or "pip install -e .").strip()
    (work / "project_install.sh").write_text(
        f"#!/bin/bash\nset -e\ncd {_sh_quote(repo_dir)}\n" + install_cmd + "\n",
        encoding="utf-8",
    )

    post = install_config.get("post_install") or []
    post_lines = ["#!/bin/bash", "set -e", f"cd {_sh_quote(repo_dir)}"]
    if isinstance(post, list):
        post_lines.extend(ln.strip() for ln in post if isinstance(ln, str) and ln.strip())
    (work / "post_install.sh").write_text("\n".join(post_lines) + "\n", encoding="utf-8")


def _write_generic_install_bundle(
    work: Path,
    install_config: dict[str, Any],
    *,
    include_pip: bool,
    repo_dir: str = "/w/repo",
) -> None:
    (work / "install_config.json").write_text(json.dumps(install_config, indent=2), encoding="utf-8")

    from .c_build import merge_c_apt_into_config

    apt_pkgs = install_config.get("apt-pkgs") or []
    if isinstance(apt_pkgs, list) and apt_pkgs:
        merged = merge_c_apt_into_config(
            install_config, [str(p) for p in apt_pkgs if str(p).strip()]
        )
        pre = merged.get("pre_install") or []
    else:
        pre = install_config.get("pre_install") or []
    pre_lines = ["#!/bin/bash", "set -e", "export DEBIAN_FRONTEND=noninteractive", f"cd {_sh_quote(repo_dir)}"]
    if isinstance(pre, list):
        pre_lines.extend(ln.strip() for ln in pre if isinstance(ln, str) and ln.strip())
    (work / "pre_install.sh").write_text("\n".join(pre_lines) + "\n", encoding="utf-8")

    plines = ["#!/bin/bash", "set -e", f"cd {_sh_quote(repo_dir)}"]
    if include_pip:
        pip_pkgs = install_config.get("pip_packages") or []
        if isinstance(pip_pkgs, list):
            for p in pip_pkgs:
                if isinstance(p, str) and p.strip():
                    plines.append(f"python3 -m pip install -q {_sh_quote(p.strip())}")
    (work / "pip_packages.sh").write_text("\n".join(plines) + "\n", encoding="utf-8")
    (work / "reqs_path.sh").write_text(
        f"#!/bin/bash\nset -e\ncd {_sh_quote(repo_dir)}\n", encoding="utf-8"
    )

    install_cmd = str(install_config.get("install") or "true").strip()
    if install_cmd.startswith("#"):
        install_cmd = "true"
    (work / "project_install.sh").write_text(
        f"#!/bin/bash\nset -e\ncd {_sh_quote(repo_dir)}\n" + install_cmd + "\n",
        encoding="utf-8",
    )

    post = install_config.get("post_install") or []
    post_lines = ["#!/bin/bash", "set -e", f"cd {_sh_quote(repo_dir)}"]
    if isinstance(post, list):
        post_lines.extend(ln.strip() for ln in post if isinstance(ln, str) and ln.strip())
    (work / "post_install.sh").write_text("\n".join(post_lines) + "\n", encoding="utf-8")
