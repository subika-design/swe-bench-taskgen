"""Restore language runtime deps after ``git reset`` (tests-only and two-phase harness)."""

from __future__ import annotations

from typing import Any


def _sh_quote_repo(repo_dir: str) -> str:
    if repo_dir.startswith("/"):
        return repo_dir
    return f'"{repo_dir}"'


def runtime_deps_restore_shell(
    language: str,
    install_config: dict[str, Any],
    *,
    repo_dir: str = "/testbed",
) -> str:
    """
    Bash snippet: re-run install when vendor/node_modules missing after reset.

    Mirrors ``_js_restore_deps_if_needed`` in ``docker_entry.py``.
    """
    lang = str(language or install_config.get("language") or "").lower()
    qrepo = _sh_quote_repo(repo_dir)
    install = str(install_config.get("install") or "").strip()

    if lang in ("javascript", "js", "typescript", "ts", "node"):
        return f"""
_restore_runtime_deps_if_needed() {{
  if [[ -d {qrepo}/node_modules ]] && [[ -n "$(ls -A {qrepo}/node_modules 2>/dev/null)" ]]; then
    return 0
  fi
  echo "[docker] node_modules missing after reset; re-running project_install" >&2
  (cd {qrepo} && bash /w/project_install.sh && bash /w/post_install.sh) || true
}}
_restore_runtime_deps_if_needed
"""

    if lang in ("ruby", "rb"):
        from .ruby_build import ruby_bundle_install_shell_cmd

        cmd = ruby_bundle_install_shell_cmd(install_config)
        return f"""
_restore_runtime_deps_if_needed() {{
  if (cd {qrepo} && bundle check >/dev/null 2>&1); then
    return 0
  fi
  echo "[docker] bundle check failed after reset; re-running install" >&2
  (cd {qrepo} && {cmd}) || true
}}
_restore_runtime_deps_if_needed
"""

    if lang == "php":
        cmd = install if install and install != "true" else "composer install --no-interaction --prefer-dist || true"
        return f"""
_restore_runtime_deps_if_needed() {{
  if [[ -x {qrepo}/vendor/bin/phpunit ]] || [[ -x {qrepo}/vendor/bin/simple-phpunit ]]; then
    return 0
  fi
  echo "[docker] vendor/bin/phpunit missing after reset; re-running install" >&2
  (cd {qrepo} && {cmd}) || true
}}
_restore_runtime_deps_if_needed
"""

    if lang == "java":
        post = install_config.get("post_install")
        lines: list[str] = []
        if isinstance(post, list):
            lines = [str(x).strip() for x in post if str(x).strip()]
        elif isinstance(post, str) and post.strip():
            lines = [post.strip()]
        if not lines:
            lines = ["chmod +x ./gradlew 2>/dev/null || true"]
        body = "\n  ".join(f"(cd {qrepo} && {ln}) || true" for ln in lines[:3])
        return f"""
_restore_runtime_deps_if_needed() {{
  if [[ -x {qrepo}/gradlew ]] || [[ -x {qrepo}/mvnw ]]; then
    :
  else
    echo "[docker] java wrapper missing after reset; refreshing install steps" >&2
  fi
  {body}
}}
_restore_runtime_deps_if_needed
"""

    if lang == "c" and install_config.get("cmake_runtests_build"):
        return f"""
_restore_runtime_deps_if_needed() {{
  if [[ -d {qrepo}/build ]] && [[ -f {qrepo}/build/src/curl || -x {qrepo}/build/curl ]]; then
    return 0
  fi
  echo "[docker] cmake build missing after reset; re-running project_install" >&2
  (cd {qrepo} && bash /w/project_install.sh && bash /w/post_install.sh) || true
}}
_restore_runtime_deps_if_needed
"""

    return ""
