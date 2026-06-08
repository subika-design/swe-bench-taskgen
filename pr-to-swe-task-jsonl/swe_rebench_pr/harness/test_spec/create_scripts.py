from swe_rebench_pr.harness.constants import (
    MAP_REPO_TO_EXT,
)
from swe_rebench_pr.harness.git_clone_cmds import (
    git_clone_branch_arg,
    git_fetch_and_reset_commands,
    git_post_reset_hygiene_commands,
    python_conda_activate_commands,
    python_swebench_marker_commands,
)
from swe_rebench_pr.harness.test_spec.javascript import (
    make_eval_script_list_js,
)
from swe_rebench_pr.harness.test_spec.python import (
    make_repo_script_list_py,
    make_env_script_list_py,
    make_eval_script_list_py,
)
from swe_rebench_pr.harness.test_spec.utils import (
    make_env_script_list_common,
    make_eval_script_list_common,
    make_repo_script_list_common,
)


def _clear_repo_directory_commands(repo_directory: str) -> list[str]:
    """Clear a repo path without deleting a Docker bind-mount root (e.g. ``/testbed``)."""
    return [
        "cd /",
        f"mkdir -p {repo_directory}",
        f"find {repo_directory} -mindepth 1 -maxdepth 1 -exec rm -rf {{}} + 2>/dev/null || true",
    ]


def _git_safe_directory_commands(repo_directory: str) -> list[str]:
    """Allow git in bind-mounted repo dirs (host uid != container uid)."""
    return [f"git config --global --add safe.directory {repo_directory}"]


def make_repo_clone_script_list(
    specs, repo, repo_directory, base_commit, env_name
) -> list:
    """
    Clone/checkout/git hygiene only (no project install).

    Used when discover runs from the env image: install steps come from
    ``install_config`` entry scripts on each container start.
    """
    ext = MAP_REPO_TO_EXT[repo]
    if ext == "py":
        branch = git_clone_branch_arg(repo, base_commit)
        return [
            *_clear_repo_directory_commands(repo_directory),
            f"git clone -o origin {branch} --single-branch https://github.com/{repo} {repo_directory}",
            f"chmod -R 777 {repo_directory}",
            *_git_safe_directory_commands(repo_directory),
            f"cd {repo_directory}",
            *git_fetch_and_reset_commands(base_commit),
            *git_post_reset_hygiene_commands(base_commit),
            *python_conda_activate_commands(env_name),
            *python_swebench_marker_commands(),
        ]
    return [
        *_clear_repo_directory_commands(repo_directory),
        f"git clone -o origin https://github.com/{repo} {repo_directory}",
        f"chmod -R 777 {repo_directory}",
        *_git_safe_directory_commands(repo_directory),
        f"cd {repo_directory}",
        *git_fetch_and_reset_commands(base_commit),
        "git remote remove origin",
    ]


def make_repo_script_list(specs, repo, repo_directory, base_commit, env_name) -> list:
    """
    Create a list of bash commands to set up the repository for testing.
    This is the setup script for the instance image.
    """
    ext = MAP_REPO_TO_EXT[repo]
    func = {
        "py": make_repo_script_list_py,
    }.get(ext, make_repo_script_list_common)
    return func(specs, repo, repo_directory, base_commit, env_name)


def make_env_script_list(instance, specs, env_name) -> list:
    """
    Creates the list of commands to set up the environment for testing.
    This is the setup script for the environment image.
    """
    ext = MAP_REPO_TO_EXT[instance["repo"]]
    func = {
        "py": make_env_script_list_py,
    }.get(ext, make_env_script_list_common)
    return func(instance, specs, env_name)


def make_eval_script_list(
    instance, specs, env_name, repo_directory, base_commit, test_patch
) -> list:
    """
    Applies the test patch and runs the tests.
    """
    ext = MAP_REPO_TO_EXT[instance["repo"]]
    common_func = make_eval_script_list_common
    func = {
        "js": make_eval_script_list_js,
        "py": make_eval_script_list_py,
    }.get(ext, common_func)
    return func(instance, specs, env_name, repo_directory, base_commit, test_patch)
