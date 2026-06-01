from swe_rebench_pr.diff_split import split_impl_and_test_patch
from swe_rebench_pr.patch_sanitize import filter_junk_from_unified_diff, is_junk_patch_path


def test_is_junk_patch_path_ds_store():
    assert is_junk_patch_path(".DS_Store")
    assert is_junk_patch_path("foo/.DS_Store")
    assert is_junk_patch_path("__MACOSX/foo")


def test_filter_junk_from_unified_diff():
    diff = (
        "diff --git a/.DS_Store b/.DS_Store\n"
        "Binary files differ\n"
        "diff --git a/src/foo.py b/src/foo.py\n"
        "--- a/src/foo.py\n+++ b/src/foo.py\n"
        "@@ -1 +1 @@\n"
        "+pass\n"
    )
    out = filter_junk_from_unified_diff(diff)
    assert ".DS_Store" not in out
    assert "src/foo.py" in out


def test_split_impl_skips_ds_store():
    diff = (
        "diff --git a/.DS_Store b/.DS_Store\n"
        "Binary files differ\n"
        "diff --git a/lib/x.py b/lib/x.py\n"
        "--- a/lib/x.py\n+++ b/lib/x.py\n"
        "@@ -1 +1 @@\n"
        "+1\n"
    )
    impl, test = split_impl_and_test_patch(diff)
    assert ".DS_Store" not in impl
    assert "lib/x.py" in impl
