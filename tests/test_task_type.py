from swe_rebench_pr.task_type import (
    TASK_TYPE_PARTIALLY_VALID,
    TASK_TYPE_VALID,
    classify_task_type,
    task_type_skip_reason,
)


def test_valid_when_f2p_and_zero_failures():
    assert (
        classify_task_type(
            f2p=["tests/test_foo.py::test_bar"],
            test_patch_failures=0,
            test_patch_remediated=False,
        )
        == TASK_TYPE_VALID
    )


def test_partially_valid_when_f2p_zero_failures_and_test_patch_agent():
    assert (
        classify_task_type(
            f2p=["tests/test_foo.py::test_bar"],
            test_patch_failures=0,
            test_patch_remediated=True,
        )
        == TASK_TYPE_PARTIALLY_VALID
    )


def test_partially_valid_when_llm_created_test_patch():
    assert (
        classify_task_type(
            f2p=["tests/test_foo.py::test_bar"],
            test_patch_failures=0,
            test_patch_created_by_llm=True,
        )
        == TASK_TYPE_PARTIALLY_VALID
    )


def test_skip_when_failures_remain_even_if_agent_ran():
    assert (
        classify_task_type(
            f2p=["tests/test_foo.py::test_bar"],
            test_patch_failures=1,
            test_patch_remediated=True,
        )
        == ""
    )


def test_skip_when_f2p_but_failures_without_agent():
    assert (
        classify_task_type(
            f2p=["tests/test_foo.py::test_bar"],
            test_patch_failures=62,
            test_patch_remediated=False,
        )
        == ""
    )


def test_empty_f2p_returns_blank():
    assert classify_task_type(f2p=[], test_patch_failures=0) == ""


def test_skip_when_install_or_apply_failed():
    assert (
        classify_task_type(
            f2p=["tests/test_foo.py::test_bar"],
            test_patch_failures=0,
            install_or_apply_failed=True,
        )
        == ""
    )
    assert (
        classify_task_type(
            f2p=[],
            test_patch_failures=0,
            test_patch_created_by_llm=True,
            install_or_apply_failed=True,
        )
        == ""
    )


def test_skip_reason_install_failed():
    assert (
        task_type_skip_reason(
            f2p=[],
            test_patch_failures=0,
            install_or_apply_failed=True,
        )
        == "install or patch apply failed on last Docker run"
    )


def test_skip_reason_after_llm_test_patch_still_failing():
    reason = task_type_skip_reason(
        f2p=["t"],
        test_patch_failures=3,
        test_patch_remediated=True,
    )
    assert "after LLM test_patch edits" in reason
