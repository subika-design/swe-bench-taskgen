"""FAIL_TO_PASS / PASS_TO_PASS classification for Docker discover."""

from pathlib import Path

from swe_rebench_pr.docker_discover import _compute_f2p_p2p
from swe_rebench_pr.swebench_align import canonicalize_java_gradle_test_maps


def test_f2p_when_absent_at_base_passing_after_patch():
    base_map: dict[str, str] = {}
    patch_map = {"com.example.FooTests > bar": "PASSED"}
    f2p, p2p = _compute_f2p_p2p(base_map, patch_map, Path("/tmp"), "java")
    assert f2p == ["com.example.FooTests > bar"]
    assert p2p == []


def test_f2p_when_failed_at_base_passing_after_patch():
    base_map = {"com.example.FooTests > bar": "FAILED"}
    patch_map = {"com.example.FooTests > bar": "PASSED"}
    f2p, p2p = _compute_f2p_p2p(base_map, patch_map, Path("/tmp"), "java")
    assert f2p == ["com.example.FooTests > bar"]
    assert p2p == []


def test_f2p_vitest_style_nodeid_with_spaces_in_name():
    """Vitest names may contain spaces after ``::``; still gradable for javascript."""
    key = "tests/foo.test.ts::suite > should work"
    base_map = {key: "FAILED"}
    patch_map = {key: "PASSED"}
    f2p, p2p = _compute_f2p_p2p(base_map, patch_map, Path("/tmp"), "javascript")
    assert f2p == [key]
    assert p2p == []


def test_p2p_only_when_passing_in_both_runs():
    key = "com.example.FooTests > bar"
    base_map = {key: "PASSED"}
    patch_map = {key: "PASSED"}
    f2p, p2p = _compute_f2p_p2p(base_map, patch_map, Path("/tmp"), "java")
    assert f2p == []
    assert p2p == [key]


def test_canonicalize_does_not_cross_contaminate_phases():
    """Second-phase Gradle log must not mark base tests as PASSED."""
    cls = "org.springframework.boot.ssl.SslOptionsTests"
    patch_log = "\n".join(f"{cls} > method{i} PASSED" for i in range(1, 4))
    base_map = canonicalize_java_gradle_test_maps({}, "")
    patch_map = canonicalize_java_gradle_test_maps(
        {f"{cls} > method{i}": "passed" for i in range(1, 4)},
        patch_log,
    )
    f2p, p2p = _compute_f2p_p2p(base_map, patch_map, Path("/tmp"), "java")
    assert len(f2p) == 3
    assert p2p == []
