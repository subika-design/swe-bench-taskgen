"""LLM updates to PR test_patch from Docker pytest failures."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .llm_client import chat_completions, extract_json_object, load_prompt
from .patch_validate import validate_git_patch

TEST_PATCH_APPLY_MAX_ATTEMPTS = 5


def _existing_test_files_note(repo: Path, paths: list[str]) -> str:
    """Tell the LLM to emit modify hunks when the test file already exists at base_commit."""
    existing = [p for p in paths if p and (repo / p).is_file()]
    if not existing:
        return ""
    lines = ["\n\n## Files already on disk at base_commit (MODIFY only — never new file)\n"]
    for p in existing[:3]:
        lines.append(f"- {p}")
        try:
            excerpt = (repo / p).read_text(encoding="utf-8", errors="replace")
            excerpt = excerpt[:3500]
            if len(excerpt) >= 3500:
                excerpt += "\n... (truncated)"
            lines.append(f"\nExisting content of `{p}` (extend with +lines, keep package/class name):\n")
            lines.append(f"```\n{excerpt}\n```")
        except OSError:
            pass
    lines.append(
        "\nUse `--- a/<path>` `+++ b/<path>` hunks against the content above. "
        "Do **not** use `new file mode` or `--- /dev/null`.\n"
    )
    return "\n".join(lines)


def build_java_harness_context_for_repo(
    repo: Path | None,
    test_paths: list[str],
    *,
    llm: tuple[str, str, str, int] | None = None,
    repo_id: str = "",
    instance_id: str = "",
    test_cmd: str = "",
) -> str:
    """Resolve Gradle projects and format harness instructions for LLM prompts."""
    paths = [p.replace("\\", "/") for p in test_paths if p.strip()]
    if not paths or repo is None:
        return ""
    from .java_build import format_java_harness_context_for_llm
    from .java_gradle_llm import resolve_gradle_projects_for_test_paths

    gradle_map = resolve_gradle_projects_for_test_paths(
        repo,
        paths,
        api_key=llm[0] if llm else None,
        base_url=llm[1] if llm else "",
        model=llm[2] if llm else "",
        timeout_s=llm[3] if llm else 120,
        repo_id=repo_id,
        instance_id=instance_id,
    )
    cmd = test_cmd.strip()
    if not cmd:
        from .java_build import java_install_config_for_repo

        cfg = java_install_config_for_repo(
            repo, test_paths=paths, gradle_path_by_test_path=gradle_map
        )
        cmd = str(cfg.get("test_cmd") or "")
    return format_java_harness_context_for_llm(
        paths, gradle_path_by_test_path=gradle_map, test_cmd=cmd
    )


def java_label_mismatch_diagnostics(
    tp_only: list[str],
    patch_map: dict[str, str],
    *,
    test_cmd: str = "",
) -> list[tuple[str, str]]:
    """Single actionable failure for Java JUnit label mismatch (not 40 build-plugin passes)."""
    from .java_build import java_fqcn_from_test_path

    fqcns = [java_fqcn_from_test_path(p) for p in tp_only]
    fqcns = [f for f in fqcns if f]
    noise = [
        k
        for k in patch_map
        if "build/" in k or "ConventionsPlugin" in k or "AntoraAsciidoc" in k
    ][:6]
    msg = (
        "LABEL_MISMATCH: Gradle/JUnit output contains **no tests** from test_patch paths. "
        f"test_patch_paths_only={tp_only}. "
        f"Required --tests FQCN(s)={fqcns}. "
        f"Docker Gradle command: {(test_cmd or '(see stdout)')[:600]}. "
        "Fix the test_patch diff: (1) keep the **same** file path and public class name; "
        "(2) if the file exists at base_commit use **modify** diff not new-file; "
        "(3) put tests in the **same Gradle module** as impl.patch; "
        "(4) add @Test methods that fail before impl.patch and pass after. "
        f"Ignore unrelated passing keys from build plugin tests, e.g. {noise}."
    )
    return [("(label_mismatch)", msg)]


def _java_harness_placeholder(language: str, body: str) -> str:
    if (language or "").strip().lower() != "java" or not body.strip():
        return ""
    return "\n" + body.strip() + "\n"


def _language_notes(language: str, repo_id: str, *, repo: Path | None = None) -> str:
    lang = (language or "python").strip().lower()
    if lang == "java" or (
        repo is not None
        and (repo / "gradlew").is_file()
        and ((repo / "build.gradle").is_file() or (repo / "build.gradle.kts").is_file())
    ):
        return (
            "- Build: Gradle (`gradlew`); Docker runs the exact `test_cmd` with `--tests '<fqcn>'`.\n"
            "- See **Java harness** below for required file path, Gradle project, and class name.\n"
            "- Use JUnit 5; one test file; stable path/class across attempts.\n"
        )
    if lang == "python":
        return "- Use pytest `def test_*` under `tests/`.\n"
    if lang == "go":
        return "- Use `*_test.go` in the package under test.\n"
    return ""


def _parse_test_patch_response(raw: str, *, error_label: str) -> str:
    obj = extract_json_object(raw)
    if not isinstance(obj, dict):
        raise ValueError(f"{error_label} model output is not a JSON object")
    updated = obj.get("test_patch")
    if not isinstance(updated, str) or not updated.strip():
        raise ValueError(f"{error_label} output missing non-empty test_patch string")
    out = _normalize_test_patch(updated)
    if "diff --git" not in out:
        raise ValueError(f"{error_label} output is not a unified diff")
    return out


def _normalize_test_patch(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("```"):
        text = re.sub(r"^```(?:diff)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip() + ("\n" if text.strip() and not text.endswith("\n") else "")


def llm_create_test_patch_from_pr(
    *,
    problem_statement: str,
    patch: str,
    repo_id: str,
    hints_text: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    language: str = "python",
    suggested_test_paths: list[str] | None = None,
    existing_files_note: str = "",
    java_harness_context: str = "",
    prior_apply_error: str = "",
    attempt: int = 1,
    max_attempts: int = TEST_PATCH_APPLY_MAX_ATTEMPTS,
    modify_only: bool = False,
) -> str:
    """Return a new unified ``test_patch`` when the PR has no test-file changes."""
    tpl = load_prompt("create_test_patch_from_pr.txt")
    paths_txt = (
        "\n".join(f"- {p}" for p in (suggested_test_paths or [])[:20])
        or "(infer from impl.patch paths)"
    )
    lang_notes = _language_notes(language, repo_id)
    retry = ""
    if prior_apply_error.strip():
        retry = (
            f"\n\n## Previous attempt failed apply-check (attempt {attempt - 1} of {max_attempts})\n"
            f"Fix the structural diff errors:\n{prior_apply_error.strip()}\n"
            "Return a **new** valid unified diff — do not repeat the same broken hunk.\n"
        )
    user = (
        tpl.replace("{{repo}}", repo_id or "unknown")
        .replace("{{language}}", language or "python")
        .replace("{{language_notes}}", lang_notes)
        .replace("{{suggested_test_paths}}", paths_txt)
        .replace("{{problem_statement}}", (problem_statement or "")[-40_000:])
        .replace("{{patch}}", (patch or "")[-80_000:])
        .replace("{{hints_text}}", (hints_text or "(none)")[-20_000:])
        .replace(
            "{{java_harness_context}}",
            _java_harness_placeholder(language, java_harness_context),
        )
        + (existing_files_note or "")
        + retry
    )
    if modify_only:
        system = (
            "You write MODIFY-only git unified diffs for existing Java test files. "
            "Never use new file mode, never use --- /dev/null. "
            "Return only JSON {\"test_patch\": \"...\"}. No markdown."
        )
    else:
        system = (
            "You write valid git unified diffs for test files only. "
            "Return only a JSON object with key test_patch (string). "
            "The diff must pass git apply --check. No markdown, no commentary."
        )
    raw = chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=system,
        user=user,
        timeout_s=timeout_s,
        json_object=True,
    )
    return _parse_test_patch_response(raw, error_label=f"Create-test-patch-{attempt}")


def llm_fix_test_patch_apply_check(
    test_patch: str,
    apply_error: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    attempt: int = 1,
    max_attempts: int = TEST_PATCH_APPLY_MAX_ATTEMPTS,
    language: str = "python",
    repo_id: str = "",
    problem_statement: str = "",
    impl_patch: str = "",
    previous_edit_unchanged: bool = False,
    java_harness_context: str = "",
) -> str:
    """Fix a test_patch that failed ``git apply --check`` (structural / corrupt diff)."""
    tpl = load_prompt("fix_test_patch_apply_check.txt")
    if previous_edit_unchanged:
        retry_note = (
            "Your last fix still failed apply-check with a similar error, or you returned "
            "the same diff. Produce a **structurally different** valid unified diff."
        )
    else:
        retry_note = (
            "The harness validates with `git apply --check` before Docker. "
            "Fix hunk headers, line prefixes (+/-/space), and file paths."
        )
    user = (
        tpl.replace("{{attempt}}", str(attempt))
        .replace("{{max_attempts}}", str(max_attempts))
        .replace("{{retry_note}}", retry_note)
        .replace("{{repo}}", repo_id or "unknown")
        .replace("{{language}}", language or "python")
        .replace("{{language_notes}}", _language_notes(language, repo_id))
        .replace("{{problem_context}}", (problem_statement or "(none)")[-30_000:])
        .replace("{{impl_patch_excerpt}}", (impl_patch or "")[-50_000:])
        .replace("{{test_patch}}", test_patch[-80_000:])
        .replace("{{apply_error}}", (apply_error or "unknown")[-8_000:])
        .replace(
            "{{java_harness_context}}",
            _java_harness_placeholder(language, java_harness_context),
        )
    )
    raw = chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=(
            'You repair broken git unified diffs. Return only JSON {"test_patch": "..."}. '
            "No markdown."
        ),
        user=user,
        timeout_s=timeout_s,
        json_object=True,
    )
    return _parse_test_patch_response(raw, error_label=f"Fix-apply-check-{attempt}")


def remediate_test_patch_until_applies(
    test_patch: str,
    repo: Path,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    max_attempts: int = TEST_PATCH_APPLY_MAX_ATTEMPTS,
    language: str = "python",
    repo_id: str = "",
    problem_statement: str = "",
    impl_patch: str = "",
    instance_id: str = "",
    test_paths: list[str] | None = None,
    java_harness_context: str = "",
) -> tuple[str, bool, str]:
    """
    Validate with ``git apply --check``; LLM-fix until valid or max attempts.

    Returns ``(patch_text, ok, last_error)``.
    """
    current = _normalize_test_patch(test_patch)
    last_err = ""
    prev = current
    cap = max(1, max_attempts)

    for attempt in range(1, cap + 1):
        ok, err = validate_git_patch(current, repo)
        if ok:
            if attempt > 1 and instance_id:
                print(
                    f"  {instance_id}: test_patch apply-check OK on attempt {attempt}/{cap}",
                    file=sys.stderr,
                )
            return current, True, ""
        last_err = err
        if attempt >= cap:
            break
        if instance_id:
            print(
                f"  {instance_id}: test_patch apply-check failed ({err}); "
                f"LLM fix attempt {attempt + 1}/{cap}",
                file=sys.stderr,
            )
        unchanged_from_prev = current.strip() == prev.strip()
        candidate = llm_fix_test_patch_apply_check(
            current,
            err,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_s=timeout_s,
            attempt=attempt + 1,
            max_attempts=cap,
            language=language,
            repo_id=repo_id,
            problem_statement=problem_statement,
            impl_patch=impl_patch,
            previous_edit_unchanged=unchanged_from_prev,
            java_harness_context=java_harness_context,
        )
        prev = current
        current = candidate

    return current, False, last_err


def create_and_validate_test_patch(
    *,
    problem_statement: str,
    patch: str,
    repo: Path,
    repo_id: str,
    hints_text: str,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    language: str,
    suggested_test_paths: list[str],
    instance_id: str,
    max_attempts: int = TEST_PATCH_APPLY_MAX_ATTEMPTS,
    llm: tuple[str, str, str, int] | None = None,
    test_cmd: str = "",
) -> tuple[str, bool]:
    """
    LLM-create test_patch, then apply-check + fix loop (up to ``max_attempts`` create/fix rounds).
    """
    cap = max(1, max_attempts)
    last_err = ""
    current = ""

    for create_attempt in range(1, cap + 1):
        if instance_id:
            print(
                f"  {instance_id}: LLM create test_patch attempt {create_attempt}/{cap}",
                file=sys.stderr,
            )
        try:
            java_ctx = ""
            if (language or "").lower() == "java":
                java_ctx = build_java_harness_context_for_repo(
                    repo,
                    suggested_test_paths,
                    llm=(api_key, base_url, model, timeout_s),
                    repo_id=repo_id,
                    instance_id=instance_id,
                    test_cmd=test_cmd,
                )
            existing_note = _existing_test_files_note(repo, suggested_test_paths)
            modify_only = bool(existing_note.strip()) and (language or "").lower() == "java"
            if modify_only and instance_id:
                print(
                    f"  {instance_id}: test file exists at base_commit — MODIFY-only create",
                    file=sys.stderr,
                )
            current = llm_create_test_patch_from_pr(
                problem_statement=problem_statement,
                patch=patch,
                repo_id=repo_id,
                hints_text=hints_text,
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_s=timeout_s,
                language=language,
                suggested_test_paths=suggested_test_paths,
                existing_files_note=existing_note,
                java_harness_context=java_ctx,
                prior_apply_error=last_err,
                attempt=create_attempt,
                max_attempts=cap,
                modify_only=modify_only,
            )
        except Exception as ex:
            if instance_id:
                print(
                    f"  {instance_id}: test_patch create LLM failed (attempt {create_attempt}): {ex}",
                    file=sys.stderr,
                )
            last_err = str(ex)
            continue

        patched, ok, err = remediate_test_patch_until_applies(
            current,
            repo,
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_s=timeout_s,
            max_attempts=cap,
            language=language,
            repo_id=repo_id,
            problem_statement=problem_statement,
            impl_patch=patch,
            instance_id=instance_id,
            test_paths=suggested_test_paths,
            java_harness_context=java_ctx if (language or "").lower() == "java" else "",
        )
        if ok:
            if instance_id:
                print(
                    f"  {instance_id}: LLM test_patch ready ({len(patched)} bytes) "
                    f"after create attempt {create_attempt}",
                    file=sys.stderr,
                )
            return patched, True
        last_err = err
        current = patched
        if instance_id:
            print(
                f"  {instance_id}: create attempt {create_attempt} — apply-check still failing; "
                f"retrying create with error feedback",
                file=sys.stderr,
            )

    return current, False


def llm_fix_test_patch_from_docker_tests(
    test_patch: str,
    diagnostics_text: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
    attempt: int = 1,
    max_attempts: int = 3,
    previous_edit_unchanged: bool = False,
    django_runtests: bool = False,
    problem_context: str = "",
    language: str = "python",
    repo_id: str = "",
    java_harness_context: str = "",
) -> str:
    """Return an updated unified ``test_patch`` diff from Docker test failure diagnostics."""
    tpl = load_prompt("fix_test_patch_from_tests.txt")
    if previous_edit_unchanged:
        retry_note = (
            "Your previous test_patch edit was applied and tests were re-run, but the "
            "same failure(s)/ERROR(s) remain (or you returned an identical diff). Produce a "
            "**different** fix targeting the diagnostics and tracebacks below."
        )
    else:
        retry_note = (
            "After you return a new test_patch, the harness will apply it and re-run tests. "
            "If tests still fail or ERROR, you will receive updated diagnostics on a later attempt."
        )
    if django_runtests:
        runner_note = (
            "The harness runs **Django's** `tests/runtests.py` with labels derived from "
            "test_patch file paths (e.g. `handlers_tests.test_pickle`), not bare pytest. "
            "Tests must be standard `django.test.TestCase` / `SimpleTestCase` methods named `test_*`."
        )
    elif (language or "").lower() == "java":
        runner_note = (
            "The harness runs the **Gradle command** in the Java harness section "
            "(`--tests '<fqcn>'` on the listed project). "
            "JUnit keys must match your test_patch file path / public class — "
            "not `org.springframework.boot.build.*` plugin tests."
        )
    else:
        runner_note = (
            "The harness runs `python3 -m pytest` on test_patch paths inside Docker. "
            "Tests see `sys.argv` with `-m pytest` when they inspect the interpreter command line."
        )
    user = (
        tpl.replace("{{attempt}}", str(attempt))
        .replace("{{max_attempts}}", str(max_attempts))
        .replace("{{retry_note}}", retry_note)
        .replace("{{runner_note}}", runner_note)
        .replace("{{problem_context}}", (problem_context or "(none)")[-30_000:])
        .replace("{{test_patch}}", test_patch[-80_000:])
        .replace("{{cut_logs}}", diagnostics_text[-120_000:])
        .replace(
            "{{java_harness_context}}",
            _java_harness_placeholder(language, java_harness_context),
        )
    )
    raw = chat_completions(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=(
            "Return only a JSON object with key test_patch (unified diff string). "
            "The diff must be valid for git apply. No markdown, no commentary."
        ),
        user=user,
        timeout_s=timeout_s,
        json_object=True,
    )
    return _parse_test_patch_response(raw, error_label="Fix-test-patch")
