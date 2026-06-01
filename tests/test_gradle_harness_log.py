from swe_rebench_pr.swebench_align import (
    _build_java_display_name_to_gradle_aliases,
    canonicalize_java_gradle_test_maps,
)
from swe_rebench_pr.test_log_parsers import parse_gradle_harness_log


def test_parse_gradle_harness_log_parameterized():
    cls = (
        "org.springframework.boot.autoconfigure.security.oauth2.resource.reactive."
        "ReactiveOAuth2ResourceServerAutoConfigurationTests"
    )
    method = (
        "autoConfigurationShouldConfigureResourceServerWithJwtConverterCustomizations"
        "(String[], Jwt, String, String[])"
    )
    log = f"""{cls} > {method}[1] PASSED
{cls} > {method}[2] PASSED
"""
    parsed = parse_gradle_harness_log(log)
    assert parsed[f"{cls} > {method}[1]"] == "PASSED"
    assert parsed[f"{cls} > {method}[2]"] == "PASSED"


def test_canonicalize_maps_display_names_to_gradle_keys():
    cls = (
        "org.springframework.boot.autoconfigure.security.oauth2.resource.reactive."
        "ReactiveOAuth2ResourceServerAutoConfigurationTests"
    )
    method = (
        "autoConfigurationShouldConfigureResourceServerWithJwtConverterCustomizations"
        "(String[], Jwt, String, String[])"
    )
    gradle_log = "\n".join(
        f"{cls} > {method}[{i}] PASSED" for i in range(1, 6)
    )
    junit_map = {
        f"{cls} > All JWT converter customizations": "passed",
        f"{cls} > Custom JWT authority claim name": "passed",
        f"{cls} > Custom JWT principal claim name": "passed",
        f"{cls} > Custom delimiter for JWT scopes": "passed",
        f"{cls} > Custom prefix for GrantedAuthority": "passed",
    }
    merged = canonicalize_java_gradle_test_maps(junit_map, gradle_log)
    assert merged[f"{cls} > {method}[2]"] == "PASSED"
    aliases = _build_java_display_name_to_gradle_aliases(
        list(junit_map.keys()), merged
    )
    assert aliases[f"{cls} > Custom JWT authority claim name"] == f"{cls} > {method}[2]"
