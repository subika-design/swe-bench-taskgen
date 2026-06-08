from swe_rebench_pr.test_log_parsers import parse_pytest_log


def test_parse_pytest_log_status_first():
    log = "PASSED tests/test_a.py::test_x\nFAILED tests/test_b.py::test_y\n"
    m = parse_pytest_log(log)
    assert m["tests/test_a.py::test_x"] == "PASSED"
    assert m["tests/test_b.py::test_y"] == "FAILED"


def test_parse_pytest_log_trailing_status():
    log = "tests/test_a.py::test_x PASSED\n"
    m = parse_pytest_log(log)
    assert m["tests/test_a.py::test_x"] == "PASSED"
