from __future__ import annotations

import json
from pathlib import Path

def _stub_nps_jest_layout(tmp_path: Path) -> None:
    """Artifact cues for NPS + Jest ``test-*.js`` filtering (not package name)."""
    (tmp_path / "__tests__").mkdir(exist_ok=True)
    (tmp_path / "__tests__" / "test-stub.js").write_text("// stub\n", encoding="utf-8")


from swe_rebench_pr.docker_discover import _docker_install_failed
from swe_rebench_pr.docker_entry import _js_body
from swe_rebench_pr.repo_detect import should_apply_nps_jest_target_filter

from swe_rebench_pr.js_build import (
    DEFAULT_NODE_VERSION,
    _filter_isomorphic_git_targets,
    _semver_tuple,
    detect_js_test_runner,
    ensure_js_docker_specs,
    isomorphic_git_needs_http_build,
    isomorphic_git_repo,
    jest_test_cmd_from_targets,
    jest_use_experimental_vm_modules,
    js_install_config_for_repo,
    js_test_cmd_for_docker_entry,
    merge_js_build_into_config,
    nps_resolve_jest_subset_script,
    nps_test_node_uses_unsupported_node_options,
    npm_run_test_cmd,
    normalize_js_test_cmd,
    normalize_nvm_installable_version,
    normalize_vitest_test_cmd,
    package_json_uses_npm_alias_protocol,
    parse_engines_node,
    remediate_js_install_from_log,
    repo_uses_electron,
    resolve_node_version_for_repo,
    sanitize_js_docker_specs,
    uses_nps_test_script,
    vitest_test_cmd_from_targets,
)


def test_parse_engines_node_prefers_22():
    pkg = Path("/tmp/pkg.json")
    pkg.write_text(
        json.dumps({"engines": {"node": ">=22.12.0"}}),
        encoding="utf-8",
    )
    try:
        assert parse_engines_node(pkg) == "22.12.0"
    finally:
        pkg.unlink(missing_ok=True)


def test_normalize_js_test_cmd_strips_exit_zero_and_adds_junit():
    raw = "npx jest test/foo.js 2>&1; exit 0"
    out = normalize_js_test_cmd(raw)
    assert "exit 0" not in out
    assert "jest-junit" in out
    assert "__JUNIT_OUT__" in out


def test_normalize_jest_compound_cmd_injects_junit_before_teardown():
    raw = (
        "cd /testbed && (npx nps test.setup || true) && "
        '((NODE_OPTIONS="--experimental-vm-modules" npx jest --ci --reporters=default "t.js") '
        "|| (npx jest --ci --reporters=default t.js)); "
        "status=$?; (npx nps test.teardown || true); exit $status"
    )
    out = normalize_js_test_cmd(raw)
    assert "exit $status" not in out
    assert "jest-junit" in out
    assert "__JUNIT_OUT__" in out
    assert out.index("jest-junit") < out.index("npx nps test.teardown")


def test_ensure_js_docker_specs_default_node():
    cfg = ensure_js_docker_specs({}, language="javascript")
    assert cfg["docker_specs"]["node_version"] == DEFAULT_NODE_VERSION


def test_js_body_two_phase_patch_apply():
    body = _js_body(
        {"js_test_runner": "vitest", "test_cmd": "cd /testbed && npx vitest run"},
        False,
        repo_dir="/testbed",
    )
    assert "base + test_patch only" in body
    assert "_apply_one /w/test.patch" in body
    assert "_apply_one /w/impl.patch" in body
    assert "reset to base_commit" in body
    assert "-e node_modules" in body
    assert "_js_restore_deps_if_needed" in body
    assert body.index("/w/test.patch") < body.index("/w/impl.patch")


