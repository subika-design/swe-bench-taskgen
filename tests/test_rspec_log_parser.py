from swe_rebench_pr.test_log_parsers import PASSED, FAILED, parse_rspec_log


def test_parse_rspec_log_explicit_status():
    log = "PASSED spec/foo_spec.rb::registers an offense\nFAILED spec/bar_spec.rb::other case"
    m = parse_rspec_log(log)
    assert m["spec/foo_spec.rb::registers an offense"] == PASSED
    assert m["spec/bar_spec.rb::other case"] == FAILED


def test_parse_rspec_log_failure_line():
    log = "rspec ./spec/foo_spec.rb:12 # Cop example registers an offense"
    m = parse_rspec_log(log)
    assert "spec/foo_spec.rb::Cop example registers an offense" in m
    assert m["spec/foo_spec.rb::Cop example registers an offense"] == FAILED
