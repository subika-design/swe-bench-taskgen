from swe_rebench_pr.diff_split import (
    pytest_style_nodeid_to_gradle_test_key,
    swebench_gradable_nodeid,
)


def test_pytest_nodeid_to_gradle_key():
    nid = (
        "org/springframework/boot/docs/howto/webserver/"
        "enablemultipleconnectors/jetty/MyJettyConfigurationTests.py"
        "::connectorCustomizerBeanIsRegistered()"
    )
    assert (
        pytest_style_nodeid_to_gradle_test_key(nid)
        == "org.springframework.boot.docs.howto.webserver.enablemultipleconnectors.jetty."
        "MyJettyConfigurationTests > connectorCustomizerBeanIsRegistered"
    )


def test_java_gradable_accepts_gradle_key():
    key = (
        "org.springframework.boot.context.metrics.buffering.BufferingApplicationStartupTests"
        " > sourceFilesShouldHaveCorrectCopyrightHeaderPattern"
    )
    assert swebench_gradable_nodeid(key, language="java") is True
