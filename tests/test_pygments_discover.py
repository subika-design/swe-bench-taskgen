"""Pygments-style pytest data-file tests (snippets/examplefiles)."""

from __future__ import annotations

from pathlib import Path

from swe_rebench_pr.diff_split import (
    junit_outcome_counts_for_paths,
    parse_junit,
)
from swe_rebench_pr.docker_entry import _python_body
from swe_rebench_pr.languages import (
    collect_test_targets_from_test_patch,
    filter_python_pytest_targets,
    is_test_path,
    get_language_spec,
)


PYGMENTS_TEST_PATCH = """\
diff --git a/tests/examplefiles/lateralus/fibonacci.ltl b/tests/examplefiles/lateralus/fibonacci.ltl
new file mode 100644
--- /dev/null
+++ b/tests/examplefiles/lateralus/fibonacci.ltl
@@ -0,0 +1 @@
+1
diff --git a/tests/examplefiles/lateralus/fibonacci.ltl.output b/tests/examplefiles/lateralus/fibonacci.ltl.output
new file mode 100644
--- /dev/null
+++ b/tests/examplefiles/lateralus/fibonacci.ltl.output
@@ -0,0 +1 @@
+'1'
diff --git a/tests/snippets/lateralus/pipeline.txt b/tests/snippets/lateralus/pipeline.txt
new file mode 100644
--- /dev/null
+++ b/tests/snippets/lateralus/pipeline.txt
@@ -0,0 +1 @@
+x
"""


def test_pygments_test_paths_recognized_and_output_filtered():
    spec = get_language_spec("python")
    assert is_test_path("tests/snippets/lateralus/pipeline.txt", spec)
    assert is_test_path("tests/examplefiles/lateralus/fibonacci.ltl", spec)
    assert not is_test_path("tests/examplefiles/lateralus/fibonacci.ltl.output", spec)

    collected = collect_test_targets_from_test_patch("python", PYGMENTS_TEST_PATCH)
    assert "tests/snippets/lateralus/pipeline.txt" in collected
    assert "tests/examplefiles/lateralus/fibonacci.ltl" in collected
    assert "tests/examplefiles/lateralus/fibonacci.ltl.output" not in collected

    filtered = filter_python_pytest_targets(
        [
            "tests/snippets/lateralus/pipeline.txt",
            "tests/examplefiles/lateralus/fibonacci.ltl.output",
        ]
    )
    assert filtered == ["tests/snippets/lateralus/pipeline.txt"]


def test_pygments_junit_classname_to_nodeid(tmp_path: Path):
    repo = tmp_path / "repo"
    snippet = repo / "tests/snippets/lateralus/pipeline.txt"
    snippet.parent.mkdir(parents=True)
    snippet.write_text("x\n", encoding="utf-8")

    junit = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="1">
  <testcase classname="tests.snippets.lateralus.pipeline.txt" name="" time="0.01"/>
</testsuite></testsuites>"""
    jpath = tmp_path / "junit.xml"
    jpath.write_text(junit, encoding="utf-8")

    case_map = parse_junit(jpath, repo, language="python")
    assert case_map == {"tests/snippets/lateralus/pipeline.txt::": "passed"}

    tp = ["tests/snippets/lateralus/pipeline.txt"]
    pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(case_map, tp)
    assert tot == 1 and pa == 1 and fa == 0


def test_python_body_two_phase_patch_apply():
    body = _python_body({}, False, repo_dir="/testbed")
    assert "base + test_patch only" in body
    assert "test_patch + impl.patch" in body
    assert "_apply_one /w/test.patch" in body
    assert "_apply_one /w/impl.patch" in body
    assert "reset to base_commit" in body
    assert "git clean -ffdx" in body
    assert body.index("/w/test.patch") < body.index("/w/impl.patch")