def test_js_body_uses_install_config_test_cmd(tmp_path: Path):
    cfg = {
        "test_cmd": "cd /testbed && npx jest test/a.test.js --outputFile=__JUNIT_OUT__",
    }
    body = _js_body(cfg, False, repo_dir="/testbed")
    assert "JS_TEST_CMD=" in body
    assert "test/a.test.js" in body
    assert "_run_js_tests" in body
    assert 'npx jest --ci --forceExit' in body or "npm test" in body


def test_js_body_empty_test_cmd_uses_targets_fallback():
    body = _js_body({}, False, repo_dir="/testbed")
    assert 'JS_TEST_CMD=""' in body
    assert '${#T[@]}' in body or "${#T[@]}" in body


def test_docker_install_failed_js_empty_junit_not_install(tmp_path: Path):
    assert not _docker_install_failed(
        docker_exit=0,
        n_patch=0,
        n_targets=3,
        log_tail="",
        lang="javascript",
    )
    assert _docker_install_failed(
        docker_exit=1,
        n_patch=0,
        n_targets=3,
        log_tail="",
        lang="javascript",
    )


def test_merge_js_electron_install(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"electron": "30.0.0"},
                "scripts": {"postinstall": "electron-builder install-app-deps"},
            }
        ),
        encoding="utf-8",
    )
    assert repo_uses_electron(tmp_path)
    cfg = merge_js_build_into_config({}, tmp_path, ["test/x.test.js"])
    assert "ignore-scripts" in str(cfg.get("install") or "")
    assert "jest-junit" in str(cfg.get("post_install") or [])
    assert js_test_cmd_for_docker_entry(cfg)


def test_junit_xml_path_matching(tmp_path: Path):
    from swe_rebench_pr.diff_split import (
        junit_outcome_counts_for_paths,
        junit_reported_test_count,
        parse_test_status_map,
    )

    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0"?>
<testsuites>
  <testsuite name="jest" tests="1">
    <testcase classname="test/foo.test.js" name="does thing" time="0.1"/>
  </testsuite>
</testsuites>""",
        encoding="utf-8",
    )
    m = parse_test_status_map(junit, tmp_path, "javascript")
    assert len(m) >= 1
    assert junit_reported_test_count(junit) == 1
    pa, _, _, _, tot = junit_outcome_counts_for_paths(m, ["test/foo.test.js"])
    assert tot == 1 and pa == 1


def test_nested_jest_junit_parsed(tmp_path: Path):
    from swe_rebench_pr.diff_split import junit_reported_test_count, parse_test_status_map

    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0"?>
<testsuites>
  <testsuite name="root" tests="2">
    <testsuite name="test/foo.test.js" tests="2">
      <testcase classname="test/foo.test.js" name="a" time="0.1"/>
      <testcase classname="test/foo.test.js" name="b" time="0.1"/>
    </testsuite>
  </testsuite>
</testsuites>""",
        encoding="utf-8",
    )
    assert junit_reported_test_count(junit) == 2
    m = parse_test_status_map(junit, tmp_path, "javascript")
    assert len(m) == 2


def test_merge_js_scopes_test_cmd_to_paths(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    cfg = merge_js_build_into_config({}, tmp_path, ["src/foo.test.js"])
    cmd = str(cfg["test_cmd"])
    assert "src/foo.test.js" in cmd
    assert "jest" in cmd.lower()


def test_detect_vitest_from_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"test": "npm run test:vitest", "test:vitest": "vitest run"},
                "devDependencies": {"vitest": "^4.1.7"},
            }
        ),
        encoding="utf-8",
    )
    assert detect_js_test_runner(tmp_path) == "vitest"


def test_normalize_vitest_test_cmd_adds_junit_reporter():
    out = normalize_vitest_test_cmd("cd /testbed && npx vitest run test/a.test.ts")
    assert "reporter=junit" in out
    assert "__JUNIT_OUT__" in out


def test_vitest_test_cmd_from_targets():
    cmd = vitest_test_cmd_from_targets(["test/unit/auth.test.ts"])
    assert "vitest run" in cmd
    assert "test/unit/auth.test.ts" in cmd
    assert "reporter=junit" in cmd


