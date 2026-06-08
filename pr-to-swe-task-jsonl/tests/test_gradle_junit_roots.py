from swe_rebench_pr.java_build import gradle_junit_report_roots


def test_gradle_junit_report_roots_from_test_path():
    tp = "module/spring-boot-jms/src/test/java/org/example/FooTests.java"
    assert gradle_junit_report_roots([tp]) == ["module/spring-boot-jms/build/test-results"]


def test_gradle_junit_report_roots_picocli_subproject(tmp_path: Path):
    from swe_rebench_pr.java_gradle_llm import resolve_gradle_projects_for_test_paths

    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\ninclude 'picocli-tests-java8'\n",
        encoding="utf-8",
    )
    test_dir = tmp_path / "picocli-tests-java8" / "src" / "test" / "java" / "picocli"
    test_dir.mkdir(parents=True)
    (test_dir / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    tp = "picocli-tests-java8/src/test/java/picocli/AutoCompleteTest.java"
    mapping = resolve_gradle_projects_for_test_paths(tmp_path, [tp], api_key=None)
    assert gradle_junit_report_roots(
        [tp], gradle_path_by_test_path=mapping, repo=tmp_path
    ) == ["picocli-tests-java8/build/test-results"]
