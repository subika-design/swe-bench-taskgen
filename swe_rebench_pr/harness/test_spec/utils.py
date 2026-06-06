from swe_rebench_pr.harness.constants import (
    END_TEST_OUTPUT,
    MAP_REPO_VERSION_TO_SPECS,
    START_TEST_OUTPUT,
)
from swe_rebench_pr.harness.git_clone_cmds import git_fetch_and_reset_commands
from swe_rebench_pr.harness.utils import get_modified_files


# MARK: Test Command Creation Functions


def get_test_cmds(instance) -> list:
    test_cmd = MAP_REPO_VERSION_TO_SPECS[instance["repo"]][instance["version"]][
        "test_cmd"
    ]
    return [test_cmd] if isinstance(test_cmd, str) else test_cmd


# MARK: Script Creation Functions


def _append_spec_commands(commands: list[str], value: str | list | None) -> None:
    """Append harness spec commands; strings must not be extended char-by-char."""
    if value is None:
        return
    if isinstance(value, str):
        s = value.strip()
        if s:
            commands.append(s)
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                commands.append(item.strip())


def make_repo_script_list_common(
    specs, repo, repo_directory, base_commit, env_name
) -> list:
    """
    Create a list of bash commands to set up the repository for testing.
    This is the setup script for the instance image.
    """
    setup_commands = [
        f"git clone -o origin https://github.com/{repo} {repo_directory}",
        f"chmod -R 777 {repo_directory}",  # So nonroot user can run tests
        f"cd {repo_directory}",
        *git_fetch_and_reset_commands(base_commit),
        "git remote remove origin",  # Remove the remote so the agent won't see newer commits
    ]
    if "pre_install" in specs:
        _append_spec_commands(setup_commands, specs["pre_install"])
    if "install" in specs:
        _append_spec_commands(setup_commands, specs["install"])
    if "build" in specs:
        _append_spec_commands(setup_commands, specs["build"])
    return setup_commands


def env_apt_setup_commands(specs: dict) -> list[str]:
    """Debian packages for the harness env image (required + optional best-effort)."""
    cmds: list[str] = []
    apt_pkgs = specs.get("apt-pkgs") or []
    optional = specs.get("apt-pkgs-optional") or []
    if apt_pkgs or optional:
        cmds.append("apt-get update -qq")
    if apt_pkgs:
        cmds.append(f"apt-get install -y --no-install-recommends {' '.join(apt_pkgs)}")
    for pkg in optional:
        cmds.append(
            f"apt-get install -y --no-install-recommends {pkg} || true"
        )
    return cmds


def make_env_script_list_common(instance, specs, env_name) -> list:
    """
    Creates the list of commands to set up the environment for testing.
    This is the setup script for the environment image.
    """
    return env_apt_setup_commands(specs)


def make_eval_script_list_common(
    instance, specs, env_name, repo_directory, base_commit, test_patch
) -> list:
    """
    Applies the test patch and runs the tests.
    """
    HEREDOC_DELIMITER = "EOF_114329324912"
    test_files = get_modified_files(test_patch)
    # Reset test files to the state they should be in before the patch.
    if test_files:
        reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
    else:
        reset_tests_command = 'echo "No test files to reset"'

    build_commands: list[str] = []
    if "build" in specs:
        _append_spec_commands(build_commands, specs["build"])

    apply_test_patch_command = f"git apply --verbose --reject - <<'{HEREDOC_DELIMITER}'\n{test_patch}\n{HEREDOC_DELIMITER}"
    test_commands = get_test_cmds(instance)
    eval_commands = [
        f"cd {repo_directory}",
        f"git config --global --add safe.directory {repo_directory}",  # for nonroot user
        f"cd {repo_directory}",
        # This is just informational, so we have a record
        # f"git status",
        # f"git show",
        # f"git -c core.fileMode=false diff {base_commit}",
        reset_tests_command,
        apply_test_patch_command,
        *build_commands,
        f": '{START_TEST_OUTPUT}'",
        *test_commands,
        f": '{END_TEST_OUTPUT}'",
        reset_tests_command,
    ]
    return eval_commands