def test_merge_js_vitest_repo(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"test": "vitest run"},
                "devDependencies": {"vitest": "^4.0.0"},
            }
        ),
        encoding="utf-8",
    )
    cfg = merge_js_build_into_config({}, tmp_path, ["test/foo.test.ts"])
    assert cfg.get("js_test_runner") == "vitest"
    assert "vitest" in str(cfg["test_cmd"]).lower()
    assert "jest-junit" not in str(cfg.get("post_install") or "")


def test_vitest_junit_with_spaces_in_test_name(tmp_path: Path):
    from swe_rebench_pr.diff_split import filter_swebench_gradable_nodeids, parse_test_status_map

    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0"?>
<testsuites>
  <testsuite name="tests/unit/auth.test.ts" tests="1">
    <testcase classname="tests/unit/auth.test.ts"
              name="auth suite > login works" time="0.01"/>
  </testsuite>
</testsuites>""",
        encoding="utf-8",
    )
    m = parse_test_status_map(junit, tmp_path, "javascript")
    assert len(m) == 1
    nid = next(iter(m))
    kept, dropped = filter_swebench_gradable_nodeids(list(m), language="javascript")
    assert kept and not dropped
    assert "login works" in nid


def test_vitest_junit_xml_path_matching(tmp_path: Path):
    from swe_rebench_pr.diff_split import (
        junit_outcome_counts_for_paths,
        junit_reported_test_count,
        parse_test_status_map,
    )

    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0"?>
<testsuites>
  <testsuite name="test/unit/auth.test.ts" tests="1" failures="0" errors="0">
    <testcase classname="test/unit/auth.test.ts" name="login works" time="0.01"
              file="/testbed/test/unit/auth.test.ts"/>
  </testsuite>
</testsuites>""",
        encoding="utf-8",
    )
    m = parse_test_status_map(junit, tmp_path, "javascript")
    assert len(m) >= 1
    assert junit_reported_test_count(junit) == 1
    pa, _, _, _, tot = junit_outcome_counts_for_paths(m, ["test/unit/auth.test.ts"])
    assert tot == 1 and pa == 1


def test_js_body_includes_jest_junit_harvest_helper():
    body = _js_body(
        {"js_test_runner": "jest", "test_cmd": "cd /testbed && npm run test"},
        False,
        repo_dir="/testbed",
    )
    assert "_harvest_jest_junit_to" in body
    assert "harvest_jest_junit.py" in body


def test_js_body_precleans_nps_daemons_for_nps_setup_cmd():
    body = _js_body(
        {
            "js_test_runner": "jest",
            "test_cmd": (
                "cd /testbed && (npx nps proxy.stop || true) && "
                "(npx nps gitserver.stop || true) && "
                "(npx nps test.setup || true) && (npx nps test.node); "
                "(npx nps test.teardown || true)"
            ),
        },
        False,
        repo_dir="/testbed",
    )
    assert 'if [[ "$cmd" == *"npx nps test.setup"* ]]; then' in body
    assert "npx nps proxy.stop >/dev/null 2>&1" in body
    assert "npx nps gitserver.stop >/dev/null 2>&1" in body
    assert 'JEST_JUNIT_OUTPUT_DIR="/w"' in body
    assert 'JEST_JUNIT_OUTPUT_NAME="$(basename "$junit_out")"' in body
    assert 'JEST_JUNIT_ADD_FILE_ATTRIBUTE="true"' in body
    assert 'JEST_JUNIT_CLASSNAME="{filepath}"' in body
    assert "_js_ensure_jest_http_node_build" in body
    assert "build.rollup" in body


def test_js_body_vitest_runner(tmp_path: Path):
    cfg = {
        "js_test_runner": "vitest",
        "test_cmd": "cd /testbed && npx vitest run --reporter=junit --outputFile=__JUNIT_OUT__",
    }
    body = _js_body(cfg, False, repo_dir="/testbed")
    assert 'JS_TEST_RUNNER="vitest"' in body
    assert "vitest" in body


