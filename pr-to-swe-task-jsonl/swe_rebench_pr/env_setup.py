from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _venv_bin(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts"
    return venv_root / "bin"


def _ensure_venv(py_parent: Path) -> Path:
    vdir = py_parent / ".install_venv"
    py = _venv_bin(vdir) / ("python.exe" if sys.platform == "win32" else "python")
    if not py.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(vdir)], check=True, cwd=str(py_parent))
    return py


def _run_bash(script: str, *, cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-lc", script],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def try_pip_install_and_freeze(
    repo: Path,
    work: Path,
    install_config: dict[str, Any],
    *,
    timeout_s: int,
) -> tuple[str, str, str]:
    """
    Best-effort: create venv in ``work``, run install steps, ``pip freeze``.

    Returns ``(requirements, environment, combined_log)``.
    ``environment`` is empty unless conda export is added later.
    """
    py = _ensure_venv(work)
    vbin = py.parent
    env = os.environ.copy()
    env["PATH"] = f"{vbin}{os.pathsep}{env.get('PATH', '')}"
    env["VIRTUAL_ENV"] = str(py.parent.parent)

    logs: list[str] = []

    def log_cp(cp: subprocess.CompletedProcess[str], label: str) -> None:
        logs.append(f"=== {label} ===\n{(cp.stdout or '')}\n{(cp.stderr or '')}")

    # Upgrade pip
    cp0 = subprocess.run(
        [str(py), "-m", "pip", "install", "-U", "pip", "wheel", "setuptools"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=min(300, timeout_s),
        env=env,
    )
    log_cp(cp0, "pip_upgrade")
    if cp0.returncode != 0:
        return "", "", "\n".join(logs)

    is_darwin = platform.system() == "Darwin"
    for line in install_config.get("pre_install") or []:
        if not isinstance(line, str) or not line.strip():
            continue
        if is_darwin and "apt-get" in line:
            logs.append(f"skipped pre_install on Darwin: {line}")
            continue
        cp = _run_bash(line.strip(), cwd=repo, env=env, timeout=min(600, timeout_s))
        log_cp(cp, f"pre_install: {line[:80]}")
        if cp.returncode != 0:
            return "", "", "\n".join(logs)

    for pkg in install_config.get("pip_packages") or []:
        if not isinstance(pkg, str) or not pkg.strip():
            continue
        cp = subprocess.run(
            [str(py), "-m", "pip", "install", pkg.strip()],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=min(1200, timeout_s),
            env=env,
        )
        log_cp(cp, f"pip_packages: {pkg}")
        if cp.returncode != 0:
            return "", "", "\n".join(logs)

    for rel in install_config.get("reqs_path") or []:
        if not isinstance(rel, str):
            continue
        p = repo / rel
        if p.is_file():
            cp = subprocess.run(
                [str(py), "-m", "pip", "install", "-r", str(p)],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=min(1200, timeout_s),
                env=env,
            )
            log_cp(cp, f"reqs_path: {rel}")
            if cp.returncode != 0:
                return "", "", "\n".join(logs)

    install_cmd = str(install_config.get("install") or "").strip()
    if install_cmd:
        cp = _run_bash(install_cmd, cwd=repo, env=env, timeout=timeout_s)
        log_cp(cp, "install")
        if cp.returncode != 0:
            return "", "", "\n".join(logs)

    for line in install_config.get("post_install") or []:
        if not isinstance(line, str) or not line.strip():
            continue
        cp = _run_bash(line.strip(), cwd=repo, env=env, timeout=min(1200, timeout_s))
        log_cp(cp, f"post_install: {line[:80]}")
        if cp.returncode != 0:
            return "", "", "\n".join(logs)

    cp_f = subprocess.run(
        [str(py), "-m", "pip", "freeze"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    log_cp(cp_f, "pip_freeze")
    if cp_f.returncode != 0:
        return "", "", "\n".join(logs)
    return (cp_f.stdout or "").strip(), "", "\n".join(logs)
