from swe_rebench_pr.harness.test_spec.utils import make_repo_script_list_common


def test_make_repo_script_list_common_install_string_not_split():
    specs = {
        "pre_install": ["apt-get update -qq"],
        "install": "chmod +x ./gradlew && ./gradlew :foo:compileTestJava",
    }
    cmds = make_repo_script_list_common(
        specs, "spring-projects/spring-boot", "/testbed", "abc123", "testbed"
    )
    assert "chmod +x ./gradlew && ./gradlew :foo:compileTestJava" in cmds
    assert "c" not in cmds[cmds.index("apt-get update -qq") + 1 :]
