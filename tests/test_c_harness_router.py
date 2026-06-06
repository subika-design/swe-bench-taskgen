"""Tests for PR-driven CMake/C harness router."""

from pathlib import Path

from swe_rebench_pr.c_harness_router import (
    HarnessKind,
    apply_c_harness_router,
    clear_all_harness_flags,
    resolve_c_harness_kind,
)
from swe_rebench_pr.integration_build import native_integration_discover_active


def _runtests_repo(tmp_path: Path) -> Path:
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "runtests.pl").write_text("#!/usr/bin/env perl\n", encoding="utf-8")
    http = tests / "http"
    http.mkdir()
    (http / "conftest.py").write_text("# pytest\n", encoding="utf-8")
    return tmp_path


def test_resolve_runtests_over_ci_pytest(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    tp = (
        "diff --git a/tests/libtest/lib1677.c b/tests/libtest/lib1677.c\n"
        "diff --git a/tests/libtest/lib1556.c b/tests/libtest/lib1556.c\n"
    )
    assert resolve_c_harness_kind(repo, "", tp) == HarnessKind.RUNTESTS


def test_router_clears_stale_native_for_libtest(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    tp = "diff --git a/tests/libtest/lib1677.c b/tests/libtest/lib1677.c\n"
    cfg = {
        "language": "c",
        "native_integration_build": True,
        "native_integration_pytest_root": "tests/http",
        "install": "cmake .. -DUSE_NGTCP2=ON -DCMAKE_BUILD_TYPE=Release",
        "test_cmd": "python3 -m pytest tests/http/test_foo.py",
    }
    out = apply_c_harness_router(cfg, repo, test_patch=tp)
    assert not native_integration_discover_active(out)
    assert out.get("cmake_runtests_build")
    assert out.get("result_format") == "runtests_log"
    assert "runtests.pl" in str(out.get("test_cmd") or "")
    assert "1677" in str(out.get("test_cmd") or "")
    assert "USE_NGTCP2" not in str(out.get("install") or "").upper()


def test_resolve_native_pytest_for_http_patch(tmp_path: Path):
    repo = _runtests_repo(tmp_path)
    tp = "diff --git a/tests/http/test_05_01.py b/tests/http/test_05_01.py\n"
    assert resolve_c_harness_kind(repo, "", tp) == HarnessKind.NATIVE_PYTEST


def test_clear_all_harness_flags():
    cfg = {
        "native_integration_build": True,
        "cmake_runtests_build": True,
        "install": "cmake ..",
    }
    out = clear_all_harness_flags(cfg)
    assert "native_integration_build" not in out
    assert "cmake_runtests_build" not in out
    assert out["install"] == "cmake .."
