"""Java/Gradle vs Maven detection and Docker-replayable install recipes."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

_BUILD_JAVA_VERSION_RE = re.compile(r"BUILD_JAVA_VERSION\s*=\s*(\d+)")
_JAVA_TOOLCHAIN_RE = re.compile(
    r"(?:toolchainVersion|javaToolchainVersion|javaVersion)\s*=\s*(\d+)",
    re.IGNORECASE,
)
_JAVA_VERSION_PROP_RE = re.compile(r"(?:^|\n)\s*java\.version\s*=\s*(\d+)")
_MAVEN_JAVA_RELEASE_RE = re.compile(
    r"<(?:java\.version|maven\.compiler\.release)>\s*(\d+)\s*</",
    re.IGNORECASE,
)
_MAVEN_COMPILER_IN_POM_RE = re.compile(
    r"<maven\.compiler\.(?:source|target)>\s*([^<]+?)\s*</",
    re.IGNORECASE,
)
_MAVEN_UNSUPPORTED_SOURCE_RE = re.compile(
    r"source option (\d+) is no longer supported",
    re.IGNORECASE,
)
_SUPPORTED_JAVA_MAJORS = frozenset({8, 11, 17, 21, 22, 23, 24, 25})


def detect_required_java_major_version(repo: Path) -> int:
    """
    Minimum JDK major version required to compile/run tests in ``repo``.

    Spring Boot 4.x+ encodes this as ``BUILD_JAVA_VERSION`` in ``JavaConventions``.
    Defaults to 17 when nothing is found.
    """
    for path in sorted(repo.glob("buildSrc/**/JavaConventions.java")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:40_000]
        except OSError:
            continue
        m = _BUILD_JAVA_VERSION_RE.search(text)
        if m:
            return int(m.group(1))

    gp = repo / "gradle.properties"
    if gp.is_file():
        try:
            text = gp.read_text(encoding="utf-8", errors="replace")[:20_000]
        except OSError:
            text = ""
        for pat in (_JAVA_TOOLCHAIN_RE, _JAVA_VERSION_PROP_RE):
            m = pat.search(text)
            if m:
                return int(m.group(1))

    jv = repo / ".java-version"
    if jv.is_file():
        try:
            raw = jv.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            raw = ""
        if raw:
            major = int(raw.split(".", 1)[0])
            if major in _SUPPORTED_JAVA_MAJORS:
                return major

    pom = repo / "pom.xml"
    if pom.is_file():
        try:
            text = pom.read_text(encoding="utf-8", errors="replace")[:40_000]
        except OSError:
            text = ""
        m = _MAVEN_JAVA_RELEASE_RE.search(text)
        if m:
            return int(m.group(1))

    return 17


def eclipse_temurin_docker_image(java_major: int) -> str:
    """Docker image for Java builds (SWE-bench harness + discover)."""
    major = int(java_major)
    if major not in _SUPPORTED_JAVA_MAJORS:
        major = 17
    return f"eclipse-temurin:{major}-jdk-jammy"


def detect_java_build_system(repo: Path) -> str | None:
    """Return ``gradle`` or ``maven`` from repo markers, or ``None`` if not a Java project."""
    if (repo / "gradlew").is_file():
        return "gradle"
    if (repo / "build.gradle").is_file() or (repo / "build.gradle.kts").is_file():
        return "gradle"
    if (repo / "pom.xml").is_file():
        return "maven"
    for child in repo.iterdir():
        if not child.is_dir():
            continue
        if (child / "build.gradle").is_file() or (child / "build.gradle.kts").is_file():
            return "gradle"
    return None


def infer_gradle_module_from_test_path(path: str) -> str | None:
    """
    Map a test file path to a Gradle project path (``:a:b`` style).

    ``spring-boot-project/spring-boot-docs/src/test/java/Foo.java``
    -> ``spring-boot-project:spring-boot-docs``
    """
    prefix = module_prefix_before_java_test(path)
    if prefix is None:
        return None
    if prefix == "":
        return ""
    parts = [x for x in prefix.split("/") if x]
    if len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    if len(parts) == 1:
        return parts[0]
    return ""


def module_prefix_before_java_test(path: str) -> str | None:
    """
    Directory prefix before ``src/test/``; ``""`` for repo-root ``src/test/`` layout.

    Returns ``None`` when the path is not a Java test path.
    """
    p = path.replace("\\", "/").strip().lstrip("/")
    if p.startswith("src/test/") or p.startswith("src/intTest/"):
        return ""
    if "/src/test/" in p:
        return p.split("/src/test/", 1)[0].strip("/")
    if "/src/intTest/" in p:
        return p.split("/src/intTest/", 1)[0].strip("/")
    return None


def suggest_test_paths_from_impl_patch(language: str, patch: str, *, max_paths: int = 12) -> list[str]:
    """
    When the PR has no test files, suggest where new tests should live (from production paths).
    """
    lang = (language or "").strip().lower()
    suggested: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"^diff --git a/(\S+) b/\1$", patch or "", re.MULTILINE):
        path = m.group(1).replace("\\", "/")
        if path in seen:
            continue
        if lang == "java" and "/src/main/java/" in path and path.endswith(".java"):
            pkg_path = path.split("/src/main/java/", 1)[1]
            class_file = pkg_path.rsplit("/", 1)[-1]
            if class_file.endswith(".java"):
                test_class = class_file[:-5] + "Tests.java"
                test_path = path.replace("/src/main/java/", "/src/test/java/").replace(
                    class_file, test_class
                )
                if test_path not in seen:
                    seen.add(test_path)
                    suggested.append(test_path)
        if len(suggested) >= max_paths:
            break
    return suggested


def infer_maven_module_from_test_path(path: str) -> str | None:
    """
    Map a test file path to a Maven module directory name.

    ``gson/src/test/java/com/google/gson/Foo.java`` -> ``gson``
    """
    p = path.replace("\\", "/").strip()
    if "/src/test/" not in p:
        return None
    prefix = p.split("/src/test/", 1)[0]
    parts = [x for x in prefix.split("/") if x]
    return parts[-1] if parts else None


def maven_modules_from_test_paths(test_paths: list[str]) -> list[str]:
    mods: set[str] = set()
    for p in test_paths:
        m = infer_maven_module_from_test_path(p)
        if m:
            mods.add(m)
    return sorted(mods)


def maven_junit_report_roots(test_paths: list[str]) -> list[str]:
    mods = maven_modules_from_test_paths(test_paths)
    if mods:
        return [f"{m}/target/surefire-reports" for m in mods]
    return ["target/surefire-reports"]


def _parse_maven_compiler_token(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("1."):
        try:
            return int(raw.split(".", 1)[1])
        except ValueError:
            return None
    try:
        return int(raw.split(".", 1)[0])
    except ValueError:
        return None


def detect_maven_compiler_major(repo: Path) -> int | None:
    """
    Oldest ``maven.compiler.source`` / ``target`` across root and child ``pom.xml`` files.

    Returns the minimum major (e.g. 6 for ``1.6``) so bytecode level is not underestimated.
    """
    majors: list[int] = []
    poms = [repo / "pom.xml", *sorted(repo.glob("*/pom.xml"))[:24]]
    for pom in poms:
        if not pom.is_file():
            continue
        try:
            text = pom.read_text(encoding="utf-8", errors="replace")[:80_000]
        except OSError:
            continue
        for m in _MAVEN_COMPILER_IN_POM_RE.finditer(text):
            v = _parse_maven_compiler_token(m.group(1))
            if v is not None:
                majors.append(v)
    if not majors:
        return None
    return min(majors)


def maven_runtime_jdk_major(compiler_major: int | None) -> int:
    """JDK major to run Maven with for a given compiler source level."""
    if compiler_major is None:
        return 17
    if compiler_major <= 8:
        return 8
    if compiler_major in _SUPPORTED_JAVA_MAJORS:
        return compiler_major
    return 17


def maven_docker_image(jdk_major: int) -> str:
    major = int(jdk_major)
    if major not in _SUPPORTED_JAVA_MAJORS:
        major = 17
    if major <= 8:
        major = 8
    return f"maven:3.9-eclipse-temurin-{major}"


def _maven_compiler_dm_flags(compiler_major: int | None) -> str:
    if compiler_major is None or compiler_major > 8:
        return ""
    if compiler_major <= 6:
        src = "1.6"
    elif compiler_major == 7:
        src = "1.7"
    else:
        src = "1.8"
    return f"-Dmaven.compiler.source={src} -Dmaven.compiler.target={src} "


def _maven_install_cmd(modules: list[str], *, compiler_major: int | None = None) -> str:
    dm = _maven_compiler_dm_flags(compiler_major)
    if modules:
        pl = ",".join(modules)
        return f"mvn -q {dm}-DskipTests install -pl {pl} -am"
    return f"mvn -q {dm}-DskipTests package || mvn -q {dm}-DskipTests compile"


def _maven_test_cmd(
    modules: list[str],
    test_paths: list[str] | None = None,
    *,
    compiler_major: int | None = None,
) -> str:
    dm = _maven_compiler_dm_flags(compiler_major)
    flags = "-Dmaven.test.failure.ignore=true"
    paths = list(test_paths or [])
    if modules:
        pl = ",".join(modules)
        cmd = f"mvn -q {dm}test -pl {pl} -am {flags}"
    else:
        cmd = f"mvn -q {dm}test {flags}"
    fqcns = sorted({f for f in (java_fqcn_from_test_path(p) for p in paths) if f})
    if len(fqcns) == 1:
        simple = fqcns[0].rsplit(".", 1)[-1]
        cmd += f" -Dtest={simple}"
    return cmd + " || true"


def gradle_modules_from_test_paths(
    test_paths: list[str],
    *,
    gradle_path_by_test_path: dict[str, str] | None = None,
) -> list[str]:
    mods: set[str] = set()
    mapping = gradle_path_by_test_path or {}
    for p in test_paths:
        norm = p.replace("\\", "/")
        gp = mapping.get(norm)
        if gp:
            mods.add(gp.lstrip(":"))
            continue
        m = infer_gradle_module_from_test_path(p)
        if m:
            mods.add(m)
    return sorted(mods)


GRADLE_HARNESS_INIT_REL = "gradle/swebench-harness-logging.init.gradle"

_GRADLE_HARNESS_INIT_CONTENT = r"""import org.gradle.api.tasks.testing.Test
import org.gradle.api.tasks.testing.TestDescriptor
import org.gradle.api.tasks.testing.TestListener
import org.gradle.api.tasks.testing.TestResult

