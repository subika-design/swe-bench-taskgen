"""FAIL_TO_PASS scope filter must match Java JUnit nodeids to test_patch paths."""

from swe_rebench_pr.docker_discover import _filter_f2p_to_test_patch_scope


def test_filter_f2p_keeps_java_nodeids_for_test_patch_paths():
    tp = [
        "spring-boot-project/spring-boot-docs/src/test/java/"
        "org/springframework/boot/docs/howto/webserver/"
        "enablemultipleconnectors/jetty/MyJettyConfigurationTests.java"
    ]
    f2p = [
        (
            "org/springframework/boot/docs/howto/webserver/"
            "enablemultipleconnectors/jetty/MyJettyConfigurationTests.py::contextLoads()"
        ),
        (
            "org/springframework/boot/docs/howto/webserver/"
            "enablemultipleconnectors/jetty/MyJettyConfigurationTests.py::jettyHasTwoConnectors()"
        ),
    ]
    kept = _filter_f2p_to_test_patch_scope(f2p, tp, "java")
    assert len(kept) == 2


def test_filter_f2p_drops_unrelated_java_tests():
    tp = [
        "spring-boot-project/spring-boot-docs/src/test/java/"
        "org/springframework/boot/docs/FooTests.java"
    ]
    f2p = ["org/springframework/boot/build/OtherTests.py::x()"]
    assert _filter_f2p_to_test_patch_scope(f2p, tp, "java") == []
