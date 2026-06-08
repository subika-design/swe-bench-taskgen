from pathlib import Path

from swe_rebench_pr.django_runtests import paths_to_runtests_labels
from swe_rebench_pr.install_llm import (
    apt_debian_packages_for_reqs_path,
    django_test_apps_from_targets,
    django_test_module_from_path,
    merge_pre_install_debian_packages,
    render_django_pytest_settings,
    sanitize_install_config_for_docker,
)
from swe_rebench_pr.swebench_align import export_install_config_for_harness
from swe_rebench_pr.test_log_parsers import parse_django_runtests_log


def test_django_sanitize_uses_runtests_for_swebench(tmp_path: Path):
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "runtests.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        'requires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    req_dir = tmp_path / "tests" / "requirements"
    req_dir.mkdir(parents=True)
    (req_dir / "py3.txt").write_text("pylibmc\n", encoding="utf-8")
    cfg = sanitize_install_config_for_docker(
        {"python": "3.10", "install": "pip install -e .", "test_cmd": "pytest -rA"},
        "django/django",
        repo=tmp_path,
    )
    harness = export_install_config_for_harness(cfg)
    assert "django_runtests" not in harness
    assert cfg.get("django_runtests") is True
    assert cfg.get("python") == "3.12"
    assert cfg.get("install") == "pip install -e ."
    assert cfg.get("post_install") == []
    assert "runtests.py" in harness.get("test_cmd", "")
    assert "test_sqlite" in harness.get("test_cmd", "")
    assert harness.get("pytest_plugins") is None
    assert "tests/requirements/py3.txt" in (harness.get("reqs_path") or [])
    assert "mysql.txt" not in " ".join(harness.get("reqs_path") or [])
    pre = "\n".join(harness.get("pre_install") or [])
    assert "libmemcached-dev" in pre
    eval_cmds = "\n".join(harness.get("eval_commands") or [])
    assert "LANG=en_US.UTF-8" in eval_cmds


def test_django_instance_id_strips_meson_llm_recipe(tmp_path: Path):
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "runtests.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('requires-python = ">=3.12"\n', encoding="utf-8")
    cfg = sanitize_install_config_for_docker(
        {
            "python": "3.11",
            "install": "# editable install in post_install",
            "post_install": ["meson setup", "python3 -m pip install -q -e ."],
            "pip_packages": ["meson", "pyarrow", "pytest-django"],
        },
        "django__django-21319",
        repo=tmp_path,
    )
    assert cfg.get("python") == "3.12"
    assert cfg.get("install") == "pip install -e ."
    assert cfg.get("post_install") == []
    assert "meson" not in " ".join(cfg.get("pip_packages") or []).lower()


def test_paths_to_runtests_labels():
    labels = paths_to_runtests_labels(
        [
            "tests/generic_views/test_dates.py",
            "tests/generic_views/test_utils.py",
        ]
    )
    assert labels == ["generic_views.test_dates", "generic_views.test_utils"]


def test_result_paths_use_runtests_logs_for_django():
    from pathlib import Path

    from swe_rebench_pr.docker_discover import _result_paths

    work = Path("/tmp/w")
    base, patch = _result_paths(
        work,
        "python",
        install_config={"test_cmd": "./tests/runtests.py --verbosity 2"},
    )
    assert base.name == "test-base.log"
    assert patch.name == "test-patch.log"


def test_parse_django_runtests_log():
    log = (
        "test_archive_view (generic_views.test_dates.ArchiveIndexViewTests) ... ok\n"
        "test_bad (generic_views.test_dates.ArchiveIndexViewTests) ... FAIL\n"
    )
    m = parse_django_runtests_log(log)
    assert m["test_archive_view (generic_views.test_dates.ArchiveIndexViewTests)"] == "PASSED"
    assert m["test_bad (generic_views.test_dates.ArchiveIndexViewTests)"] == "FAILED"


def test_apt_packages_for_py3_txt(tmp_path: Path):
    req = tmp_path / "tests" / "requirements"
    req.mkdir(parents=True)
    (req / "py3.txt").write_text("pylibmc\n", encoding="utf-8")
    deb = apt_debian_packages_for_reqs_path(
        ["tests/requirements/py3.txt"],
        repo=tmp_path,
    )
    assert "libmemcached-dev" in deb


def test_apt_packages_for_mysql_reqs_path():
    deb = apt_debian_packages_for_reqs_path(["tests/requirements/mysql.txt"])
    assert "pkg-config" in deb
    assert "libmariadb-dev" in deb


def test_heuristic_fix_harness_build_pylibmc():
    from swe_rebench_pr.swebench_images import heuristic_fix_install_config_from_harness_build

    cfg = heuristic_fix_install_config_from_harness_build(
        {"python": "3.12", "pre_install": []},
        "Failed building wheel for pylibmc\nlibmemcached/memcached.h: No such file",
        repo_id="django/django",
    )
    pre = "\n".join(cfg.get("pre_install") or [])
    assert "libmemcached-dev" in pre
    apt = (cfg.get("apt-pkgs") or []) + (cfg.get("apt-pkgs-optional") or [])
    assert "libmemcached-dev" in apt


def test_env_script_installs_apt_before_pip_for_pylibmc():
    from swe_rebench_pr.harness.test_spec.python import _env_apt_setup_commands

    cmds = _env_apt_setup_commands({}, "pylibmc; sys_platform != 'win32'\n")
    joined = "\n".join(cmds)
    assert "apt-get update" in joined
    assert "libmemcached-dev" in joined


def test_merge_pre_install_adds_mysql_packages():
    pre = merge_pre_install_debian_packages(
        [
            "apt-get update -qq",
            "apt-get install -y --no-install-recommends git build-essential",
        ],
        ["pkg-config", "libmariadb-dev"],
    )
    joined = "\n".join(pre)
    assert "pkg-config" in joined
    assert "libmariadb-dev" in joined


def test_render_django_pytest_settings_includes_test_apps():
    body = render_django_pytest_settings(
        [
            "tests/admin_views/test_autocomplete_view.py",
            "tests/view_tests/tests/test_i18n.py",
        ]
    )
    assert "'admin_views'" in body
    assert "'view_tests'" in body
    assert "'view_tests.tests'" in body
    assert "TEMPLATES" in body
    assert django_test_module_from_path("tests/admin_views/test_autocomplete_view.py") == "admin_views"
    assert django_test_apps_from_targets(["tests/view_tests/tests/test_i18n.py"]) == [
        "view_tests",
        "view_tests.tests",
    ]
