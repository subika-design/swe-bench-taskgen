"""Tests for git patch validation."""

from pathlib import Path

from swe_rebench_pr.patch_validate import validate_git_patch


def test_validate_rejects_empty():
    ok, err = validate_git_patch("", Path("/tmp"))
    assert not ok
    assert err


def test_validate_rejects_without_diff_git():
    ok, err = validate_git_patch("hello world\n", Path("/tmp"))
    assert not ok
    assert err


def test_validate_reports_apply_errors(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    patch = """diff --git a/tests/FooTest.java b/tests/FooTest.java
new file mode 100644
--- /dev/null
+++ b/tests/FooTest.java
@@ -0,0 +1,3 @@
+class FooTest {
+}
"""
    ok, err = validate_git_patch(patch, repo)
    if not ok:
        assert err
