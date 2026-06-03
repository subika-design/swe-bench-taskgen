from pathlib import Path

from swe_rebench_pr.docker_discover import _docker_install_failed
from swe_rebench_pr.ci_extract import (
    DEFAULT_NATIVE_HTTP3_CMAKE_DEFINITIONS,
    apt_packages_from_ci_workflows,
    cmake_definitions_from_ci_for_http3_pytest,
)
from swe_rebench_pr.integration_build import (
    ci_pre_pytest_setup_lines,
    ci_pytest_run_lines,
    discover_pytest_integration_roots,
    integration_profile_for_repo,
    integration_sync_config_from_build,
    native_build_install_config,
    native_integration_already_applied,
    native_integration_cmake_src_symlink_lines,
    native_integration_setup_lines,
    native_integration_test_cmd,
    pytest_root_for_test_paths,
    repo_needs_cmake_src_tool_symlinks,
    _native_http3_pre_install_lines,
    repo_wants_http3_pytest_cmake,
    strip_unsafe_native_shell_lines,
)
from swe_rebench_pr.swebench_align import internal_install_keys, merge_internal_install_keys


def test_discover_pytest_integration_roots(tmp_path: Path):
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "conftest.py").write_text("# pytest\n", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    roots = discover_pytest_integration_roots(tmp_path)
    assert "tests/http" in roots


def test_integration_profile_from_test_paths(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "conftest.py").write_text("", encoding="utf-8")
    paths = ["tests/http/test_foo.py", "tests/http/conftest.py"]
    profile = integration_profile_for_repo(tmp_path, test_paths=paths)
    assert profile is not None
    assert profile.pytest_root == "tests/http"


def test_pytest_root_for_test_paths_common_parent():
    paths = [
        "tests/http/test_a.py",
        "tests/http/testenv/env.py",
    ]
    assert pytest_root_for_test_paths(paths) == "tests/http"


def test_apt_packages_from_ci_workflows(tmp_path: Path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "      - run: sudo apt-get install -y libssl-dev pkg-config cmake\n",
        encoding="utf-8",
    )
    pkgs = apt_packages_from_ci_workflows(tmp_path)
    assert "libssl-dev" in pkgs
    assert "cmake" in pkgs


def test_integration_sync_config_from_build(tmp_path: Path):
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "config.ini.in").write_text("[http]\n", encoding="utf-8")
    lines = integration_sync_config_from_build(tmp_path, "tests/http")
    assert lines
    assert "config.ini" in lines[0]
    assert "build/tests/http" in lines[0]


def test_ci_pre_pytest_empty_when_pytest_only_multiline(tmp_path: Path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "linux.yml").write_text(
        """
      - run: autoreconf -fi
      - run: sudo apt-get install -y cmake
      - run: |
          cmake --build bld --target curl-pytest-ci
""",
        encoding="utf-8",
    )
    assert ci_pre_pytest_setup_lines(tmp_path) == []


def test_strip_unsafe_native_shell_lines():
    lines = [
        "cmake --build build --target testdeps",
        "autoreconf -fi",
        "sudo apt-get install -y foo",
        'cmake -B bld -DCMAKE_BUILD_TYPE=Debug ..',
    ]
    kept = strip_unsafe_native_shell_lines(lines)
    assert kept == ["cmake --build build --target testdeps"]


def test_native_integration_setup_excludes_ci_noise(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    tests_cmake = tmp_path / "tests"
    tests_cmake.mkdir(parents=True, exist_ok=True)
    (tests_cmake / "CMakeLists.txt").write_text("add_custom_target(testdeps)\n", encoding="utf-8")
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "conftest.py").write_text("", encoding="utf-8")
    (http / "config.ini.in").write_text("", encoding="utf-8")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "linux.yml").write_text(
        """
      - run: autoreconf -fi
      - run: make -C bld V=1 -C tests
      - run: |
          pytest -n auto tests/http
""",
        encoding="utf-8",
    )
    setup = native_integration_setup_lines(tmp_path, "tests/http")
    joined = " ".join(setup)
    assert "testdeps" in joined
    assert "autoreconf" not in joined
    assert "make -C bld" not in joined


def test_ci_pre_pytest_skips_pytest_ci_target(tmp_path: Path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        """
      - run: cmake --build build --target testdeps
      - run: cmake --build build --target curl-pytest-ci
      - run: pytest -n auto tests/http
""",
        encoding="utf-8",
    )
    lines = ci_pre_pytest_setup_lines(tmp_path)
    assert any("testdeps" in ln for ln in lines)
    assert not any("curl-pytest-ci" in ln for ln in lines)
    assert not any(ln.strip().startswith("pytest") for ln in lines)


