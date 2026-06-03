from swe_rebench_pr.apt_from_log import (
    merge_apt_into_config,
    sanitize_apt_package_names,
    sanitize_native_integration_apt_config,
)
from swe_rebench_pr.ci_extract import apt_packages_from_ci_workflows
from swe_rebench_pr.integration_build import native_build_install_config


def test_sanitize_drops_caddy_and_h2o():
    assert sanitize_apt_package_names(["libssl-dev", "caddy", "h2o", "cmake"]) == [
        "libssl-dev",
        "cmake",
    ]


def test_native_build_config_excludes_blocklisted_apt(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text(
        "find_package(OpenSSL REQUIRED)\n",
        encoding="utf-8",
    )
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "conftest.py").write_text("", encoding="utf-8")
    (http / "CMakeLists.txt").write_text(
        'find_program(H2O "h2o")\nfind_program(CADDY "caddy")\n',
        encoding="utf-8",
    )
    cfg = native_build_install_config(
        {"language": "python"},
        tmp_path,
        test_paths=["tests/http/test_x.py"],
    )
    apt = [p.lower() for p in (cfg.get("apt-pkgs") or [])]
    assert "caddy" not in apt
    assert "h2o" not in apt


def test_ci_workflow_apt_sanitized(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "      - run: sudo apt-get install -y libssl-dev caddy h2o cmake\n",
        encoding="utf-8",
    )
    pkgs = apt_packages_from_ci_workflows(tmp_path)
    assert "libssl-dev" in pkgs
    assert "caddy" not in pkgs
    assert "h2o" not in pkgs


def test_sanitize_native_integration_scrubs_pre_install():
    cfg = sanitize_native_integration_apt_config(
        {
            "native_integration_build": True,
            "apt-pkgs": ["caddy", "cmake"],
            "pre_install": [
                "apt-get update -qq",
                "apt-get install -y --no-install-recommends caddy h2o cmake",
            ],
        }
    )
    assert "caddy" not in (cfg.get("apt-pkgs") or [])
    pre = " ".join(cfg.get("pre_install") or [])
    assert "caddy" not in pre
    assert "h2o" not in pre
    assert "cmake" in pre


def test_merge_apt_always_sanitizes():
    cfg = merge_apt_into_config({}, ["caddy", "git"])
    assert cfg.get("apt-pkgs") == ["git"]
