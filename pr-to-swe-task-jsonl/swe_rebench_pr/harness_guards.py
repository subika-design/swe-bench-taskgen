"""Validate harness install_config fields after LLM remediation; structured failure logs."""

from __future__ import annotations

import re
from typing import Any

_GRADLE_TEST_CMD_RE = re.compile(r"\./gradlew\b")
_MAVEN_TEST_CMD_RE = re.compile(r"\bmvn(?:w)?\b")
_PHP_TEST_CMD_RE = re.compile(
    r"(?:vendor/bin/(?:simple-phpunit|phpunit|pest)|phpunit\b|artisan\s+test)",
    re.I,
)
_GARBAGE_TEST_CMD_RE = re.compile(
    r"(?:^|\s)(?:echo\s|base64\s|mkdir\s+-p\s+gradle\s+&&\s+echo)",
    re.I,
)

_COMPOSER_PROBLEM_RE = re.compile(
    r"(?:Problem \d+|Your requirements could not be resolved|composer install)",
    re.I,
)
_GRADLE_FAILURE_RE = re.compile(
    r"(?:BUILD FAILED|What went wrong:|Caused by:|FAILURE: Build failed)",
    re.I,
)
_HARNESS_GRADLE_INIT_MARKERS = (
    "swebench-harness-logging.init.gradle",
    "base64 -d",
)


def _gradle_harness_init_prefix_ok(tc: str) -> bool:
    """True when ``echo``/``base64`` are the harness logging init script, not LLM junk."""
    return all(m in tc for m in _HARNESS_GRADLE_INIT_MARKERS)


def is_valid_java_test_cmd(test_cmd: str, cfg: dict[str, Any] | None = None) -> bool:
    tc = str(test_cmd or "").strip()
    if not tc:
        return False
    if _gradle_harness_init_prefix_ok(tc):
        return bool(_GRADLE_TEST_CMD_RE.search(tc))
    if _GARBAGE_TEST_CMD_RE.search(tc):
        return False
    if re.search(r"\becho\b", tc, re.I):
        return False
    jbs = str((cfg or {}).get("java_build_system") or "").lower()
    if jbs == "maven" or tc.strip().startswith("mvn "):
        return bool(_MAVEN_TEST_CMD_RE.search(tc))
    if jbs == "gradle" or "./gradlew" in tc:
        return bool(_GRADLE_TEST_CMD_RE.search(tc))
    return bool(_GRADLE_TEST_CMD_RE.search(tc) or _MAVEN_TEST_CMD_RE.search(tc))


def is_valid_php_test_cmd(test_cmd: str) -> bool:
    tc = str(test_cmd or "").strip()
    if not tc or _GARBAGE_TEST_CMD_RE.search(tc):
        return False
    return bool(_PHP_TEST_CMD_RE.search(tc))


def is_valid_harness_test_cmd(language: str, test_cmd: str, cfg: dict[str, Any] | None = None) -> bool:
    lang = str(language or (cfg or {}).get("language") or "").lower()
    if lang == "java":
        return is_valid_java_test_cmd(test_cmd, cfg)
    if lang == "php":
        return is_valid_php_test_cmd(test_cmd)
    return bool(str(test_cmd or "").strip())


def restore_test_cmd_if_invalid(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    language: str | None = None,
) -> dict[str, Any]:
    """Keep prior ``test_cmd`` when LLM output is not a valid harness runner command."""
    lang = str(language or before.get("language") or after.get("language") or "").lower()
    out = dict(after)
    tc_before = str(before.get("test_cmd") or "").strip()
    tc_after = str(out.get("test_cmd") or "").strip()
    if not tc_before:
        return out
    if tc_after == tc_before:
        return out
    if is_valid_harness_test_cmd(lang, tc_after, out):
        return out
    if is_valid_harness_test_cmd(lang, tc_before, before):
        out["test_cmd"] = before["test_cmd"]
    return out


def extract_structured_failure_log(log_tail: str, *, language: str = "") -> str:
    """
    Pull actionable failure excerpts from noisy Docker logs for LLM remediation.
    """
    text = log_tail or ""
    if not text.strip():
        return ""
    lang = str(language or "").lower()
    chunks: list[str] = []

    for pat in (
        r"\[docker\][^\n]*",
        r"patch apply (?:check )?failed[^\n]*",
    ):
        for m in re.finditer(pat, text, re.I):
            chunks.append(m.group(0).strip())

    if lang == "java" or "BUILD FAILED" in text or "What went wrong" in text:
        for m in re.finditer(
            r"(?:FAILURE: Build failed with an exception\.|What went wrong:\n.*?(?:\n\*|\nBUILD FAILED))",
            text,
            re.I | re.DOTALL,
        ):
            block = m.group(0).strip()
            if len(block) > 4000:
                block = block[:4000] + "…"
            chunks.append(block)
        for m in re.finditer(
            r"No tests found for given includes[^\n]*(?:\n[^\n]+){0,3}",
            text,
            re.I,
        ):
            chunks.append(m.group(0).strip()[:2000])
        for m in re.finditer(r"Caused by:[^\n]+(?:\n(?:\t| {4,})[^\n]+)*", text):
            chunks.append(m.group(0).strip()[:2000])

    if lang == "php" or _COMPOSER_PROBLEM_RE.search(text):
        for m in re.finditer(
            r"(?:Your requirements could not be resolved[^\n]*\n(?:\s+[^\n]+\n)+|Problem \d+[^\n]+\n(?:\s+[^\n]+\n)+)",
            text,
            re.I,
        ):
            chunks.append(m.group(0).strip()[:3000])
        for m in re.finditer(r"Script .+ returned with error code \d+", text, re.I):
            chunks.append(m.group(0).strip())

    if not chunks:
        return text[-8000:]

    seen: set[str] = set()
    out: list[str] = []
    for c in chunks:
        key = c[:200]
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    joined = "\n\n---\n\n".join(out)
    return joined[-12000:]


