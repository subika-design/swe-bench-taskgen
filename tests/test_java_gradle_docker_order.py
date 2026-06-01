"""Gradle Docker entry applies test_patch before impl.patch (SWE-bench semantics)."""

from swe_rebench_pr.docker_entry import _java_gradle_body


def test_java_gradle_docker_applies_test_patch_before_impl():
    body = _java_gradle_body({"test_cmd": "./gradlew test", "gradle_junit_roots": []}, False)
    assert "gradle test (base + test_patch only)" in body
    assert "gradle test (test_patch + impl.patch)" in body
    assert "git reset --hard HEAD" in body
    assert "git clean -ffdx" in body
    first_test_apply = body.index("_apply_one /w/test.patch")
    reset_pos = body.index("git reset --hard HEAD")
    second_test_apply = body.index("_apply_one /w/test.patch", reset_pos)
    impl_apply = body.index("_apply_one /w/impl.patch", reset_pos)
    assert first_test_apply < reset_pos < second_test_apply < impl_apply
    assert "junit-base.xml" in body[:reset_pos]
    assert "junit-patch.xml" in body[reset_pos:]
    fn_def = body.index("_apply_one() {")
    assert fn_def < first_test_apply


def test_apply_patches_block_defines_apply_one():
    from swe_rebench_pr.docker_entry import _apply_patches_block

    body = _apply_patches_block()
    assert "_apply_one() {" in body
    assert body.index("_apply_one() {") < body.index("_apply_one /w/impl.patch")
