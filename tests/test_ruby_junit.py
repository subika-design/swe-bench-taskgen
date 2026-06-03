"""RSpec JUnit node id and path matching."""

from pathlib import Path

from swe_rebench_pr.diff_split import (
    harness_test_label,
    junit_outcome_counts_for_paths,
    parse_junit,
)
from swe_rebench_pr.ruby_build import (
    remediate_ruby_install_from_log,
    rspec_junit_nodeid_in_test_patch_paths,
)


def test_rspec_junit_nodeid_uses_spec_file(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    spec = repo / "spec" / "rubocop" / "cop" / "style" / "empty_literal_spec.rb"
    spec.parent.mkdir(parents=True)
    spec.write_text("RSpec.describe 'x' do\nend\n", encoding="utf-8")

    junit = tmp_path / "junit.xml"
    junit.write_text(
        f"""<?xml version="1.0"?>
<testsuites>
  <testsuite name="RuboCop::Cop::Style::EmptyLiteral" tests="1" file="{spec}">
    <testcase classname="RuboCop::Cop::Style::EmptyLiteral"
              name="registers an offense"
              time="0.01"/>
  </testsuite>
</testsuites>""",
        encoding="utf-8",
    )

    case_map = parse_junit(junit, repo, language="ruby")
    assert len(case_map) == 1
    nid = next(iter(case_map))
    assert nid.startswith("spec/rubocop/cop/style/empty_literal_spec.rb::")
    assert "registers an offense" in nid

    tp = ["spec/rubocop/cop/style/empty_literal_spec.rb"]
    pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(
        case_map, tp, language="ruby"
    )
    assert tot == 1
    assert pa == 1


def test_rspec_junit_nodeid_matches_basename_alias():
    paths = ["spec/rubocop/cop/style/empty_literal_spec.rb"]
    assert rspec_junit_nodeid_in_test_patch_paths(
        "spec/rubocop/cop/style/empty_literal_spec.rb::registers an offense",
        paths,
    )
    assert rspec_junit_nodeid_in_test_patch_paths(
        "empty_literal_spec.rb::registers an offense",
        paths,
    )


def test_remediate_ruby_formatter_missing_adds_post_install():
    cfg = {"install": "bundle install", "post_install": []}
    log = "LoadError: uninitialized constant RspecJunitFormatter"
    out = remediate_ruby_install_from_log(cfg, log)
    post = "\n".join(out.get("post_install") or [])
    assert "rspec_junit_formatter" in post
    assert "bundle add" in post


def test_remediate_ruby_gem_missing_retries_bundle_install():
    cfg = {"install": "true", "post_install": []}
    log = "Could not find gem 'parser' (>= 3.3.0) in locally installed gems"
    out = remediate_ruby_install_from_log(cfg, log)
    assert "bundle install" in out["install"]
    assert "disable_version_check" in out["install"]


def test_harness_test_label_ruby_branch(tmp_path: Path):
    import xml.etree.ElementTree as ET

    repo = tmp_path / "repo"
    repo.mkdir()
    spec = repo / "spec" / "foo_spec.rb"
    spec.parent.mkdir(parents=True)
    spec.write_text("", encoding="utf-8")
    case = ET.Element(
        "testcase",
        {
            "classname": "Foo",
            "name": "bar",
            "file": str(spec),
        },
    )
    label = harness_test_label(case, repo, language="ruby")
    assert label == "spec/foo_spec.rb::bar"
