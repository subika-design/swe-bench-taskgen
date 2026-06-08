from swe_rebench_pr.patch_paths import (
    collect_gradable_test_paths_from_diff,
    collect_impl_paths_from_diff,
    is_gradable_test_path,
    is_non_test_infrastructure_path,
)


def test_non_test_infrastructure_paths():
    assert is_non_test_infrastructure_path(".github/workflows/test.yml")
    assert is_non_test_infrastructure_path(".golangci.yml")
    assert is_non_test_infrastructure_path("Cargo.lock")
    assert is_non_test_infrastructure_path("GUIDE.md")
    assert not is_non_test_infrastructure_path("command_test.go")


def test_gradable_go_test_file():
    assert is_gradable_test_path("command_test.go", "go")
    assert not is_gradable_test_path(".github/workflows/test.yml", "go")


def test_gradable_rust_integration_test():
    assert is_gradable_test_path("tests/feature.rs", "rust")
    assert not is_gradable_test_path("Cargo.lock", "rust")


def test_collect_gradable_excludes_ci_from_diff():
    diff = (
        "diff --git a/.github/workflows/test.yml b/.github/workflows/test.yml\n"
        "--- a/.github/workflows/test.yml\n"
        "+++ b/.github/workflows/test.yml\n"
        "@@ -1 +1 @@\n"
        "+x\n"
        "diff --git a/command_test.go b/command_test.go\n"
        "--- a/command_test.go\n"
        "+++ b/command_test.go\n"
        "@@ -1 +1 @@\n"
        "+// test\n"
    )
    paths = collect_gradable_test_paths_from_diff(diff, "go")
    assert paths == ["command_test.go"]


def test_collect_impl_paths_excludes_test_and_infra():
    diff = (
        "diff --git a/.github/workflows/test.yml b/.github/workflows/test.yml\n"
        "--- a/.github/workflows/test.yml\n"
        "+++ b/.github/workflows/test.yml\n"
        "@@ -1 +1 @@\n"
        "+x\n"
        "diff --git a/command_test.go b/command_test.go\n"
        "--- a/command_test.go\n"
        "+++ b/command_test.go\n"
        "@@ -1 +1 @@\n"
        "+// test\n"
        "diff --git a/command.go b/command.go\n"
        "--- a/command.go\n"
        "+++ b/command.go\n"
        "@@ -1 +1 @@\n"
        "+// impl\n"
    )
    paths = collect_impl_paths_from_diff(diff, {"command_test.go"}, "go")
    assert paths == ["command.go"]
