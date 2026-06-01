from pathlib import Path

from swe_rebench_pr.diff_split import (
    _mocha_title_nodeid_matches_js_paths,
    has_test_patch_label_mismatch,
    junit_outcome_counts_for_paths,
    parse_junit,
)


def test_mocha_junit_uses_testsuite_file_for_nodeids(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    test_file = repo / "__tests__" / "common" / "transforms.test.js"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("// test\n", encoding="utf-8")

    junit = tmp_path / "junit.xml"
    junit.write_text(
        f"""<?xml version="1.0"?>
<testsuites>
  <testsuite name="transforms" tests="2" file="{test_file}">
    <testcase classname="converts rem to pt"
              name="common transforms dimension size/remToPt converts rem to pt"
              time="0.01"/>
    <testcase classname="allows every property to be optional"
              name="common transforms composed border/css/shorthand allows every property"
              time="0.02"/>
  </testsuite>
</testsuites>""",
        encoding="utf-8",
    )

    case_map = parse_junit(junit, repo, language="javascript")
    assert len(case_map) == 2
    for nid in case_map:
        assert nid.startswith("__tests__/common/transforms.test.js::")

    tp = ["__tests__/common/transforms.test.js"]
    pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(case_map, tp)
    assert tot == 2
    assert pa == 2
    assert not has_test_patch_label_mismatch(case_map, tp, language="javascript")


def test_mocha_title_only_nodeid_matches_single_test_file():
    tp = ["__integration__/android.test.js"]
    assert _mocha_title_nodeid_matches_js_paths("should export tokens::passes", tp)
    assert not has_test_patch_label_mismatch(
        {"should export tokens::passes": "passed"},
        tp,
        language="javascript",
    )


def test_mocha_title_matches_file_stem_in_nodeid():
    tp = ["__tests__/common/transforms.test.js"]
    nid = "common transforms::converts rem to pt"
    assert _mocha_title_nodeid_matches_js_paths(nid, tp)
    assert not has_test_patch_label_mismatch({nid: "passed"}, tp, language="javascript")
