from swe_rebench_pr.java_build import suggest_test_paths_from_impl_patch


def test_suggest_java_test_path_from_main():
    patch = """diff --git a/foo/src/main/java/com/example/DemoService.java b/foo/src/main/java/com/example/DemoService.java
--- a/foo/src/main/java/com/example/DemoService.java
+++ b/foo/src/main/java/com/example/DemoService.java
"""
    paths = suggest_test_paths_from_impl_patch("java", patch)
    assert paths == ["foo/src/test/java/com/example/DemoServiceTests.java"]
