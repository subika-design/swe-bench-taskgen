"""Tests for jest-junit directory merge helper."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HARVEST_SCRIPT = Path(__file__).resolve().parents[1] / "swe_rebench_pr" / "docker_entry.py"


def _harvest_py_source() -> str:
    text = HARVEST_SCRIPT.read_text(encoding="utf-8")
    marker = "HARVEST_JEST_JUNIT_PY = r'''"
    start = text.index(marker) + len(marker)
    end = text.index("'''", start)
    return text[start:end]


def test_merge_junit_dir_combines_testcases(tmp_path: Path, monkeypatch):
    script = tmp_path / "harvest_jest_junit.py"
    script.write_text(_harvest_py_source().strip() + "\n", encoding="utf-8")
    junit_dir = tmp_path / "junit"
    junit_dir.mkdir()
    (junit_dir / "TESTS-node.xml").write_text(
        '<?xml version="1.0"?><testsuite name="s">'
        '<testcase classname="c" name="n"/></testsuite>\n',
        encoding="utf-8",
    )
    out = tmp_path / "merged.xml"
    proc = subprocess.run(
        [sys.executable, str(script), str(out), str(junit_dir)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "[harvest] merged" in proc.stderr
    assert "testcase" in out.read_text(encoding="utf-8")
