"""Unit tests for tri-state rubric acceptance."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_evaluator import (  # noqa: E402
    RUBRIC_STATUS_ACCEPTED,
    RUBRIC_STATUS_PARTIALLY_ACCEPTED,
    RUBRIC_STATUS_REJECTED,
    _count_rubric_by_status,
    _count_rubric_goal_prs,
    _normalize_rubric_accepted_status,
    _rubric_acceptance_status,
    _rubric_scores_meet_acceptance_rule,
)


def _block(score: int) -> dict:
    return {"score": score, "reasoning": "x"}


def test_acceptance_rule_pass_and_fail():
    assert _rubric_scores_meet_acceptance_rule(
        {"a": _block(0), "b": _block(1), "c": _block(2)}
    )
    assert not _rubric_scores_meet_acceptance_rule({"a": _block(3)})
    assert not _rubric_scores_meet_acceptance_rule(
        {"a": _block(2), "b": _block(2), "c": _block(2)}
    )


def test_full_acceptance_with_tests():
    rubrics = {
        "issue_clarity": _block(0),
        "gold_patch_clarity": _block(1),
        "test_clarity": _block(0),
    }
    assert (
        _rubric_acceptance_status(rubrics, no_tests=False) == RUBRIC_STATUS_ACCEPTED
    )


def test_partial_acceptance_without_tests():
    rubrics = {
        "issue_clarity": _block(0),
        "gold_patch_clarity": _block(1),
        "gold_patch_to_issue_alignment": _block(0),
    }
    assert (
        _rubric_acceptance_status(rubrics, no_tests=True)
        == RUBRIC_STATUS_PARTIALLY_ACCEPTED
    )


def test_no_tests_but_poor_non_test_scores_rejected():
    rubrics = {"issue_clarity": _block(3)}
    assert (
        _rubric_acceptance_status(rubrics, no_tests=True) == RUBRIC_STATUS_REJECTED
    )


def test_normalize_legacy_boolean():
    assert _normalize_rubric_accepted_status(True) == RUBRIC_STATUS_ACCEPTED
    assert _normalize_rubric_accepted_status(False) == RUBRIC_STATUS_REJECTED


def test_count_goal_and_by_status():
    rows = [
        {"rubric_accepted": RUBRIC_STATUS_ACCEPTED},
        {"rubric_accepted": RUBRIC_STATUS_PARTIALLY_ACCEPTED},
        {"rubric_accepted": RUBRIC_STATUS_REJECTED},
        {"rubric_accepted": True},
    ]
    assert _count_rubric_goal_prs(rows) == 3
    counts = _count_rubric_by_status(rows)
    assert counts[RUBRIC_STATUS_ACCEPTED] == 2
    assert counts[RUBRIC_STATUS_PARTIALLY_ACCEPTED] == 1
    assert counts[RUBRIC_STATUS_REJECTED] == 1
