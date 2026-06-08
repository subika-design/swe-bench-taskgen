"""Tests for Java Gradle/Maven detection."""

from pathlib import Path

from swe_rebench_pr.java_build import (
    gradle_default_build_install_command,
    gradle_junit_report_roots,
    install_cmd_is_gradle_chmod_only,
    GRADLE_HARNESS_INIT_REL,
    _GRADLE_HARNESS_INIT_CONTENT,
    detect_java_build_system,
    detect_maven_compiler_major,
    detect_required_java_major_version,
    eclipse_temurin_docker_image,
    ensure_java_docker_specs,
    infer_gradle_module_from_test_path,
    infer_maven_module_from_test_path,
    install_cmd_is_noop,
    install_config_remediation_unchanged,
    java_fqcn_from_test_path,
    java_install_config_for_repo,
    log_indicates_maven_tests_ran,
    log_indicates_maven_unsupported_compiler_source,
    merge_java_build_into_config,
    merge_java_harness_fields_after_llm,
    remediate_maven_compiler_jdk,
    repair_gradle_install_config_for_harness,
)
from swe_rebench_pr.swebench_align import export_install_config_for_harness


def test_detect_gradle_from_gradlew(tmp_path: Path):
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
    assert detect_java_build_system(tmp_path) == "gradle"


def test_detect_maven_from_pom(tmp_path: Path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    assert detect_java_build_system(tmp_path) == "maven"


def test_detect_java_build_none_without_markers(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"name":"app"}', encoding="utf-8")
    assert detect_java_build_system(tmp_path) is None


def test_merge_java_skips_non_java_repo(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"name":"app"}', encoding="utf-8")
    cfg = {"install": "npm ci || npm install", "test_cmd": "npm test -- --ci"}
    out = merge_java_build_into_config(cfg, tmp_path, ["src/foo.test.js"])
    assert out == cfg


def test_java_fqcn_from_test_path():
    p = "spring-boot-project/spring-boot-docs/src/test/java/org/example/FooTest.java"
    assert java_fqcn_from_test_path(p) == "org.example.FooTest"


def test_infer_gradle_module_spring_style():
    p = "spring-boot-project/spring-boot-docs/src/test/java/com/example/FooTest.java"
    assert infer_gradle_module_from_test_path(p) == "spring-boot-project:spring-boot-docs"


def test_detect_java_25_from_java_conventions(tmp_path: Path):
    conv = tmp_path / "buildSrc/src/main/java/org/example/JavaConventions.java"
    conv.parent.mkdir(parents=True)
    conv.write_text("public static final int BUILD_JAVA_VERSION = 25;\n", encoding="utf-8")
    assert detect_required_java_major_version(tmp_path) == 25
    assert eclipse_temurin_docker_image(25) == "eclipse-temurin:25-jdk-jammy"


def test_java_install_config_uses_detected_java_major(tmp_path: Path):
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    conv = tmp_path / "buildSrc/src/main/java/x/JavaConventions.java"
    conv.parent.mkdir(parents=True)
    conv.write_text("int BUILD_JAVA_VERSION = 25;\n", encoding="utf-8")
    cfg = java_install_config_for_repo(
        tmp_path, test_paths=["module/foo/src/test/java/org/example/FooTest.java"]
    )
    assert cfg["docker_image"] == "eclipse-temurin:25-jdk-jammy"
    assert cfg["docker_specs"]["java_version"] == "25"


def test_java_install_config_gradle(tmp_path: Path):
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "build.gradle").write_text("", encoding="utf-8")
    paths = ["spring-boot-project/spring-boot-docs/src/test/java/FooTest.java"]
    cfg = java_install_config_for_repo(tmp_path, test_paths=paths)
    assert cfg["java_build_system"] == "gradle"
    assert "temurin" in cfg["docker_image"]
    assert "gradlew" in cfg["install"]
    assert ":spring-boot-project:spring-boot-docs:compileTestJava" in cfg["post_install"][0]
    assert "base64 -d" in cfg["pre_install"][-1]
    assert " -q " not in f" {cfg['test_cmd']} "
    assert "--configure-on-demand" in cfg["test_cmd"]
    assert "base64 -d" in cfg["test_cmd"]
    assert "SWEBENCH_GRADLE_LOGGING_EOF" not in cfg["test_cmd"]
    assert "descriptor.className" in _GRADLE_HARNESS_INIT_CONTENT