def test_uses_nps_test_script(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "nps test"}}),
        encoding="utf-8",
    )
    assert uses_nps_test_script(tmp_path)


def test_jest_test_cmd_nps_repo_uses_npm_run_test(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "nps test"}, "devDependencies": {"jest": "29"}}),
        encoding="utf-8",
    )
    cmd = jest_test_cmd_from_targets([], repo=tmp_path)
    assert cmd == (
        "cd /testbed && "
        "(npx nps proxy.stop || true) && "
        "(npx nps gitserver.stop || true) && "
        "(npx nps test.setup || true) && "
        "(npx nps test.node); "
        "(npx nps test.teardown || true)"
    )


def test_normalize_nvm_maps_types_node_patch_to_lts():
    assert normalize_nvm_installable_version("12.7.2") == "12.22.12"
    assert normalize_nvm_installable_version("12.22.12") == "12.22.12"
    assert normalize_nvm_installable_version("20.19.0") == "20.19.0"


def test_resolve_node_version_types_node_not_runtime(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "engines": {"node": ">=12"},
                "devDependencies": {"@types/node": "12.7.2", "jest": "^27.5.1"},
            }
        ),
        encoding="utf-8",
    )
    assert resolve_node_version_for_repo(tmp_path) == "12.22.12"


def test_sanitize_js_docker_specs_python_system():
    specs = sanitize_js_docker_specs(
        {"node_version": "12.7.2", "python_version": "system"}
    )
    assert specs["node_version"] == "12.22.12"
    assert specs["python_version"] == "3.9"


def test_resolve_node_version_jest30_overrides_low_engines(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "engines": {"node": ">=14.17"},
                "devDependencies": {
                    "jest": "^30.2.0",
                    "@types/node": "^20.19.16",
                    "jest-puppeteer": "^11.0.0",
                },
            }
        ),
        encoding="utf-8",
    )
    assert parse_engines_node(tmp_path / "package.json") == "14.17.0"
    ver = resolve_node_version_for_repo(tmp_path)
    assert ver.startswith("20."), ver
    assert _semver_tuple(ver)[0] == 20


def test_jest_test_cmd_nps_repo_uses_safe_jest_when_scoped_paths(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {"test": "nps test"},
                "devDependencies": {"jest": "^30.2.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "jest.config.js").write_text("module.exports = {}", encoding="utf-8")
    cmd = jest_test_cmd_from_targets(
        ["__tests__/test-status.js", "__tests__/test-statusMatrix.js"],
        repo=tmp_path,
    )
    assert "npx nps test.setup" in cmd
    assert "npx nps test.node" not in cmd
    assert "npx jest --ci --forceExit --coverage" in cmd
    assert 'NODE_OPTIONS="--experimental-vm-modules"' not in cmd
    assert "__tests__/test-status.js" in cmd
    assert "npx nps test.teardown" in cmd


def test_nps_resolve_jest_subset_script_node_vs_jest(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "nps test"}}),
        encoding="utf-8",
    )
    (tmp_path / "package-scripts.cjs").write_text(
        "module.exports = { scripts: { test: { default: series.nps('test.setup', 'test.node', 'test.teardown'), node: 'jest' } } }",
        encoding="utf-8",
    )
    assert nps_resolve_jest_subset_script(tmp_path) == "test.node"

    (tmp_path / "package-scripts.cjs").write_text(
        "module.exports = { scripts: { test: { default: series.nps('test.setup', 'test.jest', 'test.teardown'), jest: 'jest --ci' } } }",
        encoding="utf-8",
    )
    assert nps_resolve_jest_subset_script(tmp_path) == "test.jest"