_PATCH_APPLY_FAILED_RE = re.compile(
    r"patch apply (?:check )?failed|patch does not apply|error: patch failed",
    re.I,
)


def log_indicates_patch_apply_failed(log_tail: str) -> bool:
    return bool(_PATCH_APPLY_FAILED_RE.search(log_tail or ""))


def log_indicates_ngtcp2_quictls_missing(log_tail: str) -> bool:
    """CMake could not find ``libngtcp2_crypto_quictls`` (OpenSSL QUIC backend)."""
    low = (log_tail or "").lower()
    return (
        "libngtcp2_crypto_quictls" in low
        or "no package 'libngtcp2_crypto_quictls'" in low
    )


def log_indicates_gradle_no_tests_found_for_includes(
    log_tail: str,
    *,
    n_patch: int | None = None,
) -> bool:
    """
    Gradle ran scoped ``--tests`` filters but matched zero test classes.

    Usually wrong Gradle module (e.g. java9plus vs java8), not compile/install failure.
    Subproject ``No tests found`` lines are ignored when patch JUnit already harvested
    (``n_patch > 0``) or when the root ``:test`` task ran and reported passes.
    """
    low = (log_tail or "").lower()
    if "no tests found for given includes" not in low:
        return False
    if n_patch is not None and n_patch > 0:
        return False
    if not (
        "[docker] gradle test" in low
        or "[docker] gradle test_cmd" in low
        or "./gradlew" in low
    ):
        return False
    if re.search(r"> Task :test FAILED", log_tail or "", re.I):
        return True
    if re.search(r"> Task :test\b", log_tail or "") and re.search(
        r" PASSED", log_tail or ""
    ):
        return False
    return True


def log_indicates_gradle_build_failed_during_tests(log_tail: str) -> bool:
    """True when Gradle failed after patches applied (compile/test phase, not pre_install)."""
    if log_indicates_gradle_no_tests_found_for_includes(log_tail):
        return False
    low = (log_tail or "").lower()
    if "build failed" not in low and "failure: build failed" not in low:
        return False
    if "[docker] applying patch" not in low:
        return False
    if "[docker] gradle test" in low or "[docker] gradle test_cmd" in low:
        return True
    return "./gradlew" in low and "test" in low


def docker_failure_class(
    *,
    docker_exit: int,
    log_tail: str,
    lang: str,
    install_failed: bool,
    after_patch_empty: bool = False,
    n_base: int = 0,
    n_patch: int = 0,
) -> str:
    """Coarse failure bucket for remediation routing."""
    if log_indicates_patch_apply_failed(log_tail) and docker_exit != 0:
        return "patch_apply_failed"
    if lang == "java" and log_indicates_gradle_no_tests_found_for_includes(
        log_tail, n_patch=n_patch
    ):
        return "test_target_failed"
    if lang == "java" and log_indicates_gradle_build_failed_during_tests(log_tail):
        return "build_failed"
    if after_patch_empty or (docker_exit == 0 and n_base > 0 and n_patch == 0):
        return "patch_phase_empty"
    if docker_exit != 0 and install_failed:
        return "install_failed"
    if install_failed:
        return "install_failed"
    return "ok"


def needs_junit_fix_reason(
    lang: str,
    *,
    n_base: int = 0,
    n_patch: int = 0,
    log_tail: str = "",
) -> str:
    """Human-readable install remediation reason when JUnit is empty after patch."""
    language = str(lang or "").lower()
    if language == "php":
        if n_base > 0 and n_patch == 0:
            return (
                "patch phase empty — check test-patch.log / impl.patch apply "
                "(not composer install)"
            )
        return "junit empty (fix test_cmd / simple-phpunit / junit paths)"
    if language in ("ruby", "rb"):
        if n_base > 0 and n_patch == 0:
            return "rspec junit empty after patch — bundle/Gemfile or scoped spec paths"
        return "junit empty (fix test_cmd / RspecJunitFormatter / spec paths)"
    if language == "java":
        if log_indicates_gradle_no_tests_found_for_includes(
            log_tail, n_patch=n_patch
        ):
            return (
                "gradle no tests for --tests filter (fix Gradle module / FQCN, "
                "not install)"
            )
        if log_indicates_gradle_build_failed_during_tests(log_tail):
            return (
                "gradle BUILD FAILED during test (fix compile/install/post_install, "
                "not test_cmd)"
            )
        return "junit empty (fix test_cmd / Gradle module / junit roots)"
    if language in ("javascript", "js", "typescript", "ts", "node"):
        return "junit empty (fix test_cmd / reporter / targets)"
    if language == "python":
        return "junit empty (fix test_cmd / integration pytest root / CI env)"
    if language == "go" and log_indicates_patch_apply_failed(log_tail):
        return "patch apply failed — fix impl/test_patch (not go install)"
    return "junit empty (fix test_cmd / junit roots)"