def test_repo_needs_cmake_src_tool_symlinks_for_curl_layout(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "CMakeLists.txt").write_text(
        'add_executable(curl "")\nadd_executable(curlinfo EXCLUDE_FROM_ALL "curlinfo.c")\n',
        encoding="utf-8",
    )
    assert repo_needs_cmake_src_tool_symlinks(tmp_path)
    links = native_integration_cmake_src_symlink_lines(tmp_path)
    assert any("src/curlinfo" in ln for ln in links)
    assert any("build/src/curlinfo" in ln for ln in links)


def test_native_integration_setup_includes_config_sync(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "CMakeLists.txt").write_text(
        'add_executable(curl "")\nadd_executable(curlinfo EXCLUDE_FROM_ALL "curlinfo.c")\n',
        encoding="utf-8",
    )
    tests_cmake = tmp_path / "tests"
    tests_cmake.mkdir(parents=True, exist_ok=True)
    (tests_cmake / "CMakeLists.txt").write_text(
        "add_custom_target(testdeps)\nadd_custom_target(build-certs)\n",
        encoding="utf-8",
    )
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "conftest.py").write_text("", encoding="utf-8")
    (http / "config.ini.in").write_text("", encoding="utf-8")
    setup = native_integration_setup_lines(tmp_path, "tests/http")
    assert any("config.ini" in ln for ln in setup)
    assert any("testdeps" in ln for ln in setup)
    assert any("curlinfo" in ln for ln in setup)
    assert any("ln -sfn" in ln and "src/curlinfo" in ln for ln in setup)
    assert any("sed -i" in ln and "sshd =" in ln for ln in setup)


def test_filter_native_integration_apt_drops_openssh_server():
    from swe_rebench_pr.integration_build import filter_native_integration_apt_packages

    assert "openssh-server" not in filter_native_integration_apt_packages(
        ["libssl-dev", "openssh-server", "vsftpd"]
    )


def test_integration_sanitize_config_ini_clears_sshd(tmp_path: Path):
    from swe_rebench_pr.integration_build import integration_sanitize_config_ini_lines

    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "config.ini").write_text(
        "[sshd]\nsshd = /usr/sbin/sshd\nsftpd = /usr/lib/openssh/sftp-server\n",
        encoding="utf-8",
    )
    lines = integration_sanitize_config_ini_lines("tests/http")
    assert lines
    shell = "\n".join(lines)
    assert "sed -i" in shell and "sshd =" in shell


def test_sanitize_native_integration_strips_unsafe_post_install():
    from swe_rebench_pr.apt_from_log import sanitize_native_integration_apt_config

    cfg = sanitize_native_integration_apt_config(
        {
            "native_integration_build": True,
            "post_install": [
                "cmake --build build --target testdeps",
                "autoreconf -fi",
            ],
        }
    )
    post = cfg.get("post_install") or []
    assert any("testdeps" in ln for ln in post)
    assert not any("autoreconf" in ln for ln in post)


def test_native_build_install_config_sets_cmake_install(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text(
        "find_package(OpenSSL REQUIRED)\nfind_package(Libpsl REQUIRED)\n",
        encoding="utf-8",
    )
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "conftest.py").write_text("", encoding="utf-8")
    (http / "config.ini.in").write_text("", encoding="utf-8")
    (http / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "CMakeLists.txt").write_text("add_custom_target(testdeps)\n", encoding="utf-8")
    cfg = native_build_install_config(
        {"language": "python", "install": "pip install ."},
        tmp_path,
        test_paths=["tests/http/test_x.py"],
    )
    assert native_integration_already_applied(cfg)
    assert "cmake" in str(cfg.get("install") or "").lower()
    assert "libssl-dev" in (cfg.get("apt-pkgs") or [])
    post = " ".join(cfg.get("post_install") or [])
    assert "requirements.txt" in post
    assert cfg.get("native_integration_pytest_root") == "tests/http"
    tc = str(cfg.get("test_cmd") or "")
    assert "__JUNIT_OUT__" in tc and "__TARGETS__" in tc
    assert "pytest" in tc
    setup = cfg.get("native_integration_setup") or []
    assert any("config.ini" in str(ln) for ln in setup)
    eval_joined = " ".join(cfg.get("eval_commands") or [])
    assert "export PATH" in eval_joined
    assert "export CURL=" in eval_joined
    assert "/testbed/build/src/curl" in eval_joined
    assert "cmake --build" not in eval_joined
    assert cfg.get("test_env", {}).get("CURL") == "/testbed/build/src/curl"
    assert cfg.get("test_env", {}).get("CURL_CI") == "1"
    assert "openssh-server" not in (cfg.get("apt-pkgs") or [])
    post_joined = " ".join(cfg.get("post_install") or [])
    assert "cmake --build" in post_joined


