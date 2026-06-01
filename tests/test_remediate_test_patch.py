"""Regression tests for test_patch apply-check remediation."""

from pathlib import Path
from unittest.mock import patch

from swe_rebench_pr.test_patch_llm import remediate_test_patch_until_applies


def test_remediate_does_not_reference_candidate_before_assignment(tmp_path: Path):
    repo = tmp_path
    bad_patch = (
        "diff --git a/missing/b/File.java b/missing/b/File.java\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/missing/b/File.java\n"
        "@@ -0,0 +99999 @@\n"
        "+// corrupt hunk counts\n"
    )

    with (
        patch(
            "swe_rebench_pr.test_patch_llm.validate_git_patch",
            side_effect=[(False, "corrupt patch"), (False, "still bad")],
        ),
        patch(
            "swe_rebench_pr.test_patch_llm.llm_fix_test_patch_apply_check",
            return_value=bad_patch,
        ) as mock_fix,
    ):
        _, ok, _ = remediate_test_patch_until_applies(
            bad_patch,
            repo,
            api_key="k",
            base_url="http://localhost",
            model="m",
            timeout_s=1,
            max_attempts=2,
        )
    assert ok is False
    mock_fix.assert_called_once()
    assert "previous_edit_unchanged" in mock_fix.call_args.kwargs
