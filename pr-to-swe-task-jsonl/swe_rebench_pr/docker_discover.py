from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .diff_split import (
    collect_heuristic_test_paths_from_patch,
    _nodeid_in_test_patch_paths,
    _path_filter_sets,
    collect_test_targets,
    collect_test_targets_from_test_patch,
    filter_swebench_gradable_nodeids,
    junit_fail_error_skip_messages_for_paths,
    junit_fail_error_skip_messages_limited,
    junit_outcome_counts_all,
    junit_outcome_counts_for_paths,
    log_junit_test_patch_mismatch,
    parse_test_status_map,
    has_test_patch_label_mismatch,
    test_reported_count,
)
from .django_runtests import (
    _case_map_key_matches_paths,
    django_fail_error_skip_messages_for_paths,
    django_outcome_counts_for_paths,
    paths_to_runtests_labels,
)
from .swebench_align import outcome_passed
from .docker_entry import write_entry_script
from .java_build import (
    detect_java_build_system,
    detect_maven_compiler_major,
    install_config_affects_env_image,
    install_config_remediation_unchanged,
    install_config_substantive_change,
    log_indicates_git_clone_failure,
    log_indicates_gradle_build_ok,
    log_indicates_maven_missing_project,
    log_indicates_maven_tests_ran,
    log_indicates_maven_unsupported_compiler_source,
    merge_java_build_into_config,
    remediate_maven_compiler_jdk,
)
from .languages import get_language_spec, normalize_language
from .patch_validate import validate_git_patch
from .gh_pr import ParsedPR, clone_repo_at, strip_mailbox_to_unified


def _test_patch_only_targets(language: str, test_patch: str) -> list[str]:
    """Test paths from ``test_patch`` (language rules, then split heuristics)."""
    if str(language or "").strip().lower() == "c":
        from .integration_build import integration_pytest_paths_from_patches

        int_paths = integration_pytest_paths_from_patches("", test_patch)
        if int_paths:
            return int_paths
        from .runtests_build import collect_runtests_numbers
        from .integration_build import patch_diff_touches_libtest

        if patch_diff_touches_libtest(test_patch):
            paths = collect_test_targets_from_test_patch(language, test_patch)
            if paths:
                return paths
            nums = collect_runtests_numbers(test_patch)
            return [f"tests/data/test{n}" for n in nums]
        return collect_test_targets_from_test_patch(language, test_patch)
    paths = collect_test_targets_from_test_patch(language, test_patch)
    if paths:
        return paths
    return collect_heuristic_test_paths_from_patch(test_patch)
from .install_llm import llm_fix_recipe_from_docker_tests, sanitize_install_config_for_docker
from .swebench_align import (
    export_install_config_for_harness,
    internal_install_keys,
    merge_internal_install_keys,
    uses_runtests_test_cmd,
)
from .task_type import classify_task_type, task_type_skip_reason
from .test_patch_fixes import build_failure_source_context, pytest_argv_mismatch_hint
from .test_patch_llm import (
    TEST_PATCH_APPLY_MAX_ATTEMPTS,
    build_java_harness_context_for_repo,
    create_and_validate_test_patch,
    java_label_mismatch_diagnostics,
    llm_fix_test_patch_from_docker_tests,
    remediate_test_patch_until_applies,
)

# Headless Docker cannot build PyQt/PySide from pip (needs Qt/qmake). LLMs often add these for "GUI tests".
_DOCKER_SKIP_PKG = re.compile(r"(?i)pyqt|pyside|pyobjc")
# Skips that may be fixed by adding deps to install_config (not xfail / intentional).
_FIXABLE_SKIP = re.compile(
    r"(?i)no module named|not installed|could not import|importorskip|modulenotfounderror"
)


def _print_test_patch_junit_diagnostics(
    instance_id: str,
    failures: list[tuple[str, str]],
    errors: list[tuple[str, str]],
    skips: list[tuple[str, str]],
    *,
    max_distinct_messages: int = 25,
    max_message_chars: int = 900,
) -> None:
    """Print grouped JUnit ``message``/body text for fail/error/skip in the test_patch slice."""

    def dump(kind: str, pairs: list[tuple[str, str]]) -> None:
        if not pairs:
            return
        by_msg: dict[str, list[str]] = defaultdict(list)
        for nid, msg in pairs:
            by_msg[msg].append(nid)
        print(
            f"  {instance_id}: test_patch {kind} — {len(pairs)} case(s), "
            f"{len(by_msg)} distinct message(s):",
            file=sys.stderr,
        )
        rows = sorted(by_msg.items(), key=lambda kv: -len(kv[1]))
        for i, (msg, nids) in enumerate(rows):
            if i >= max_distinct_messages:
                print(
                    f"    … and {len(rows) - max_distinct_messages} more distinct {kind} messages",
                    file=sys.stderr,
                )
                break
            short = msg if len(msg) <= max_message_chars else msg[: max_message_chars - 1] + "…"
            print(f"    ({len(nids)}×) {short}", file=sys.stderr)
            print(f"        example: {nids[0]}", file=sys.stderr)

    dump("FAIL", failures)
    dump("ERROR", errors)
    dump("SKIP", skips)


def _llm_diagnostics_blob(
    pr: ParsedPR,
    *,
    tp_only: list[str],
    patch_junit: Path,
    repo_root: Path,
    pa: int,
    fa: int,
    ea: int,
    sk: int,
    tot: int,
    docker_stderr_tail: str,
    docker_stdout_tail: str = "",
    install_failed: bool = False,
    docker_exit: int = 0,
    n_patch: int = 0,
    n_targets: int = 0,
    original_tp_only: list[str] | None = None,
    failures: list[tuple[str, str]] | None = None,
    errors: list[tuple[str, str]] | None = None,
    django_runtests: bool = False,
    install_config: dict[str, Any] | None = None,
) -> str:
    if failures is None or errors is None:
        diag_lang = str((install_config or {}).get("language") or "python")
        if tp_only and django_runtests:
            fl, el, sl = django_fail_error_skip_messages_for_paths(patch_junit, tp_only)
        elif tp_only:
            fl, el, sl = junit_fail_error_skip_messages_for_paths(
                patch_junit,
                repo_root,
                tp_only,
                language=diag_lang,
                native_integration_pytest_root=_native_integration_pytest_root(
                    install_config or {}
                ),
            )
        else:
            fl, el, sl = junit_fail_error_skip_messages_limited(
                patch_junit, repo_root, limit=200, language=diag_lang
            )
        failures = fl
        errors = el
    else:
        sl = []
    lines = [
        f"instance_id: {pr.instance_id}",
    ]
    lines.extend(
        [
            f"install_failed: {install_failed} (docker_exit={docker_exit}, junit_after_patch={n_patch}, "
            f"pytest_targets={n_targets})",
            f"slice_stats: passed={pa} failure={fa} error={ea} skipped={sk} total={tot}",
            f"test_patch_paths_only: {tp_only[:30]}{'...' if len(tp_only) > 30 else ''}",
        ]
    )
    if original_tp_only and original_tp_only != tp_only:
        lines.append(
            f"original_test_patch_paths (keep all): {original_tp_only[:30]}"
            f"{'...' if len(original_tp_only) > 30 else ''}"
        )
    if install_config:
        lines.extend(
            [
                f"java_build_system: {install_config.get('java_build_system')}",
                f"docker_image: {install_config.get('docker_image')}",
                f"install: {str(install_config.get('install') or '')[:500]}",
                f"test_cmd: {str(install_config.get('test_cmd') or '')[:500]}",
                f"maven_junit_roots: {install_config.get('maven_junit_roots')}",
                f"gradle_junit_roots: {install_config.get('gradle_junit_roots')}",
            ]
        )
    log_blob = (docker_stdout_tail or "") + "\n" + (docker_stderr_tail or "")
    if log_indicates_maven_unsupported_compiler_source(log_blob):
        lines.append(
            "hint: Maven 'Source option N is no longer supported' means the JDK is too new for "
            "pom compiler level — set docker_image to maven:3.9-eclipse-temurin-8 and add "
            "-Dmaven.compiler.source=1.6 -Dmaven.compiler.target=1.6 (match pom) on install and test_cmd."
        )
    lines.extend(
        [
            "\n--- docker stdout (tail) ---\n" + docker_stdout_tail[-4000:],
            "\n--- docker stderr (tail) ---\n" + docker_stderr_tail[-12000:],
        ]
    )
    if django_runtests and patch_junit.is_file():
        log_tail = patch_junit.read_text(encoding="utf-8", errors="replace")
        lines.append("\n--- runtests log (tail) ---\n" + log_tail[-16_000:])
    lines.append("\n--- FAIL ---")
    for nid, msg in failures[:40]:
        lines.append(f"{nid}\n  {msg[:800]}")
    lines.append("\n--- ERROR ---")
    for nid, msg in errors[:40]:
        lines.append(f"{nid}\n  {msg[:800]}")
    hint = pytest_argv_mismatch_hint(failures)
    if hint:
        lines.append("\n--- hint ---\n" + hint)
    ctx = build_failure_source_context(repo_root, failures, errors)
    if ctx:
        lines.append("\n--- failing test source ---\n" + ctx)
    if sl:
        lines.append("\n--- SKIP (sample) ---")
        for nid, msg in sl[:25]:
            lines.append(f"{nid}\n  {msg[:500]}")
    return "\n".join(lines)


def _has_fixable_env_skips(skips: list[tuple[str, str]]) -> bool:
    return any(_FIXABLE_SKIP.search(msg) for _, msg in skips)


def _apply_pip_freeze_to_row(row: dict[str, Any], work: Path, instance_id: str) -> None:
    fz = work / "pip-freeze.txt"
    if not fz.is_file():
        return
    txt = fz.read_text(encoding="utf-8", errors="replace").strip()
    if txt:
        row["requirements"] = txt
        print(
            f"  {instance_id}: pip freeze -> requirements ({len(txt.splitlines())} lines)",
            file=sys.stderr,
        )


def _docker_log_tail_for_display(stderr: str, stdout: str, *, max_len: int = 4000) -> str:
    """Prefer harness/patch/Gradle lines over apt install progress noise."""
    lines = (stderr + "\n" + stdout).splitlines()
    markers = (
        "[docker]",
        "[harvest]",
        "BUILD FAILED",
        "BUILD SUCCESSFUL",
        "What went wrong:",
        "Caused by:",
        "FAILURE:",
        "patch apply",
        "Corrupt patch",
        "npm ERR!",
        "composer install",
        "Your requirements could not be resolved",
        "Problem 1",
        "Script ",
        "error:",
    )
    picked = [ln for ln in lines if any(m in ln for m in markers)]
    if picked:
        return "\n".join(picked[-60:])
    return (stderr + "\n" + stdout)[-max_len:]


def _docker_install_failed(
    *,
    docker_exit: int,
    n_patch: int,
    n_targets: int,
    log_tail: str,
    django_runtests: bool = False,
    install_config: dict[str, Any] | None = None,
    lang: str = "python",
) -> bool:
    gradle = (
        install_config is not None
        and str(install_config.get("java_build_system") or "").lower() == "gradle"
    )
    maven = (
        install_config is not None
        and str(install_config.get("java_build_system") or "").lower() == "maven"
    )
    if docker_exit != 0:
        from .harness_guards import log_indicates_patch_apply_failed

        if log_indicates_patch_apply_failed(log_tail):
            return False
        if (
            gradle
            and log_indicates_gradle_build_ok(log_tail)
            and n_patch > 0
            and "patch apply check failed" not in log_tail.lower()
            and "corrupt patch" not in log_tail.lower()
        ):
            return False
        low = log_tail.lower()
        from .harness_guards import (
            log_indicates_gradle_build_failed_during_tests,
            log_indicates_gradle_no_tests_found_for_includes,
        )

        if (
            lang == "java"
            and gradle
            and log_indicates_gradle_build_failed_during_tests(log_tail)
            and "[docker] applying patch" in low
        ):
            return False
        if (
            lang == "java"
            and gradle
            and log_indicates_gradle_no_tests_found_for_includes(log_tail)
        ):
            return False
        native_integration = (
            install_config is not None and install_config.get("native_integration_build")
        )
        if (
            lang in ("python", "c")
            and native_integration
            and "[docker] integration pytest" in log_tail
            and "patch apply check failed" not in low
            and "patch apply failed" not in low
            and "corrupt patch" not in low
        ):
            return False
        return True
    # runtests entry uses ``|| true``; empty parse is a discovery issue, not pip install.
    # JavaScript: empty JUnit is usually test_cmd/reporter — not install (unless docker exited).
    if lang == "javascript" and docker_exit == 0:
        return False
    # PHP: empty JUnit is usually test_cmd/runner — not composer install (unless docker exited).
    if lang == "php" and docker_exit == 0:
        return False
    # C harness may compile successfully but emit non-path-matching/empty JUnit.
    # Treat this as discovery mismatch, not install failure, when docker run succeeded.
    if (
        lang == "c"
        and docker_exit == 0
        and n_targets > 0
        and n_patch == 0
        and not (install_config or {}).get("native_integration_build")
    ):
        from .runtests_build import cmake_runtests_discover_active

        if cmake_runtests_discover_active(install_config or {}):
            return False
        if "ctest" in str((install_config or {}).get("test_cmd") or "").lower():
            return False
        return False
    if (
        lang == "go"
        and docker_exit == 0
        and n_targets > 0
        and n_patch == 0
    ):
        return False
    if (
        lang == "ruby"
        and docker_exit == 0
        and n_targets > 0
        and n_patch == 0
    ):
        from .ruby_build import log_indicates_ruby_gem_not_found

        if log_indicates_ruby_gem_not_found(log_tail):
            return True
        return False
    if lang == "python" and docker_exit == 0 and n_targets > 0 and n_patch == 0:
        low = log_tail.lower()
        install_markers = (
            "no module named",
            "modulenotfounderror",
            "metadata-generation-failed",
            "could not find a version",
            "pip install",
            "subprocess-exited-with-error",
            "error: failed",
        )
        if any(m in low for m in install_markers):
            return True
        if any(
            m in low
            for m in (
                "pytest",
                "collecting",
                "collected 0 items",
                "junit",
                "no tests ran",
            )
        ):
            return False
    if (
        lang in ("python", "c")
        and docker_exit == 0
        and n_targets > 0
        and n_patch == 0
        and install_config is not None
        and install_config.get("native_integration_build")
    ):
        return False
    if not django_runtests and n_targets > 0 and n_patch == 0:
        if gradle and log_indicates_gradle_build_ok(log_tail):
            if "patch apply check failed" not in log_tail.lower():
                return False
        if maven and log_indicates_maven_tests_ran(log_tail):
            if "patch apply check failed" not in log_tail.lower():
                return False
        from .harness_guards import (
            log_indicates_gradle_build_failed_during_tests,
            log_indicates_gradle_no_tests_found_for_includes,
        )

        if (
            lang == "java"
            and gradle
            and log_indicates_gradle_build_failed_during_tests(log_tail)
        ):
            return False
        if (
            lang == "java"
            and gradle
            and log_indicates_gradle_no_tests_found_for_includes(log_tail)
        ):
            return False
        return True
    low = log_tail.lower()
    if "pyprojectoptionexception" in low and "qmake" in low:
        return True
    if "metadata-generation-failed" in low and ("pyqt" in low or "pyside" in low):
        return True
    if "subprocess-exited-with-error" in low and "preparing metadata" in low:
        return True
    if "requires a different python" in low and ">=3.12" in low:
        return True
    if "does not seem to be a meson source directory" in low:
        return True
    if "failed building wheel for pylibmc" in low or "libmemcached/memcached.h" in low:
        return True
    return False


