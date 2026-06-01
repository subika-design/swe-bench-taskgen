"""Tests for harness versioning."""

from swe_rebench_pr.versioning import harness_version_for_instance


def test_harness_version_uses_pr_number_for_all_languages():
    iid = "pygments__pygments-3107"
    assert harness_version_for_instance(iid, "python", "1.2") == "3107"
    assert harness_version_for_instance(iid, "javascript", "1.2") == "3107"
    assert harness_version_for_instance(iid, "ruby", "1.2") == "3107"


def test_harness_version_fallback_without_pr_suffix():
    assert harness_version_for_instance("foo__bar", "python", "0.0-abc12345") == "0.0-abc12345"
