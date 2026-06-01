from swe_rebench_pr.java_build import gradle_junit_report_roots


def test_gradle_junit_report_roots_from_test_path():
    tp = "module/spring-boot-jms/src/test/java/org/example/FooTests.java"
    assert gradle_junit_report_roots([tp]) == ["module/spring-boot-jms/build/test-results"]
