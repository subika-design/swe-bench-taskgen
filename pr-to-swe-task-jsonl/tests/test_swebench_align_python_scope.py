"""Harness export/repair scopes Python ``test_cmd`` for Pygments data-file tests."""

from __future__ import annotations

import json

from swe_rebench_pr.ci_fidelity import pytest_cmd_has_scoped_paths, pytest_cmd_needs_explicit_paths
from swe_rebench_pr.python_build import (
    expand_pytest_discover_targets,
    pytest_test_cmd_from_targets,
    scope_python_test_cmd_for_harness,
)
from swe_rebench_pr.swebench_align import repair_jsonl_row_install_config

_PYGMENTS_TEST_PATCH = """diff --git a/tests/examplefiles/pddl/example-domain.pddl b/tests/examplefiles/pddl/example-domain.pddl
new file mode 100644
--- /dev/null
+++ b/tests/examplefiles/pddl/example-domain.pddl
@@ -0,0 +1 @@
+(define (domain example-domain))
diff --git a/tests/examplefiles/pddl/example-domain.pddl.output b/tests/examplefiles/pddl/example-domain.pddl.output
new file mode 100644
--- /dev/null
+++ b/tests/examplefiles/pddl/example-domain.pddl.output
@@ -0,0 +1 @@
+'x'
diff --git a/tests/examplefiles/pddl/example-problem.pddl b/tests/examplefiles/pddl/example-problem.pddl
new file mode 100644
--- /dev/null
+++ b/tests/examplefiles/pddl/example-problem.pddl
@@ -0,0 +1 @@
+(define (problem example-problem))
"""


def _pygments_row() -> dict:
    return {
        "instance_id": "pygments__pygments-2799",
        "language": "python",
        "test_patch": _PYGMENTS_TEST_PATCH,
        "FAIL_TO_PASS": json.dumps(
            [
                "tests/examplefiles/pddl/example-domain.pddl::",
                "tests/examplefiles/pddl/example-problem.pddl::",
            ]
        ),
        "PASS_TO_PASS": "[]",
        "install_config": {"install": "pip install -e .", "test_cmd": "pytest -rA"},
    }


def test_expand_pytest_discover_targets_keeps_pygments_data_files():
    paths = [
        "tests/examplefiles/pddl/example-domain.pddl",
        "tests/examplefiles/pddl/example-domain.pddl.output",
        "tests/snippets/foo/bar.txt",
    ]
    expanded = expand_pytest_discover_targets(paths, include_parent_dir=False)
    assert expanded == [
        "tests/examplefiles/pddl/example-domain.pddl",
        "tests/snippets/foo/bar.txt",
    ]


def test_pytest_cmd_helpers_recognize_pygments_paths():
    paths = ["tests/examplefiles/pddl/example-domain.pddl"]
    assert pytest_cmd_needs_explicit_paths("pytest -rA", paths)
    scoped = pytest_test_cmd_from_targets(paths, "pytest -rA")
    assert pytest_cmd_has_scoped_paths(scoped)
    assert "example-domain.pddl" in scoped
    assert ".output" not in scoped


def test_scope_python_test_cmd_for_harness_pygments():
    row = _pygments_row()
    cfg = scope_python_test_cmd_for_harness(dict(row["install_config"]), test_patch=row["test_patch"])
    tc = cfg["test_cmd"]
    assert tc.startswith("pytest")
    assert "example-domain.pddl" in tc
    assert "example-problem.pddl" in tc
    assert ".output" not in tc


def test_repair_jsonl_row_scopes_pygments_test_cmd():
    fixed = repair_jsonl_row_install_config(_pygments_row())
    tc = fixed["install_config"]["test_cmd"]
    assert "example-domain.pddl" in tc
    assert ".output" not in tc