def test_repair_gradle_strips_quiet_and_adds_harness_flags():
    old = {
        "java_build_system": "gradle",
        "test_cmd": "./gradlew --no-daemon -q :core:spring-boot:test --tests 'Foo' || true",
        "post_install": ["./gradlew --no-daemon -q :core:spring-boot:compileTestJava -x check || true"],
        "pre_install": ["apt-get update -qq"],
    }
    fixed = repair_gradle_install_config_for_harness(old)
    assert " -q " not in f" {fixed['test_cmd']} "
    assert "--configure-on-demand" in fixed["test_cmd"]
    assert GRADLE_HARNESS_INIT_REL in fixed["test_cmd"]
    assert any("base64 -d" in str(x) for x in fixed["pre_install"])
    assert "SWEBENCH_GRADLE_LOGGING_EOF" not in fixed["test_cmd"]


def test_ensure_java_docker_specs_after_llm_strips_specs():
    before = {
        "language": "java",
        "java_build_system": "gradle",
        "test_cmd": "./gradlew :core:spring-boot:test --tests 'Foo'",
        "docker_specs": {"java_version": "25"},
        "docker_image": "eclipse-temurin:25-jdk-jammy",
    }
    llm_out = {
        "python": "3.11",
        "install": "pip install -e .",
        "test_cmd": "pytest -rA",
        "pip_packages": ["pytest"],
    }
    merged = merge_java_harness_fields_after_llm(before, llm_out)
    assert merged["docker_specs"]["java_version"] == "25"
    assert "./gradlew" in merged["test_cmd"]
    exported = export_install_config_for_harness(merged, language="java")
    assert exported["docker_specs"]["java_version"] == "25"


def test_ensure_java_docker_specs_infers_from_gradle_test_cmd():
    cfg = {"test_cmd": "./gradlew :foo:test", "java_build_system": "gradle"}
    out = ensure_java_docker_specs(cfg)
    assert out["docker_specs"]["java_version"] == "17"


def test_merge_java_overrides_maven_defaults(tmp_path: Path):
    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    cfg = merge_java_build_into_config(
        {"install": "mvn test", "docker_image": "maven:3.9-eclipse-temurin-17"},
        tmp_path,
        [],
    )
    assert cfg["java_build_system"] == "gradle"
    assert "gradlew" in cfg["install"]


def test_install_cmd_is_noop():
    assert install_cmd_is_noop("# comment only")
    assert not install_cmd_is_noop("./gradlew test")


def test_infer_maven_module_from_gson_path():
    p = "gson/src/test/java/com/google/gson/FieldNamingPolicyTest.java"
    assert infer_maven_module_from_test_path(p) == "gson"