allprojects {
  tasks.withType(Test).configureEach { task ->
    task.testLogging {
      events = []
      exceptionFormat org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL
      showStandardStreams = true
    }
    task.addTestListener(new TestListener() {
      void beforeSuite(TestDescriptor descriptor) {}
      void afterSuite(TestDescriptor descriptor, TestResult result) {}
      void beforeTest(TestDescriptor descriptor) {}
      void afterTest(TestDescriptor descriptor, TestResult result) {
        if (descriptor == null || descriptor.className == null || descriptor.className.isEmpty()) {
          return
        }
        def method = (descriptor.name ?: '').replaceAll(/\(\)$/, '')
        def status
        switch (result.resultType) {
          case TestResult.ResultType.SUCCESS:
            status = 'PASSED'
            break
          case TestResult.ResultType.FAILURE:
            status = 'FAILED'
            break
          case TestResult.ResultType.SKIPPED:
            status = 'SKIPPED'
            break
          default:
            status = result.resultType.toString()
        }
        println "${descriptor.className} > ${method} ${status}"
      }
    })
  }
}
"""


def _gradle_write_harness_logging_init_script() -> str:
    """
    Create init script so Gradle prints ``fqcn > method PASSED`` lines for SWE-bench.

    Must be a **single shell line** — ``eval.sh`` splits ``test_cmd`` on newlines, which
    breaks embedded heredocs.
    """
    payload = base64.b64encode(_GRADLE_HARNESS_INIT_CONTENT.encode()).decode()
    return (
        f"mkdir -p gradle && echo {payload} | base64 -d > {GRADLE_HARNESS_INIT_REL}"
    )


def _gradle_wrapper_flags() -> str:
    return (
        f"--no-daemon --configure-on-demand -I {GRADLE_HARNESS_INIT_REL}"
    )


def _gradle_compile_tasks(modules: list[str], *, repo: Path | None = None) -> str:
    from .java_gradle_llm import (
        discover_gradle_projects_from_settings,
        gradle_task_for_project,
    )

    flags = _gradle_wrapper_flags()
    if not modules:
        return f"./gradlew {flags} classes -x check || true"
    index = discover_gradle_projects_from_settings(repo) if repo is not None else None
    task_names: list[str] = []
    for m in modules:
        proj = f":{m}" if m else ":"
        if index is not None and repo is not None:
            task_names.append(
                gradle_task_for_project(proj, "compileTestJava", index, repo)
            )
        else:
            task_names.append(f":{m}:compileTestJava" if m else ":compileTestJava")
    tasks = " ".join(task_names)
    return f"./gradlew {flags} {tasks} -x check || true"


def gradle_default_build_install_command() -> str:
    """Docker-replayable compile install (CI ``./gradlew build`` style, tests skipped)."""
    from .ci_install_normalize import _normalize_gradlew_build_command

    return _normalize_gradlew_build_command("./gradlew clean build --no-daemon")


def install_cmd_is_gradle_chmod_only(install: str) -> bool:
    """True when ``install`` only chmods the wrapper without compiling."""
    s = (install or "").strip()
    if not s or install_cmd_is_noop(s):
        return True
    if "./gradlew" not in s and "gradlew" not in s:
        return False
    low = s.lower()
    return not any(tok in low for tok in ("build", "classes", "assemble", "compile", ":compile"))


def gradle_harness_install_and_post(
    modules: list[str],
    *,
    repo: Path | None = None,
) -> tuple[str, list[str]]:
    """Default Gradle ``install`` + ``post_install`` for harness discover."""
    install = gradle_default_build_install_command()
    post = [_gradle_compile_tasks(modules, repo=repo)] if modules else []
    return install, post


def format_java_harness_context_for_llm(
    test_paths: list[str],
    *,
    gradle_path_by_test_path: dict[str, str] | None = None,
    test_cmd: str = "",
) -> str:
    """Prompt block: how Docker runs Gradle and what class names must match."""
    paths = [p.replace("\\", "/") for p in test_paths if p.strip()]
    if not paths:
        return ""
    mapping = gradle_path_by_test_path or {}
    lines = [
        "## Java harness (Docker) — follow exactly",
        "",
        "After `git apply` of patches, tests run via **one Gradle command** (not pytest):",
    ]
    if test_cmd.strip():
        lines.append(f"```\n{test_cmd.strip()}\n```")
    else:
        lines.append(
            "`./gradlew :<gradle-project>:test --tests '<fully.qualified.ClassName>'`"
        )
    lines.extend(
        [
            "",
            "The **public top-level class name** must equal the filename (without `.java`).",
        "Docker runs `:module:test` with `--tests '<fully.qualified.ClassName>'` (exact class), "
        "and merges JUnit only from that module's `build/test-results/`.",
            "",
            "Per test file:",
        ]
    )
    for p in paths[:12]:
        gp = mapping.get(p, "(unresolved)")
        fqcn = java_fqcn_from_test_path(p) or "(invalid path)"
        simple = p.rsplit("/", 1)[-1]
        lines.append(f"- path: `{p}`")
        lines.append(f"  - Gradle project: `{gp}`")
        lines.append(f"  - Required `--tests` FQCN: `{fqcn}`")
        lines.append(f"  - Public class must be named: `{simple[:-5] if simple.endswith('.java') else simple}`")
    lines.extend(
        [
            "",
            "Rules:",
            "- **One** test file path in the diff — do not rename the file or class between attempts.",
            "- If the path **already exists** at base_commit → **MODIFY** diff only (`--- a/...` `+++ b/...`).",
            "- If impl.patch touches `spring-boot-docs` samples, prefer editing the **existing** "
            "`*Tests.java` in the same package (docs samples often pre-exist).",
            "- Add 1–2 `@Test` methods that **fail** with only test_patch applied and **pass** "
            "after impl.patch + test_patch.",
            "- Do not add tests under `buildSrc/` or root `build/` — those are not your test_patch slice.",
        ]
    )
    return "\n".join(lines)


def _gradle_test_filter_for_fqcn(fqcn: str) -> str:
    """Gradle ``--tests`` filter for a single test class (exact FQCN)."""
    return f"--tests '{fqcn}'"


def gradle_junit_report_roots(
    test_paths: list[str],
    *,
    gradle_path_by_test_path: dict[str, str] | None = None,
    repo: Path | None = None,
) -> list[str]:
    """Module-relative ``build/test-results`` dirs to merge (avoids buildSrc noise)."""
    from .java_gradle_llm import (
        _root_gradle_project,
        discover_gradle_projects_from_settings,
    )

    mapping = gradle_path_by_test_path or {}
    index = discover_gradle_projects_from_settings(repo) if repo is not None else None
    roots: set[str] = set()
    for p in test_paths:
        norm = p.replace("\\", "/")
        gp = mapping.get(norm)
        if gp and index is not None and repo is not None:
            root = _root_gradle_project(index, repo)
            if gp == root:
                roots.add("build/test-results")
            else:
                rel = gp.lstrip(":").replace(":", "/")
                roots.add(f"{rel}/build/test-results")
            continue
        prefix = module_prefix_before_java_test(p)
        if prefix is None:
            continue
        if prefix:
            roots.add(prefix + "/build/test-results")
        else:
            roots.add("build/test-results")
    return sorted(roots)


def java_fqcn_from_test_path(path: str) -> str | None:
    """``src/test/java/pkg/Foo.java`` -> ``pkg.Foo``."""
    p = path.replace("\\", "/").strip().lstrip("/")
    for marker in ("src/test/java/", "src/intTest/java/"):
        if marker in p and p.endswith(".java"):
            return p.split(marker, 1)[1][:-5].replace("/", ".")
    return None


def java_fqcn_from_test_path_on_disk(repo: Path, test_path: str) -> str | None:
    """FQCN from on-disk source (``package`` decl) when the file exists."""
    from .java_gradle_llm import _find_test_file_on_disk, java_fqcn_from_source_file

    disk = _find_test_file_on_disk(repo, test_path)
    if disk is not None:
        fqcn = java_fqcn_from_source_file(disk)
        if fqcn:
            return fqcn
        return java_fqcn_from_test_path(disk.relative_to(repo).as_posix())
    return java_fqcn_from_test_path(test_path)


def _gradle_test_cmd_prefix() -> str:
    """Shell prefix: write harness logging init script (runs each eval)."""
    return _gradle_write_harness_logging_init_script() + " && "


def _gradle_test_tasks(
    modules: list[str],
    test_paths: list[str] | None = None,
    *,
    gradle_path_by_test_path: dict[str, str] | None = None,
    repo: Path | None = None,
) -> str:
    from .java_gradle_llm import (
        discover_gradle_projects_from_settings,
        gradle_test_task_for_project,
    )

    paths = list(test_paths or [])
    mapping = gradle_path_by_test_path or {}
    index = discover_gradle_projects_from_settings(repo) if repo is not None else None
    by_proj: dict[str, list[str]] = {}
    for tp in paths:
        norm = tp.replace("\\", "/")
        gp = mapping.get(norm)
        if gp:
            proj = gp if gp.startswith(":") else f":{gp}"
        else:
            mod = infer_gradle_module_from_test_path(tp)
            proj = f":{mod}" if mod else ":"
        fqcn = (
            java_fqcn_from_test_path_on_disk(repo, tp)
            if repo is not None
            else java_fqcn_from_test_path(tp)
        )
        if fqcn is not None:
            by_proj.setdefault(proj, []).append(fqcn)
    flags = _gradle_wrapper_flags()
    gradle_props = "-Dorg.gradle.parallel=false --continue"
    if by_proj:
        chunks: list[str] = []
        for proj in sorted(by_proj, key=lambda p: p or ""):
            fqcns = sorted(set(by_proj[proj]))
            filters = " ".join(
                _gradle_test_filter_for_fqcn(fqcn) for fqcn in fqcns
            )
            if index is not None and repo is not None:
                task = gradle_test_task_for_project(proj, index, repo)
            else:
                tail = proj.lstrip(":")
                task = f":{tail}:test" if tail else ":test"
            chunks.append(f"{task} {filters}")
        body = f"./gradlew {flags} {' '.join(chunks)} {gradle_props} || true"
        return _gradle_test_cmd_prefix() + body
    if not modules:
        body = f"./gradlew {flags} :test {gradle_props} || true"
        return _gradle_test_cmd_prefix() + body
    if index is not None and repo is not None:
        tasks = " ".join(
            gradle_test_task_for_project(f":{m}" if m else ":", index, repo)
            for m in modules
        )
    else:
        tasks = " ".join(f":{m}:test" if m else ":test" for m in modules)
    body = f"./gradlew {flags} {tasks} {gradle_props} || true"
    return _gradle_test_cmd_prefix() + body


def java_install_config_for_repo(
    repo: Path,
    *,
    test_paths: list[str] | None = None,
    base: dict[str, Any] | None = None,
    gradle_path_by_test_path: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Heuristic ``install_config`` for Java repos (Gradle or Maven)."""
    from .languages import get_language_spec

    cfg = dict(base or get_language_spec("java").default_install_config)
    paths = list(test_paths or [])
    modules = gradle_modules_from_test_paths(
        paths, gradle_path_by_test_path=gradle_path_by_test_path
    )
    bs = detect_java_build_system(repo) or "maven"

    pre = [
        "apt-get update -qq",
        "apt-get install -y --no-install-recommends git python3 unzip curl",
        _gradle_write_harness_logging_init_script(),
    ]

    if bs == "gradle":
        java_major = detect_required_java_major_version(repo)
        cfg["java_build_system"] = "gradle"
        cfg["docker_image"] = eclipse_temurin_docker_image(java_major)
        cfg["pre_install"] = pre
        cfg["install"], cfg["post_install"] = gradle_harness_install_and_post(
            modules, repo=repo
        )
        cfg["test_cmd"] = _gradle_test_tasks(
            modules,
            paths,
            gradle_path_by_test_path=gradle_path_by_test_path,
            repo=repo,
        )
        cfg["gradle_junit_roots"] = gradle_junit_report_roots(
            paths,
            gradle_path_by_test_path=gradle_path_by_test_path,
            repo=repo,
        )
        cfg["docker_specs"] = {"java_version": str(java_major)}
        cfg["pip_packages"] = []
        cfg["reqs_path"] = []
        return cfg

    compiler_major = detect_maven_compiler_major(repo)
    jdk_major = maven_runtime_jdk_major(compiler_major)
    cfg["java_build_system"] = "maven"
    cfg["docker_image"] = maven_docker_image(jdk_major)
    cfg["docker_specs"] = {"java_version": str(jdk_major)}
    cfg["pre_install"] = [
        "apt-get update -qq",
        "apt-get install -y --no-install-recommends git python3 unzip",
    ]
    modules = maven_modules_from_test_paths(paths)
    cfg["install"] = _maven_install_cmd(modules, compiler_major=compiler_major)
    cfg["test_cmd"] = _maven_test_cmd(modules, paths, compiler_major=compiler_major)
    cfg["maven_junit_roots"] = maven_junit_report_roots(paths)
    cfg["post_install"] = []
    return cfg


