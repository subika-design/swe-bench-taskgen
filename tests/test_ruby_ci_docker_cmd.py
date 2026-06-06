from swe_rebench_pr.ci_fidelity import mark_ci_test_cmd_trusted
from swe_rebench_pr.ruby_build import apply_ruby_runner_to_config, ruby_test_cmd_for_docker_entry


def test_apply_ruby_runner_preserves_ci_test_cmd(tmp_path):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    cfg = mark_ci_test_cmd_trusted(
        {
            "test_cmd": "bundle exec rake spec",
            "_ci_excerpt": "bundle exec rake spec",
        }
    )
    out = apply_ruby_runner_to_config(cfg, tmp_path)
    assert out["test_cmd"] == "bundle exec rake spec"


def test_ruby_docker_cmd_from_ci():
    cfg = mark_ci_test_cmd_trusted({"test_cmd": "bundle exec rake spec"})
    assert "rake spec" in ruby_test_cmd_for_docker_entry(cfg)
