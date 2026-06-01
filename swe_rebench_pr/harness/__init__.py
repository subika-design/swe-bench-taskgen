"""Bundled SWE-bench-style Docker image builder (standalone; no external SWE-bench checkout)."""

from swe_rebench_pr.harness.docker_build import build_instance_images
from swe_rebench_pr.harness.test_spec.test_spec import TestSpec, make_test_spec

__all__ = ["TestSpec", "build_instance_images", "make_test_spec"]
