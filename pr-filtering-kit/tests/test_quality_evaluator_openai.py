"""Tests for QualityEvaluator._call_openai retry logic."""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

import eval_kit.llm_client
from eval_kit.llm_client import MAX_RETRIES as _MAX_RETRIES
from eval_kit.quality_evaluator import QualityEvaluator


class _RetryableError(Exception):
    pass


class _NonRetryableError(Exception):
    pass


eval_kit.llm_client.RETRYABLE_ERRORS = (_RetryableError,)


def _make_agent_result(content: str) -> MagicMock:
    result = MagicMock()
    result.output = content
    return result


@pytest.fixture
def evaluator():
    return QualityEvaluator()


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "LLM_PROVIDER": "openai"})
@patch("pydantic_ai.Agent.run_sync")
@patch("eval_kit.llm_client.time.sleep")
def test_success_on_first_attempt(mock_sleep, mock_run_sync, evaluator):
    mock_run_sync.return_value = _make_agent_result('{"ok": true}')

    result = evaluator._call_openai("prompt")

    assert result == '{"ok": true}'
    assert mock_run_sync.call_count == 1
    mock_sleep.assert_not_called()


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "LLM_PROVIDER": "openai"})
@patch("pydantic_ai.Agent.run_sync")
@patch("eval_kit.llm_client.time.sleep")
def test_retries_on_transient_error_then_succeeds(mock_sleep, mock_run_sync, evaluator):
    mock_run_sync.side_effect = [
        _RetryableError(),
        _RetryableError(),
        _make_agent_result('{"result": "ok"}'),
    ]

    result = evaluator._call_openai("prompt")

    assert result == '{"result": "ok"}'
    assert mock_run_sync.call_count == 3
    assert mock_sleep.call_count == 2


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "LLM_PROVIDER": "openai"})
@patch("pydantic_ai.Agent.run_sync")
@patch("eval_kit.llm_client.time.sleep")
def test_exhausts_all_retries_and_raises(mock_sleep, mock_run_sync, evaluator):
    mock_run_sync.side_effect = _RetryableError()

    with pytest.raises(_RetryableError):
        evaluator._call_openai("prompt")

    assert mock_run_sync.call_count == _MAX_RETRIES
    assert mock_sleep.call_count == _MAX_RETRIES


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "LLM_PROVIDER": "openai"})
@patch("pydantic_ai.Agent.run_sync")
@patch("eval_kit.llm_client.time.sleep")
def test_logs_warning_on_each_retry(mock_sleep, mock_run_sync, evaluator, caplog):
    mock_run_sync.side_effect = _RetryableError()

    with caplog.at_level(logging.WARNING, logger="eval_kit.llm_client"):
        with pytest.raises(_RetryableError):
            evaluator._call_openai("prompt")

    warning_messages = [
        r.message for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert len(warning_messages) == _MAX_RETRIES
    assert all("retrying" in m for m in warning_messages)


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "LLM_PROVIDER": "openai"})
@patch("pydantic_ai.Agent.run_sync")
@patch("eval_kit.llm_client.time.sleep")
def test_logs_error_after_all_retries_exhausted(
    mock_sleep, mock_run_sync, evaluator, caplog
):
    mock_run_sync.side_effect = _RetryableError()

    with caplog.at_level(logging.ERROR, logger="eval_kit.llm_client"):
        with pytest.raises(_RetryableError):
            evaluator._call_openai("prompt")

    error_messages = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert any("after" in m and "retries" in m for m in error_messages)


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "LLM_PROVIDER": "openai"})
@patch("pydantic_ai.Agent.run_sync")
@patch("eval_kit.llm_client.time.sleep")
def test_exponential_backoff_increases(mock_sleep, mock_run_sync, evaluator):
    mock_run_sync.side_effect = _RetryableError()

    with pytest.raises(_RetryableError):
        evaluator._call_openai("prompt")

    sleep_durations = [call.args[0] for call in mock_sleep.call_args_list]
    for i, duration in enumerate(sleep_durations):
        assert duration >= 2**i, f"Attempt {i}: sleep {duration} < base {2**i}"
    assert sleep_durations[-1] > sleep_durations[0]


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "LLM_PROVIDER": "openai"})
@patch("pydantic_ai.Agent.run_sync")
@patch("eval_kit.llm_client.time.sleep")
def test_raises_immediately_on_non_retryable_error(
    mock_sleep, mock_run_sync, evaluator
):
    mock_run_sync.side_effect = _NonRetryableError("unexpected")

    with pytest.raises(_NonRetryableError):
        evaluator._call_openai("prompt")

    assert mock_run_sync.call_count == 1
    mock_sleep.assert_not_called()


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "LLM_PROVIDER": "openai"})
@patch("pydantic_ai.Agent.run_sync")
@patch("eval_kit.llm_client.time.sleep")
def test_logs_error_on_non_retryable_error(
    mock_sleep, mock_run_sync, evaluator, caplog
):
    mock_run_sync.side_effect = _NonRetryableError("unexpected")

    with caplog.at_level(logging.ERROR, logger="eval_kit.llm_client"):
        with pytest.raises(_NonRetryableError):
            evaluator._call_openai("prompt")

    assert any("LLM call failed" in r.message for r in caplog.records)


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "LLM_PROVIDER": "openai"})
@patch("pydantic_ai.Agent.run_sync")
@patch("eval_kit.llm_client.time.sleep")
def test_temperature_is_zero(mock_sleep, mock_run_sync, evaluator):
    mock_run_sync.return_value = _make_agent_result("{}")

    evaluator._call_openai("prompt")

    call_kwargs = mock_run_sync.call_args.kwargs
    assert call_kwargs.get("model_settings", {}).get("temperature") == 0


@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "LLM_PROVIDER": "openai"})
@patch("pydantic_ai.Agent.__init__", return_value=None)
@patch("pydantic_ai.Agent.run_sync")
@patch("eval_kit.llm_client.time.sleep")
def test_prompt_is_passed_as_user_message(
    mock_sleep, mock_run_sync, mock_init, evaluator
):
    mock_run_sync.return_value = _make_agent_result("{}")

    evaluator._call_openai("my test prompt")

    call_args = mock_run_sync.call_args
    assert "my test prompt" in call_args.args[0]
