"""RSpec JUnit node id and path matching."""

from pathlib import Path

from swe_rebench_pr.diff_split import (
    harness_test_label,
    junit_outcome_counts_for_paths,
    parse_junit,
)
from swe_rebench_pr.ruby_build import (
    _strip_ruby_rspec_junit_fallback,
    apply_ruby_runner_to_config,
    filter_rspec_map_to_test_patch_paths,
    log_indicates_ruby_gem_not_found,
    refine_ruby_junit_maps_for_discover,
    remediate_ruby_install_from_log,
    rspec_junit_nodeid_in_test_patch_paths,
    rspec_log_indicates_all_passed,
    rspec_log_indicates_examples_ran,
    ruby_rspec_spec_paths,
    ruby_test_cmd_for_runner,
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


def test_strip_ruby_rspec_junit_fallback():
    raw = (
        "bundle exec rspec --format RspecJunitFormatter --out __JUNIT_OUT__ "
        "2>/dev/null || bundle exec rspec"
    )
    assert "2>/dev/null" not in _strip_ruby_rspec_junit_fallback(raw)
    assert "|| bundle exec rspec" not in _strip_ruby_rspec_junit_fallback(raw)


def test_ruby_rspec_spec_paths_filters_non_spec():
    paths = [
        "spec/rubocop/formatter/json_formatter_spec.rb",
        "lib/rubocop/formatter/json_formatter.rb",
        "spec/other_spec.rb",
    ]
    assert ruby_rspec_spec_paths(paths) == [
        "spec/other_spec.rb",
        "spec/rubocop/formatter/json_formatter_spec.rb",
    ]


def test_ruby_test_cmd_scoped_to_spec_paths():
    paths = ["spec/rubocop/formatter/json_formatter_spec.rb"]
    cmd = ruby_test_cmd_for_runner("rspec", spec_paths=paths)
    assert "json_formatter_spec.rb" in cmd
    assert "RspecJunitFormatter" in cmd
    assert "__JUNIT_OUT__" in cmd
    assert "2>/dev/null" not in cmd


def test_apply_ruby_runner_scopes_test_cmd(tmp_path: Path):
    (tmp_path / "spec").mkdir()
    (tmp_path / "spec" / "spec_helper.rb").write_text("", encoding="utf-8")
    cfg = apply_ruby_runner_to_config(
        {"install": "bundle install", "test_cmd": "true"},
        tmp_path,
        test_paths=["spec/foo_spec.rb", "lib/foo.rb"],
    )
    assert "spec/foo_spec.rb" in cfg["test_cmd"]
    assert "RspecJunitFormatter" in cfg["test_cmd"]


def test_filter_rspec_map_to_test_patch_paths():
    case_map = {
        "spec/alpha_spec.rb::example one": "passed",
        "spec/beta_spec.rb::example two": "failed",
        "spec/gamma_spec.rb::other": "passed",
    }
    filtered = filter_rspec_map_to_test_patch_paths(
        case_map, ["spec/alpha_spec.rb", "spec/beta_spec.rb"]
    )
    assert set(filtered) == {
        "spec/alpha_spec.rb::example one",
        "spec/beta_spec.rb::example two",
    }


def test_rspec_log_indicators():
    assert rspec_log_indicates_examples_ran("Finished in 1.2s\n78 examples, 4 failures")
    assert rspec_log_indicates_all_passed("78 examples, 0 failures")
    assert not rspec_log_indicates_all_passed("No examples found.")
    assert not rspec_log_indicates_all_passed("78 examples, 4 failures")


def test_refine_ruby_junit_maps_scopes_base(tmp_path: Path):
    tp = ["spec/foo_spec.rb"]
    base_map = {
        "spec/foo_spec.rb::a": "failed",
        "spec/other_spec.rb::b": "passed",
    }
    patch_map = {"spec/other_spec.rb::b": "passed"}
    scoped_base, scoped_patch = refine_ruby_junit_maps_for_discover(
        base_map,
        patch_map,
        test_patch_paths=tp,
        work_dir=tmp_path,
    )
    assert scoped_base == {"spec/foo_spec.rb::a": "failed"}
    assert scoped_patch == {}


def test_refine_ruby_junit_maps_log_fallback_when_patch_xml_empty(tmp_path: Path):
    tp = ["spec/foo_spec.rb"]
    base_map = {"spec/foo_spec.rb::a": "failed", "spec/foo_spec.rb::b": "failed"}
    patch_map: dict[str, str] = {}
    (tmp_path / "test-patch.log").write_text(
        "78 examples, 0 failures\nFinished in 2.1 seconds\n",
        encoding="utf-8",
    )
    scoped_base, scoped_patch = refine_ruby_junit_maps_for_discover(
        base_map,
        patch_map,
        test_patch_paths=tp,
        work_dir=tmp_path,
    )
    assert scoped_base == base_map
    assert scoped_patch == {
        "spec/foo_spec.rb::a": "passed",
        "spec/foo_spec.rb::b": "passed",
    }


def test_log_indicates_ruby_gem_not_found_asciidoctor():
    log = "Bundler::GemNotFound: Could not find gem 'asciidoctor' in locally installed gems"
    assert log_indicates_ruby_gem_not_found(log)


def test_docker_install_failed_ruby_gem_not_found_after_patch():
    from swe_rebench_pr.docker_discover import _docker_install_failed

    log = """
[docker] rspec (test_patch + impl.patch)
Bundler::GemNotFound: Could not find gem 'asciidoctor'
"""
    assert _docker_install_failed(
        docker_exit=0,
        n_patch=0,
        n_targets=3,
        log_tail=log,
        install_config={"language": "ruby"},
        lang="ruby",
    )


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