def test_native_integration_test_cmd():
    tc = native_integration_test_cmd("tests/http")
    assert "__JUNIT_OUT__" in tc and "__TARGETS__" in tc
    assert "pytest" in tc


def test_native_integration_eval_commands_strips_cmake_setup():
    from swe_rebench_pr.integration_build import native_integration_eval_commands

    cfg = {
        "eval_commands": [
            'export PATH="/testbed/build/src:$PATH"',
            "cmake --build build --target testdeps",
        ],
    }
    ev = native_integration_eval_commands(cfg)
    assert any(ln.startswith("export PATH") for ln in ev)
    assert any("export CURL=" in ln for ln in ev)
    assert not any("cmake --build" in ln for ln in ev)


def test_python_docker_entry_uses_integration_test_cmd(tmp_path: Path):
    from swe_rebench_pr.docker_entry import write_entry_script

    work = tmp_path / "w"
    work.mkdir()
    cfg = {
        "native_integration_build": True,
        "native_integration_pytest_root": "tests/http",
        "native_integration_setup": ["cmake --build build --target testdeps"],
        "test_cmd": (
            "python3 -m pytest --junitxml=__JUNIT_OUT__ __TARGETS__"
        ),
    }
    write_entry_script(work, "python", ["tests/http/test_a.py"], cfg, harness_image=True)
    script = (work / "docker_entry.sh").read_text(encoding="utf-8")
    assert "_run_python_integration_tests" in script
    assert "PY_INTEGRATION_TEST_CMD" in script
    assert "__JUNIT_OUT__" in script
    assert "-e build " in script
    assert "-e tests/http/config.ini " in script
    assert "(cd /testbed && cmake --build build --target testdeps)" in script


def test_cmake_native_prepare_targets(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "CMakeLists.txt").write_text(
        "add_custom_target(testdeps)\nadd_custom_target(libtests)\n",
        encoding="utf-8",
    )
    from swe_rebench_pr.integration_build import cmake_native_prepare_targets

    assert "testdeps" in cmake_native_prepare_targets(tmp_path)


def test_ci_pytest_run_lines(tmp_path: Path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "      - run: cd tests/http && pytest -rA test_foo.py\n",
        encoding="utf-8",
    )
    lines = ci_pytest_run_lines(tmp_path)
    assert any("pytest" in ln for ln in lines)


def test_internal_install_keys_preserve_native_integration():
    cfg = {
        "install": "cmake",
        "native_integration_build": True,
        "native_integration_pytest_root": "tests/http",
    }
    merged = merge_internal_install_keys(
        {"install": "pip install ."},
        internal_install_keys(cfg),
    )
    assert merged.get("native_integration_build") is True
    assert merged.get("native_integration_pytest_root") == "tests/http"


def test_python_empty_junit_not_install_failed_with_native_integration_flag():
    assert (
        _docker_install_failed(
            docker_exit=0,
            n_patch=0,
            n_targets=6,
            log_tail="pytest finished",
            django_runtests=False,
            install_config={"language": "python", "native_integration_build": True},
            lang="python",
        )
        is False
    )


def test_cmake_definitions_from_ci_for_http3_pytest(tmp_path: Path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "linux.yml").write_text(
        """
    strategy:
      matrix:
        include:
          - name: address-sanitizer H3 c-ares
            generate: |
              -DCMAKE_BUILD_TYPE=Release
              -DUSE_NGTCP2=ON
              -DUSE_SSLS_EXPORT=ON
              -DENABLE_ARES=ON
              -DUSE_PROXY_HTTP3=ON
              -DCURL_CLANG_TIDY=ON
            install_steps: pytest
""",
        encoding="utf-8",
    )
    flags = cmake_definitions_from_ci_for_http3_pytest(tmp_path)
    assert "-DUSE_NGTCP2=ON" in flags
    assert "-DUSE_PROXY_HTTP3=ON" in flags
    assert all("CLANG_TIDY" not in f for f in flags)