def merge_java_build_into_config(
    cfg: dict[str, Any],
    repo: Path,
    test_paths: list[str],
    *,
    llm: tuple[str, str, str, int] | None = None,
    repo_id: str = "",
    instance_id: str = "",
    patch: str = "",
    test_patch: str = "",
    gradle_projects_output: str = "",
) -> dict[str, Any]:
    """Enrich Java install_config with Gradle or Maven heuristics from test paths."""
    bs = detect_java_build_system(repo)
    if bs is None:
        return cfg
    if bs == "maven":
        hinted = java_install_config_for_repo(repo, test_paths=test_paths, base=cfg)
        out = dict(cfg)
        for key in (
            "java_build_system",
            "docker_image",
            "docker_specs",
            "install",
            "test_cmd",
            "pre_install",
            "post_install",
            "maven_junit_roots",
        ):
            if key in hinted and hinted[key]:
                out[key] = hinted[key]
        return out
    if bs != "gradle":
        return cfg
    if str(cfg.get("java_build_system") or "").strip().lower() == "maven":
        return cfg
    gradle_map: dict[str, str] | None = None
    if test_paths:
        from .java_gradle_llm import resolve_gradle_projects_for_test_paths

        gradle_map = resolve_gradle_projects_for_test_paths(
            repo,
            test_paths,
            api_key=llm[0] if llm else None,
            base_url=llm[1] if llm else "",
            model=llm[2] if llm else "",
            timeout_s=llm[3] if llm else 120,
            repo_id=repo_id,
            instance_id=instance_id,
            patch=patch,
            test_patch=test_patch,
            gradle_projects_output=gradle_projects_output,
        )
    hinted = java_install_config_for_repo(
        repo,
        test_paths=test_paths,
        base=cfg,
        gradle_path_by_test_path=gradle_map,
    )
    out = dict(cfg)
    for key in (
        "java_build_system",
        "docker_image",
        "docker_specs",
        "install",
        "test_cmd",
        "pre_install",
        "post_install",
        "gradle_junit_roots",
    ):
        if key in hinted:
            out[key] = hinted[key]
    inst = str(out.get("install") or "").strip()
    hinted_inst = str(hinted.get("install") or "").strip()
    if install_cmd_is_gradle_chmod_only(inst) or install_cmd_is_noop(inst):
        if hinted_inst and not install_cmd_is_gradle_chmod_only(hinted_inst):
            out["install"] = hinted["install"]
        elif not out.get("_ci_install_trusted"):
            out["install"] = gradle_default_build_install_command()
        if hinted.get("post_install") and not out.get("post_install"):
            out["post_install"] = hinted["post_install"]
        elif modules and not out.get("post_install"):
            out["post_install"] = [_gradle_compile_tasks(modules, repo=repo)]
    return repair_gradle_install_config_for_harness(out)