def test_java_install_config_maven_scoped(tmp_path: Path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    paths = ["gson/src/test/java/com/google/gson/FooTest.java"]
    cfg = java_install_config_for_repo(tmp_path, test_paths=paths)
    assert cfg["java_build_system"] == "maven"
    assert "-pl gson" in cfg["install"]
    assert "-pl gson" in cfg["test_cmd"]
    assert cfg["maven_junit_roots"] == ["gson/target/surefire-reports"]


def test_merge_java_build_maven_repo(tmp_path: Path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    paths = ["gson/src/test/java/com/google/gson/FooTest.java"]
    cfg = merge_java_build_into_config({"install": "mvn test"}, tmp_path, paths)
    assert "-pl gson" in cfg["test_cmd"]
    assert cfg.get("maven_junit_roots")


def test_log_indicates_maven_tests_ran():
    log = "[docker] mvn test (before patch)\nTests run: 42, Failures: 0"
    assert log_indicates_maven_tests_ran(log)


def test_install_config_remediation_unchanged():
    cfg = {"install": "mvn test", "test_cmd": "mvn -pl gson test"}
    assert install_config_remediation_unchanged(cfg, dict(cfg))
    assert not install_config_remediation_unchanged(cfg, {**cfg, "test_cmd": "mvn -pl other test"})
    assert not install_config_remediation_unchanged(
        {**cfg, "docker_image": "maven:3.9-eclipse-temurin-17"},
        {**cfg, "docker_image": "maven:3.9-eclipse-temurin-8"},
    )


def test_detect_maven_compiler_major_from_pom(tmp_path: Path):
    (tmp_path / "pom.xml").write_text(
        "<project><properties>"
        "<maven.compiler.source>1.6</maven.compiler.source>"
        "<maven.compiler.target>1.6</maven.compiler.target>"
        "</properties></project>",
        encoding="utf-8",
    )
    assert detect_maven_compiler_major(tmp_path) == 6


def test_java_install_config_maven_java6_uses_jdk8(tmp_path: Path):
    (tmp_path / "pom.xml").write_text(
        "<project><properties><maven.compiler.source>1.6</maven.compiler.source></properties></project>",
        encoding="utf-8",
    )
    paths = ["gson/src/test/java/com/google/gson/FooTest.java"]
    cfg = java_install_config_for_repo(tmp_path, test_paths=paths)
    assert cfg["docker_image"] == "maven:3.9-eclipse-temurin-8"
    assert cfg["docker_specs"]["java_version"] == "8"
    assert "maven.compiler.source=1.6" in cfg["install"]
    assert "maven.compiler.source=1.6" in cfg["test_cmd"]
    assert "-pl gson" in cfg["install"]


def test_remediate_maven_compiler_from_log(tmp_path: Path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    paths = ["gson/src/test/java/com/google/gson/FooTest.java"]
    cfg = {"java_build_system": "maven", "docker_image": "maven:3.9-eclipse-temurin-17", "install": "mvn test"}
    log = "error: Source option 6 is no longer supported. Use 8 or later."
    fixed = remediate_maven_compiler_jdk(cfg, tmp_path, paths, log_tail=log)
    assert fixed["docker_image"] == "maven:3.9-eclipse-temurin-8"
    assert "maven.compiler.source=1.6" in fixed["install"]


def test_log_indicates_maven_unsupported_compiler_source():
    assert log_indicates_maven_unsupported_compiler_source("Source option 6 is no longer supported")


def test_merge_java_preserves_jdk8_and_compiler_flags():
    before = {
        "language": "java",
        "java_build_system": "maven",
        "docker_image": "maven:3.9-eclipse-temurin-8",
        "docker_specs": {"java_version": "8"},
        "install": "mvn -q -Dmaven.compiler.source=1.6 -Dmaven.compiler.target=1.6 -DskipTests install -pl gson -am",
        "test_cmd": "mvn -q -Dmaven.compiler.source=1.6 test -pl gson -am || true",
    }
    llm_out = {
        "python": "3.11",
        "docker_image": "maven:3.9-eclipse-temurin-17",
        "install": "mvn -q -DskipTests install -pl gson -am",
        "test_cmd": "mvn -q test -pl gson -am || true",
    }
    merged = merge_java_harness_fields_after_llm(before, llm_out)
    assert merged["docker_image"] == "maven:3.9-eclipse-temurin-8"
    assert "maven.compiler.source=1.6" in merged["install"]
    assert "maven.compiler.source=1.6" in merged["test_cmd"]


def test_install_cmd_is_gradle_chmod_only():
    assert install_cmd_is_gradle_chmod_only("chmod +x ./gradlew 2>/dev/null || true")
    assert not install_cmd_is_gradle_chmod_only(
        gradle_default_build_install_command()
    )


def test_java_install_config_gradle_includes_build(tmp_path: Path):
    from swe_rebench_pr.java_gradle_llm import resolve_gradle_projects_for_test_paths

    (tmp_path / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "settings.gradle").write_text(
        "rootProject.name = 'picocli'\ninclude 'picocli-tests-java8'\n",
        encoding="utf-8",
    )
    test_dir = tmp_path / "picocli-tests-java8" / "src" / "test" / "java" / "picocli"
    test_dir.mkdir(parents=True)
    (test_dir / "AutoCompleteTest.java").write_text("package picocli;\n", encoding="utf-8")
    paths = ["picocli-tests-java8/src/test/java/picocli/AutoCompleteTest.java"]
    gradle_map = resolve_gradle_projects_for_test_paths(tmp_path, paths, api_key=None)
    cfg = java_install_config_for_repo(
        tmp_path, test_paths=paths, gradle_path_by_test_path=gradle_map
    )
    assert "build -x test" in cfg["install"]
    assert "-x check" in cfg["install"]
    assert cfg["gradle_junit_roots"] == ["picocli-tests-java8/build/test-results"]
    assert ":picocli-tests-java8:test" in cfg["test_cmd"]
    assert ":picocli:test" not in cfg["test_cmd"]


def test_merge_java_harness_restores_build_install_after_chmod_llm():
    before = {
        "language": "java",
        "java_build_system": "gradle",
        "install": gradle_default_build_install_command(),
        "post_install": ["./gradlew :picocli:compileTestJava -x check || true"],
        "test_cmd": "./gradlew :picocli:test --tests 'picocli.Foo'",
    }
    llm_out = {
        "install": "chmod +x ./gradlew 2>/dev/null || true",
        "post_install": [],
        "test_cmd": "pytest -rA",
    }
    merged = merge_java_harness_fields_after_llm(before, llm_out)
    assert "build -x test" in merged["install"]
    assert merged.get("post_install")
    assert "./gradlew" in merged["test_cmd"]
