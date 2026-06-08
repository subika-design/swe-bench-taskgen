"""Java JUnit nodeid alignment with test_patch paths."""

from swe_rebench_pr.diff_split import (
    has_test_patch_label_mismatch,
    java_fqcn_from_test_path,
    junit_outcome_counts_for_paths,
)
from swe_rebench_pr.java_build import java_install_config_for_repo


def test_java_fqcn_from_test_path():
    p = (
        "spring-boot-project/spring-boot-docs/src/test/java/"
        "org/springframework/boot/docs/howto/webserver/"
        "enablemultipleconnectors/jetty/MyJettyConfigurationTests.java"
    )
    fqcn = java_fqcn_from_test_path(p)
    assert fqcn == (
        "org.springframework.boot.docs.howto.webserver."
        "enablemultipleconnectors.jetty.MyJettyConfigurationTests"
    )


def test_junit_matches_java_test_patch_path():
    tp = [
        "spring-boot-project/spring-boot-docs/src/test/java/"
        "org/springframework/boot/docs/howto/webserver/"
        "enablemultipleconnectors/jetty/MyJettyConfigurationTests.java"
    ]
    case_map = {
        (
            "org/springframework/boot/docs/howto/webserver/"
            "enablemultipleconnectors/jetty/MyJettyConfigurationTests.py::contextLoads()"
        ): "passed",
        "org/springframework/boot/build/ConventionsPluginTests.py::jarIncludesLegalFiles()": "passed",
    }
    assert has_test_patch_label_mismatch(case_map, tp) is False
    pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(case_map, tp)
    assert tot == 1 and pa == 1 and fa == 0


def test_gradle_test_cmd_scoped_to_fqcn(tmp_path):
    from pathlib import Path

    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("", encoding="utf-8")
    paths = [
        "spring-boot-project/spring-boot-docs/src/test/java/"
        "org/springframework/boot/docs/FooTests.java"
    ]
    cfg = java_install_config_for_repo(tmp_path, test_paths=paths)
    assert ":spring-boot-project:spring-boot-docs:test" in cfg["test_cmd"]
    assert "--tests 'org.springframework.boot.docs.FooTests'" in cfg["test_cmd"]