def test_jest_use_experimental_vm_modules_only_when_nps_needs_it(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "nps test"}}),
        encoding="utf-8",
    )
    (tmp_path / "package-scripts.cjs").write_text(
        "module.exports = { scripts: { test: { jest: 'jest --ci --coverage' } } }",
        encoding="utf-8",
    )
    assert jest_use_experimental_vm_modules(tmp_path) is False
    (tmp_path / "package-scripts.cjs").write_text(
        'module.exports = { scripts: { test: { node: \'NODE_OPTIONS="--max-old-space-size-percentage=80" jest\' } } }',
        encoding="utf-8",
    )
    assert jest_use_experimental_vm_modules(tmp_path) is True


def test_jest_test_cmd_nps_legacy_jest_script_uses_safe_direct_jest(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "nps test"}, "devDependencies": {"jest": "^29"}}),
        encoding="utf-8",
    )
    (tmp_path / "package-scripts.cjs").write_text(
        "module.exports = { scripts: { test: { default: series.nps('test.setup', 'test.jest', 'test.teardown'), jest: 'jest --ci --coverage' } } }",
        encoding="utf-8",
    )
    cmd = jest_test_cmd_from_targets(["__tests__/test-foo.js"], repo=tmp_path)
    assert "npx nps test.jest" not in cmd
    assert "npx jest --ci --forceExit --coverage" in cmd
    assert 'NODE_OPTIONS="--experimental-vm-modules"' not in cmd
    assert "__tests__/test-foo.js" in cmd


def test_npm_run_test_cmd_nps_subset_uses_resolved_script():
    cmd = npm_run_test_cmd(nps_subset=True, nps_script="test.jest")
    assert "npx nps test.jest" in cmd
    assert "npx nps test.node" not in cmd


def test_normalize_jest_injects_force_exit_and_timeout():
    raw = "cd /testbed && npx jest --ci --coverage __tests__/t.js"
    out = normalize_js_test_cmd(raw)
    assert "--forceExit" in out
    assert "--testTimeout=120000" in out


def test_npm_run_test_cmd_nps_subset():
    cmd = npm_run_test_cmd(nps_subset=True)
    assert "npx nps proxy.stop" in cmd
    assert "npx nps gitserver.stop" in cmd
    assert "npx nps test.setup || true" in cmd
    assert "npx nps test.node" in cmd
    assert "npx nps test.teardown || true" in cmd
    assert "exit $status" not in cmd


def test_nps_unsupported_node_options_detection(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "nps test"}}),
        encoding="utf-8",
    )
    (tmp_path / "package-scripts.cjs").write_text(
        'module.exports = { scripts: { test: { node: \'NODE_OPTIONS="--experimental-vm-modules --max-old-space-size-percentage=80" jest\' } } }',
        encoding="utf-8",
    )
    assert nps_test_node_uses_unsupported_node_options(tmp_path) is True


def test_jest_test_cmd_nps_uses_safe_direct_jest_when_node_options_unsupported(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "nps test"}, "devDependencies": {"jest": "^30.2.0"}}),
        encoding="utf-8",
    )
    (tmp_path / "package-scripts.cjs").write_text(
        'module.exports = { scripts: { test: { node: \'NODE_OPTIONS="--experimental-vm-modules --max-old-space-size-percentage=80" jest\' } } }',
        encoding="utf-8",
    )
    (tmp_path / "jest.config.js").write_text("module.exports = {}", encoding="utf-8")
    cmd = jest_test_cmd_from_targets(["__tests__/test-status.js"], repo=tmp_path)
    assert "npx nps test.setup" in cmd
    assert "npx nps test.node" not in cmd
    assert 'NODE_OPTIONS="--experimental-vm-modules" npx jest --ci --forceExit --coverage' in cmd
    assert "--max-old-space-size-percentage" not in cmd
    assert "__JUNIT_OUT__" in cmd
    assert "npx nps test.teardown" in cmd


