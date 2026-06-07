"""Tests for Docker daemon availability helpers."""

from unittest.mock import MagicMock, patch

from swe_rebench_pr.docker_runtime import (
    docker_daemon_available,
    docker_daemon_error_message,
    is_docker_daemon_unavailable_error,
    reset_docker_daemon_cache,
)


def setup_function():
    reset_docker_daemon_cache()


def test_is_docker_daemon_unavailable_error():
    err = (
        "Error while fetching server API version: "
        "('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))"
    )
    assert is_docker_daemon_unavailable_error(err)


def test_docker_daemon_error_message_includes_hint():
    msg = docker_daemon_error_message("Docker daemon not running")
    assert "Docker Desktop" in msg
    assert "docker info" in msg


def test_docker_daemon_available_caches_success():
    mock_client = MagicMock()
    with patch("docker.from_env", return_value=mock_client):
        ok1, _ = docker_daemon_available(refresh=True)
        ok2, _ = docker_daemon_available()
    assert ok1 is True
    assert ok2 is True
    mock_client.ping.assert_called_once()


def test_docker_daemon_available_detects_daemon_down():
    with patch(
        "docker.from_env",
        side_effect=Exception(
            "Error while fetching server API version: "
            "('Connection aborted.', FileNotFoundError(2, 'No such file or directory'))"
        ),
    ):
        ok, reason = docker_daemon_available(refresh=True)
    assert ok is False
    assert "daemon not running" in reason.lower()