def test_native_build_install_config_http3_cmake_and_h2o(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "conftest.py").write_text("", encoding="utf-8")
    (http / "config.ini.in").write_text("", encoding="utf-8")
    (http / "test_60_h3_proxy.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "CMakeLists.txt").write_text("add_custom_target(testdeps)\n", encoding="utf-8")
    assert repo_wants_http3_pytest_cmake(tmp_path, ["tests/http/test_60_h3_proxy.py"])
    cfg = native_build_install_config(
        {"language": "c", "install": "true"},
        tmp_path,
        test_paths=["tests/http/test_60_h3_proxy.py"],
    )
    install = str(cfg.get("install") or "")
    assert "USE_NGTCP2" in install.upper()
    assert "Ninja" in install or "ninja" in install.lower()
    pre = " ".join(cfg.get("pre_install") or [])
    assert "h2o" in pre
    assert "libngtcp2-crypto-gnutls-dev" in pre
    assert "bookworm-backports" not in pre
    assert "libngtcp2-crypto-ossl-dev" not in (cfg.get("apt-pkgs") or [])
    assert cfg.get("language") == "c"
    if not cmake_definitions_from_ci_for_http3_pytest(tmp_path):
        for flag in DEFAULT_NATIVE_HTTP3_CMAKE_DEFINITIONS:
            assert flag in install


def test_native_http3_pre_install_no_backports():
    pre = "\n".join(_native_http3_pre_install_lines(None))
    assert "bookworm-backports" not in pre
    assert "libngtcp2-crypto-gnutls-dev" in pre
    assert "libngtcp2-crypto-ossl-dev" in pre


def test_repo_wants_http3_from_cmake_and_test_paths(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20)\n",
        encoding="utf-8",
    )
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "conftest.py").write_text("", encoding="utf-8")
    assert repo_wants_http3_pytest_cmake(tmp_path, ["tests/http/test_foo_h3.py"])
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20)\nset(USE_NGTCP2 ON)\n",
        encoding="utf-8",
    )
    assert repo_wants_http3_pytest_cmake(tmp_path, ["tests/unit/test_plain.py"])


def test_merge_pre_install_handles_update_and_install_line():
    from swe_rebench_pr.install_llm import merge_pre_install_debian_packages

    pre = [
        "export DEBIAN_FRONTEND=noninteractive",
        "apt-get update -qq && apt-get install -y --no-install-recommends ninja-build libngtcp2-dev",
    ]
    merged = merge_pre_install_debian_packages(pre, ["cmake", "pkg-config", "libssl-dev"])
    joined = "\n".join(merged)
    assert "apt-get update -qq && apt-get install" in joined
    assert "cmake" in joined and "pkg-config" in joined
    assert "ninja-build" in joined
    assert joined.count("apt-get install -y") == 1


def test_http3_native_build_strips_redundant_c_apt_preinstall(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8")
    http = tmp_path / "tests" / "http"
    http.mkdir(parents=True)
    (http / "conftest.py").write_text("", encoding="utf-8")
    (http / "test_60_h3_proxy.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (http / "config.ini.in").write_text("", encoding="utf-8")
    cfg = native_build_install_config(
        {"language": "c", "install": "true"},
        tmp_path,
        test_paths=["tests/http/test_60_h3_proxy.py"],
    )
    pre = cfg.get("pre_install") or []
    assert any("libnghttp3-dev" in ln for ln in pre)
    assert not any(
        ln.strip() == "apt-get update -qq"
        or (
            ln.strip().startswith("apt-get install")
            and "cmake" in ln
            and "apt-get update" not in ln
        )
        for ln in pre
    )


def test_filter_http3_apt_drops_bookworm_missing_ossl_crypto():
    from swe_rebench_pr.integration_build import filter_http3_apt_for_harness

    pkgs = filter_http3_apt_for_harness(
        ["libngtcp2-dev", "libngtcp2-crypto-ossl-dev", "ninja-build"]
    )
    assert "libngtcp2-dev" in pkgs
    assert "libngtcp2-crypto-ossl-dev" not in pkgs


def test_sanitize_native_integration_keeps_h2o_for_http3():
    from swe_rebench_pr.apt_from_log import sanitize_native_integration_apt_config

    cfg = sanitize_native_integration_apt_config(
        {
            "native_integration_build": True,
            "install": "cmake -G Ninja .. -DUSE_NGTCP2=ON",
            "apt-pkgs": ["h2o", "libssl-dev"],
            "pre_install": [
                "apt-get install -y --no-install-recommends h2o libngtcp2-dev",
            ],
        }
    )
    assert "h2o" in (cfg.get("apt-pkgs") or [])
    assert "h2o" in " ".join(cfg.get("pre_install") or [])
