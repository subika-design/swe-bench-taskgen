"""Docker daemon availability checks for the discover pipeline."""

from __future__ import annotations

import shutil
from typing import Final

_DOCKER_OK: bool | None = None
_DOCKER_ERR: str = ""

DOCKER_DAEMON_START_HINT: Final[str] = (
    "Start Docker Desktop (or your Docker daemon) and wait until it reports Running, "
    "then verify with: docker info"
)


def is_docker_daemon_unavailable_error(exc: BaseException | str) -> bool:
    text = str(exc).lower()
    return (
        "error while fetching server api version" in text
        or ("connection aborted" in text and "no such file or directory" in text)
        or "cannot connect to the docker daemon" in text
        or "docker daemon is not running" in text
        or "is the docker daemon running" in text
        or "connect: no such file or directory" in text
    )


def docker_daemon_available(*, refresh: bool = False) -> tuple[bool, str]:
    """
    Return ``(ok, reason)`` — True when the docker CLI exists and the daemon responds.

    Result is cached for the process unless ``refresh=True``.
    """
    global _DOCKER_OK, _DOCKER_ERR
    if _DOCKER_OK is not None and not refresh:
        return _DOCKER_OK, _DOCKER_ERR

    if shutil.which("docker") is None:
        _DOCKER_OK, _DOCKER_ERR = False, "docker not found in PATH"
        return _DOCKER_OK, _DOCKER_ERR

    try:
        import docker
    except ImportError:
        _DOCKER_OK, _DOCKER_ERR = (
            False,
            "python docker SDK not installed (pip install docker)",
        )
        return _DOCKER_OK, _DOCKER_ERR

    try:
        client = docker.from_env()
        client.ping()
        _DOCKER_OK, _DOCKER_ERR = True, ""
        return _DOCKER_OK, _DOCKER_ERR
    except Exception as e:
        msg = str(e).strip() or type(e).__name__
        if is_docker_daemon_unavailable_error(msg):
            msg = f"Docker daemon not running ({msg})"
        _DOCKER_OK, _DOCKER_ERR = False, msg
        return _DOCKER_OK, _DOCKER_ERR


def docker_daemon_error_message(reason: str) -> str:
    return f"{reason}. {DOCKER_DAEMON_START_HINT}"


def reset_docker_daemon_cache() -> None:
    global _DOCKER_OK, _DOCKER_ERR
    _DOCKER_OK = None
    _DOCKER_ERR = ""
