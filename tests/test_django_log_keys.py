import json

from swe_rebench_pr.diff_split import filter_swebench_gradable_nodeids
from swe_rebench_pr.django_runtests import _reason_from_log_line
from swe_rebench_pr.swebench_align import (
    _parse_django_log_local,
    normalize_django_runtests_test_key,
    normalize_django_runtests_status_map,
)


def test_normalize_strips_migration_prefix():
    raw = (
        "Applying sites.0002_alter_domain_unique..."
        "test_editable (model_fields.test_binaryfield.BinaryFieldTests.test_editable)"
    )
    assert (
        normalize_django_runtests_test_key(raw)
        == "test_editable (model_fields.test_binaryfield.BinaryFieldTests.test_editable)"
    )


def test_parse_local_ok_line_with_migration_noise():
    log = (
        "Applying sites.0002_alter_domain_unique..."
        "test_editable (model_fields.test_binaryfield.BinaryFieldTests.test_editable) ... ok\n"
        "test_filter (model_fields.test_binaryfield.BinaryFieldTests.test_filter) ... ok\n"
    )
    m = _parse_django_log_local(log)
    assert (
        "test_editable (model_fields.test_binaryfield.BinaryFieldTests.test_editable)" in m
    )
    assert m[
        "test_editable (model_fields.test_binaryfield.BinaryFieldTests.test_editable)"
    ] == "PASSED"
    assert not any("Applying sites" in k for k in m)


def test_multiline_ok_uses_normalized_prev_test():
    log = (
        "Applying sites.0002_alter_domain_unique..."
        "test_editable (model_fields.test_binaryfield.BinaryFieldTests.test_editable) "
        "... Testing against Django installed in ...\n"
        "ok\n"
    )
    m = _parse_django_log_local(log)
    assert (
        m.get(
            "test_editable (model_fields.test_binaryfield.BinaryFieldTests.test_editable)"
        )
        == "PASSED"
    )


def test_status_map_merge_prefers_passed_on_duplicate_keys():
    raw = {
        "Applying sites.0002...test_foo (pkg.test_foo)": "FAILED",
        "test_foo (pkg.test_foo)": "PASSED",
    }
    m = normalize_django_runtests_status_map(raw)
    assert m == {"test_foo (pkg.test_foo)": "PASSED"}


def test_filter_normalizes_migration_prefixed_keys():
    kept, dropped = filter_swebench_gradable_nodeids(
        [
            "test_ok (pkg.mod.test_ok)",
            "Applying sites.0002...test_bad (pkg.mod.test_bad)",
        ],
        django_runtests=True,
    )
    assert kept == ["test_ok (pkg.mod.test_ok)", "test_bad (pkg.mod.test_bad)"]
    assert dropped == []


def test_repair_jsonl_row_strips_migration_prefix():
    from swe_rebench_pr.swebench_align import repair_jsonl_row_test_labels

    row = {
        "instance_id": "django__django-21145",
        "FAIL_TO_PASS": json.dumps(
            [
                "Applying sites.0002...test_invalid_data (model_fields.test_binaryfield.BinaryFieldTests.test_invalid_data)"
            ]
        ),
        "PASS_TO_PASS": json.dumps(
            [
                "Applying sites.0002...test_editable (model_fields.test_binaryfield.BinaryFieldTests.test_editable)"
            ]
        ),
    }
    fixed = repair_jsonl_row_test_labels(row)
    f2p = json.loads(fixed["FAIL_TO_PASS"])
    p2p = json.loads(fixed["PASS_TO_PASS"])
    assert f2p == [
        "test_invalid_data (model_fields.test_binaryfield.BinaryFieldTests.test_invalid_data)"
    ]
    assert p2p == [
        "test_editable (model_fields.test_binaryfield.BinaryFieldTests.test_editable)"
    ]


def test_docstring_style_unittest_names_remain_gradable():
    kept, _ = filter_swebench_gradable_nodeids(
        ["A ModelAdmin might not have any actions."],
        django_runtests=True,
    )
    assert kept == ["A ModelAdmin might not have any actions."]


def test_reason_from_log_line_includes_traceback():
    key = (
        "test_pickle_test_client_response_with_resolver_match "
        "(handlers_tests.test_pickle.TestClientResponsePickleTests."
        "test_pickle_test_client_response_with_resolver_match)"
    )
    log = (
        f"{key} ... ERROR\n"
        "Traceback (most recent call last):\n"
        '  File "/w/repo/tests/handlers_tests/test_pickle.py", line 42, in test_pickle\n'
        "    pickle.dumps(response)\n"
        "TypeError: cannot pickle\n"
        "test_other (handlers_tests.test_pickle.OtherTests.test_other) ... ok\n"
    )
    reason = _reason_from_log_line(log, key, "ERROR")
    assert "Traceback" in reason
    assert "TypeError" in reason
    assert "test_other" not in reason
