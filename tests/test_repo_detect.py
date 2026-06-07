"""Artifact-based repo detection (not GitHub slug matching)."""

import json
from pathlib import Path

from swe_rebench_pr.repo_detect import (
    JAVASCRIPT_SNAPSHOT_CHMOD_CMD,
    apply_repo_overrides,
    discover_javascript_snapshot_dirs,
    discover_jest_config_path,
    filter_nps_jest_test_targets,
    javascript_snapshot_post_install,
    mocha_snapshot_post_install,
    repo_has_django_runtests,
    repo_has_javascript_snapshots,
    repo_needs_dateutil_zoneinfo,
    repo_needs_jest_http_rollup_build,
    repo_uses_meson_python_backend,
    should_apply_nps_jest_target_filter,
    uses_django_runtests,
)


def test_repo_has_django_runtests(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "runtests.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    assert repo_has_django_runtests(tmp_path)
    assert uses_django_runtests(repo=tmp_path)


def test_repo_uses_meson_python_backend(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["meson-python"]\nbuild-backend = "mesonpy"\n',
        encoding="utf-8",
    )
    assert repo_uses_meson_python_backend(tmp_path)


def test_meson_build_alone_is_not_meson_python_backend(tmp_path: Path):
    (tmp_path / "meson.build").write_text("project('mpv', 'c')\n", encoding="utf-8")
    assert not repo_uses_meson_python_backend(tmp_path)


def test_repo_needs_dateutil_zoneinfo(tmp_path: Path):
    (tmp_path / "updatezinfo.py").write_text("#\n", encoding="utf-8")
    (tmp_path / "zonefile_metadata.json").write_text("{}", encoding="utf-8")
    assert repo_needs_dateutil_zoneinfo(tmp_path)


def test_repo_needs_jest_http_rollup_build(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "any-lib", "scripts": {"test": "nps test"}}),
        encoding="utf-8",
    )
    (tmp_path / "jest.config.cjs").write_text(
        "module.exports = { moduleNameMapper: { '^lib/http$': '<rootDir>/http/node' } }",
        encoding="utf-8",
    )
    assert repo_needs_jest_http_rollup_build(tmp_path)
    (tmp_path / "http").mkdir()
    (tmp_path / "http" / "node").mkdir()
    (tmp_path / "http" / "node" / "index.cjs").write_text("// ok", encoding="utf-8")
    assert not repo_needs_jest_http_rollup_build(tmp_path)


def test_filter_nps_jest_targets_without_package_name(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "other-lib", "scripts": {"test": "nps test"}}),
        encoding="utf-8",
    )
    (tmp_path / "jest.config.js").write_text(
        'module.exports = { testRegex: "/test-.*\\\\.js$/" };',
        encoding="utf-8",
    )
    (tmp_path / "__tests__").mkdir()
    (tmp_path / "__tests__" / "test-foo.js").write_text("// t", encoding="utf-8")
    assert should_apply_nps_jest_target_filter(tmp_path)
    paths = [
        "__tests__/test-foo.js",
        "__tests__/test-foo-in-submodule.js",
        "__tests__/index.webpack.js",
    ]
    assert filter_nps_jest_test_targets(tmp_path, paths) == ["__tests__/test-foo.js"]


def test_discover_javascript_snapshot_dirs_nested(tmp_path: Path):
    nested = tmp_path / "__integration__" / "formats" / "__snapshots__"
    nested.mkdir(parents=True)
    found = discover_javascript_snapshot_dirs(tmp_path)
    assert "__integration__/formats/__snapshots__" in found
    assert repo_has_javascript_snapshots(tmp_path)


def test_javascript_snapshot_post_install_uses_find(tmp_path: Path):
    (tmp_path / "__integration__" / "__snapshots__").mkdir(parents=True)
    lines = javascript_snapshot_post_install(tmp_path)
    assert lines == [JAVASCRIPT_SNAPSHOT_CHMOD_CMD]
    assert mocha_snapshot_post_install(tmp_path) == lines


def test_discover_jest_config_near_test_path(tmp_path: Path):
    pkg = tmp_path / "examples" / "packages" / "widget"
    pkg.mkdir(parents=True)
    (pkg / "jest.config.js").write_text("module.exports = {};\n", encoding="utf-8")
    test_file = pkg / "src" / "widget.test.js"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("// t\n", encoding="utf-8")
    rel_test = str(test_file.relative_to(tmp_path)).replace("\\", "/")
    cfg = discover_jest_config_path(tmp_path, [rel_test])
    assert cfg == "examples/packages/widget/jest.config.js"


def test_apply_repo_overrides_adds_snapshot_chmod(tmp_path: Path):
    (tmp_path / "__tests__" / "__snapshots__").mkdir(parents=True)
    cfg = apply_repo_overrides({"post_install": []}, "any/owner-repo", repo=tmp_path)
    assert JAVASCRIPT_SNAPSHOT_CHMOD_CMD in cfg["post_install"]
