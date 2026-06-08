"""repo_overrides.yaml loading and apply."""

from pathlib import Path

from swe_rebench_pr.repo_detect import (
    JAVASCRIPT_SNAPSHOT_CHMOD_CMD,
    _parse_repo_overrides_yaml,
    apply_repo_overrides,
    javascript_snapshot_post_install,
)


def test_parse_repo_overrides_yaml():
    text = '''
"owner/repo":
  post_install:
    - "chmod snapshots"
'''
    data = _parse_repo_overrides_yaml(text)
    assert data["owner/repo"]["post_install"] == ["chmod snapshots"]


def test_apply_repo_overrides_merges_post_install():
    cfg = apply_repo_overrides(
        {"post_install": ["existing"]},
        "style-dictionary/style-dictionary",
    )
    assert "existing" in cfg["post_install"]


def test_javascript_snapshot_post_install(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"mocha"}}',
        encoding="utf-8",
    )
    (tmp_path / "__integration__" / "__snapshots__").mkdir(parents=True)
    lines = javascript_snapshot_post_install(tmp_path)
    assert lines == [JAVASCRIPT_SNAPSHOT_CHMOD_CMD]