def test_isomorphic_git_jest_cmd_uses_long_timeout(tmp_path: Path):
    _stub_nps_jest_layout(tmp_path)
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "isomorphic-git", "scripts": {"test": "nps test"}}),
        encoding="utf-8",
    )
    (tmp_path / "package-scripts.cjs").write_text(
        "module.exports = { scripts: { test: { jest: 'jest --ci' } } }",
        encoding="utf-8",
    )
    cmd = jest_test_cmd_from_targets(["__tests__/test-clone.js"], repo=tmp_path)
    assert "--testTimeout=120000" in cmd


def test_isomorphic_git_jest_target_filter(tmp_path: Path):
    _stub_nps_jest_layout(tmp_path)
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "isomorphic-git", "scripts": {"test": "nps test"}}),
        encoding="utf-8",
    )
    paths = [
        "__tests__/test-clone.js",
        "__tests__/test-clone-checkout-huge-repo.js",
        "__tests__/index.webpack.js",
        "__tests__/__helpers__/setup-abort-controller.js",
        "__tests__/test-status-in-submodule.js",
    ]
    filtered = _filter_isomorphic_git_targets(tmp_path, paths)
    assert filtered == ["__tests__/test-clone.js"]


def test_jest_test_cmd_nps_isomorphic_git_drops_submodule_targets(tmp_path: Path):
    _stub_nps_jest_layout(tmp_path)
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "isomorphic-git",
                "scripts": {"test": "nps test"},
                "devDependencies": {"jest": "^30.2.0"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "package-scripts.cjs").write_text(
        'module.exports = { scripts: { test: { node: \'NODE_OPTIONS="--experimental-vm-modules --max-old-space-size-percentage=80" jest\' } } }',
        encoding="utf-8",
    )
    cmd = jest_test_cmd_from_targets(
        [
            "__tests__/test-status-in-submodule.js",
            "__tests__/test-status.js",
        ],
        repo=tmp_path,
    )
    assert "__tests__/test-status.js" in cmd
    assert "__tests__/test-status-in-submodule.js" not in cmd


def test_isomorphic_git_needs_http_build(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "isomorphic-git", "scripts": {"test": "nps test"}}),
        encoding="utf-8",
    )
    (tmp_path / "jest.config.cjs").write_text(
        "module.exports = { moduleNameMapper: { '^isomorphic-git/http$': '<rootDir>/http/node' } }",
        encoding="utf-8",
    )
    assert isomorphic_git_needs_http_build(tmp_path)
    (tmp_path / "http").mkdir()
    (tmp_path / "http" / "node").mkdir()
    (tmp_path / "http" / "node" / "index.cjs").write_text("// stub", encoding="utf-8")
    assert not isomorphic_git_needs_http_build(tmp_path)


def test_jest_test_cmd_includes_http_build_prefix(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "isomorphic-git", "scripts": {"test": "nps test"}}),
        encoding="utf-8",
    )
    (tmp_path / "jest.config.js").write_text(
        "export default { moduleNameMapper: { '^isomorphic-git/http$': '<rootDir>/http/node' } }",
        encoding="utf-8",
    )
    (tmp_path / "package-scripts.cjs").write_text(
        'module.exports = { scripts: { test: { node: \'NODE_OPTIONS="--experimental-vm-modules --max-old-space-size-percentage=80" jest\' } } }',
        encoding="utf-8",
    )
    cmd = jest_test_cmd_from_targets(["__tests__/test-clone.js"], repo=tmp_path)
    assert "build.rollup" in cmd
    assert "npx jest" in cmd
    assert "jest-junit" in cmd


def test_ensure_js_docker_specs_uses_deps_node_floor(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "engines": {"node": ">=14.17"},
                "devDependencies": {"jest": "^30.2.0", "@types/node": "^20.19.16"},
            }
        ),
        encoding="utf-8",
    )
    cfg = ensure_js_docker_specs({}, repo=tmp_path, language="javascript")
    assert cfg["docker_specs"]["node_version"].startswith("20.")