_JAVA_HARNESS_PRESERVE_KEYS: tuple[str, ...] = (
    "docker_specs",
    "java_build_system",
    "docker_image",
    "gradle_junit_roots",
    "maven_junit_roots",
)


def is_java_harness_config(cfg: dict[str, Any]) -> bool:
    if str(cfg.get("language") or "").lower() == "java":
        return True
    jbs = str(cfg.get("java_build_system") or "").lower()
    if jbs in ("gradle", "maven"):
        return True
    tc = str(cfg.get("test_cmd") or "")
    return "./gradlew" in tc or tc.strip().startswith("mvn ")


def infer_java_version_for_harness(cfg: dict[str, Any]) -> str:
    specs = cfg.get("docker_specs")
    if isinstance(specs, dict) and specs.get("java_version"):
        return str(specs["java_version"])
    img = str(cfg.get("docker_image") or "")
    m = re.search(r"temurin[:\-](\d+)", img)
    if m:
        return m.group(1)
    tc = str(cfg.get("test_cmd") or "")
    if ":core:" in tc or ":module:" in tc or ":configuration-metadata:" in tc:
        return "25"
    return "17"


def ensure_java_docker_specs(cfg: dict[str, Any], *, language: str | None = None) -> dict[str, Any]:
    """Guarantee ``docker_specs.java_version`` for harness Java base image builds."""
    probe = dict(cfg)
    if language:
        probe["language"] = language
    if not is_java_harness_config(probe):
        return cfg
    out = dict(cfg)
    specs = dict(out["docker_specs"]) if isinstance(out.get("docker_specs"), dict) else {}
    if not specs.get("java_version"):
        specs["java_version"] = infer_java_version_for_harness(out)
    out["docker_specs"] = specs
    return out


