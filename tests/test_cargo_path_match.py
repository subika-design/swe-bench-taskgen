"""Rust cargo log key matching for test_patch paths."""

from swe_rebench_pr.diff_split import (
    _cargo_log_key_in_test_patch_paths,
    has_test_patch_label_mismatch,
    junit_outcome_counts_for_paths,
)


def test_cargo_key_matches_integration_test_stem():
    paths = ["tests/backtrace.rs"]
    assert _cargo_log_key_in_test_patch_paths("backtrace::can_include_a_backtrace", paths)
    assert not _cargo_log_key_in_test_patch_paths("accepts_trailing_commas", paths)


def test_junit_counts_for_cargo_scoped_paths():
    case_map = {
        "context_selector_name::test_foo": "passed",
        "other::test_bar": "passed",
    }
    pa, _, _, _, tot = junit_outcome_counts_for_paths(
        case_map,
        ["tests/context_selector_name.rs"],
        language="rust",
    )
    assert tot == 1
    assert pa == 1


def test_no_label_mismatch_when_cargo_keys_match():
    case_map = {"backtrace::x": "passed"}
    assert not has_test_patch_label_mismatch(
        case_map,
        ["tests/backtrace.rs"],
        language="rust",
    )