def test_detect_mocha_for_style_dictionary_like_repo(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "web-test-runner --coverage",
                    "test:node": (
                        'mocha -r mocha-hooks.mjs "./__integration__/**/*.test.js" '
                        '"./__tests__/**/*.test.js"'
                    ),
                },
                "devDependencies": {"mocha": "^11.0.0", "@web/test-runner": "^0.20.0"},
            }
        ),
        encoding="utf-8",
    )
    paths = ["__tests__/common/transforms.test.js"]
    assert detect_js_test_runner(tmp_path, paths) == "mocha"


def test_mocha_test_cmd_uses_hooks_and_junit(tmp_path: Path):
    (tmp_path / "mocha-hooks.mjs").write_text("export const mochaHooks = {};\n", encoding="utf-8")
    from swe_rebench_pr.js_build import mocha_test_cmd_from_targets

    cmd = mocha_test_cmd_from_targets(
        ["__tests__/common/transforms.test.js"],
        repo=tmp_path,
    )
    assert "npx mocha" in cmd
    assert "-r mocha-hooks.mjs" in cmd
    assert "__MOCHA_JUNIT_REPORTER__" in cmd
    assert "__tests__/common/transforms.test.js" in cmd


def test_merge_js_mocha_repo_filters_snapshots(tmp_path: Path):
    from swe_rebench_pr.js_build import MOCHA_JUNIT_REPORTER_MODULE

    (tmp_path / "mocha-hooks.mjs").write_text("export const mochaHooks = {};\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "test": "web-test-runner --coverage",
                    "test:node": 'mocha -r mocha-hooks.mjs "./__tests__/**/*.test.js"',
                },
                "devDependencies": {"mocha": "^11.0.0"},
            }
        ),
        encoding="utf-8",
    )
    cfg = merge_js_build_into_config(
        {},
        tmp_path,
        [
            "__integration__/__snapshots__/android.test.snap.js",
            "__tests__/common/transforms.test.js",
        ],
    )
    assert cfg.get("js_test_runner") == "mocha"
    cmd = str(cfg["test_cmd"])
    assert "mocha" in cmd
    assert "transforms.test.js" in cmd
    assert "snapshots" not in cmd
    assert "__MOCHA_JUNIT_REPORTER__" in cmd
    assert "legacy-peer-deps" in str(cfg.get("post_install") or "")
    assert MOCHA_JUNIT_REPORTER_MODULE in str(cfg.get("post_install") or "")


def test_docker_mocha_reporter_prefers_repo_module_name():
    from swe_rebench_pr.docker_entry import _js_restore_deps_fn

    shell = _js_restore_deps_fn()
    assert 'MOCHA_JUNIT_REPORTER="mocha-junit-reporter"' in shell
    assert "legacy-peer-deps" in shell


def test_docker_mocha_reporter_substitution_avoids_double_replace():
    from swe_rebench_pr.docker_entry import _js_restore_deps_fn

    shell = _js_restore_deps_fn()
    assert "_js_apply_mocha_junit_reporter" in shell
    assert '${cmd//node_modules\\/mocha-junit-reporter/$MOCHA_JUNIT_REPORTER}' not in shell


def test_npm_alias_protocol_raises_node_floor(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "engines": {"node": ">=10"},
                "dependencies": {
                    "@react-navigation/bottom-tabs": (
                        "npm:@zulip/react-navigation-bottom-tabs@5.11.16-0.zulip.1"
                    )
                },
            }
        ),
        encoding="utf-8",
    )
    assert package_json_uses_npm_alias_protocol(tmp_path)
    assert resolve_node_version_for_repo(tmp_path).startswith("16.")


def test_yarn_lock_repo_uses_yarn_install(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"name":"x","scripts":{"test":"jest"}}', encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n", encoding="utf-8")
    cfg = js_install_config_for_repo(tmp_path)
    assert "yarn install" in str(cfg.get("install") or "")
    assert any("yarn" in ln for ln in (cfg.get("pre_install") or []))