def _docker_install_config_effective(
    install_config: dict[str, Any],
    pr: ParsedPR,
    *,
    repo: Path | None = None,
) -> dict[str, Any]:
    """Sanitize ``install_config`` for headless Docker (replayable; includes meson-python guardrails)."""
    cfg = sanitize_install_config_for_docker(dict(install_config), pr.repo_id, repo=repo)

    dropped: list[str] = []

    def bad_pkg_line(s: str) -> bool:
        return bool(_DOCKER_SKIP_PKG.search(s))

    pkgs = cfg.get("pip_packages")
    if isinstance(pkgs, list):
        kept: list[str] = []
        for p in pkgs:
            if not isinstance(p, str) or not p.strip():
                continue
            if bad_pkg_line(p):
                dropped.append(p.strip())
                continue
            kept.append(p.strip())
        cfg["pip_packages"] = kept
    else:
        cfg["pip_packages"] = []

    pre = cfg.get("pre_install")
    if isinstance(pre, list):
        kept_pre: list[str] = []
        for ln in pre:
            if not isinstance(ln, str) or not ln.strip():
                continue
            if bad_pkg_line(ln):
                dropped.append(ln.strip())
                continue
            kept_pre.append(ln.strip())
        cfg["pre_install"] = kept_pre
    else:
        cfg["pre_install"] = []

    reqs = cfg.get("reqs_path")
    if isinstance(reqs, list):
        kept_r: list[str] = []
        for rel in reqs:
            if not isinstance(rel, str) or not rel.strip():
                continue
            if bad_pkg_line(rel):
                dropped.append(rel.strip())
                continue
            kept_r.append(rel.strip())
        cfg["reqs_path"] = kept_r
    else:
        cfg["reqs_path"] = []

    if dropped:
        print(
            f"  {pr.instance_id}: docker install_config sanitized (dropped {len(dropped)}): "
            f"{dropped[:5]}{'...' if len(dropped) > 5 else ''}",
            file=sys.stderr,
        )

    lang = str(cfg.get("language") or "").lower()
    if lang in ("ruby", "rb"):
        from .ruby_build import _filter_ruby_apt_packages

        apt = _filter_ruby_apt_packages(list(cfg.get("apt-pkgs") or []))
        if apt:
            cfg["apt-pkgs"] = apt
        elif cfg.get("apt-pkgs"):
            cfg["apt-pkgs"] = []

    if lang in ("python", "py", ""):
        from .python_build import finalize_python_install_config

        cfg = finalize_python_install_config(cfg, repo, repo_id=pr.repo_id)
        from .integration_build import (
            apply_native_build_if_integration,
            native_integration_http3_disabled,
        )

        if not native_integration_http3_disabled(cfg):
            cfg = apply_native_build_if_integration(cfg, repo)
        from .apt_from_log import sanitize_native_integration_apt_config

        cfg = sanitize_native_integration_apt_config(cfg)
    elif lang == "c" and repo is not None:
        from .runtests_build import sanitize_cmake_http3_for_harness

        cfg = sanitize_cmake_http3_for_harness(cfg, repo)
        cfg["language"] = "c"
    return cfg


