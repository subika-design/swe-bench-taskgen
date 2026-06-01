"""Canonical field order for SWE-rebench-style JSONL rows (16 fields)."""

OUTPUT_KEYS: tuple[str, ...] = (
    "instance_id",
    "patch",
    "repo",
    "base_commit",
    "hints_text",
    "created_at",
    "test_patch",
    "problem_statement",
    "version",
    "environment_setup_commit",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "task_type",
    "language",
    "install_config",
    "requirements",
    "environment",
)