def test_detect_makefile_mocha_runner_eslint_like(tmp_path: Path):
    (tmp_path / "Makefile.js").write_text(
        'const MOCHA = `${NODE_MODULES}mocha/bin/_mocha `;\n'
        "target.mocha = () => { exec(`${getBinFile('c8')} -- ${MOCHA}`); };\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "node Makefile.js test"}, "devDependencies": {"mocha": "^11.0.0"}}),
        encoding="utf-8",
    )
    (tmp_path / "tests" / "fixtures" / "broken").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "broken" / "package.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "tests" / "fixtures" / "bom" / "package.json").write_text("{ not json", encoding="utf-8")
    (tmp_path / "tests" / "fixtures" / "other" / "package.json").write_text("{}", encoding="utf-8")
    assert detect_js_test_runner(tmp_path, ["tests/lib/rules/foo.js"]) == "mocha"


def test_merge_js_makefile_repo_uses_mocha_not_jest(tmp_path: Path):
    (tmp_path / "Makefile.js").write_text(
        "const MOCHA = 'mocha';\n target.mocha = function() {};\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "node Makefile.js test"}, "devDependencies": {"mocha": "^11.0.0", "c8": "^10.0.0"}}),
        encoding="utf-8",
    )
    for sub in ("broken", "bom", "other"):
        d = tmp_path / "tests" / "fixtures" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.json").write_text("{", encoding="utf-8")
    cfg = merge_js_build_into_config(
        {"test_cmd": "node Makefile.js mocha"},
        tmp_path,
        ["tests/lib/rules/foo.js"],
    )
    assert cfg.get("js_test_runner") == "mocha"
    cmd = str(cfg["test_cmd"]).lower()
    assert "jest" not in cmd
    assert "mocha" in cmd
    assert "tests/lib/rules/foo.js" in str(cfg["test_cmd"])


def test_remediate_haste_map_switches_to_mocha(tmp_path: Path):
    from swe_rebench_pr.js_build import remediate_js_jest_haste_to_mocha

    (tmp_path / "Makefile.js").write_text("target.mocha = () => {};\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "node Makefile.js test"}, "devDependencies": {"mocha": "^11.0.0"}}),
        encoding="utf-8",
    )
    log = "jest-haste-map: Cannot parse /testbed/tests/fixtures/config-file/bom/package.json as JSON"
    out = remediate_js_install_from_log(
        {
            "js_test_runner": "jest",
            "test_cmd": "cd /testbed && npx jest tests/lib/rules/foo.js",
        },
        log,
        repo=tmp_path,
        test_paths=["tests/lib/rules/foo.js"],
    )
    assert out.get("js_test_runner") == "mocha"
    assert "jest" not in str(out.get("test_cmd") or "").lower()
    assert "mocha" in str(out.get("test_cmd") or "").lower()

    direct = remediate_js_jest_haste_to_mocha(
        {"test_cmd": "npx jest foo.js"},
        tmp_path,
        ["tests/lib/rules/foo.js"],
    )
    assert direct.get("js_test_runner") == "mocha"


def test_ci_extract_prefers_makefile_mocha():
    from swe_rebench_pr.ci_extract import _pick_best_line, _TEST_SCORES

    lines = [
        "npx jest --ci",
        "node Makefile.js mocha",
        "npm test",
    ]
    best = _pick_best_line(lines, _TEST_SCORES)
    assert best == "node Makefile.js mocha"


def test_remediate_js_install_eunsupportedprotocol(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "pkg": "npm:foo@1.0.0",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "yarn.lock").write_text("# yarn lockfile v1\n", encoding="utf-8")
    log = 'npm ERR! Unsupported URL Type "npm:": npm:foo@1.0.0'
    out = remediate_js_install_from_log({"docker_specs": {"node_version": "10.0.0"}}, log, repo=tmp_path)
    assert out["docker_specs"]["node_version"].startswith("16.")
    assert "yarn install" in str(out.get("install") or "")
