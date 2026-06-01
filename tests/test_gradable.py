from pathlib import Path

from swe_rebench_pr.diff_split import (
    filter_swebench_gradable_nodeids,
    pytest_marked_xfail_in_repo,
    swebench_gradable_nodeid,
    swebench_pytest_log_parseable,
)


def test_parseable_without_spaces():
    assert swebench_pytest_log_parseable(
        "pandas/tests/foo.py::test_bar[string=object]"
    )


def test_not_parseable_with_spaces_in_param():
    assert not swebench_pytest_log_parseable(
        "pandas/tests/groupby/test_cumulative.py::test_take_kwargs_deprecated[DataFrameGroupBy-Passing additional arguments to DataFrameGroupBy.take]"
    )


def test_javascript_gradable_allows_spaces_in_test_name():
    nid = "tests/unit/auth.test.ts::suite > login works"
    assert swebench_gradable_nodeid(nid, language="javascript")
    assert not swebench_gradable_nodeid("bad path.ts::x", language="javascript")


def test_filter_splits_gradable():
    kept, dropped = filter_swebench_gradable_nodeids(
        [
            "a.py::test_ok[x]",
            "a.py::test_bad[has space]",
        ],
        for_pass_to_pass=False,
    )
    assert kept == ["a.py::test_ok[x]"]
    assert len(dropped) == 1


def test_xfail_detected_in_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    ext = repo / "pandas/tests/extension/json"
    ext.mkdir(parents=True)
    (ext / "test_json.py").write_text(
        """
import pytest

class TestJSONArray:
    @pytest.mark.xfail(reason="flaky")
    def test_combine_first(self):
        pass
""",
        encoding="utf-8",
    )
    nid = "pandas/tests/extension/json/test_json.py::TestJSONArray::test_combine_first"
    assert pytest_marked_xfail_in_repo(repo, nid)
    kept, dropped = filter_swebench_gradable_nodeids(
        [nid], repo, for_pass_to_pass=True
    )
    assert kept == []
    assert dropped == [nid]
