from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .builder import build_row, row_to_jsonl_line
from .env_config import load_env as _load_taskgen_env
from .gh_pr import parse_pr_url
from .task_type import TASK_TYPE_SKIP, is_gradable_task_type
from .llm_client import DEFAULT_LLM_MODEL, is_anthropic_model, resolve_llm_api_key
from .swebench_align import repair_jsonl_file


def read_urls(path: Path) -> list[str]:
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def main(argv: list[str] | None = None) -> int:
    _load_taskgen_env()

    if shutil.which("gh") is None:
        print("This tool requires the GitHub CLI `gh` in PATH.", file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser(
        description="Convert a list of GitHub PR URLs into SWE-rebench-style JSONL (16 fields)."
    )
    ap.add_argument(
        "--urls",
        type=Path,
        help="Text file with one https://github.com/owner/repo/pull/N URL per line",
    )
    ap.add_argument("-o", "--output", type=Path, help="Output JSONL path")
    ap.add_argument(
        "--repair-jsonl",
        type=Path,
        metavar="JSONL",
        help=(
            "Rewrite an existing task JSONL for SWE-bench (install_config docker_specs, "
            "Gradle test keys). Writes to --output; does not fetch PRs or run Docker."
        ),
    )
    ap.add_argument(
        "--work-parent",
        type=Path,
        default=Path(tempfile.gettempdir()),
        help="Temp directory for git clones (default: system temp)",
    )
    ap.add_argument(
        "--clone-depth",
        type=int,
        default=0,
        help="0 = full clone (reliable for any base SHA); >0 shallow clone",
    )
    ap.add_argument("--clone-timeout", type=int, default=600, help="git clone timeout (seconds)")
    ap.add_argument("--no-llm-patch-split", action="store_true", help="Heuristic-only patch vs test_patch")
    ap.add_argument(
        "--no-llm-install",
        action="store_true",
        help="Skip install recipe LLM; use minimal heuristic install_config",
    )
    ap.add_argument("--llm-timeout", type=int, default=120, help="Per LLM HTTP timeout (seconds)")
    ap.add_argument(
        "--run-install-freeze",
        action="store_true",
        help="After recipe, run pip install steps in a venv and fill requirements (slow; Linux best)",
    )
    ap.add_argument(
        "--install-timeout",
        type=int,
        default=3600,
        help="Timeout for combined install+freeze attempt",
    )
    ap.add_argument(
        "--no-discover-tests-docker",
        action="store_true",
        help="Skip Docker test discovery (leave FAIL_TO_PASS / PASS_TO_PASS as [])",
    )
    ap.add_argument(
        "--no-docker-pip-freeze-after",
        action="store_true",
        help="Do not run pip freeze inside Docker after pytest (default: freeze runs and fills requirements)",
    )
    ap.add_argument(
        "--no-docker-llm-remediation",
        action="store_true",
        help="Skip LLM-driven install_config retries in Docker (default: on; requires OPENAI_API_KEY)",
    )
    ap.add_argument(
        "--docker-remediation-rounds",
        type=int,
        default=3,
        help="Max Docker attempts when LLM remediation is enabled (default 3)",
    )
    ap.add_argument(
        "--test-patch-apply-attempts",
        type=int,
        default=5,
        help=(
            "Max LLM rounds to create/fix test_patch until git apply --check passes "
            "(default 5; uses max of this and --docker-remediation-rounds)"
        ),
    )
    ap.add_argument(
        "--docker-remediate-skips",
        action="store_true",
        help="Also re-run install+tests when only dependency skips remain (default: stop after 0 failure/error)",
    )
    ap.add_argument(
        "--docker-timeout",
        type=int,
        default=7200,
        help="Wall-clock timeout for one docker discover run (build + 2x test runs)",
    )
    ap.add_argument(
        "--language",
        default="auto",
        help=(
            "Test language for discovery: c, go, java, javascript, php, python, ruby, rust, "
            "or auto (detect from patch/repo). Default: auto"
        ),
    )
    ap.add_argument(
        "--force-rebuild-harness-images",
        action="store_true",
        help="Force rebuild SWE-bench harness images even if tags already exist",
    )
    ap.add_argument(
        "--build-instance-images",
        action="store_true",
        help=(
            "Bake a per-task instance Docker image (clone+install at build time). "
            "Default: env image only; clone/checkout/install run at discover container start."
        ),
    )
    ap.add_argument(
        "--prefilter",
        action="store_true",
        help=(
            "Skip PRs that fail cheap preflight (mobile/Rails/no tests in diff) before Docker discover"
        ),
    )
    ap.add_argument(
        "--preflight-only",
        type=Path,
        metavar="REPORT",
        help="Run preflight on --urls and write report; do not run task generation",
    )
    ap.add_argument(
        "--allow-llm-test-patch",
        action="store_true",
        help="Prefilter: do not require test paths in PR diff (allow LLM-created test_patch)",
    )

    args = ap.parse_args(argv)

    if args.preflight_only:
        if not args.urls:
            print("--preflight-only requires --urls", file=sys.stderr)
            return 1
        if not args.urls.is_file():
            print(f"Missing URLs file: {args.urls}", file=sys.stderr)
            return 1
        from .preflight import preflight_urls, write_preflight_report

        urls = read_urls(args.urls)
        results = preflight_urls(
            urls,
            language=args.language,
            require_test_paths_in_diff=not args.allow_llm_test_patch,
            allow_llm_test_patch=args.allow_llm_test_patch,
        )
        write_preflight_report(results, args.preflight_only)
        passed = sum(1 for r in results if r.passed)
        print(f"# preflight {passed}/{len(results)} passed -> {args.preflight_only}", file=sys.stderr)
        for r in results:
            if not r.passed:
                print(f"# FAIL {r.pr.instance_id}: {'; '.join(r.blockers)}", file=sys.stderr)
        return 0 if passed else 2

    if args.repair_jsonl:
        if not args.output:
            print("--repair-jsonl requires -o / --output", file=sys.stderr)
            return 1
        if not args.repair_jsonl.is_file():
            print(f"Missing JSONL: {args.repair_jsonl}", file=sys.stderr)
            return 1
        n = repair_jsonl_file(args.repair_jsonl, args.output)
        print(f"# repaired {n} row(s) -> {args.output}", file=sys.stderr)
        return 0 if n else 2

    if not args.urls or not args.output:
        print("PR generation requires --urls and -o / --output", file=sys.stderr)
        return 1

    discover_docker = not args.no_discover_tests_docker
    if discover_docker:
        from .swebench_images import harness_images_available

        if not harness_images_available():
            print(
                "Docker test discovery requires the bundled harness "
                "(pip install docker requests unidiff).",
                file=sys.stderr,
            )
            return 1
        if args.build_instance_images:
            print(
                "# Docker discover: bundled harness images (base/env/instance)",
                file=sys.stderr,
            )
        else:
            print(
                "# Docker discover: env image only (clone+install at container start)",
                file=sys.stderr,
            )
    if discover_docker and shutil.which("docker") is None:
        print(
            "Docker is required for test discovery (default). Install Docker or pass "
            "--no-discover-tests-docker to skip FAIL_TO_PASS / PASS_TO_PASS.",
            file=sys.stderr,
        )
        return 1
    if discover_docker:
        from .docker_runtime import docker_daemon_available, docker_daemon_error_message

        daemon_ok, daemon_reason = docker_daemon_available()
        if not daemon_ok:
            print(docker_daemon_error_message(daemon_reason), file=sys.stderr)
            return 1

    if not args.urls.is_file():
        print(f"Missing URLs file: {args.urls}", file=sys.stderr)
        return 1

    model = (os.environ.get("OPENAI_MODEL") or os.environ.get("LLM_MODEL") or DEFAULT_LLM_MODEL).strip()
    api_key = resolve_llm_api_key(model)
    base_url = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip()
    to = max(15, args.llm_timeout)

    llm_patch: tuple[str, str, str, int] | None = None
    if not args.no_llm_patch_split and api_key:
        llm_patch = (api_key, base_url, model, to)
    elif not args.no_llm_patch_split and not api_key:
        key_hint = (
            "TASKGEN_ANTHROPIC_API_KEY"
            if is_anthropic_model(model)
            else "TASKGEN_OPENAI_API_KEY"
        )
        print(f"# {key_hint} unset: heuristic patch/test_patch split", file=sys.stderr)

    llm_install: tuple[str, str, str, int] | None = None
    if not args.no_llm_install and api_key:
        llm_install = (api_key, base_url, model, to)
    elif not args.no_llm_install and not api_key:
        key_hint = (
            "TASKGEN_ANTHROPIC_API_KEY"
            if is_anthropic_model(model)
            else "TASKGEN_OPENAI_API_KEY"
        )
        print(f"# {key_hint} unset: heuristic install_config only", file=sys.stderr)

    if discover_docker and not args.no_docker_llm_remediation and not api_key:
        key_hint = (
            "TASKGEN_ANTHROPIC_API_KEY"
            if is_anthropic_model(model)
            else "TASKGEN_OPENAI_API_KEY"
        )
        print(
            f"Docker test discovery requires {key_hint} in .env (LLM remediation is enabled by default). "
            "Set the key or pass --no-docker-llm-remediation to skip remediation.",
            file=sys.stderr,
        )
        return 1

    llm_docker_rem: tuple[str, str, str, int] | None = None
    if discover_docker and not args.no_docker_llm_remediation:
        llm_docker_rem = (api_key, base_url, model, to)

    remediation_rounds = max(1, args.docker_remediation_rounds)
    docker_pip_freeze_after = discover_docker and not args.no_docker_pip_freeze_after
    docker_remediate_skips = bool(args.docker_remediate_skips)

    urls = read_urls(args.urls)
    if args.prefilter:
        from .preflight import preflight_pr

        filtered: list[str] = []
        for url in urls:
            pr = parse_pr_url(url)
            pf = preflight_pr(
                pr,
                language=args.language,
                require_test_paths_in_diff=not args.allow_llm_test_patch,
                allow_llm_test_patch=args.allow_llm_test_patch,
            )
            if pf.passed:
                filtered.append(url)
            else:
                print(
                    f"# prefilter skip {pr.instance_id}: {'; '.join(pf.blockers)}",
                    file=sys.stderr,
                )
        print(f"# prefilter {len(filtered)}/{len(urls)} URLs passed", file=sys.stderr)
        urls = filtered

    args.output.parent.mkdir(parents=True, exist_ok=True)

    ok = 0
    skipped_early = 0
    skipped_f2p = 0
    skipped_not_gradable = 0

    def _fail_to_pass_len(row: dict) -> int:
        try:
            return len(json.loads(row.get("FAIL_TO_PASS") or "[]"))
        except (json.JSONDecodeError, TypeError):
            return 0

    with args.output.open("w", encoding="utf-8") as out:
        for url in urls:
            try:
                pr = parse_pr_url(url)
                row = build_row(
                    pr,
                    llm_patch_split=llm_patch,
                    llm_install=llm_install,
                    clone_depth=args.clone_depth,
                    clone_timeout=args.clone_timeout,
                    run_install_freeze=args.run_install_freeze,
                    install_timeout=args.install_timeout,
                    discover_tests_docker=discover_docker,
                    discover_work_parent=args.work_parent.resolve(),
                    discover_clone_depth=args.clone_depth,
                    docker_timeout=args.docker_timeout,
                    llm_docker_remediate=llm_docker_rem,
                    docker_remediation_rounds=remediation_rounds,
                    test_patch_apply_attempts=max(1, args.test_patch_apply_attempts),
                    docker_pip_freeze_after=docker_pip_freeze_after,
                    docker_remediate_skips=docker_remediate_skips,
                    language=args.language,
                    force_rebuild_harness_images=bool(args.force_rebuild_harness_images),
                    build_instance_harness_images=bool(args.build_instance_images),
                )
                tt = str(row.get("task_type") or "")
                if discover_docker and not is_gradable_task_type(tt):
                    if tt == TASK_TYPE_SKIP:
                        skipped_early += 1
                        print(
                            f"# skip {pr.instance_id}: not written "
                            f"(patches do not apply at base_commit)",
                            file=sys.stderr,
                        )
                    elif _fail_to_pass_len(row) < 1:
                        skipped_f2p += 1
                        from .docker_runtime import docker_daemon_available

                        daemon_ok, _ = docker_daemon_available()
                        if discover_docker and not daemon_ok:
                            print(
                                f"# skip {pr.instance_id}: not written "
                                f"(Docker discover unavailable — start Docker Desktop)",
                                file=sys.stderr,
                            )
                        else:
                            print(
                                f"# skip {pr.instance_id}: not written "
                                f"(empty FAIL_TO_PASS or install/patch apply failed)",
                                file=sys.stderr,
                            )
                    else:
                        skipped_not_gradable += 1
                        print(
                            f"# skip {pr.instance_id}: not written "
                            f"(FAIL_TO_PASS={_fail_to_pass_len(row)} but test_patch slice "
                            f"still has failures/errors after remediation)",
                            file=sys.stderr,
                        )
                    continue
                out.write(row_to_jsonl_line(row))
                out.flush()
                ok += 1
                print(f"{pr.instance_id} task_type={tt or '?'}", file=sys.stderr)
            except Exception as e:
                print(f"# skip {url!r}: {e}", file=sys.stderr)

    print(f"# wrote {ok}/{len(urls)} rows -> {args.output}", file=sys.stderr)
    if discover_docker and skipped_early:
        print(
            f"# skipped {skipped_early} row(s) with task_type=skip (patch apply at base)",
            file=sys.stderr,
        )
    if discover_docker and skipped_f2p:
        print(f"# skipped {skipped_f2p} row(s) with empty FAIL_TO_PASS", file=sys.stderr)
    if discover_docker and skipped_not_gradable:
        print(
            f"# skipped {skipped_not_gradable} row(s) with FAIL_TO_PASS but remaining test failures",
            file=sys.stderr,
        )
    if ok and args.no_discover_tests_docker:
        print(
            "# Tip: you used --no-discover-tests-docker; FAIL_TO_PASS / PASS_TO_PASS are []",
            file=sys.stderr,
        )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
