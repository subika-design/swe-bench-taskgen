"""JUnit path matching when pytest runs from native_integration_pytest_root."""

from swe_rebench_pr.diff_split import (
    _nodeid_in_test_patch_paths,
    _path_filter_sets,
    has_test_patch_label_mismatch,
    junit_outcome_counts_for_paths,
)


def _curl_tp_paths() -> list[str]:
    return [
        "tests/http/conftest.py",
        "tests/http/test_60_h3_proxy.py",
        "tests/http/testenv/curl.py",
        "tests/http/testenv/env.py",
        "tests/http/testenv/h2o.py",
        "tests/http/testenv/nghttpx.py",
    ]


def test_native_integration_nodeid_matches_curl_h3_proxy():
    paths = _curl_tp_paths()
    path_set, dotted, java_fqcns = _path_filter_sets(paths)
    nid = (
        "test_60_h3_proxy/TestH3ProxyConnectionManagement.py::"
        "test_60_10_proxy_basic_auth"
    )
    assert _nodeid_in_test_patch_paths(
        nid,
        path_set,
        dotted,
        java_fqcns,
        test_patch_paths=paths,
        native_integration_pytest_root="tests/http",
    )


def test_native_integration_mismatch_without_root():
    paths = _curl_tp_paths()
    path_set, dotted, java_fqcns = _path_filter_sets(paths)
    nid = "test_60_h3_proxy/TestH3ProxyConnectionManagement.py::test_60_10"
    assert not _nodeid_in_test_patch_paths(nid, path_set, dotted, java_fqcns)


def test_native_integration_has_no_label_mismatch():
    case_map = {
        "test_60_h3_proxy/TestH3ProxyDataTransfer.py::test_60_07_large_download": "skipped",
        "test_60_h3_proxy/TestH3ProxyRobustness.py::test_60_05_graceful_shutdown": "skipped",
    }
    assert not has_test_patch_label_mismatch(
        case_map,
        _curl_tp_paths(),
        native_integration_pytest_root="tests/http",
    )


def test_native_integration_outcome_counts_include_skipped():
    case_map = {
        "test_60_h3_proxy/TestH3ProxyDataTransfer.py::test_60_07_large_download": "skipped",
        "tests/http/other.py::test_x": "passed",
    }
    pa, fa, ea, sk, tot = junit_outcome_counts_for_paths(
        case_map,
        _curl_tp_paths(),
        native_integration_pytest_root="tests/http",
    )
    assert tot == 1
    assert sk == 1
    assert pa == 0
