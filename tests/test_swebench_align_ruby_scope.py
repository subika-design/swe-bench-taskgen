"""Harness export/repair scopes Ruby ``test_cmd`` to ``test_patch`` specs."""

from __future__ import annotations

import json
from pathlib import Path

from swe_rebench_pr.ci_fidelity import rspec_cmd_has_scoped_paths, rspec_cmd_needs_explicit_paths
from swe_rebench_pr.ruby_build import (
    merge_ruby_harness_fields_after_llm,
    ruby_rspec_spec_paths_from_nodeids,
    scope_ruby_test_cmd_for_harness,
)
from swe_rebench_pr.swebench_align import (
    repair_jsonl_row_install_config,
    scope_install_config_test_cmd_for_harness,
)

_RUBOCOP_CI_CMD = (
    "bundle exec rspec --format RspecJunitFormatter --out __JUNIT_OUT__ "
    "2>/dev/null || bundle exec rspec"
)

_RUBOCOP_TEST_PATCH = """diff --git a/spec/rubocop/config_obsoletion_spec.rb b/spec/rubocop/config_obsoletion_spec.rb
index 2a31e97e9c7c..9ad381cc1713 100644
--- a/spec/rubocop/config_obsoletion_spec.rb
+++ b/spec/rubocop/config_obsoletion_spec.rb
@@ -576,6 +576,40 @@ def plugin_stub(name)
       end
     end
+
+    context 'when the configuration includes parameters renamed for consistency' do
+      it 'prints a warning message and does not raise' do
+        expect { config_obsoletion.reject_obsolete! }.not_to raise_error
+      end
+    end
"""


def _rubocop_like_row() -> dict:
    return {
        "instance_id": "rubocop__rubocop-15194",
        "language": "ruby",
        "test_patch": _RUBOCOP_TEST_PATCH,
        "FAIL_TO_PASS": json.dumps(
            [
                "./spec/rubocop/config_obsoletion_spec.rb::RuboCop::ConfigObsoletion "
                "prints a warning message and does not raise"
            ]
        ),
        "PASS_TO_PASS": "[]",
        "install_config": {
            "install": "bundle install || true",
            "test_cmd": _RUBOCOP_CI_CMD,
            "ruby_test_runner": "rspec",
            "_ci_test_cmd_trusted": True,
        },
    }


def test_rspec_cmd_helpers():
    assert not rspec_cmd_has_scoped_paths("bundle exec rspec")
    assert rspec_cmd_has_scoped_paths(
        "bundle exec rspec spec/foo_spec.rb spec/bar_spec.rb"
    )
    assert rspec_cmd_needs_explicit_paths(
        "bundle exec rspec",
        ["spec/foo_spec.rb"],
    )
    assert not rspec_cmd_needs_explicit_paths(
        "bundle exec rspec spec/foo_spec.rb",
        ["spec/foo_spec.rb"],
    )


def test_ruby_rspec_spec_paths_from_nodeids():
    nodeids = [
        "./spec/rubocop/config_obsoletion_spec.rb::RuboCop::ConfigObsoletion prints",
        "./spec/rubocop/cop/bundler/gem_comment_spec.rb::RuboCop::Cop::Bundler::GemComment",
    ]
    paths = ruby_rspec_spec_paths_from_nodeids(nodeids)
    assert "spec/rubocop/config_obsoletion_spec.rb" in paths
    assert "spec/rubocop/cop/bundler/gem_comment_spec.rb" in paths


def test_scope_ruby_test_cmd_from_test_patch():
    row = _rubocop_like_row()
    cfg = scope_install_config_test_cmd_for_harness(row, dict(row["install_config"]))
    tc = cfg["test_cmd"]
    assert "config_obsoletion_spec.rb" in tc
    assert rspec_cmd_has_scoped_paths(tc)
    assert cfg.get("_ci_test_cmd_trusted") is None
    eval_cmds = cfg.get("eval_commands") or []
    assert "bundle check >/dev/null 2>&1 || bundle install" in eval_cmds


def test_scope_ruby_test_cmd_falls_back_to_f2p_nodeids():
    cfg = scope_ruby_test_cmd_for_harness(
        {"install": "bundle install", "test_cmd": _RUBOCOP_CI_CMD, "ruby_test_runner": "rspec"},
        test_patch="",
        fail_to_pass=[
            "./spec/rubocop/cop/style/fetch_env_var_spec.rb::RuboCop::Cop::Style::FetchEnvVar"
        ],
    )
    assert "fetch_env_var_spec.rb" in cfg["test_cmd"]


def test_repair_jsonl_row_scopes_rubocop_test_cmd():
    row = _rubocop_like_row()
    fixed = repair_jsonl_row_install_config(row)
    tc = fixed["install_config"]["test_cmd"]
    assert "config_obsoletion_spec.rb" in tc
    assert rspec_cmd_has_scoped_paths(tc)


def test_repair_jsonl_file_scopes_fixture(tmp_path: Path):
    from swe_rebench_pr.swebench_align import repair_jsonl_file

    src = tmp_path / "in.jsonl"
    dst = tmp_path / "out.jsonl"
    src.write_text(json.dumps(_rubocop_like_row()) + "\n", encoding="utf-8")
    repair_jsonl_file(src, dst)
    row = json.loads(dst.read_text(encoding="utf-8").strip())
    tc = row["install_config"]["test_cmd"]
    assert "config_obsoletion_spec.rb" in tc


def test_scope_ruby_adds_eval_commands_when_patch_touches_gemfile():
    patch = "diff --git a/Gemfile b/Gemfile\n+++ b/Gemfile\n+gem 'rubydex'\n"
    cfg = scope_ruby_test_cmd_for_harness(
        {"install": "true", "test_cmd": "bundle exec rspec spec/foo_spec.rb"},
        test_patch="diff --git a/spec/foo_spec.rb b/spec/foo_spec.rb\n",
        patch=patch,
    )
    assert "bundle check >/dev/null 2>&1 || bundle install" in (cfg.get("eval_commands") or [])


def test_merge_ruby_harness_fields_after_llm_preserves_scoped_paths():
    scoped = scope_ruby_test_cmd_for_harness(
        {"language": "ruby", "test_cmd": _RUBOCOP_CI_CMD, "ruby_test_runner": "rspec"},
        test_patch=_RUBOCOP_TEST_PATCH,
    )
    after_llm = {
        "language": "ruby",
        "test_cmd": "bundle exec rspec",
        "ruby_test_runner": "rspec",
    }
    merged = merge_ruby_harness_fields_after_llm(scoped, after_llm)
    assert rspec_cmd_has_scoped_paths(merged["test_cmd"])
    assert "config_obsoletion_spec.rb" in merged["test_cmd"]