def _temurin_major_from_docker_image(docker_image: str) -> int | None:
    m = re.search(r"temurin[:\-](\d+)", docker_image or "")
    return int(m.group(1)) if m else None


def _maven_cmd_has_compiler_overrides(cmd: str) -> bool:
    return "maven.compiler.source" in (cmd or "")


def merge_java_harness_fields_after_llm(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Restore Gradle/Maven harness fields when install LLM returns a Python-shaped config."""
    if not is_java_harness_config(before):
        return after
    out = dict(after)
    for key in _JAVA_HARNESS_PRESERVE_KEYS:
        if before.get(key) and not out.get(key):
            out[key] = before[key]
    before_jdk = _temurin_major_from_docker_image(str(before.get("docker_image") or ""))
    after_jdk = _temurin_major_from_docker_image(str(out.get("docker_image") or ""))
    if before_jdk == 8 and after_jdk is not None and after_jdk > 8:
        out["docker_image"] = before["docker_image"]
        if isinstance(before.get("docker_specs"), dict):
            out["docker_specs"] = dict(before["docker_specs"])
    tc_before = str(before.get("test_cmd") or "")
    tc_after = str(out.get("test_cmd") or "")
    from .harness_guards import is_valid_java_test_cmd, restore_test_cmd_if_invalid

    out = restore_test_cmd_if_invalid(before, out, language="java")
    tc_after = str(out.get("test_cmd") or "")
    if "./gradlew" in tc_before and not is_valid_java_test_cmd(tc_after, out):
        out["test_cmd"] = tc_before
    if tc_before.strip().startswith("mvn ") and not is_valid_java_test_cmd(tc_after, out):
        out["test_cmd"] = tc_before
    elif _maven_cmd_has_compiler_overrides(tc_before) and not _maven_cmd_has_compiler_overrides(tc_after):
        if tc_after.strip().startswith("mvn "):
            out["test_cmd"] = tc_before
    inst_before = str(before.get("install") or "")
    inst_after = str(out.get("install") or "")
    if install_cmd_is_noop(inst_after) and before.get("install"):
        out["install"] = before["install"]
    elif install_cmd_is_gradle_chmod_only(inst_after):
        if inst_before.strip() and not install_cmd_is_gradle_chmod_only(inst_before):
            out["install"] = before["install"]
        elif str(out.get("java_build_system") or "").lower() == "gradle":
            out["install"] = gradle_default_build_install_command()
    elif _maven_cmd_has_compiler_overrides(inst_before) and not _maven_cmd_has_compiler_overrides(inst_after):
        if inst_after.strip().startswith("mvn "):
            out["install"] = inst_before
    for key in ("pre_install", "post_install"):
        if isinstance(before.get(key), list) and before[key]:
            if not isinstance(out.get(key), list) or not out.get(key):
                out[key] = list(before[key])
    if (
        str(out.get("java_build_system") or "").lower() == "gradle"
        and install_cmd_is_gradle_chmod_only(str(out.get("install") or ""))
        and isinstance(before.get("post_install"), list)
        and before["post_install"]
        and not out.get("post_install")
    ):
        out["post_install"] = list(before["post_install"])
    out = ensure_java_docker_specs(out, language="java")
    return repair_gradle_install_config_for_harness(out)


def repair_gradle_install_config_for_harness(cfg: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize Gradle commands for SWE-bench grading: no ``-q``, test logging init,
    ``--configure-on-demand``, and skip lint/check tasks during compile install.
    """
    test_cmd_hint = str(cfg.get("test_cmd") or "")
    is_gradle = (
        str(cfg.get("java_build_system") or "").strip().lower() == "gradle"
        or "./gradlew" in test_cmd_hint
    )
    if not is_gradle:
        return cfg
    out = dict(cfg)
    init_cmd = _gradle_write_harness_logging_init_script()
    pre = list(out.get("pre_install") or [])
    if not any("swebench-harness-logging.init.gradle" in str(x) for x in pre):
        pre = [*pre, init_cmd] if pre else [init_cmd]
    out["pre_install"] = pre

    def _ensure_gradle_install_skips_lint(cmd: str) -> str:
        s = (cmd or "").strip()
        if not s or "./gradlew" not in s:
            return s
        low = s.lower()
        if "build" not in low and "assemble" not in low:
            return s
        if "-x check" in low:
            return s
        if "-x test" in low:
            return re.sub(r"(-x\s+test\b)", r"\1 -x check", s, count=1)
        return s.replace("./gradlew", "./gradlew -x test -x check", 1)

    def _fix_cmd(cmd: str) -> str:
        s = (cmd or "").strip()
        if not s or "./gradlew" not in s:
            return s
        s = re.sub(r"\s+-q\b", "", s)
        _, _, rest = s.partition("./gradlew")
        rest = rest.strip()
        for flag in (
            "--no-daemon",
            "--configure-on-demand",
            "-I",
            GRADLE_HARNESS_INIT_REL,
        ):
            rest = rest.replace(flag, "")
        rest = re.sub(r"\s+", " ", rest).strip()
        body = f"./gradlew {_gradle_wrapper_flags()} {rest}".strip()
        if GRADLE_HARNESS_INIT_REL not in s or "base64 -d" not in s:
            return _gradle_test_cmd_prefix() + body
        return body

    from .harness_guards import is_valid_java_test_cmd

    tc = str(out.get("test_cmd") or "")
    if tc and is_valid_java_test_cmd(tc, out):
        out["test_cmd"] = _fix_cmd(tc)
    inst = str(out.get("install") or "")
    if inst and "./gradlew" in inst:
        out["install"] = _ensure_gradle_install_skips_lint(inst)
    post = out.get("post_install")
    if isinstance(post, list):
        out["post_install"] = [_fix_cmd(str(x)) for x in post]
    elif isinstance(post, str) and post.strip():
        out["post_install"] = [_fix_cmd(post)]
    return out


def install_cmd_is_noop(install: str) -> bool:
    s = (install or "").strip()
    return not s or s.startswith("#")


def log_indicates_gradle_build_ok(log_tail: str) -> bool:
    return "BUILD SUCCESSFUL" in log_tail and "gradle" in log_tail.lower()


def log_indicates_gradle_module_slice_mismatch(
    *,
    n_base: int,
    n_patch: int,
    tp_tot: int,
) -> bool:
    """JUnit ran (often wrong module) but zero cases matched ``test_patch`` paths."""
    return n_base > 0 and n_patch == 0 and tp_tot == 0


def log_indicates_gradle_project_not_found(log_tail: str) -> bool:
    low = (log_tail or "").lower()
    return "project '" in low and "not found" in low and "gradle" in low


def extract_gradle_projects_output_from_log(log_tail: str) -> str:
    """Return ``./gradlew projects`` section from discover logs when present."""
    if "Project '" not in (log_tail or ""):
        return ""
    return log_tail or ""


def log_indicates_maven_missing_project(log_tail: str) -> bool:
    return "MissingProjectException" in log_tail and "no POM" in log_tail


def log_indicates_maven_unsupported_compiler_source(log_tail: str) -> bool:
    return bool(_MAVEN_UNSUPPORTED_SOURCE_RE.search(log_tail or ""))


def log_indicates_maven_tests_ran(log_tail: str) -> bool:
    low = log_tail.lower()
    if "mvn test" in low or "[docker] mvn test" in low:
        if "tests run:" in low or "test-jpms" in low or "proguard" in low:
            return True
        if "build success" in low:
            return True
    return False


def install_config_affects_env_image(cfg: dict[str, Any]) -> dict[str, Any]:
    """Subset of install_config that changes harness env/base Docker images."""
    keys = (
        "docker_image",
        "docker_specs",
        "pre_install",
        "pip_packages",
        "reqs_path",
        "python",
        "apt-pkgs",
        "apt-pkgs-optional",
        "install",
        "post_install",
    )
    return {k: cfg.get(k) for k in keys if k in cfg}


def remediate_maven_compiler_jdk(
    cfg: dict[str, Any],
    repo: Path,
    test_paths: list[str],
    *,
    log_tail: str = "",
) -> dict[str, Any]:
    """
    Align Maven ``docker_image`` / compiler ``-D`` flags with ``pom.xml`` or javac errors.

    Old multi-module repos (e.g. Gson) pin ``maven.compiler.source`` 1.6; JDK 17 rejects that.
    """
    jbs = str(cfg.get("java_build_system") or "").strip().lower()
    if jbs != "maven" and detect_java_build_system(repo) != "maven":
        return cfg
    compiler_major = detect_maven_compiler_major(repo)
    if log_indicates_maven_unsupported_compiler_source(log_tail):
        m = _MAVEN_UNSUPPORTED_SOURCE_RE.search(log_tail)
        if m:
            from_log = int(m.group(1))
            compiler_major = min(compiler_major or from_log, from_log)
        elif compiler_major is None:
            compiler_major = 6
    if compiler_major is None or compiler_major > 8:
        return cfg
    jdk_major = maven_runtime_jdk_major(compiler_major)
    paths = list(test_paths or [])
    modules = maven_modules_from_test_paths(paths)
    out = dict(cfg)
    out["java_build_system"] = "maven"
    out["docker_image"] = maven_docker_image(jdk_major)
    out["docker_specs"] = {"java_version": str(jdk_major)}
    out["install"] = _maven_install_cmd(modules, compiler_major=compiler_major)
    out["test_cmd"] = _maven_test_cmd(modules, paths, compiler_major=compiler_major)
    if paths:
        out["maven_junit_roots"] = maven_junit_report_roots(paths)
    return out


def install_config_remediation_unchanged(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """True when LLM remediation did not meaningfully change install_config."""
    keys = (
        "install",
        "test_cmd",
        "pre_install",
        "post_install",
        "pip_packages",
        "reqs_path",
        "pytest_plugins",
        "maven_junit_roots",
        "gradle_junit_roots",
        "docker_image",
        "docker_specs",
        "js_test_runner",
        "php_test_runner",
        "apt-pkgs",
    )
    return all(before.get(k) == after.get(k) for k in keys)


_REMEDIATION_SUBSTANTIVE_KEYS = (
    "install",
    "test_cmd",
    "post_install",
    "pip_packages",
    "reqs_path",
    "pytest_plugins",
    "maven_junit_roots",
    "gradle_junit_roots",
    "docker_image",
    "docker_specs",
    "js_test_runner",
)


def install_config_substantive_change(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """True when remediation changed more than apt/pre_install-only hygiene."""
    if install_config_remediation_unchanged(before, after):
        return False
    return any(before.get(k) != after.get(k) for k in _REMEDIATION_SUBSTANTIVE_KEYS)


def log_indicates_git_clone_failure(log: str, *, docker_exit: int = 0) -> bool:
    """True when Docker failed before install/tests due to git checkout."""
    low = (log or "").lower()
    if docker_exit == 128:
        return True
    return any(
        needle in low
        for needle in (
            "could not parse object",
            "fatal: ambiguous argument",
            "fatal: reference is not a tree",
            "remote did not send all necessary objects",
        )
    )
