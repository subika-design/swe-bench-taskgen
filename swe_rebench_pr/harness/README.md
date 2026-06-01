# Bundled evaluation harness (Docker images)

Vendored from [SWE-bench](https://github.com/SWE-bench/SWE-bench) `swebench/harness` (image build + `TestSpec` only).

- **Standalone**: no `SWEBENCH_PATH` or sibling checkout required.
- **Repo specs**: `MAP_REPO_VERSION_TO_SPECS` starts empty; `swebench_images.register_task_harness_specs()` fills it from each task's `install_config`.
- **Refresh from upstream**: from repo root, re-copy and rewrite imports:

```bash
SRC=../SWE-bench/swebench/harness
DEST=pr_to_swe_rebench_jsonl/swe_rebench_pr/harness
# preserve constants/__init__.py and utils.py (slim/local), then copy dockerfiles, docker_build.py, docker_utils.py, test_spec/
```