def _result_paths(
    work: Path,
    language: str,
    *,
    result_format: str | None = None,
    install_config: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    fmt = result_format
    if fmt is None and install_config is not None:
        django_rt = uses_runtests_test_cmd(install_config) or bool(
            install_config.get("django_runtests")
        )
        fmt = _effective_result_format("", install_config, django_rt=django_rt)
    if fmt is None:
        fmt = get_language_spec(language).result_format
    if fmt == "junit":
        return work / "junit-base.xml", work / "junit-patch.xml"
    return work / "test-base.log", work / "test-patch.log"


def _effective_result_format(
    lang: str,
    install_config: dict[str, Any],
    *,
    django_rt: bool = False,
) -> str | None:
    if django_rt:
        return "django_log"
    fmt = install_config.get("result_format")
    if isinstance(fmt, str) and fmt.strip():
        return fmt.strip()
    if install_config.get("native_integration_build"):
        return "junit"
    if install_config.get("cmake_runtests_build"):
        return "runtests_log"
    from .c_build import is_premake_config

    if is_premake_config(install_config):
        return "googletest_log"
    if "ctest" in str(install_config.get("test_cmd") or "").lower():
        return "ctest_log"
    return None


def _sh_single_quoted(s: str) -> str:
    """Wrap for bash single-quoted string (escape embedded quotes)."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def shlex_quote_shell(s: str) -> str:
    if re.match(r"^[a-zA-Z0-9@%_+=:,./-]+$", s):
        return s
    return _sh_single_quoted(s)


def _write_install_bundle(work: Path, install_config: dict[str, Any]) -> None:
    """Write helper scripts under ``work/`` from ``install_config`` only (replayable)."""
    (work / "install_config.json").write_text(
        json.dumps(install_config, indent=2),
        encoding="utf-8",
    )

    pre = install_config.get("pre_install") or []
    if not isinstance(pre, list):
        pre = []
    pre_lines = ["#!/bin/bash", "set -e", "export DEBIAN_FRONTEND=noninteractive", "cd /w/repo"]
    pre_lines.extend(ln.strip() for ln in pre if isinstance(ln, str) and ln.strip())
    (work / "pre_install.sh").write_text("\n".join(pre_lines) + "\n", encoding="utf-8")

    pip_pkgs = install_config.get("pip_packages") or []
    if not isinstance(pip_pkgs, list):
        pip_pkgs = []
    plines = ["#!/bin/bash", "set -e", "cd /w/repo"]
    for p in pip_pkgs:
        if isinstance(p, str) and p.strip():
            plines.append(f'python3 -m pip install -q {shlex_quote_shell(p.strip())}')
    (work / "pip_packages.sh").write_text("\n".join(plines) + "\n", encoding="utf-8")

    reqs = install_config.get("reqs_path") or []
    if not isinstance(reqs, list):
        reqs = []
    rlines = ["#!/bin/bash", "set -e", "cd /w/repo"]
    for rel in reqs:
        if not isinstance(rel, str):
            continue
        rlines.append(f'if [[ -f {_sh_single_quoted(rel)} ]]; then python3 -m pip install -q -r {_sh_single_quoted(rel)}; fi')
    (work / "reqs_path.sh").write_text("\n".join(rlines) + "\n", encoding="utf-8")

    install_cmd = str(install_config.get("install") or "pip install -e .").strip()
    (work / "project_install.sh").write_text(
        "#!/bin/bash\nset -e\ncd /w/repo\n" + install_cmd + "\n",
        encoding="utf-8",
    )

    post = install_config.get("post_install") or []
    if not isinstance(post, list):
        post = []
    post_lines = ["#!/bin/bash", "set -e", "cd /w/repo"]
    post_lines.extend(ln.strip() for ln in post if isinstance(ln, str) and ln.strip())
    custom = (os.environ.get("SWEBENCH_POST_CLONE_SH") or "").strip()
    if custom:
        post_lines.append(f"bash -lc {_sh_single_quoted(custom)}")
    (work / "post_install.sh").write_text("\n".join(post_lines) + "\n", encoding="utf-8")


def _read_phase_gradle_log(work: Path, phase: str) -> str:
    """Gradle harness tee log for one Docker phase (``base`` or ``patch``)."""
    name = "test-base.log" if phase == "base" else "test-patch.log"
    path = work / name
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _compute_f2p_p2p(
    base_map: dict[str, str],
    patch_map: dict[str, str],
    repo_root: Path,
    lang: str,
    *,
    django_runtests: bool = False,
) -> tuple[list[str], list[str], dict[str, int]]:
    """
    Classify fail→pass vs pass→pass.

    FAIL_TO_PASS: not passing (or absent) at base+test_patch, passing at test_patch+impl.
    PASS_TO_PASS: passing in both runs.

    Third element is classification stats for discover logging.
    """
    cand_f2p: list[str] = []
    cand_p2p: list[str] = []
    all_ids = sorted(set(base_map) | set(patch_map))
    for nid in all_ids:
        b = base_map.get(nid)
        a = patch_map.get(nid)
        if (b or "").upper() == "SKIPPED":
            continue
        if not outcome_passed(a):
            continue
        if b is None or not outcome_passed(b):
            cand_f2p.append(nid)
        else:
            cand_p2p.append(nid)
    f2p, dropped_f2p = filter_swebench_gradable_nodeids(
        cand_f2p,
        repo_root,
        for_pass_to_pass=False,
        language=lang,
        django_runtests=django_runtests,
    )
    p2p, dropped_p2p = filter_swebench_gradable_nodeids(
        cand_p2p,
        repo_root,
        for_pass_to_pass=True,
        language=lang,
        django_runtests=django_runtests,
    )
    stats = {
        "cand_f2p": len(cand_f2p),
        "cand_p2p": len(cand_p2p),
        "dropped_f2p": len(dropped_f2p),
        "dropped_p2p": len(dropped_p2p),
        "all_passed_both_phases": len(cand_p2p) if not cand_f2p else 0,
    }
    if not cand_f2p and cand_p2p:
        stats["all_passed_both_phases"] = len(cand_p2p)
    return f2p, p2p, stats


def _native_integration_pytest_root(cfg: dict[str, Any]) -> str:
    if not cfg.get("native_integration_build"):
        return ""
    return str(cfg.get("native_integration_pytest_root") or "").strip().strip("/")


def _filter_f2p_to_test_patch_scope(
    f2p: list[str],
    tp_only: list[str],
    lang: str,
    *,
    django_runtests: bool = False,
    native_integration_pytest_root: str = "",
    test_patch: str = "",
) -> list[str]:
    """Keep only FAIL_TO_PASS node ids that belong to ``test_patch`` paths."""
    if not tp_only:
        return f2p
    kept: list[str] = []
    if django_runtests:
        for nid in f2p:
            if _case_map_key_matches_paths(nid, tp_only):
                kept.append(nid)
        return kept
    path_set, dotted, java_fqcns = _path_filter_sets(tp_only)
    for nid in f2p:
        if _nodeid_in_test_patch_paths(
            nid,
            path_set,
            dotted,
            java_fqcns,
            test_patch_paths=tp_only,
            test_patch=test_patch,
            language=lang,
            native_integration_pytest_root=native_integration_pytest_root,
        ):
            kept.append(nid)
    return kept


def _pytest_plugin_bash_array(install_config: dict[str, Any]) -> str:
    plugins = install_config.get("pytest_plugins") or []
    if not isinstance(plugins, list):
        return ""
    parts: list[str] = []
    for p in plugins:
        if isinstance(p, str) and p.strip():
            parts.append(f'PYT_EXTRA+=(-p {shlex_quote_shell(p.strip())})')
    return "\n".join(parts)


def _docker_pytest_attempt(
    *,
    work: Path,
    pr: ParsedPR,
    eff_cfg: dict[str, Any],
    lang: str,
    pytest_targets: list[str],
    tp_only: list[str],
    targets: list[str],
    test_patch: str,
    docker_timeout: int,
    docker_pip_freeze_after: bool,
    attempt_label: str,
    row: dict[str, Any],
    force_rebuild_harness_images: bool = False,
    build_instance_harness_images: bool = False,
    llm_remediate: tuple[str, str, str, int] | None = None,
    remediation_max_rounds: int = 3,
    tests_only: bool = False,
    base_commit: str = "",
) -> dict[str, Any]:
    """One Docker install + test run before/after patch (pytest or Django runtests)."""
    eff_cfg.setdefault("language", lang)
    from .integration_build import discover_harness_language, native_integration_discover_active

    harness_lang = discover_harness_language(lang, eff_cfg)
    native_py = native_integration_discover_active(eff_cfg)
    django_rt = uses_runtests_test_cmd(eff_cfg) or bool(eff_cfg.get("django_runtests"))
    result_fmt = _effective_result_format(lang, eff_cfg, django_rt=django_rt)
    run_targets = paths_to_runtests_labels(pytest_targets) if django_rt else pytest_targets

    from .swebench_images import build_discover_image, write_harness_setup_repo_script

    discover_internal = internal_install_keys(eff_cfg)
    image, eff_cfg = build_discover_image(
        row,
        eff_cfg,
        harness_lang,
        force_rebuild=force_rebuild_harness_images,
        llm_remediate=llm_remediate,
        remediation_max_rounds=remediation_max_rounds,
        repo_id=pr.repo_id,
        build_instance_images=build_instance_harness_images,
    )
    eff_cfg = merge_internal_install_keys(eff_cfg, discover_internal)
    result_fmt = _effective_result_format(lang, eff_cfg, django_rt=django_rt)

    if not build_instance_harness_images:
        write_harness_setup_repo_script(work, row, eff_cfg, harness_lang)

    testbed_dir: Path | None = None
    if not build_instance_harness_images:
        testbed_dir = work / "testbed"
        testbed_dir.mkdir(parents=True, exist_ok=True)

    write_entry_script(
        work,
        harness_lang,
        run_targets,
        eff_cfg,
        run_pip_freeze=docker_pip_freeze_after,
        harness_image=True,
        harness_env_only=not build_instance_harness_images,
        tests_only=tests_only,
    )

    env = os.environ.copy()
    workdir = "/testbed" if build_instance_harness_images else "/"
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{work}:/w",
        "-w",
        workdir,
    ]
    if testbed_dir is not None:
        cmd.extend(["-v", f"{testbed_dir}:/testbed"])
    if base_commit.strip():
        cmd.extend(["-e", f"SWEBENCH_BASE_COMMIT={base_commit.strip()}"])
    if eff_cfg.get("discovery_patch_full_suite"):
        cmd.extend(["-e", "DISCOVERY_PATCH_FULL_SUITE=1"])
    for k in ("SWEBENCH_POST_CLONE_SH", "GITHUB_TOKEN", "GH_TOKEN"):
        if env.get(k):
            cmd.extend(["-e", f"{k}={env[k]}"])
    cmd.extend(["-e", "PIP_ROOT_USER_ACTION=ignore"])
    if docker_pip_freeze_after:
        cmd.extend(["-e", "RUN_PIP_FREEZE=1"])
    cmd.extend([image, "bash", "/w/docker_entry.sh"])

    n_paths = len(run_targets)
    if django_rt:
        runner = "runtests"
    elif lang == "java":
        runner = (
            "gradle"
            if str(eff_cfg.get("java_build_system") or "").lower() == "gradle"
            else "mvn"
        )
    elif lang == "go":
        runner = "go test"
    elif lang == "rust":
        runner = "cargo"
    elif lang == "javascript":
        runner = "npm test"
    elif lang == "php":
        runner = "phpunit"
    elif lang == "ruby":
        runner = "rspec"
    elif lang == "c":
        if native_py:
            runner = "pytest (native integration)"
        else:
            from .c_build import is_premake_config
            from .runtests_build import cmake_runtests_discover_active

            if cmake_runtests_discover_active(eff_cfg):
                runner = "runtests.pl"
            elif is_premake_config(eff_cfg):
                runner = "premake5 test"
            else:
                runner = "ctest"
    else:
        runner = "pytest"
    harness_label = "harness-instance" if build_instance_harness_images else "harness-env"
    mode_label = "tests-only" if tests_only else harness_label
    print(
        f"  {pr.instance_id}: docker discover using {image} [{mode_label}] "
        f"({runner} {n_paths} target(s); {attempt_label}) ...",
        file=sys.stderr,
    )
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=docker_timeout, env=env)
    dstderr = r.stderr or ""
    dstdout = r.stdout or ""
    log_tail = dstderr + "\n" + dstdout
    for line in dstderr.splitlines():
        if (
            "[docker] pytest" in line
            or "[docker] runtests" in line
            or "[docker] gradle" in line
            or "[docker] js" in line
            or "[harvest]" in line
            or "[docker] applying" in line
            or "[docker] patch apply" in line
            or "[docker] premake" in line
        ):
            print(f"  {pr.instance_id}: {line.strip()}", file=sys.stderr)
    if r.returncode != 0:
        print(
            f"  {pr.instance_id}: docker run exit {r.returncode} (install or entry script failed)",
            file=sys.stderr,
        )
        print(_docker_log_tail_for_display(dstderr, dstdout, max_len=6000), file=sys.stderr)

    repo_root = testbed_dir if testbed_dir is not None else (work / "repo")
    base_result, patch_result = _result_paths(
        work, lang, result_format=result_fmt, install_config=eff_cfg
    )
    base_map = parse_test_status_map(base_result, repo_root, lang, result_format=result_fmt)
    patch_map = parse_test_status_map(patch_result, repo_root, lang, result_format=result_fmt)
    refine_junit = False
    if lang == "ruby" and pytest_targets:
        from .ruby_build import refine_ruby_junit_maps_for_discover

        base_map, patch_map = refine_ruby_junit_maps_for_discover(
            base_map,
            patch_map,
            test_patch_paths=pytest_targets,
            work_dir=work,
        )
        refine_junit = True
    elif lang == "python" and pytest_targets:
        from .python_build import refine_python_junit_maps_for_discover

        base_map, patch_map = refine_python_junit_maps_for_discover(
            base_map,
            patch_map,
            test_patch_paths=pytest_targets,
            work_dir=work,
            test_patch=str(row.get("test_patch") or ""),
        )
        refine_junit = True
    n_base = (
        len(base_map)
        if refine_junit and pytest_targets
        else test_reported_count(base_result, lang, result_format=result_fmt)
    )
    n_patch = (
        len(patch_map)
        if refine_junit and pytest_targets
        else test_reported_count(patch_result, lang, result_format=result_fmt)
    )
    java_gradle = (
        lang == "java" and str(eff_cfg.get("java_build_system") or "").lower() == "gradle"
    )
    from .c_build import is_premake_config

    premake_two_phase = lang == "c" and is_premake_config(eff_cfg)
    from .runtests_build import cmake_runtests_discover_active

    cmake_runtests_two_phase = lang == "c" and cmake_runtests_discover_active(eff_cfg)
    native_c_pytest = lang == "c" and bool(eff_cfg.get("native_integration_build"))
    two_phase_js = lang == "javascript"
    two_phase_py = lang == "python" or native_c_pytest
    two_phase_rust = lang == "rust"
    two_phase_go = lang == "go"
    two_phase_php = lang == "php"
    two_phase_ruby = lang == "ruby"
    two_phase = (
        java_gradle
        or premake_two_phase
        or cmake_runtests_two_phase
        or two_phase_js
        or two_phase_py
        or two_phase_rust
        or two_phase_go
        or two_phase_php
        or two_phase_ruby
    )
    before_label = "base+test_patch" if two_phase else "before patch"
    after_label = "test_patch+impl" if two_phase else "after patch"
    print(
        f"  {pr.instance_id}: tests reported — "
        f"{before_label}: {n_base}, {after_label}: {n_patch} (language={lang})",
        file=sys.stderr,
    )
    if (
        lang == "java"
        and str(eff_cfg.get("java_build_system") or "").lower() == "gradle"
        and n_patch == 0
        and pytest_targets
    ):
        print(
            f"  {pr.instance_id}: gradle junit empty — check test_cmd and "
            f"module build/test-results; docker stderr tail:\n"
            f"{_docker_log_tail_for_display(dstderr, dstdout, max_len=3000)}",
            file=sys.stderr,
        )
        for log_name in ("test-base.log", "test-patch.log"):
            log_path = work / log_name
            if not log_path.is_file():
                continue
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                tail = ""
            if tail.strip():
                print(
                    f"  {pr.instance_id}: gradle {log_name} tail:\n{tail}",
                    file=sys.stderr,
                )
        for phase in ("base", "patch"):
            phase_log = _read_phase_gradle_log(work, phase)
            if phase_log.strip():
                print(
                    f"  {pr.instance_id}: gradle {phase} phase log tail:\n{phase_log[-4000:]}",
                    file=sys.stderr,
                )
    if lang == "javascript" and n_patch == 0 and pytest_targets:
        patch_log = _read_phase_gradle_log(work, "patch")
        print(
            f"  {pr.instance_id}: javascript junit empty — check npm/nps stage before jest-junit. "
            f"docker stderr/stdout tail:\n{_docker_log_tail_for_display(dstderr, dstdout, max_len=3000)}",
            file=sys.stderr,
        )
        if patch_log.strip():
            print(
                f"  {pr.instance_id}: javascript test-patch.log tail:\n{patch_log[-4000:]}",
                file=sys.stderr,
            )
    if lang == "ruby" and n_patch == 0 and pytest_targets:
        print(
            f"  {pr.instance_id}: ruby junit empty — check bundle install, "
            f"RspecJunitFormatter, and scoped spec paths. "
            f"docker stderr/stdout tail:\n{_docker_log_tail_for_display(dstderr, dstdout, max_len=3000)}",
            file=sys.stderr,
        )
        for log_name in ("test-base.log", "test-patch.log"):
            log_path = work / log_name
            if not log_path.is_file():
                continue
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                tail = ""
            if tail.strip():
                print(
                    f"  {pr.instance_id}: ruby {log_name} tail:\n{tail}",
                    file=sys.stderr,
                )
    if lang == "php" and n_patch == 0 and pytest_targets:
        print(
            f"  {pr.instance_id}: php junit empty — check test-patch.log, "
            f"impl.patch apply, and simple-phpunit scope. "
            f"docker stderr/stdout tail:\n"
            f"{_docker_log_tail_for_display(dstderr, dstdout, max_len=3000)}",
            file=sys.stderr,
        )
        for log_name in ("test-base.log", "test-patch.log"):
            log_path = work / log_name
            if not log_path.is_file():
                continue
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                tail = ""
            if tail.strip():
                print(
                    f"  {pr.instance_id}: php {log_name} tail:\n{tail}",
                    file=sys.stderr,
                )
    if (
        (lang == "python" or native_c_pytest)
        and n_patch == 0
        and pytest_targets
        and eff_cfg.get("native_integration_build")
    ):
        for log_name in ("test-base.log", "test-patch.log"):
            log_path = work / log_name
            if not log_path.is_file():
                continue
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-5000:]
            except OSError:
                tail = ""
            if tail.strip():
                print(
                    f"  {pr.instance_id}: integration {log_name} tail:\n{tail}",
                    file=sys.stderr,
                )
        print(
            f"  {pr.instance_id}: native integration junit empty — check testdeps, "
            f"integration servers (cmake find_program), and pytest log. "
            f"docker stderr/stdout tail:\n"
            f"{_docker_log_tail_for_display(dstderr, dstdout, max_len=3000)}",
            file=sys.stderr,
        )
    if (
        lang == "java"
        and str(eff_cfg.get("java_build_system") or "").lower() == "gradle"
    ):
        from .swebench_align import canonicalize_java_gradle_test_maps

        # Per-phase tee logs only — using combined docker output marks every test
        # PASSED in both maps when the second Gradle run succeeds (false P2P).
        base_map = canonicalize_java_gradle_test_maps(
            base_map, _read_phase_gradle_log(work, "base")
        )
        patch_map = canonicalize_java_gradle_test_maps(
            patch_map, _read_phase_gradle_log(work, "patch")
        )

    native_pytest_root = _native_integration_pytest_root(eff_cfg)
    if tp_only:
        if django_rt:
            pa, fa, ea, sk, tot = django_outcome_counts_for_paths(patch_map, tp_only)
        else:
            pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(
                patch_map,
                tp_only,
                language=lang,
                native_integration_pytest_root=native_pytest_root,
                test_patch=test_patch,
            )
    else:
        pa, fa, ea, sk, tot = junit_outcome_counts_all(patch_map)

    fl: list[tuple[str, str]] = []
    el: list[tuple[str, str]] = []
    sl: list[tuple[str, str]] = []
    if tp_only and (fa or ea or sk):
        if django_rt:
            fl, el, sl = django_fail_error_skip_messages_for_paths(patch_result, tp_only)
        elif get_language_spec(lang).result_format == "junit":
            fl, el, sl = junit_fail_error_skip_messages_for_paths(
                patch_result,
                repo_root,
                tp_only,
                language=lang,
                native_integration_pytest_root=native_pytest_root,
                test_patch=test_patch,
            )
    elif (fa or ea or sk) and get_language_spec(lang).result_format == "junit":
        fl, el, sl = junit_fail_error_skip_messages_limited(
            patch_result, repo_root, limit=200, language=lang
        )

    if tp_only:
        extra = ""
        if tot > pa:
            extra = f" (failed={fa}, error={ea}, skipped={sk})"
        elif tot == 0:
            if django_rt and patch_result.is_file() and patch_result.stat().st_size > 0:
                extra = " (runtests log present but no cases matched test_patch labels)"
            elif lang == "go" and patch_map:
                extra = " (no gotest cases matched test_patch Test* names)"
            elif lang == "ruby" and patch_map:
                extra = " (no rspec junit cases matched test_patch *_spec.rb paths)"
            else:
                extra = " (no junit cases matched test_patch paths)"
        print(
            f"  {pr.instance_id}: test_patch tests after apply — {pa}/{tot} passed{extra}",
            file=sys.stderr,
        )
        if tot == 0 and patch_map:
            log_junit_test_patch_mismatch(
                pr.instance_id,
                patch_map,
                tp_only,
                native_integration_pytest_root=native_pytest_root,
                test_patch=test_patch,
                language=lang,
            )
        if fa or ea or sk:
            _print_test_patch_junit_diagnostics(pr.instance_id, fl, el, sl)
    else:
        print(
            f"  {pr.instance_id}: test_patch tests after apply — n/a "
            f"(no test .py paths only in test_patch; failure={fa} error={ea} skipped={sk})",
            file=sys.stderr,
        )

    install_failed = _docker_install_failed(
        docker_exit=r.returncode,
        n_patch=n_patch if not django_rt else len(patch_map),
        n_targets=len(targets),
        log_tail=log_tail,
        django_runtests=django_rt,
        install_config=eff_cfg,
        lang=lang,
    )

    f2p: list[str] = []
    p2p: list[str] = []
    f2p_stats: dict[str, int] = {}
    if not install_failed and (n_patch > 0 or (django_rt and patch_map)):
        f2p, p2p, f2p_stats = _compute_f2p_p2p(
            base_map, patch_map, repo_root, lang, django_runtests=django_rt
        )

    tp_mismatch = bool(
        tp_only
        and patch_map
        and has_test_patch_label_mismatch(
            patch_map,
            tp_only,
            django_runtests=django_rt,
            language=lang,
            native_integration_pytest_root=native_pytest_root,
            test_patch=test_patch,
        )
    )
    after_patch_empty = bool(
        tp_only and n_base > 0 and len(patch_map) == 0 and n_patch == 0
    )
    if lang == "ruby" and after_patch_empty:
        from .ruby_build import rspec_log_indicates_all_passed

        patch_log = work / "test-patch.log"
        if patch_log.is_file() and rspec_log_indicates_all_passed(
            patch_log.read_text(encoding="utf-8", errors="replace")
        ):
            after_patch_empty = False
    from .harness_guards import log_indicates_patch_apply_failed

    patch_apply_failed = r.returncode != 0 and log_indicates_patch_apply_failed(log_tail)

    return {
        "f2p": f2p,
        "p2p": p2p,
        "f2p_stats": f2p_stats,
        "n_base": n_base,
        "n_patch": len(patch_map) if django_rt else n_patch,
        "pa": pa,
        "fa": fa,
        "ea": ea,
        "sk": sk,
        "tot": tot,
        "test_patch_label_mismatch": tp_mismatch,
        "after_patch_empty": after_patch_empty,
        "patch_apply_failed": patch_apply_failed,
        "install_failed": install_failed,
        "fl": fl,
        "el": el,
        "sl": sl,
        "patch_result": patch_result,
        "repo_root": repo_root,
        "install_config": eff_cfg,
        "dstderr": dstderr,
        "dstdout": dstdout,
        "docker_exit": r.returncode,
        "testbed_ready": bool(
            not install_failed
            and (
                build_instance_harness_images
                or (testbed_dir is not None and (testbed_dir / ".git").is_dir())
            )
        ),
    }


def _reset_docker_repo(repo: Path, base_commit: str, clone_timeout: int) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "reset", "--hard", base_commit],
        check=True,
        timeout=min(clone_timeout, 300),
    )
    subprocess.run(
        ["git", "-C", str(repo), "clean", "-ffdx", "-e", "subprojects"],
        check=False,
        timeout=min(clone_timeout, 300),
    )


def discover_fail_to_pass_pass_to_pass_docker(
    row: dict[str, Any],
    pr: ParsedPR,
    install_config: dict[str, Any],
    language: str,
    *,
    work_parent: Path,
    clone_timeout: int,
    clone_depth: int,
    docker_timeout: int,
    llm_remediate: tuple[str, str, str, int] | None = None,
    remediation_max_rounds: int = 3,
    test_patch_apply_attempts: int = TEST_PATCH_APPLY_MAX_ATTEMPTS,
    docker_pip_freeze_after: bool = False,
    remediate_skips: bool = False,
    force_rebuild_harness_images: bool = False,
    build_instance_harness_images: bool = False,
) -> tuple[list[str], list[str], str]:
    """
    Run install + tests before/after applying patches inside ``docker run``.

    Optional LLM remediation: up to ``remediation_max_rounds`` attempts. When install fails or
    tests fail/error, an LLM updates ``install_config``; the next attempt replays that config
    (install + pytest) and refreshes ``FAIL_TO_PASS`` / ``PASS_TO_PASS`` and ``requirements``.

    When tests fail, error, or fail to collect, an LLM updates ``install_config`` (e.g. Django
    ``DJANGO_SETTINGS_MODULE``) and Docker re-runs install + pytest on the same test_patch paths.

    Optional skips (moto, fastparquet, …) do **not** trigger another round unless
    ``remediate_skips`` is true — add those deps via ``install_config`` / ``requirements`` only
    when you opt in.
    """
    if shutil.which("docker") is None:
        print("  docker not in PATH; skip docker discover", file=sys.stderr)
        return [], [], ""

    from .docker_runtime import (
        docker_daemon_available,
        docker_daemon_error_message,
        is_docker_daemon_unavailable_error,
    )

    daemon_ok, daemon_reason = docker_daemon_available()
    if not daemon_ok:
        print(
            f"  {pr.instance_id}: skip docker discover — "
            f"{docker_daemon_error_message(daemon_reason)}",
            file=sys.stderr,
        )
        return [], [], ""

    lang = normalize_language(language)
    patch = str(row.get("patch") or "")
    test_patch = str(row.get("test_patch") or "")
    base_commit = str(row.get("base_commit") or "")
    head_sha = str(row.get("head_commit") or "")
    test_patch_created_by_llm = False
    need_llm_test_patch = False

    patch_has_tests = bool(collect_test_targets(lang, patch, ""))
    tp_collect = collect_test_targets_from_test_patch(lang, test_patch)
    if lang == "c":
        from .integration_build import integration_pytest_paths_from_patches

        int_tp = integration_pytest_paths_from_patches(patch, test_patch)
        if int_tp:
            patch_has_tests = patch_has_tests or bool(
                integration_pytest_paths_from_patches(patch, "")
            )
            tp_collect = int_tp
    tp_heuristic = collect_heuristic_test_paths_from_patch(test_patch)
    if (
        llm_remediate
        and patch.strip()
        and not tp_collect
        and not tp_heuristic
        and not patch_has_tests
    ):
        need_llm_test_patch = True
        print(
            f"  {pr.instance_id}: no test_patch paths — LLM will create test_patch "
            f"(up to {max(test_patch_apply_attempts, remediation_max_rounds)} apply-check attempts)",
            file=sys.stderr,
        )

    targets = collect_test_targets(lang, patch, test_patch)
    if lang == "c":
        from .integration_build import merge_hybrid_c_integration_paths
        from .runtests_build import cmake_runtests_discover_active, collect_runtests_numbers

        _detection, runner = merge_hybrid_c_integration_paths(
            patch, test_patch, language=lang, test_paths=targets
        )
        if runner:
            targets = runner
        else:
            from .integration_build import patch_diff_touches_libtest
            from .runtests_build import collect_runtests_numbers

            if patch_diff_touches_libtest(test_patch):
                nums = collect_runtests_numbers(test_patch)
                if nums:
                    targets = [f"tests/data/test{n}" for n in nums]
                elif _detection:
                    targets = _detection
    if not targets:
        targets = collect_heuristic_test_paths_from_patch(
            "\n".join(p for p in (patch, test_patch) if p.strip())
        )
    if lang == "python":
        from .languages import filter_python_pytest_targets
        from .python_build import expand_pytest_discover_targets

        targets = expand_pytest_discover_targets(filter_python_pytest_targets(targets))
    if not targets and not need_llm_test_patch:
        print(
            f"  {pr.instance_id}: no test paths for language={lang} in patches; skip docker discover",
            file=sys.stderr,
        )
        return [], [], ""

    if llm_remediate:
        max_r = max(2, max(1, remediation_max_rounds))
    else:
        max_r = 1
    f2p: list[str] = []
    p2p: list[str] = []
    last_n_base = 0
    last_n_patch = 0
    last_tp_tot = 0

    work = Path(tempfile.mkdtemp(prefix="swe_rebench_docker_", dir=str(work_parent)))
    try:
        repo = work / "repo"
        clone_repo_at(pr, repo, base_commit, depth=clone_depth, timeout=clone_timeout)

        impl_body = strip_mailbox_to_unified(patch)
        test_body = strip_mailbox_to_unified(test_patch)
        apply_cap = max(test_patch_apply_attempts, remediation_max_rounds)

        if need_llm_test_patch and llm_remediate:
            api_key, base_url, model, to = llm_remediate
            suggested = collect_test_targets(lang, patch, "")
            if not suggested and lang == "java":
                from .java_build import suggest_test_paths_from_impl_patch

                suggested = suggest_test_paths_from_impl_patch(lang, patch)
            patched, ok = create_and_validate_test_patch(
                problem_statement=str(row.get("problem_statement") or ""),
                patch=patch,
                repo=repo,
                repo_id=str(row.get("repo") or pr.repo_id),
                hints_text=str(row.get("hints_text") or ""),
                api_key=api_key,
                base_url=base_url,
                model=model,
                timeout_s=to,
                language=lang,
                suggested_test_paths=suggested,
                instance_id=pr.instance_id,
                max_attempts=apply_cap,
            )
            if not ok:
                print(
                    f"  {pr.instance_id}: skip — test_patch could not pass git apply --check "
                    f"after {apply_cap} LLM attempts",
                    file=sys.stderr,
                )
                return [], [], ""
            test_patch = patched
            test_body = strip_mailbox_to_unified(patched)
            row["test_patch"] = patched
            test_patch_created_by_llm = True
            targets = collect_test_targets(lang, patch, test_patch)
            if not targets:
                targets = collect_heuristic_test_paths_from_patch(test_patch)
            if not targets:
                print(
                    f"  {pr.instance_id}: skip — LLM test_patch has no {lang} test file paths "
                    f"in diff --git headers",
                    file=sys.stderr,
                )
                return [], [], ""

        elif test_body.strip():
            from .patch_validate import recover_patches_heuristic, validate_git_patch

            ok_apply, apply_err = validate_git_patch(test_body, repo)
            if not ok_apply:
                recovered = recover_patches_heuristic(
                    pr,
                    repo,
                    base_commit=base_commit,
                    head_sha=head_sha,
                )
                if recovered is not None:
                    patch, test_patch = recovered
                    impl_body = strip_mailbox_to_unified(patch)
                    test_body = strip_mailbox_to_unified(test_patch)
                    row["patch"] = patch
                    row["test_patch"] = test_patch
                    ok_apply = True
                    print(
                        f"  {pr.instance_id}: recovered test_patch via heuristic re-split "
                        f"(was: {apply_err})",
                        file=sys.stderr,
                    )
            if not ok_apply and llm_remediate:
                api_key, base_url, model, to = llm_remediate
                patched, ok, err = remediate_test_patch_until_applies(
                    test_body,
                    repo,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout_s=to,
                    max_attempts=apply_cap,
                    language=lang,
                    repo_id=str(row.get("repo") or pr.repo_id),
                    problem_statement=str(row.get("problem_statement") or ""),
                    impl_patch=patch,
                    instance_id=pr.instance_id,
                    test_paths=tp_collect or targets,
                    base_commit=base_commit,
                    head_sha=head_sha,
                )
                if not ok:
                    print(
                        f"  {pr.instance_id}: skip — test_patch apply-check failed after "
                        f"{apply_cap} attempts: {err}",
                        file=sys.stderr,
                    )
                    return [], [], ""
                test_patch = patched
                test_body = strip_mailbox_to_unified(patched)
                row["test_patch"] = patched
            elif not ok_apply:
                print(
                    f"  {pr.instance_id}: skip — test_patch apply-check failed: {apply_err}",
                    file=sys.stderr,
                )
                return [], [], ""

        from .patch_validate import (
            PatchSplitUnrecoverableError,
            ensure_patch_commits_fetched,
            ensure_patches_for_base,
            validate_git_patch_stack,
        )

        patch = str(row.get("patch") or patch)
        test_patch = str(row.get("test_patch") or test_patch)
        impl_body = strip_mailbox_to_unified(patch)
        test_body = strip_mailbox_to_unified(test_patch)
        if base_commit.strip() and head_sha.strip():
            ensure_patch_commits_fetched(repo, base_commit, head_sha)
            try:
                patch, test_patch = ensure_patches_for_base(
                    pr,
                    repo,
                    base_commit=base_commit,
                    head_sha=head_sha,
                    patch=patch,
                    test_patch=test_patch,
                    diff="",
                    llm_split_used=False,
                    language=lang,
                )
                impl_body = strip_mailbox_to_unified(patch)
                test_body = strip_mailbox_to_unified(test_patch)
                row["patch"] = patch
                row["test_patch"] = test_patch
            except PatchSplitUnrecoverableError as ex:
                print(f"  {pr.instance_id}: skip — {ex}", file=sys.stderr)
                return [], [], ""
        else:
            ok_stack, stack_err = validate_git_patch_stack(test_body, impl_body, repo)
            if not ok_stack:
                print(
                    f"  {pr.instance_id}: skip — stacked patch apply failed pre-Docker: "
                    f"{stack_err}",
                    file=sys.stderr,
                )
                return [], [], ""

        (work / "impl.patch").write_text(impl_body, encoding="utf-8")
        (work / "test.patch").write_text(test_body, encoding="utf-8")

        tp_only = _test_patch_only_targets(lang, test_patch)
        pytest_targets = tp_only if tp_only else targets

        eff_cfg = _docker_install_config_effective(install_config, pr, repo=repo)
        if lang == "python" and repo is not None:
            from .integration_build import apply_native_build_if_integration
            from .python_build import merge_python_build_into_config

            from .apt_from_log import sanitize_native_integration_apt_config

            eff_cfg = apply_native_build_if_integration(
                eff_cfg,
                repo,
                test_paths=pytest_targets,
                test_patch=test_patch,
            )
            eff_cfg = sanitize_native_integration_apt_config(eff_cfg)
            eff_cfg = merge_python_build_into_config(
                eff_cfg,
                repo,
                pytest_targets,
                repo_id=str(row.get("repo") or pr.repo_id),
            )
            print(
                f"  {pr.instance_id}: python test_cmd="
                f"{str(eff_cfg.get('test_cmd') or '')[:80]!r}",
                file=sys.stderr,
            )
        if lang == "java":
            eff_cfg = merge_java_build_into_config(
                eff_cfg,
                repo,
                targets,
                llm=llm_remediate,
                repo_id=str(row.get("repo") or pr.repo_id),
                instance_id=pr.instance_id,
                patch=patch,
                test_patch=test_patch,
            )
            print(
                f"  {pr.instance_id}: java build_system={eff_cfg.get('java_build_system')} "
                f"docker_image={eff_cfg.get('docker_image')}",
                file=sys.stderr,
            )
        elif lang == "javascript":
            from .js_build import merge_js_build_into_config

            eff_cfg = merge_js_build_into_config(
                eff_cfg,
                repo,
                pytest_targets,
                repo_dir="/testbed",
            )
            print(
                f"  {pr.instance_id}: javascript node_version="
                f"{(eff_cfg.get('docker_specs') or {}).get('node_version')!r} "
                f"test_runner={eff_cfg.get('js_test_runner')!r} "
                f"test_cmd={str(eff_cfg.get('test_cmd') or '')[:80]!r}",
                file=sys.stderr,
            )
        elif lang == "go":
            from .go_build import ensure_go_docker_specs

            eff_cfg = ensure_go_docker_specs(eff_cfg, repo=repo, language="go")
            print(
                f"  {pr.instance_id}: go go_version="
                f"{(eff_cfg.get('docker_specs') or {}).get('go_version')!r} "
                f"test_cmd={str(eff_cfg.get('test_cmd') or '')[:80]!r}",
                file=sys.stderr,
            )
        elif lang == "ruby":
            from .ruby_build import ruby_install_config_for_repo

            eff_cfg = ruby_install_config_for_repo(repo, base=eff_cfg, test_paths=pytest_targets)
            print(
                f"  {pr.instance_id}: ruby ruby_version="
                f"{(eff_cfg.get('docker_specs') or {}).get('ruby_version')!r} "
                f"test_runner={eff_cfg.get('ruby_test_runner')!r} "
                f"test_cmd={str(eff_cfg.get('test_cmd') or '')[:80]!r}",
                file=sys.stderr,
            )
        elif lang == "rust":
            from .rust_build import rust_install_config_for_repo

            eff_cfg = rust_install_config_for_repo(repo, base=eff_cfg, targets=pytest_targets)
            feat_note = eff_cfg.get("cargo_features") or []
            print(
                f"  {pr.instance_id}: rust rust_version="
                f"{(eff_cfg.get('docker_specs') or {}).get('rust_version')!r} "
                f"cargo_features={feat_note!r} "
                f"test_cmd={str(eff_cfg.get('test_cmd') or '')[:80]!r}",
                file=sys.stderr,
            )
        elif lang == "php":
            from .php_build import php_install_config_for_repo

            eff_cfg = php_install_config_for_repo(repo, base=eff_cfg)
            print(
                f"  {pr.instance_id}: php php_version="
                f"{(eff_cfg.get('docker_specs') or {}).get('php_version')!r} "
                f"test_cmd={str(eff_cfg.get('test_cmd') or '')[:80]!r}",
                file=sys.stderr,
            )
        elif lang == "c":
            from .c_harness_router import apply_c_harness_router, c_harness_runner_label
            from .integration_build import (
                filter_integration_pytest_modules,
                merge_hybrid_c_integration_paths,
                native_integration_discover_active,
            )
            from .runtests_build import collect_runtests_numbers

            impl_patch = str(row.get("patch") or "")
            eff_cfg = apply_c_harness_router(
                eff_cfg,
                repo,
                patch=impl_patch,
                test_patch=test_patch,
                test_paths=tp_only or pytest_targets,
            )
            from .apt_from_log import sanitize_native_integration_apt_config

            eff_cfg = sanitize_native_integration_apt_config(eff_cfg)
            eff_cfg["language"] = "c"
            if native_integration_discover_active(eff_cfg):
                _det, runner = merge_hybrid_c_integration_paths(
                    impl_patch, test_patch, language="c"
                )
                if runner:
                    tp_only = _test_patch_only_targets("c", test_patch) or runner
                    pytest_targets = tp_only if tp_only else runner
                runnable = filter_integration_pytest_modules(pytest_targets)
                if not runnable:
                    print(
                        f"  {pr.instance_id}: skip docker discover — native integration "
                        f"router selected pytest but no runnable test_*.py in test_patch "
                        f"(got {pytest_targets!r})",
                        file=sys.stderr,
                    )
                    return [], [], ""
                pytest_targets = runnable
                tp_only = runnable
            elif eff_cfg.get("cmake_runtests_build"):
                nums = collect_runtests_numbers(test_patch)
                if nums:
                    tp_only = [f"tests/data/test{n}" for n in nums]
                    pytest_targets = tp_only
            runner_note = c_harness_runner_label(eff_cfg)
            print(
                f"  {pr.instance_id}: c build_system={runner_note} apt-pkgs="
                f"{(eff_cfg.get('apt-pkgs') or [])!r} "
                f"install={str(eff_cfg.get('install') or '')[:120]!r} "
                f"test_cmd={str(eff_cfg.get('test_cmd') or '')[:80]!r}",
                file=sys.stderr,
            )
        row["install_config"] = export_install_config_for_harness(eff_cfg, language=lang)
        last_slice_fa = 0
        last_slice_ea = 0
        last_slice_sk = 0
        last_install_failed = False
        last_metrics: dict[str, Any] = {}
        setup_ready = False
        last_env_fp: dict[str, Any] | None = install_config_affects_env_image(eff_cfg)
        from .integration_build import native_integration_already_applied

        integration_native_retried = native_integration_already_applied(eff_cfg)
        force_rebuild_env_next = False

        for attempt in range(1, max_r + 1):
            if attempt > 1:
                print(f"  {pr.instance_id}: docker remediation attempt {attempt}/{max_r}", file=sys.stderr)
                _reset_docker_repo(repo, base_commit, clone_timeout)

            rebuild_env = force_rebuild_harness_images or force_rebuild_env_next
            force_rebuild_env_next = False
            if attempt > 1 and last_env_fp is not None:
                rebuild_env = rebuild_env or (
                    install_config_affects_env_image(eff_cfg) != last_env_fp
                )

            last_metrics = _docker_pytest_attempt(
                work=work,
                pr=pr,
                eff_cfg=eff_cfg,
                lang=lang,
                pytest_targets=pytest_targets,
                tp_only=tp_only,
                targets=targets,
                test_patch=test_patch,
                docker_timeout=docker_timeout,
                docker_pip_freeze_after=docker_pip_freeze_after,
                attempt_label=f"install attempt {attempt}/{max_r}",
                row=row,
                force_rebuild_harness_images=rebuild_env,
                build_instance_harness_images=build_instance_harness_images,
                llm_remediate=llm_remediate,
                remediation_max_rounds=remediation_max_rounds,
                base_commit=base_commit,
            )
            if last_metrics.get("install_config"):
                eff_cfg = merge_internal_install_keys(
                    dict(last_metrics["install_config"]),
                    internal_install_keys(eff_cfg),
                )
                row["install_config"] = export_install_config_for_harness(eff_cfg, language=lang)
            f2p = list(last_metrics["f2p"])
            p2p = list(last_metrics["p2p"])
            fa = int(last_metrics["fa"])
            ea = int(last_metrics["ea"])
            sk = int(last_metrics["sk"])
            tot = int(last_metrics["tot"])
            pa = int(last_metrics["pa"])
            sl = last_metrics["sl"]
            install_failed = bool(last_metrics["install_failed"])
            last_slice_fa, last_slice_ea, last_slice_sk = fa, ea, sk
            last_n_base = int(last_metrics["n_base"])
            last_n_patch = int(last_metrics["n_patch"])
            last_tp_tot = tot
            log_tail = (last_metrics.get("dstderr") or "") + "\n" + (
                last_metrics.get("dstdout") or ""
            )
            build_failed = False
            test_target_failed = False
            if lang == "java":
                from .harness_guards import (
                    log_indicates_gradle_build_failed_during_tests,
                    log_indicates_gradle_no_tests_found_for_includes,
                )

                test_target_failed = log_indicates_gradle_no_tests_found_for_includes(
                    log_tail, n_patch=last_n_patch
                )
                build_failed = (
                    log_indicates_gradle_build_failed_during_tests(log_tail)
                    and last_n_patch == 0
                    and not test_target_failed
                )
                if install_failed and (build_failed or test_target_failed):
                    install_failed = False
            last_install_failed = install_failed
            if last_metrics.get("testbed_ready"):
                setup_ready = True
            if not bool(last_metrics.get("after_patch_empty")):
                eff_cfg.pop("discovery_patch_full_suite", None)

            if not install_failed and (last_n_patch > 0 or len(f2p) > 0):
                if docker_pip_freeze_after:
                    _apply_pip_freeze_to_row(row, work, pr.instance_id)
                stats = last_metrics.get("f2p_stats") or {}
                stats_tail = ""
                if stats:
                    stats_tail = (
                        f" cand_f2p={stats.get('cand_f2p', 0)}"
                        f" dropped_ungradable_f2p={stats.get('dropped_f2p', 0)}"
                        f" cand_p2p={stats.get('cand_p2p', 0)}"
                        f" all_passed_both_phases={stats.get('all_passed_both_phases', 0)}"
                    )
                print(
                    f"  {pr.instance_id}: docker FAIL_TO_PASS={len(f2p)} PASS_TO_PASS={len(p2p)}"
                    f"{stats_tail} (from install_config run, attempt {attempt})",
                    file=sys.stderr,
                )
            elif build_failed:
                print(
                    f"  {pr.instance_id}: gradle BUILD FAILED on attempt {attempt} "
                    f"(not install failure); "
                    f"keeping prior FAIL_TO_PASS={len(f2p)} PASS_TO_PASS={len(p2p)}",
                    file=sys.stderr,
                )
            elif test_target_failed:
                print(
                    f"  {pr.instance_id}: gradle --tests filter matched no tests on "
                    f"attempt {attempt} (wrong module/FQCN, not install); "
                    f"keeping prior FAIL_TO_PASS={len(f2p)} PASS_TO_PASS={len(p2p)}",
                    file=sys.stderr,
                )
            elif install_failed:
                print(
                    f"  {pr.instance_id}: install failed on attempt {attempt}; "
                    f"keeping prior FAIL_TO_PASS={len(f2p)} PASS_TO_PASS={len(p2p)}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  {pr.instance_id}: no junit after patch on attempt {attempt}; "
                    f"keeping prior FAIL_TO_PASS={len(f2p)} PASS_TO_PASS={len(p2p)}",
                    file=sys.stderr,
                )

            fixable_sk = _has_fixable_env_skips(sl)
            slice_only_errors = bool(tp_only) and (fa + ea) > 0 and not install_failed
            junit_empty = (
                not install_failed
                and len(pytest_targets or targets) > 0
                and last_n_patch == 0
                and lang in ("java", "javascript", "python", "php")
            )
            gradle_module_mismatch = False
            if lang == "java":
                from .java_build import (
                    extract_gradle_projects_output_from_log,
                    log_indicates_gradle_module_slice_mismatch,
                    log_indicates_gradle_project_not_found,
                )
                from .harness_guards import log_indicates_gradle_no_tests_found_for_includes

                gradle_module_mismatch = (
                    log_indicates_gradle_module_slice_mismatch(
                        n_base=last_n_base,
                        n_patch=last_n_patch,
                        tp_tot=last_tp_tot,
                    )
                    or test_target_failed
                    or log_indicates_gradle_project_not_found(log_tail)
                    or log_indicates_gradle_no_tests_found_for_includes(
                        log_tail, n_patch=last_n_patch
                    )
                )
                gradle_projects_log = extract_gradle_projects_output_from_log(log_tail)
            else:
                gradle_projects_log = ""
            after_patch_empty = bool(last_metrics.get("after_patch_empty"))
            patch_apply_failed = bool(last_metrics.get("patch_apply_failed"))
            patch_junit_ok = (
                not install_failed
                and last_n_patch > 0
                and last_tp_tot > 0
                and not patch_apply_failed
            )
            if patch_junit_ok:
                test_target_failed = False
                gradle_module_mismatch = False
            needs_junit_fix = (
                junit_empty
                and not after_patch_empty
                and not patch_apply_failed
                and (
                    lang == "javascript"
                    or lang == "php"
                    or log_indicates_maven_tests_ran(log_tail)
                    or log_indicates_gradle_build_ok(log_tail)
                    or gradle_module_mismatch
                    or (
                        lang == "python"
                        and native_integration_already_applied(eff_cfg)
                    )
                )
            )
            needs_fix = (
                install_failed
                or build_failed
                or test_target_failed
                or needs_junit_fix
                or (
                    (ea > 0 or (remediate_skips and fixable_sk)) and not slice_only_errors
                )
            )
            if slice_only_errors and not install_failed:
                from .python_build import (
                    finalize_python_install_config,
                    needs_dateutil_zoneinfo,
                    slice_failures_are_dateutil_zoneinfo,
                )

                zoneinfo_env = (
                    lang == "python"
                    and needs_dateutil_zoneinfo(repo=repo)
                    and slice_failures_are_dateutil_zoneinfo(
                        list(last_metrics.get("fl") or []),
                        list(last_metrics.get("el") or []),
                    )
                )
                if zoneinfo_env and not last_metrics.get("dateutil_zoneinfo_retried"):
                    eff_cfg = finalize_python_install_config(
                        eff_cfg, repo, repo_id=pr.repo_id
                    )
                    row["install_config"] = export_install_config_for_harness(eff_cfg)
                    print(
                        f"  {pr.instance_id}: dateutil zoneinfo tarball missing — "
                        f"re-running Docker (install + tests)",
                        file=sys.stderr,
                    )
                    last_metrics["dateutil_zoneinfo_retried"] = True
                    continue
                snapshot_env = False
                if lang == "javascript":
                    from .js_build import (
                        augment_javascript_snapshot_permissions,
                        slice_failures_are_snapshot_permissions,
                    )

                    snapshot_env = slice_failures_are_snapshot_permissions(
                        list(last_metrics.get("fl") or []),
                        list(last_metrics.get("el") or []),
                    ) or (
                        "eacces" in log_tail.lower()
                        and "__snapshots__" in log_tail.lower()
                    )
                if snapshot_env and not last_metrics.get("snapshot_chmod_retried"):
                    eff_cfg = augment_javascript_snapshot_permissions(eff_cfg)
                    row["install_config"] = export_install_config_for_harness(eff_cfg)
                    print(
                        f"  {pr.instance_id}: snapshot dir permission denied — "
                        f"re-running Docker (chmod __snapshots__ + tests)",
                        file=sys.stderr,
                    )
                    last_metrics["snapshot_chmod_retried"] = True
                    continue
                print(
                    f"  {pr.instance_id}: test_patch slice has {fa + ea} failure(s)/error(s); "
                    f"skipping install_config remediation (test_patch LLM will fix tests)",
                    file=sys.stderr,
                )
                break
            integration_native_retry = (
                lang == "python"
                and repo is not None
                and len(pytest_targets or targets) > 0
                and last_n_patch == 0
                and int(last_metrics.get("docker_exit") or 0) == 0
                and not integration_native_retried
            )
            if integration_native_retry:
                from .integration_build import (
                    native_build_install_config,
                    repo_has_cmake_integration,
                )

                if repo_has_cmake_integration(
                    repo, test_paths=tp_only or pytest_targets
                ) and not native_integration_already_applied(eff_cfg):
                    eff_cfg = native_build_install_config(
                        eff_cfg,
                        repo,
                        test_paths=tp_only or pytest_targets,
                        test_patch=test_patch,
                    )
                    row["install_config"] = export_install_config_for_harness(
                        eff_cfg, language=lang
                    )
                    integration_native_retried = True
                    force_rebuild_env_next = True
                    last_env_fp = install_config_affects_env_image(eff_cfg)
                    print(
                        f"  {pr.instance_id}: cmake+integration pytest recipe — "
                        f"re-running Docker (install + tests; rebuild env image)",
                        file=sys.stderr,
                    )
                    continue
            if (
                after_patch_empty
                and not install_failed
                and not last_metrics.get("full_suite_probe_done")
                and lang in ("php", "ruby")
                and attempt < max_r
            ):
                eff_cfg = dict(eff_cfg)
                eff_cfg["discovery_patch_full_suite"] = True
                last_metrics["full_suite_probe_done"] = True
                print(
                    f"  {pr.instance_id}: patch phase empty (base={last_n_base}, "
                    f"patch junit=0) — re-running Docker with full-suite patch probe",
                    file=sys.stderr,
                )
                continue
            if after_patch_empty and not install_failed:
                eff_cfg.pop("discovery_patch_full_suite", None)
                print(
                    f"  {pr.instance_id}: patch phase empty — skipping install_config "
                    f"remediation (test_patch LLM / logs)",
                    file=sys.stderr,
                )
                break
            if patch_apply_failed and not install_failed:
                print(
                    f"  {pr.instance_id}: patch apply failed — skipping install_config "
                    f"remediation",
                    file=sys.stderr,
                )
                break
            if not needs_fix:
                if sk > 0 and fixable_sk:
                    print(
                        f"  {pr.instance_id}: docker ok (0 failure/error); {sk} optional skip(s) "
                        f"left — not re-remediating (use --docker-remediate-skips to chase deps). "
                        f"install_config + requirements reflect attempt {attempt}.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  {pr.instance_id}: docker ok on attempt {attempt}; "
                        f"install_config + requirements finalized.",
                        file=sys.stderr,
                    )
                break

            if attempt >= max_r or llm_remediate is None:
                if install_failed and llm_remediate is None:
                    print(
                        f"  {pr.instance_id}: install failed and --no-docker-llm-remediation; stopping",
                        file=sys.stderr,
                    )
                break

            if build_failed:
                reason = "gradle build failed during test (fix compile/install, not test_cmd)"
            elif test_target_failed:
                reason = (
                    "gradle --tests filter matched no tests (fix module/FQCN, not install)"
                )
            elif install_failed:
                reason = "install failed"
            elif needs_junit_fix:
                from .harness_guards import needs_junit_fix_reason

                reason = needs_junit_fix_reason(
                    lang,
                    n_base=last_n_base,
                    n_patch=last_n_patch,
                    log_tail=log_tail,
                )
            elif ea > 0:
                reason = "test collection/setup errors"
            else:
                reason = "fixable skips"
            print(
                f"  {pr.instance_id}: LLM will update install_config ({reason}); "
                f"attempt {attempt + 1} re-runs install + tests",
                file=sys.stderr,
            )

            log_tail_fix = (last_metrics.get("dstdout") or "") + "\n" + (
                last_metrics.get("dstderr") or ""
            )
            if install_failed and log_indicates_git_clone_failure(
                log_tail_fix, docker_exit=int(last_metrics.get("docker_exit") or 0)
            ):
                if attempt < max_r:
                    print(
                        f"  {pr.instance_id}: git checkout failed — re-running Docker "
                        f"(fetch fallback for unreachable base_commit)",
                        file=sys.stderr,
                    )
                    continue
                break

            cfg_before_fix = dict(eff_cfg)
            from .harness_guards import extract_structured_failure_log

            if not build_failed:
                log_tail_fix = extract_structured_failure_log(
                    log_tail_fix, language=lang
                )
            if build_failed:
                if repo is not None and lang == "java":
                    heuristic_cfg = merge_java_build_into_config(
                        eff_cfg,
                        repo,
                        pytest_targets or targets,
                        llm=None,
                        repo_id=str(row.get("repo") or pr.repo_id),
                        instance_id=pr.instance_id,
                        patch=patch,
                        test_patch=test_patch,
                        gradle_projects_output=gradle_projects_log,
                    )
                    heuristic_cfg = _docker_install_config_effective(
                        heuristic_cfg, pr, repo=repo
                    )
                    if not install_config_remediation_unchanged(eff_cfg, heuristic_cfg):
                        eff_cfg = heuristic_cfg
                        row["install_config"] = export_install_config_for_harness(
                            eff_cfg, language=lang
                        )
                        last_env_fp = install_config_affects_env_image(eff_cfg)
                        print(
                            f"  {pr.instance_id}: applied Gradle compile install heuristics "
                            f"(install={str(eff_cfg.get('install') or '')[:100]!r}); "
                            f"re-running Docker",
                            file=sys.stderr,
                        )
                        continue
                print(
                    f"  {pr.instance_id}: gradle BUILD FAILED — compile/install heuristics "
                    f"unchanged; skipping install-config LLM",
                    file=sys.stderr,
                )
                break
            if test_target_failed and lang == "java" and repo is not None:
                print(
                    f"  {pr.instance_id}: Gradle module/FQCN mismatch "
                    f"(no tests for --tests filter) — re-resolving modules without LLM",
                    file=sys.stderr,
                )
                heuristic_cfg = merge_java_build_into_config(
                    eff_cfg,
                    repo,
                    pytest_targets or targets,
                    llm=None,
                    repo_id=str(row.get("repo") or pr.repo_id),
                    instance_id=pr.instance_id,
                    patch=patch,
                    test_patch=test_patch,
                    gradle_projects_output=gradle_projects_log,
                )
                heuristic_cfg = _docker_install_config_effective(
                    heuristic_cfg, pr, repo=repo
                )
                if not install_config_remediation_unchanged(eff_cfg, heuristic_cfg):
                    eff_cfg = heuristic_cfg
                    row["install_config"] = export_install_config_for_harness(
                        eff_cfg, language=lang
                    )
                    last_env_fp = install_config_affects_env_image(eff_cfg)
                    print(
                        f"  {pr.instance_id}: applied Java test_cmd/module heuristics "
                        f"(test_cmd={str(eff_cfg.get('test_cmd') or '')[:100]!r}); "
                        f"re-running Docker",
                        file=sys.stderr,
                    )
                    continue
                print(
                    f"  {pr.instance_id}: Gradle test-target heuristics unchanged; "
                    f"skipping install-config LLM",
                    file=sys.stderr,
                )
                break
            if install_failed and lang == "java":
                log_tail = (last_metrics.get("dstdout") or "") + "\n" + (
                    last_metrics.get("dstderr") or ""
                )
                is_maven = (
                    str(eff_cfg.get("java_build_system") or "").lower() == "maven"
                    or detect_java_build_system(repo) == "maven"
                )
                comp = detect_maven_compiler_major(repo) if is_maven else None
                need_jdk_fix = is_maven and (
                    log_indicates_maven_unsupported_compiler_source(log_tail)
                    or (comp is not None and comp <= 8)
                )
                if need_jdk_fix:
                    jdk_cfg = remediate_maven_compiler_jdk(
                        eff_cfg,
                        repo,
                        pytest_targets or targets,
                        log_tail=log_tail,
                    )
                    jdk_cfg = _docker_install_config_effective(jdk_cfg, pr, repo=repo)
                    if not install_config_remediation_unchanged(eff_cfg, jdk_cfg):
                        eff_cfg = jdk_cfg
                        row["install_config"] = export_install_config_for_harness(eff_cfg)
                        last_env_fp = install_config_affects_env_image(eff_cfg)
                        print(
                            f"  {pr.instance_id}: applied Maven JDK 8 / compiler -D flags "
                            f"(docker_image={eff_cfg.get('docker_image')!r}); re-running Docker",
                            file=sys.stderr,
                        )
                        continue
            if needs_junit_fix and lang == "java":
                java_llm = None if gradle_module_mismatch else llm_remediate
                if gradle_module_mismatch:
                    print(
                        f"  {pr.instance_id}: Gradle module slice mismatch "
                        f"(base={last_n_base}, patch={last_n_patch}, "
                        f"test_patch_matched={last_tp_tot}) — re-resolving modules without LLM",
                        file=sys.stderr,
                    )
                heuristic_cfg = merge_java_build_into_config(
                    eff_cfg,
                    repo,
                    pytest_targets or targets,
                    llm=java_llm,
                    repo_id=str(row.get("repo") or pr.repo_id),
                    instance_id=pr.instance_id,
                    patch=patch,
                    test_patch=test_patch,
                    gradle_projects_output=gradle_projects_log,
                )
                heuristic_cfg = _docker_install_config_effective(heuristic_cfg, pr, repo=repo)
                if not install_config_remediation_unchanged(eff_cfg, heuristic_cfg):
                    eff_cfg = heuristic_cfg
                    row["install_config"] = export_install_config_for_harness(eff_cfg)
                    last_env_fp = install_config_affects_env_image(eff_cfg)
                    print(
                        f"  {pr.instance_id}: applied Java test_cmd/module heuristics "
                        f"(test_cmd={str(eff_cfg.get('test_cmd') or '')[:100]!r}); re-running Docker",
                        file=sys.stderr,
                    )
                    continue
            if needs_junit_fix and lang == "php" and repo is not None:
                from .php_build import php_install_config_for_repo

                heuristic_cfg = php_install_config_for_repo(repo, base=eff_cfg)
                heuristic_cfg = _docker_install_config_effective(heuristic_cfg, pr, repo=repo)
                if not install_config_remediation_unchanged(eff_cfg, heuristic_cfg):
                    eff_cfg = heuristic_cfg
                    row["install_config"] = export_install_config_for_harness(eff_cfg, language=lang)
                    last_env_fp = install_config_affects_env_image(eff_cfg)
                    print(
                        f"  {pr.instance_id}: applied PHP test_cmd/runner heuristics "
                        f"(runner={eff_cfg.get('php_test_runner')!r}, "
                        f"test_cmd={str(eff_cfg.get('test_cmd') or '')[:100]!r}); "
                        f"re-running Docker",
                        file=sys.stderr,
                    )
                    continue
            if needs_junit_fix and lang == "javascript":
                from .js_build import (
                    install_config_remediation_unchanged_js,
                    log_indicates_jest_haste_map_failure,
                    merge_js_build_into_config,
                    remediate_js_jest_haste_to_mocha,
                    runner_from_install_config,
                )

                log_tail = (last_metrics.get("dstdout") or "") + "\n" + (
                    last_metrics.get("dstderr") or ""
                )
                if log_indicates_jest_haste_map_failure(log_tail):
                    haste_cfg = remediate_js_jest_haste_to_mocha(
                        eff_cfg,
                        repo,
                        pytest_targets or targets,
                        repo_dir="/testbed",
                    )
                    haste_cfg = _docker_install_config_effective(haste_cfg, pr, repo=repo)
                    if not install_config_remediation_unchanged_js(eff_cfg, haste_cfg):
                        eff_cfg = haste_cfg
                        row["install_config"] = export_install_config_for_harness(
                            eff_cfg, language=lang
                        )
                        last_env_fp = install_config_affects_env_image(eff_cfg)
                        print(
                            f"  {pr.instance_id}: applied JavaScript haste-map→mocha remediation "
                            f"(test_cmd={str(eff_cfg.get('test_cmd') or '')[:100]!r}); "
                            f"re-running Docker",
                            file=sys.stderr,
                        )
                        continue
                heuristic_cfg = merge_js_build_into_config(
                    eff_cfg,
                    repo,
                    pytest_targets or targets,
                    repo_dir="/testbed",
                )
                heuristic_cfg = _docker_install_config_effective(heuristic_cfg, pr, repo=repo)
                if not install_config_remediation_unchanged(eff_cfg, heuristic_cfg):
                    eff_cfg = heuristic_cfg
                    row["install_config"] = export_install_config_for_harness(eff_cfg, language=lang)
                    last_env_fp = install_config_affects_env_image(eff_cfg)
                    runner = runner_from_install_config(eff_cfg, repo)
                    print(
                        f"  {pr.instance_id}: applied JavaScript test_cmd heuristics "
                        f"({runner}, test_cmd={str(eff_cfg.get('test_cmd') or '')[:100]!r}); "
                        f"re-running Docker",
                        file=sys.stderr,
                    )
                    continue
            if install_failed and not build_failed and lang in (
                "c",
                "go",
                "rust",
                "ruby",
                "php",
                "python",
                "javascript",
            ):
                log_tail = (last_metrics.get("dstdout") or "") + "\n" + (
                    last_metrics.get("dstderr") or ""
                )
                if lang == "javascript":
                    from .js_build import remediate_js_install_from_log

                    heuristic_cfg = remediate_js_install_from_log(
                        eff_cfg,
                        log_tail,
                        repo=repo,
                        test_paths=pytest_targets or targets,
                    )
                elif lang == "c":
                    from .c_build import (
                        is_premake_repo,
                        premake_install_config_for_repo,
                        remediate_c_install_from_log,
                    )
                    from .integration_build import (
                        apply_native_build_if_integration,
                        native_integration_already_applied,
                        native_build_install_config,
                    )

                    from .harness_guards import log_indicates_ngtcp2_quictls_missing
                    from .integration_build import remediate_quictls_native_integration

                    if log_indicates_ngtcp2_quictls_missing(log_tail):
                        if repo is not None:
                            heuristic_cfg = remediate_quictls_native_integration(
                                eff_cfg,
                                repo,
                                test_paths=tp_only or pytest_targets,
                            )
                        else:
                            heuristic_cfg = remediate_c_install_from_log(
                                eff_cfg,
                                log_tail,
                                repo=repo,
                                test_paths=tp_only or pytest_targets,
                            )
                        print(
                            f"  {pr.instance_id}: stripped HTTP/3 cmake flags "
                            f"(ngtcp2 quictls missing)",
                            file=sys.stderr,
                        )
                        force_rebuild_env_next = True
                    elif native_integration_already_applied(eff_cfg) and repo is not None:
                        heuristic_cfg = native_build_install_config(
                            eff_cfg,
                            repo,
                            test_paths=tp_only or pytest_targets,
                            test_patch=test_patch,
                        )
                    else:
                        heuristic_cfg = remediate_c_install_from_log(
                            eff_cfg,
                            log_tail,
                            repo=repo,
                            test_paths=tp_only or pytest_targets,
                        )
                    if repo is not None and is_premake_repo(repo):
                        heuristic_cfg = premake_install_config_for_repo(
                            repo,
                            base=heuristic_cfg,
                            test_paths=tp_only or pytest_targets,
                            test_patch=test_patch,
                        )
                    if repo is not None:
                        from .integration_build import native_integration_http3_disabled

                        if not native_integration_http3_disabled(heuristic_cfg):
                            heuristic_cfg = apply_native_build_if_integration(
                                heuristic_cfg,
                                repo,
                                test_paths=tp_only or pytest_targets,
                                test_patch=test_patch,
                                patch=str(row.get("patch") or ""),
                            )
                elif lang == "ruby":
                    from .ruby_build import (
                        log_indicates_ruby_gem_not_found,
                        remediate_ruby_install_from_log,
                        ruby_install_config_for_repo,
                    )

                    heuristic_cfg = remediate_ruby_install_from_log(eff_cfg, log_tail)
                    if repo is not None and log_indicates_ruby_gem_not_found(log_tail):
                        heuristic_cfg = ruby_install_config_for_repo(
                            repo,
                            base=heuristic_cfg,
                            test_paths=tp_only or pytest_targets,
                        )
                elif lang == "rust":
                    from .rust_build import remediate_rust_install_from_log

                    heuristic_cfg = remediate_rust_install_from_log(
                        eff_cfg, log_tail, repo=repo, targets=pytest_targets
                    )
                elif lang == "php":
                    from .php_build import remediate_php_install_from_log

                    heuristic_cfg = remediate_php_install_from_log(eff_cfg, log_tail, repo=repo)
                elif lang == "go":
                    from .go_build import remediate_go_install_from_log

                    heuristic_cfg = remediate_go_install_from_log(
                        eff_cfg,
                        log_tail,
                        docker_exit=int(last_metrics.get("docker_exit") or 0),
                        n_patch=int(last_metrics.get("n_patch") or 0),
                    )
                elif lang == "python" and repo is not None:
                    from .harness_guards import log_indicates_ngtcp2_quictls_missing
                    from .integration_build import (
                        apply_native_build_if_integration,
                        native_build_install_config,
                        native_integration_already_applied,
                        remediate_native_integration_ngtcp2,
                        repo_has_cmake_integration,
                    )

                    from .integration_build import remediate_quictls_native_integration

                    if log_indicates_ngtcp2_quictls_missing(log_tail):
                        heuristic_cfg = remediate_quictls_native_integration(
                            eff_cfg,
                            repo,
                            test_paths=tp_only or pytest_targets,
                        )
                        print(
                            f"  {pr.instance_id}: stripped HTTP/3 cmake flags "
                            f"(ngtcp2 quictls missing)",
                            file=sys.stderr,
                        )
                        force_rebuild_env_next = True
                    elif repo_has_cmake_integration(
                        repo, test_paths=tp_only or pytest_targets
                    ) and not native_integration_already_applied(eff_cfg):
                        heuristic_cfg = native_build_install_config(
                            eff_cfg,
                            repo,
                            test_paths=tp_only or pytest_targets,
                            test_patch=test_patch,
                        )
                    elif native_integration_already_applied(eff_cfg):
                        heuristic_cfg = dict(eff_cfg)
                    else:
                        from .python_build import remediate_python_install_from_log

                        heuristic_cfg = remediate_python_install_from_log(
                            eff_cfg,
                            log_tail,
                            repo=repo,
                            docker_exit=int(last_metrics.get("docker_exit") or 0),
                        )
                else:
                    from .apt_from_log import remediate_apt_install_from_log

                    heuristic_cfg = remediate_apt_install_from_log(eff_cfg, log_tail)
                heuristic_cfg = _docker_install_config_effective(heuristic_cfg, pr, repo=repo)
                if lang in ("python", "c") and repo is not None:
                    from .integration_build import (
                        apply_native_build_if_integration,
                        native_integration_http3_disabled,
                    )

                    if not native_integration_http3_disabled(heuristic_cfg):
                        heuristic_cfg = apply_native_build_if_integration(
                            heuristic_cfg,
                            repo,
                            test_paths=tp_only or pytest_targets,
                            test_patch=test_patch,
                            patch=str(row.get("patch") or ""),
                        )
                    from .apt_from_log import sanitize_native_integration_apt_config

                    heuristic_cfg = sanitize_native_integration_apt_config(heuristic_cfg)
                    if lang == "c":
                        heuristic_cfg["language"] = "c"
                if not install_config_remediation_unchanged(eff_cfg, heuristic_cfg):
                    cfg_before_heuristic = dict(eff_cfg)
                    eff_cfg = heuristic_cfg
                    row["install_config"] = export_install_config_for_harness(eff_cfg, language=lang)
                    last_env_fp = install_config_affects_env_image(eff_cfg)
                    if install_config_substantive_change(cfg_before_heuristic, eff_cfg):
                        apt = eff_cfg.get("apt-pkgs") or []
                        print(
                            f"  {pr.instance_id}: applied {lang} install heuristics "
                            f"(apt-pkgs={apt!r}); re-running Docker",
                            file=sys.stderr,
                        )
                        continue
                    print(
                        f"  {pr.instance_id}: applied {lang} apt-only heuristics; "
                        f"proceeding to LLM install_config update",
                        file=sys.stderr,
                    )

            blob = _llm_diagnostics_blob(
                pr,
                tp_only=tp_only,
                patch_junit=last_metrics["patch_result"],
                repo_root=last_metrics["repo_root"],
                pa=pa,
                fa=fa,
                ea=ea,
                sk=sk,
                tot=tot,
                docker_stderr_tail=last_metrics["dstderr"],
                docker_stdout_tail=last_metrics["dstdout"],
                install_failed=install_failed and not build_failed,
                docker_exit=int(last_metrics["docker_exit"]),
                n_patch=last_n_patch,
                n_targets=len(targets),
                install_config=eff_cfg,
            )
            api_key, base_url, model, to = llm_remediate
            try:
                from .install_config_build import get_ci_excerpt_from_config

                eff_cfg = llm_fix_recipe_from_docker_tests(
                    eff_cfg,
                    blob,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    timeout_s=to,
                    ci_context=get_ci_excerpt_from_config(eff_cfg),
                )
                eff_cfg = _docker_install_config_effective(eff_cfg, pr, repo=repo)
                if install_failed and not build_failed:
                    log_tail = (last_metrics.get("dstdout") or "") + "\n" + (
                        last_metrics.get("dstderr") or ""
                    )
                    if lang == "c":
                        from .c_build import remediate_c_install_from_log

                        eff_cfg = remediate_c_install_from_log(
                            eff_cfg,
                            log_tail,
                            repo=repo,
                            test_paths=tp_only or pytest_targets,
                        )
                    elif lang == "ruby":
                        from .ruby_build import remediate_ruby_install_from_log

                        eff_cfg = remediate_ruby_install_from_log(eff_cfg, log_tail)
                    elif lang == "rust":
                        from .rust_build import remediate_rust_install_from_log

                        eff_cfg = remediate_rust_install_from_log(
                            eff_cfg, log_tail, repo=repo, targets=pytest_targets
                        )
                    elif lang == "php":
                        from .php_build import remediate_php_install_from_log

                        eff_cfg = remediate_php_install_from_log(eff_cfg, log_tail)
                    elif lang == "python" and repo is not None:
                        from .python_build import remediate_python_install_from_log

                        eff_cfg = remediate_python_install_from_log(
                            eff_cfg,
                            log_tail,
                            repo=repo,
                            docker_exit=int(last_metrics.get("docker_exit") or 0),
                        )
                    else:
                        from .apt_from_log import remediate_apt_install_from_log

                        eff_cfg = remediate_apt_install_from_log(eff_cfg, log_tail)
                    eff_cfg = _docker_install_config_effective(eff_cfg, pr, repo=repo)
                if lang == "java":
                    eff_cfg = merge_java_build_into_config(
                        eff_cfg,
                        repo,
                        pytest_targets or targets,
                        llm=None if build_failed else llm_remediate,
                        repo_id=str(row.get("repo") or pr.repo_id),
                        instance_id=pr.instance_id,
                        patch=patch,
                        test_patch=test_patch,
                        gradle_projects_output=gradle_projects_log,
                    )
                row["install_config"] = export_install_config_for_harness(eff_cfg)
            except Exception as ex:
                print(f"  {pr.instance_id}: install_config remediation LLM failed: {ex}", file=sys.stderr)
                break
            if install_config_remediation_unchanged(cfg_before_fix, eff_cfg):
                print(
                    f"  {pr.instance_id}: install_config LLM returned unchanged config on "
                    f"attempt {attempt}/{max_r}; stopping remediation",
                    file=sys.stderr,
                )
                break
            print(
                f"  {pr.instance_id}: updated install_config for attempt {attempt + 1} "
                f"(install={str(eff_cfg.get('install') or '')[:80]!r}, "
                f"test_cmd={str(eff_cfg.get('test_cmd') or '')[:80]!r}, "
                f"post_install={len(eff_cfg.get('post_install') or [])}, "
                f"pip_packages={len(eff_cfg.get('pip_packages') or [])}, "
                f"apt-pkgs={len(eff_cfg.get('apt-pkgs') or [])})",
                file=sys.stderr,
            )
            last_env_fp = install_config_affects_env_image(eff_cfg)

        if llm_remediate is not None and (last_install_failed or last_slice_ea > 0):
            print(
                f"  {pr.instance_id}: install remediation incomplete "
                f"(install_failed={last_install_failed}, error={last_slice_ea}). "
                f"FAIL_TO_PASS / PASS_TO_PASS / requirements are from the last Docker run.",
                file=sys.stderr,
            )

        row["install_config"] = export_install_config_for_harness(eff_cfg)

        # dateutil: build zoneinfo tarball before test_patch LLM (env failure, not bad tests).
        if (
            lang == "python"
            and (last_slice_fa + last_slice_ea) > 0
            and not last_metrics.get("dateutil_zoneinfo_retried")
        ):
            from .python_build import finalize_python_install_config, needs_dateutil_zoneinfo

            if needs_dateutil_zoneinfo(repo=repo):
                eff_cfg = finalize_python_install_config(
                    eff_cfg, repo, repo_id=pr.repo_id
                )
                row["install_config"] = export_install_config_for_harness(eff_cfg)
                print(
                    f"  {pr.instance_id}: dateutil zoneinfo — building tarball; "
                    f"re-running Docker",
                    file=sys.stderr,
                )
                _reset_docker_repo(repo, base_commit, clone_timeout)
                last_metrics = _docker_pytest_attempt(
                    work=work,
                    pr=pr,
                    eff_cfg=eff_cfg,
                    lang=lang,
                    pytest_targets=pytest_targets,
                    tp_only=tp_only,
                    targets=targets,
                    test_patch=test_patch,
                    docker_timeout=docker_timeout,
                    docker_pip_freeze_after=docker_pip_freeze_after,
                    attempt_label="dateutil zoneinfo",
                    row=row,
                    build_instance_harness_images=build_instance_harness_images,
                    llm_remediate=llm_remediate,
                    remediation_max_rounds=remediation_max_rounds,
                )
                last_metrics["dateutil_zoneinfo_retried"] = True
                if last_metrics.get("install_config"):
                    eff_cfg = merge_internal_install_keys(
                        dict(last_metrics["install_config"]),
                        internal_install_keys(eff_cfg),
                    )
                    row["install_config"] = export_install_config_for_harness(eff_cfg, language=lang)
                f2p = list(last_metrics["f2p"])
                p2p = list(last_metrics["p2p"])
                last_slice_fa = int(last_metrics["fa"])
                last_slice_ea = int(last_metrics["ea"])
                last_slice_sk = int(last_metrics["sk"])
                last_tp_tot = int(last_metrics["tot"])
                last_install_failed = bool(last_metrics["install_failed"])

        test_patch_remediated = False
        tp_failures = last_slice_fa + last_slice_ea
        tp_label_mismatch = bool(last_metrics.get("test_patch_label_mismatch"))
        from .languages import get_language_spec

        spec = get_language_spec(lang)
        cargo_log = spec.result_format == "cargo_log"
        gotest_log = spec.result_format == "gotest_log"
        if tp_label_mismatch and cargo_log and f2p and not tp_failures:
            tp_label_mismatch = False
            print(
                f"  {pr.instance_id}: rust cargo log keys do not map to test_patch paths "
                f"but FAIL_TO_PASS={len(f2p)} — skipping test_patch label-mismatch LLM",
                file=sys.stderr,
            )
        if tp_label_mismatch and gotest_log and f2p and not tp_failures:
            tp_label_mismatch = False
            print(
                f"  {pr.instance_id}: gotest log keys use Test* names (not *_test.go paths); "
                f"FAIL_TO_PASS={len(f2p)} — skipping test_patch label-mismatch LLM",
                file=sys.stderr,
            )
        after_patch_empty = bool(last_metrics.get("after_patch_empty"))
        max_tp_r = max(1, remediation_max_rounds) if llm_remediate else 0
        django_rt = uses_runtests_test_cmd(eff_cfg) or bool(eff_cfg.get("django_runtests"))

        if (
            (tp_failures > 0 or tp_label_mismatch or after_patch_empty)
            and llm_remediate
            and test_body.strip()
        ):
            api_key, base_url, model, to = llm_remediate
            prev_unchanged = False
            original_tp_only = list(tp_only)
            for tp_attempt in range(1, max_tp_r + 1):
                if tp_failures == 0 and not tp_label_mismatch and not after_patch_empty:
                    break
                repo_root = Path(last_metrics["repo_root"])
                patch_junit = Path(last_metrics["patch_result"])
                result_fmt = _effective_result_format(lang, eff_cfg, django_rt=django_rt)
                patch_map = parse_test_status_map(
                    patch_junit, repo_root, lang, result_format=result_fmt
                )
                if after_patch_empty:
                    log_parts: list[str] = []
                    for log_name in ("test-patch.log", "test-base.log"):
                        lp = work / log_name
                        if lp.is_file():
                            try:
                                log_parts.append(
                                    f"--- {log_name} ---\n"
                                    + lp.read_text(encoding="utf-8", errors="replace")[-6000:]
                                )
                            except OSError:
                                pass
                    if patch_junit.is_file():
                        try:
                            log_parts.append(
                                "--- junit-patch.xml (tail) ---\n"
                                + patch_junit.read_text(encoding="utf-8", errors="replace")[-4000:]
                            )
                        except OSError:
                            pass
                    log_tail = "\n".join(log_parts) if log_parts else "(no test logs found)"
                    slice_fl = [
                        (
                            "(after patch)",
                            "No parseable tests after impl.patch + test.patch. "
                            "Use test-patch.log / impl apply errors:\n" + log_tail,
                        )
                    ]
                    slice_el = []
                elif tp_label_mismatch and patch_map:
                    if lang == "java" and tp_only:
                        slice_fl = java_label_mismatch_diagnostics(
                            tp_only,
                            patch_map,
                            test_cmd=str(eff_cfg.get("test_cmd") or ""),
                        )
                        slice_el = []
                    else:
                        expected = (
                            paths_to_runtests_labels(tp_only)
                            if django_rt
                            else tp_only
                        )
                        slice_fl = [
                            (
                                key,
                                "Runtests/JUnit key does not match test_patch labels "
                                f"{expected[:6]}{'...' if len(expected) > 6 else ''}. "
                                "Use Django unittest TestCase methods (test_*) under the "
                                "test_patch file paths, not free-form description strings. "
                                f"Outcome: {patch_map.get(key, '?')}",
                            )
                            for key in sorted(patch_map)[:40]
                        ]
                        slice_el = []
                elif tp_only:
                    if django_rt:
                        slice_fl, slice_el, _ = django_fail_error_skip_messages_for_paths(
                            patch_junit, tp_only
                        )
                    else:
                        slice_fl, slice_el, _ = junit_fail_error_skip_messages_for_paths(
                            patch_junit,
                            repo_root,
                            tp_only,
                            language=lang,
                            native_integration_pytest_root=_native_integration_pytest_root(
                                eff_cfg
                            ),
                        )
                else:
                    slice_fl, slice_el, _ = junit_fail_error_skip_messages_limited(
                        patch_junit, repo_root, limit=200, language=lang
                    )
                if after_patch_empty:
                    reason = (
                        f"no tests ran after patch "
                        f"(before={int(last_metrics.get('n_base', 0))}, after=0); "
                        f"check impl/test_patch apply and test module imports"
                    )
                elif tp_label_mismatch:
                    reason = (
                        f"test_patch label mismatch "
                        f"({len(patch_map)} log case(s), 0 matched test_patch labels)"
                    )
                else:
                    reason = f"{tp_failures} failure(s)/error(s) in test_patch slice"
                print(
                    f"  {pr.instance_id}: LLM will update test_patch ({reason}); "
                    f"test_patch attempt {tp_attempt}/{max_tp_r}",
                    file=sys.stderr,
                )
                problem_ctx = (
                    f"problem_statement:\n{row.get('problem_statement') or ''}\n\n"
                    f"impl.patch (excerpt):\n{(row.get('patch') or '')[-40_000:]}"
                )
                blob = _llm_diagnostics_blob(
                    pr,
                    tp_only=tp_only,
                    patch_junit=patch_junit,
                    repo_root=repo_root,
                    pa=int(last_metrics["pa"]),
                    fa=last_slice_fa,
                    ea=last_slice_ea,
                    sk=last_slice_sk,
                    tot=last_tp_tot,
                    docker_stderr_tail=last_metrics["dstderr"],
                    docker_stdout_tail=last_metrics["dstdout"],
                    install_failed=False,
                    docker_exit=int(last_metrics["docker_exit"]),
                    n_patch=last_n_patch,
                    n_targets=len(targets),
                    original_tp_only=original_tp_only,
                    failures=slice_fl,
                    errors=slice_el,
                    django_runtests=django_rt,
                )
                java_ctx = ""
                if lang == "java" and tp_only:
                    java_ctx = build_java_harness_context_for_repo(
                        repo,
                        tp_only,
                        llm=llm_remediate,
                        repo_id=str(row.get("repo") or pr.repo_id),
                        instance_id=pr.instance_id,
                        test_cmd=str(eff_cfg.get("test_cmd") or ""),
                    )
                try:
                    new_test = llm_fix_test_patch_from_docker_tests(
                        test_body,
                        blob,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        timeout_s=to,
                        attempt=tp_attempt,
                        max_attempts=max_tp_r,
                        previous_edit_unchanged=prev_unchanged,
                        django_runtests=django_rt,
                        java_harness_context=java_ctx,
                        problem_context=problem_ctx,
                        language=lang,
                        repo_id=str(row.get("repo") or pr.repo_id),
                    )
                except Exception as ex:
                    print(f"  {pr.instance_id}: test_patch remediation LLM failed: {ex}", file=sys.stderr)
                    break
                unchanged = new_test.strip() == test_body.strip()
                if unchanged:
                    print(
                        f"  {pr.instance_id}: test_patch LLM returned unchanged diff on "
                        f"attempt {tp_attempt}/{max_tp_r}; re-prompting with latest failures",
                        file=sys.stderr,
                    )
                    prev_unchanged = True
                    if tp_attempt >= max_tp_r:
                        break
                    continue
                prev_unchanged = False
                test_patch_remediated = True
                test_body = strip_mailbox_to_unified(new_test)
                if llm_remediate:
                    api_key, base_url, model, to = llm_remediate
                    apply_cap = max(test_patch_apply_attempts, remediation_max_rounds)
                    tp_paths = collect_test_targets_from_test_patch(lang, test_body)
                    java_ctx = ""
                    if lang == "java" and tp_paths:
                        java_ctx = build_java_harness_context_for_repo(
                            repo,
                            tp_paths,
                            llm=llm_remediate,
                            repo_id=str(row.get("repo") or pr.repo_id),
                            instance_id=pr.instance_id,
                            test_cmd=str(eff_cfg.get("test_cmd") or ""),
                        )
                    fixed_body, apply_ok, apply_err = remediate_test_patch_until_applies(
                        test_body,
                        repo,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                        timeout_s=to,
                        max_attempts=apply_cap,
                        language=lang,
                        repo_id=str(row.get("repo") or pr.repo_id),
                        problem_statement=str(row.get("problem_statement") or ""),
                        impl_patch=patch,
                        instance_id=pr.instance_id,
                        test_paths=tp_paths,
                        java_harness_context=java_ctx,
                        base_commit=base_commit,
                        head_sha=head_sha,
                    )
                    if not apply_ok:
                        print(
                            f"  {pr.instance_id}: test_patch Docker fix failed apply-check: "
                            f"{apply_err}",
                            file=sys.stderr,
                        )
                        break
                    test_body = fixed_body
                test_patch = test_body
                row["test_patch"] = test_body
                (work / "test.patch").write_text(test_body, encoding="utf-8")
                tp_only = _test_patch_only_targets(lang, test_patch)
                pytest_targets = tp_only if tp_only else targets
                if lang == "java":
                    eff_cfg = merge_java_build_into_config(
                        eff_cfg,
                        repo,
                        pytest_targets or targets,
                        llm=llm_remediate,
                        repo_id=str(row.get("repo") or pr.repo_id),
                        instance_id=pr.instance_id,
                        patch=patch,
                        test_patch=test_patch,
                    )
                row["install_config"] = export_install_config_for_harness(eff_cfg)
                use_tests_only = setup_ready and not last_install_failed
                print(
                    f"  {pr.instance_id}: updated test_patch ({len(new_test)} bytes); "
                    f"re-running Docker"
                    + (" (tests only, skipping clone/install)" if use_tests_only else ""),
                    file=sys.stderr,
                )
                _reset_docker_repo(repo, base_commit, clone_timeout)
                last_metrics = _docker_pytest_attempt(
                    work=work,
                    pr=pr,
                    eff_cfg=eff_cfg,
                    lang=lang,
                    pytest_targets=pytest_targets,
                    tp_only=tp_only,
                    targets=targets,
                    test_patch=test_patch,
                    docker_timeout=docker_timeout,
                    docker_pip_freeze_after=docker_pip_freeze_after,
                    attempt_label=f"test_patch attempt {tp_attempt}/{max_tp_r}",
                    row=row,
                    build_instance_harness_images=build_instance_harness_images,
                    llm_remediate=llm_remediate,
                    remediation_max_rounds=remediation_max_rounds,
                    tests_only=use_tests_only,
                    base_commit=base_commit,
                )
                if last_metrics.get("install_config"):
                    eff_cfg = merge_internal_install_keys(
                        dict(last_metrics["install_config"]),
                        internal_install_keys(eff_cfg),
                    )
                    row["install_config"] = export_install_config_for_harness(eff_cfg, language=lang)
                f2p = list(last_metrics["f2p"])
                p2p = list(last_metrics["p2p"])
                last_slice_fa = int(last_metrics["fa"])
                last_slice_ea = int(last_metrics["ea"])
                last_slice_sk = int(last_metrics["sk"])
                tp_failures = last_slice_fa + last_slice_ea
                tp_label_mismatch = bool(last_metrics.get("test_patch_label_mismatch"))
                after_patch_empty = bool(last_metrics.get("after_patch_empty"))
                last_tp_tot = int(last_metrics["tot"])
                last_n_base = int(last_metrics["n_base"])
                last_n_patch = int(last_metrics["n_patch"])
                last_install_failed = bool(last_metrics["install_failed"])
                print(
                    f"  {pr.instance_id}: after test_patch attempt {tp_attempt} — "
                    f"FAIL_TO_PASS={len(f2p)} slice failures/errors={tp_failures} "
                    f"label_mismatch={tp_label_mismatch}",
                    file=sys.stderr,
                )

        if test_patch_remediated or test_patch_created_by_llm:
            f2p = _filter_f2p_to_test_patch_scope(
                f2p,
                tp_only,
                lang,
                django_runtests=django_rt,
                native_integration_pytest_root=_native_integration_pytest_root(eff_cfg),
                test_patch=test_patch,
            )
            p2p = []
            print(
                f"  {pr.instance_id}: LLM test_patch — using FAIL_TO_PASS only ({len(f2p)} test(s)); "
                f"PASS_TO_PASS cleared",
                file=sys.stderr,
            )

        task_type = classify_task_type(
            f2p=f2p,
            test_patch_failures=tp_failures,
            test_patch_remediated=test_patch_remediated,
            test_patch_created_by_llm=test_patch_created_by_llm,
            install_or_apply_failed=last_install_failed,
        )
        if not task_type:
            skip_reason = task_type_skip_reason(
                f2p=f2p,
                test_patch_failures=tp_failures,
                test_patch_remediated=test_patch_remediated,
                test_patch_created_by_llm=test_patch_created_by_llm,
                install_or_apply_failed=last_install_failed,
            )
            if skip_reason:
                print(f"  {pr.instance_id}: skip — {skip_reason}", file=sys.stderr)
        print(
            f"  {pr.instance_id}: task_type={task_type or '(skip)'}",
            file=sys.stderr,
        )
        return f2p, p2p, task_type
    except Exception as e:
        if is_docker_daemon_unavailable_error(e):
            docker_daemon_available(refresh=True)
            print(
                f"  {pr.instance_id}: skip docker discover — "
                f"{docker_daemon_error_message(str(e))}",
                file=sys.stderr,
            )
        else:
            print(f"  {pr.instance_id}: docker discover error: {e}", file=sys.stderr)
        return [], [], ""
    finally:
        shutil.rmtree(work, ignore_errors=True)
