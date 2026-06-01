from __future__ import annotations

TASK_TYPE_VALID = "valid"
TASK_TYPE_PARTIALLY_VALID = "partially_valid"


def classify_task_type(
    *,
    f2p: list[str],
    test_patch_failures: int,
    test_patch_remediated: bool = False,
    test_patch_created_by_llm: bool = False,
    install_or_apply_failed: bool = False,
) -> str:
    """
    Classify rows written to JSONL after the last Docker discover run.

    ``valid`` — non-empty ``FAIL_TO_PASS``, zero failures/errors in the
    ``test_patch`` slice, no test-patch LLM.

    ``partially_valid`` — same clean slice and test_patch was **created** or
    **edited** by LLM (only ``FAIL_TO_PASS`` is stored; ``PASS_TO_PASS`` is ``[]``).

    Returns ``""`` (skip, do not write) when any of:

    - install or ``git apply`` failed on the **last** Docker run (including after
      test_patch LLM re-runs)
    - ``FAIL_TO_PASS`` is empty
    - ``FAIL_TO_PASS`` is set but the test_patch slice still has failures/errors
      (including after LLM test_patch remediation)
    """
    if install_or_apply_failed:
        return ""
    if not f2p or test_patch_failures > 0:
        return ""
    if test_patch_remediated or test_patch_created_by_llm:
        return TASK_TYPE_PARTIALLY_VALID
    return TASK_TYPE_VALID


def task_type_skip_reason(
    *,
    f2p: list[str],
    test_patch_failures: int,
    test_patch_remediated: bool = False,
    test_patch_created_by_llm: bool = False,
    install_or_apply_failed: bool = False,
) -> str:
    """Human-readable skip reason; empty string when the row is gradable."""
    if install_or_apply_failed:
        return "install or patch apply failed on last Docker run"
    if not f2p:
        return "FAIL_TO_PASS empty"
    if test_patch_failures > 0:
        if test_patch_remediated or test_patch_created_by_llm:
            return (
                "FAIL_TO_PASS set but test_patch slice still has "
                f"{test_patch_failures} failure(s)/error(s) after LLM test_patch edits"
            )
        return (
            f"FAIL_TO_PASS set but test_patch slice still has "
            f"{test_patch_failures} failure(s)/error(s)"
        )
    return ""


def test_patch_llm_touched(*, remediated: bool, created: bool) -> bool:
    return remediated or created
