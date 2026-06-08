from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .gh_pr import strip_mailbox_to_unified
from .patch_sanitize import is_junk_patch_path
from .llm_client import chat_completions

_STATIC_TEST_MARKERS = ("/tests/", "/testing/", "_test.py", "/test/", "/spec/", "/specs/", "__tests__")


def _heuristic_test_path(path: str) -> bool:
    low = path.replace("\\", "/").lower()
    if low.startswith("tests/") or low.startswith("test/"):
        return True
    return any(t in low for t in _STATIC_TEST_MARKERS)


def heuristic_test_path(path: str) -> bool:
    """Public alias for patch-split heuristics (``__tests__/``, ``tests/``, etc.)."""
    return _heuristic_test_path(path)


def collect_heuristic_test_paths_from_patch(patch: str) -> list[str]:
    """Paths in ``diff --git`` headers that match split heuristics but may miss ``is_test_path``."""
    paths: set[str] = set()
    for m in re.finditer(r"^diff --git a/(\S+) b/\1$", patch or "", re.MULTILINE):
        path = m.group(1)
        if _heuristic_test_path(path):
            paths.add(path)
    from .languages import filter_python_pytest_targets

    return filter_python_pytest_targets(sorted(paths))


def _iter_diff_chunks(diff: str) -> list[tuple[str, str, str]]:
    chunks = re.split(r"(?=^diff --git )", diff, flags=re.MULTILINE)
    out: list[tuple[str, str, str]] = []
    for ch in chunks:
        s = ch.strip("\n")
        if not s.startswith("diff --git "):
            continue
        m = re.search(r"^diff --git a/(\S+) b/(\S+)$", s, re.MULTILINE)
        if not m:
            continue
        a_path, b_path = m.group(1), m.group(2)
        if is_junk_patch_path(a_path) or is_junk_patch_path(b_path):
            continue
        chunk = ch if ch.endswith("\n") else ch + "\n"
        out.append((a_path, b_path, chunk))
    return out


def _roles_from_llm_json(raw: str) -> dict[str, str] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    roles = data.get("roles")
    if not isinstance(roles, dict):
        return None
    norm: dict[str, str] = {}
    for k, v in roles.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        key = k.replace("\\", "/").strip()
        val = v.strip().lower()
        if val in ("test", "impl", "implementation", "prod", "production", "src"):
            norm[key] = "test" if val == "test" else "impl"
    return norm or None


def _llm_classify_patch_paths(
    paths: list[str],
    repo_id: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_s: int,
) -> dict[str, str] | None:
    if not paths:
        return {}
    system = (
        "You label each changed file path in a pull request for SWE-bench packaging. "
        "test = automated tests, test harness, test-only fixtures/goldens, "
        "self-test scripts, or CI config that only exists to run tests. "
        "impl = production/library/app/docs unrelated to asserting correctness. "
        "Return strict JSON: {\"roles\": {\"<path>\": \"test\"|\"impl\", ...}} "
        "using the exact path strings from the user message. Cover every path."
    )
    user = json.dumps({"repo": repo_id, "paths": paths}, ensure_ascii=False)
    try:
        raw = chat_completions(
            api_key=api_key,
            base_url=base_url,
            model=model,
            system=system,
            user=user,
            timeout_s=timeout_s,
            json_object=True,
        )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    return _roles_from_llm_json(raw)


def split_impl_and_test_patch(
    diff: str,
    *,
    repo_id: str = "",
    llm: Optional[tuple[str, str, str, int]] = None,
) -> tuple[str, str]:
    """Split unified PR diff into implementation ``patch`` vs ``test_patch``.

    ``llm`` when set is ``(api_key, base_url, model, timeout_s)`` for OpenAI-compatible chat.
    """
    chunks = _iter_diff_chunks(diff)
    if not chunks:
        return "", ""

    path_roles: dict[str, str] | None = None
    if llm is not None:
        api_key, base_url, model, timeout_s = llm
        uniq = sorted({p for a, b, _ in chunks for p in (a, b)})
        path_roles = _llm_classify_patch_paths(
            uniq, repo_id or "unknown", api_key=api_key, base_url=base_url, model=model, timeout_s=timeout_s
        )
        if path_roles is None:
            print("# LLM patch split failed; using heuristics", file=sys.stderr)

    impl: list[str] = []
    test: list[str] = []

    def role_for(p: str) -> Optional[str]:
        if path_roles is None:
            return None
        if p in path_roles:
            return path_roles[p]
        return path_roles.get(p.replace("\\", "/"))

    def is_test_hunk(path_a: str, path_b: str) -> bool:
        from .patch_paths import is_gradable_test_path, is_non_test_infrastructure_path

        if is_non_test_infrastructure_path(path_a) or is_non_test_infrastructure_path(
            path_b
        ):
            return False
        ra, rb = role_for(path_a), role_for(path_b)
        if ra == "test" or rb == "test":
            return is_gradable_test_path(path_a) or is_gradable_test_path(path_b)
        if ra == "impl" and rb == "impl":
            return is_gradable_test_path(path_a) or is_gradable_test_path(path_b)
        return is_gradable_test_path(path_a) or is_gradable_test_path(path_b)

    for path_a, path_b, ch in chunks:
        if is_test_hunk(path_a, path_b):
            test.append(ch)
        else:
            impl.append(ch)
    return "".join(impl), "".join(test)


def collect_test_py_targets(patch: str, test_patch: str) -> list[str]:
    """Backward-compatible alias for Python test paths."""
    return collect_test_targets("python", patch, test_patch)


def collect_test_targets(language: str, patch: str, test_patch: str) -> list[str]:
    from .languages import collect_test_targets as _collect

    return _collect(language, patch, test_patch)


def collect_test_py_targets_from_test_patch(test_patch: str) -> list[str]:
    return collect_test_targets_from_test_patch("python", test_patch)


def collect_test_targets_from_test_patch(language: str, test_patch: str) -> list[str]:
    from .languages import collect_test_targets_from_test_patch as _collect

    return _collect(language, test_patch)


_JS_TEST_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


def _nodeid_leading_relpath(nodeid: str) -> str:
    return nodeid.split("::", 1)[0].replace("\\", "/")


def _is_js_test_relpath(rel: str) -> bool:
    low = rel.replace("\\", "/").lower()
    return any(low.endswith(ext) for ext in _JS_TEST_EXTENSIONS)


def _walk_junit_testcases(root: ET.Element):
    """Yield ``(testcase, suite_file)`` preserving testsuite ``file`` inheritance."""

    def walk(el: ET.Element, suite_file: str | None) -> None:
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "testcase":
            yield el, suite_file
            return
        if tag == "testsuite":
            suite_file = el.attrib.get("file") or suite_file
        for child in el:
            yield from walk(child, suite_file)

    yield from walk(root, None)


def _iter_junit_elements(parent: ET.Element, local_name: str):
    """Yield direct and nested children whose tag local-name equals ``local_name``."""
    want = local_name
    for el in parent.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == want:
            yield el


def _junit_xml_roots(path: Path) -> tuple[ET.Element | None, list[ET.Element]]:
    if not path.is_file():
        return None, []
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return None, []
    root = tree.getroot()
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if tag == "testsuites":
        return root, list(_iter_junit_elements(root, "testsuite"))
    if tag == "testsuite":
        return root, [root]
    return root, []


def _junit_file_to_repo_relpath(rel_s: str, repo_root: Path) -> str:
    """Strip ``/w/repo/`` (Docker junit) or host absolute prefix so paths match git diff paths."""
    s = rel_s.replace("\\", "/").strip()
    if not s:
        return s
    for marker in ("/w/repo/", "/testbed/"):
        pos = s.find(marker)
        if pos != -1:
            s = s[pos + len(marker) :]
            break
    root_ps = repo_root.resolve().as_posix().rstrip("/")
    if root_ps and s.startswith(root_ps + "/"):
        return s[len(root_ps) + 1 :]
    if s.startswith("/"):
        try:
            rel = Path(s).resolve().relative_to(repo_root.resolve())
            return rel.as_posix()
        except (ValueError, OSError):
            pass
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def _test_path_aliases(rel: str) -> frozenset[str]:
    """
    Equivalent repo-relative paths for Django-style layouts.

    Git diffs use ``tests/view_tests/.../test_foo.py`` while JUnit classnames often
    resolve to ``view_tests/.../test_foo.py`` (``tests/`` is on PYTHONPATH, not the
    module prefix).
    """
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if not rel:
        return frozenset()
    aliases = {rel}
    if rel.startswith("tests/"):
        aliases.add(rel[len("tests/") :])
    else:
        aliases.add("tests/" + rel)
    return frozenset(aliases)


def _resolve_repo_py_path(repo_root: Path, rel: str) -> str | None:
    """Return the repo-relative ``.py`` path if any alias exists on disk."""
    root = repo_root.resolve()
    for candidate in _test_path_aliases(rel):
        if (root / candidate).is_file():
            return candidate
    return None


_RUBY_SPEC_SUFFIXES = ("_spec.rb", "_test.rb")


def _is_ruby_spec_relpath(rel: str) -> bool:
    low = rel.replace("\\", "/").lower()
    return any(low.endswith(suffix) for suffix in _RUBY_SPEC_SUFFIXES)


def _ruby_path_basename_aliases(rel: str) -> frozenset[str]:
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if not rel:
        return frozenset()
    name = Path(rel).name
    aliases = {name}
    for suffix in _RUBY_SPEC_SUFFIXES:
        if name.endswith(suffix):
            stem = name[: -len(suffix)]
            if stem:
                aliases.add(stem)
    return frozenset(aliases)


def _resolve_repo_test_path(repo_root: Path, rel: str) -> str | None:
    """Return repo-relative path if a Python, Ruby, or JS/TS test file exists on disk."""
    resolved = _resolve_repo_py_path(repo_root, rel)
    if resolved:
        return resolved
    root = repo_root.resolve()
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if not rel:
        return None
    candidates = {rel}
    if _is_js_test_relpath(rel):
        candidates.add(Path(rel).name)
    if _is_ruby_spec_relpath(rel):
        candidates.update(_ruby_path_basename_aliases(rel))
    for candidate in candidates:
        if (root / candidate).is_file():
            return candidate
    return None


def _js_path_basename_aliases(rel: str) -> frozenset[str]:
    """Basenames used to match jest-junit / mocha-junit classnames to git diff paths."""
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if not rel:
        return frozenset()
    name = Path(rel).name
    aliases = {name}
    for suffix in (
        ".test.js",
        ".spec.js",
        ".test.ts",
        ".spec.ts",
        ".test.jsx",
        ".spec.jsx",
        ".test.mjs",
        ".spec.mjs",
    ):
        if name.endswith(suffix):
            aliases.add(name[: -len(suffix)])
            break
    else:
        for ext in _JS_TEST_EXTENSIONS:
            if name.endswith(ext):
                aliases.add(name[: -len(ext)])
                break
    parent = Path(rel).parent.name
    if parent and parent not in (
        "__tests__",
        "__integration__",
        "__node_tests__",
        "test",
        "tests",
        ".",
    ):
        aliases.add(parent)
    return frozenset(aliases)


def _mocha_title_nodeid_matches_js_paths(nodeid: str, js_paths: list[str]) -> bool:
    """
    Match Mocha JUnit keys that use describe/it titles without a ``file`` attribute.

    When the PR touches a single JS test file, accept title-only nodeids. Otherwise
    match suite/file stems and parent directory names against the nodeid text.
    """
    if not js_paths or not nodeid:
        return False
    head = _nodeid_leading_relpath(nodeid)
    if _is_js_test_relpath(head):
        return True
    if len(js_paths) == 1 and "/" not in head:
        return True
    hay = nodeid.replace("::", " ").lower()
    for p in js_paths:
        for alias in _js_path_basename_aliases(p):
            if alias and alias.lower() in hay:
                return True
    return False


def java_fqcn_from_test_path(rel: str) -> str | None:
    """Map ``module/src/test/java/pkg/Foo.java`` to ``pkg.Foo``."""
    rel = rel.replace("\\", "/").strip()
    for marker in ("/src/test/java/", "/src/intTest/java/"):
        if marker in rel and rel.endswith(".java"):
            return rel.split(marker, 1)[1][:-5].replace("/", ".")
    return None


def _path_filter_sets(paths: list[str]) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    path_set: set[str] = set()
    dotted: set[str] = set()
    java_fqcns: set[str] = set()
    js_basenames: set[str] = set()
    ruby_basenames: set[str] = set()
    for p in paths:
        fqcn = java_fqcn_from_test_path(p)
        if fqcn:
            java_fqcns.add(fqcn)
        js_basenames.update(_js_path_basename_aliases(p))
        ruby_basenames.update(_ruby_path_basename_aliases(p))
        if p.replace("\\", "/").endswith(".lua"):
            from .c_build import premake_suite_from_test_path

            suite = premake_suite_from_test_path(p)
            if suite:
                dotted.add(suite)
        for alias in _test_path_aliases(p):
            path_set.add(alias)
            js_basenames.update(_js_path_basename_aliases(alias))
            ruby_basenames.update(_ruby_path_basename_aliases(alias))
            if alias.endswith(".py"):
                dotted.add(alias.removesuffix(".py").replace("/", "."))
            if _is_ruby_spec_relpath(alias):
                dotted.add(alias.removesuffix(".rb").replace("/", "."))
            if alias.replace("\\", "/").endswith(".lua"):
                from .c_build import premake_suite_from_test_path

                suite = premake_suite_from_test_path(alias)
                if suite:
                    dotted.add(suite)
            alias_fqcn = java_fqcn_from_test_path(alias)
            if alias_fqcn:
                java_fqcns.add(alias_fqcn)
    path_set.update(js_basenames)
    path_set.update(ruby_basenames)
    return frozenset(path_set), frozenset(dotted), frozenset(java_fqcns)


def nodeids_all_in_test_patch_paths(nodeids: list[str], paths: list[str]) -> bool:
    """True if every node id maps to a file path from the PR ``test_patch``."""
    if not paths:
        return False
    path_set, dotted_prefixes, java_fqcns = _path_filter_sets(paths)
    return all(
        _nodeid_in_test_patch_paths(nid, path_set, dotted_prefixes, java_fqcns)
        for nid in nodeids
        if nid
    )


def _rust_cargo_integration_stems(paths: list[str]) -> set[str]:
    """``tests/<name>.rs`` → ``cargo test --test <name>`` stem."""
    stems: set[str] = set()
    for raw in paths:
        p = raw.replace("\\", "/").lstrip("./")
        if p.startswith("tests/") and p.endswith(".rs") and p.count("/") == 1:
            stems.add(Path(p).stem)
    return stems


def _cargo_log_key_in_test_patch_paths(nodeid: str, paths: list[str]) -> bool:
    """Match cargo stdout keys (``mod::test_name``) to ``test_patch`` Rust paths."""
    if not nodeid or not paths:
        return False
    for stem in _rust_cargo_integration_stems(paths):
        if nodeid == stem or nodeid.startswith(stem + "::"):
            return True
    for raw in paths:
        p = raw.replace("\\", "/").lstrip("./")
        if not p.endswith(".rs"):
            continue
        stem = Path(p).stem
        if nodeid == stem or nodeid.endswith("::" + stem):
            return True
        # ``tests/foo.rs`` unit tests often log as ``foo::test_*`` or bare ``test_*``.
        if "/" in p and stem in nodeid:
            return True
    return False


def _nodeid_matches_native_integration_root(
    nodeid: str,
    test_patch_paths: list[str],
    pytest_root: str,
) -> bool:
    """
    Match JUnit keys when pytest cwd is *pytest_root* (``NATIVE_PYTEST_ROOT``).

    Git ``test_patch`` paths are repo-relative (``tests/http/test_foo.py``); JUnit
    often reports heads under the suite dir (``test_foo/TestClass.py::test_name``).
    """
    root = pytest_root.replace("\\", "/").strip().strip("/")
    if not root or not nodeid or not test_patch_paths:
        return False
    head = _nodeid_leading_relpath(nodeid)
    prefix = root + "/"
    for raw in test_patch_paths:
        p = raw.replace("\\", "/").strip().lstrip("/")
        if not p.startswith(prefix):
            continue
        rel = p[len(prefix) :]
        if not rel:
            continue
        if head == rel or head.startswith(rel + "/"):
            return True
        if rel.endswith(".py"):
            stem = Path(rel).stem
            if head == stem or head.startswith(stem + "/"):
                return True
    return False


def _nodeid_in_test_patch_paths(
    nodeid: str,
    path_set: frozenset[str],
    dotted_prefixes: frozenset[str],
    java_fqcns: frozenset[str] = frozenset(),
    *,
    test_patch_paths: list[str] | None = None,
    test_patch: str = "",
    language: str = "",
    native_integration_pytest_root: str = "",
) -> bool:
    if native_integration_pytest_root and test_patch_paths:
        if _nodeid_matches_native_integration_root(
            nodeid, test_patch_paths, native_integration_pytest_root
        ):
            return True
    if language:
        from .languages import get_language_spec

        if get_language_spec(language).result_format == "cargo_log" and test_patch_paths:
            if _cargo_log_key_in_test_patch_paths(nodeid, test_patch_paths):
                return True
        if get_language_spec(language).result_format == "gotest_log" and test_patch_paths:
            from .go_build import gotest_log_key_in_test_patch_paths

            if gotest_log_key_in_test_patch_paths(
                nodeid,
                list(test_patch_paths),
                test_patch=test_patch,
            ):
                return True
        if language.lower() in ("ruby", "rb") and test_patch_paths:
            from .ruby_build import rspec_junit_nodeid_in_test_patch_paths

            if rspec_junit_nodeid_in_test_patch_paths(nodeid, list(test_patch_paths)):
                return True
        if language.lower() == "php" and test_patch_paths:
            from .php_build import php_junit_nodeid_in_test_patch_paths

            if php_junit_nodeid_in_test_patch_paths(nodeid, list(test_patch_paths)):
                return True
        if language.lower() in ("python", "py") and test_patch_paths:
            from .python_build import pytest_junit_nodeid_in_test_patch_paths

            if pytest_junit_nodeid_in_test_patch_paths(
                nodeid, list(test_patch_paths), test_patch=test_patch
            ):
                return True
        if language.lower() == "c" and test_patch_paths:
            from .runtests_build import runtests_log_key_in_test_patch_paths

            if runtests_log_key_in_test_patch_paths(nodeid, list(test_patch_paths)):
                return True
    if " > " in nodeid:
        class_part = nodeid.split(" > ", 1)[0].strip()
        if java_fqcns and class_part in java_fqcns:
            return True
        path_like = class_part.replace(".", "/") + ".java"
        if path_like in path_set:
            return True
        if any(path_like.endswith(p) or p.endswith(path_like) for p in path_set):
            return True
    head = _nodeid_leading_relpath(nodeid)
    if head in path_set:
        return True
    if any(alias in path_set for alias in _test_path_aliases(head)):
        return True
    head_base = Path(head.replace("\\", "/")).name
    if head_base and head_base in path_set:
        return True
    if head_base:
        for ext in _JS_TEST_EXTENSIONS:
            if head_base.endswith(ext) and head_base[: -len(ext)] in path_set:
                return True
        for suffix in _RUBY_SPEC_SUFFIXES:
            if head_base.endswith(suffix):
                stem = head_base[: -len(suffix)]
                if stem in path_set or head_base in path_set:
                    return True
    if "/" not in head and _is_js_test_relpath(head):
        if head in path_set or Path(head).name in path_set:
            return True
    if java_fqcns:
        norm = head.replace("/", ".").removesuffix(".py").removesuffix(".java")
        if norm in java_fqcns:
            return True
        simple = norm.rsplit(".", 1)[-1]
        for fqcn in java_fqcns:
            if fqcn.rsplit(".", 1)[-1] == simple and (
                norm == fqcn or norm.endswith(fqcn) or fqcn.endswith(norm)
            ):
                return True
    # Module-level JUnit (no ``::``): e.g. ``tests.admin_views.test_autocomplete_view``
    if "/" not in head and "." in head:
        return any(head == m or head.startswith(m + ".") for m in dotted_prefixes if m)
    if test_patch_paths and language.lower() in (
        "javascript",
        "js",
        "typescript",
        "ts",
        "node",
    ):
        js_paths = [
            p.replace("\\", "/").strip().lstrip("/")
            for p in test_patch_paths
            if _is_js_test_relpath(p.replace("\\", "/"))
        ]
        if js_paths and _mocha_title_nodeid_matches_js_paths(nodeid, js_paths):
            return True
    return False


def has_test_patch_label_mismatch(
    case_map: dict[str, str],
    test_patch_paths: list[str],
    *,
    django_runtests: bool = False,
    language: str = "",
    native_integration_pytest_root: str = "",
    test_patch: str = "",
) -> bool:
    """True when the after-patch log has cases but none match ``test_patch`` paths/labels."""
    if not test_patch_paths or not case_map:
        return False
    if django_runtests:
        from .django_runtests import _case_map_key_matches_paths, _labels_from_test_patch_paths

        labels = _labels_from_test_patch_paths(test_patch_paths)
        return not any(_case_map_key_matches_paths(key, labels) for key in case_map)
    from .languages import get_language_spec

    if language and get_language_spec(language).id == "c":
        from .runtests_build import runtests_log_key_in_test_patch_paths

        if any(
            runtests_log_key_in_test_patch_paths(nid, test_patch_paths) for nid in case_map
        ):
            return False
    if language:
        spec = get_language_spec(language)
        if spec.result_format == "cargo_log":
            if any(
                _cargo_log_key_in_test_patch_paths(nid, test_patch_paths) for nid in case_map
            ):
                return False
        if spec.result_format == "gotest_log":
            from .go_build import gotest_log_key_in_test_patch_paths

            if any(
                gotest_log_key_in_test_patch_paths(
                    nid, test_patch_paths, test_patch=test_patch
                )
                for nid in case_map
            ):
                return False
        if language.lower() in ("ruby", "rb"):
            from .ruby_build import rspec_junit_nodeid_in_test_patch_paths

            if any(
                rspec_junit_nodeid_in_test_patch_paths(nid, test_patch_paths)
                for nid in case_map
            ):
                return False
        if language.lower() == "php":
            from .php_build import php_junit_nodeid_in_test_patch_paths

            if any(
                php_junit_nodeid_in_test_patch_paths(nid, test_patch_paths)
                for nid in case_map
            ):
                return False
        if language.lower() in ("python", "py"):
            from .python_build import pytest_junit_nodeid_in_test_patch_paths

            if any(
                pytest_junit_nodeid_in_test_patch_paths(
                    nid, test_patch_paths, test_patch=test_patch
                )
                for nid in case_map
            ):
                return False
    path_set, dotted, java_fqcns = _path_filter_sets(test_patch_paths)
    return not any(
        _nodeid_in_test_patch_paths(
            nid,
            path_set,
            dotted,
            java_fqcns,
            test_patch_paths=test_patch_paths,
            test_patch=test_patch,
            language=language,
            native_integration_pytest_root=native_integration_pytest_root,
        )
        for nid in case_map
    )


def _rust_cargo_integration_targets(paths: list[str]) -> bool:
    """True when every path is ``tests/<name>.rs`` (integration tests for ``--test``)."""
    if not paths:
        return False
    for raw in paths:
        p = raw.replace("\\", "/").lstrip("./")
        if not (p.startswith("tests/") and p.endswith(".rs") and p.count("/") == 1):
            return False
    return True


def log_junit_test_patch_mismatch(
    instance_id: str,
    case_map: dict[str, str],
    test_patch_paths: list[str],
    *,
    limit: int = 20,
    native_integration_pytest_root: str = "",
    test_patch: str = "",
    language: str = "",
) -> None:
    """Print sample JUnit nodeids when none match ``test_patch_paths`` (debugging path alignment)."""
    if not test_patch_paths or not case_map:
        return
    path_set, dotted, java_fqcns = _path_filter_sets(test_patch_paths)
    matched = [
        nid
        for nid in case_map
        if _nodeid_in_test_patch_paths(
            nid,
            path_set,
            dotted,
            java_fqcns,
            test_patch_paths=test_patch_paths,
            test_patch=test_patch,
            language=language,
            native_integration_pytest_root=native_integration_pytest_root,
        )
    ]
    unmatched = [nid for nid in case_map if nid not in matched]
    label = "gotest name(s)" if language == "go" else "test_patch path(s)"
    print(
        f"  {instance_id}: junit debug — {len(case_map)} case(s) in patch junit, "
        f"{len(matched)} matched {label}, {len(unmatched)} unmatched",
        file=sys.stderr,
    )
    print(
        f"  {instance_id}: test_patch paths expected: "
        f"{test_patch_paths[:12]}{'...' if len(test_patch_paths) > 12 else ''}",
        file=sys.stderr,
    )
    for nid in sorted(unmatched)[:limit]:
        print(f"    junit nodeid (unmatched): {nid} [{case_map.get(nid, '?')}]", file=sys.stderr)
    if len(unmatched) > limit:
        print(f"    … and {len(unmatched) - limit} more unmatched nodeid(s)", file=sys.stderr)
    if matched and len(matched) <= 8:
        for nid in sorted(matched):
            print(f"    junit nodeid (matched): {nid} [{case_map.get(nid, '?')}]", file=sys.stderr)


def junit_outcome_counts_for_paths(
    case_map: dict[str, str],
    paths: list[str],
    *,
    language: str = "",
    native_integration_pytest_root: str = "",
    test_patch: str = "",
) -> tuple[int, int, int, int, int]:
    """
    Count JUnit outcomes for test cases whose file prefix matches one of ``paths``.

    Returns ``(passed, failure, error, skipped, total)`` over matching nodeids only.
    """
    path_set, dotted_prefixes, java_fqcns = _path_filter_sets(paths)
    passed = failure = error = skipped = 0
    for nid, outcome in case_map.items():
        if not _nodeid_in_test_patch_paths(
            nid,
            path_set,
            dotted_prefixes,
            java_fqcns,
            test_patch_paths=paths,
            test_patch=test_patch,
            language=language,
            native_integration_pytest_root=native_integration_pytest_root,
        ):
            continue
        if outcome == "passed":
            passed += 1
        elif outcome == "failure":
            failure += 1
        elif outcome == "error":
            error += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            passed += 1
    total = passed + failure + error + skipped
    return passed, failure, error, skipped, total


def _junit_child_diagnostic_message(child: ET.Element) -> str:
    """Human-readable text from a ``failure`` / ``error`` / ``skipped`` JUnit element."""
    msg = (child.attrib.get("message") or "").strip()
    typ = (child.attrib.get("type") or "").strip()
    body = (child.text or "").strip()
    parts: list[str] = []
    if msg:
        parts.append(msg)
    if typ and typ not in msg:
        parts.append(f"type={typ}")
    if body and body not in msg:
        parts.append(body[:500])
    s = " — ".join(parts) if parts else ""
    s = " ".join(s.split()) if s else "(no diagnostic text in JUnit)"
    return s[:1200]


def junit_fail_error_skip_messages_for_paths(
    path: Path,
    repo_root: Path,
    paths: list[str],
    *,
    language: str = "python",
    native_integration_pytest_root: str = "",
    test_patch: str = "",
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """
    Collect ``(nodeid, reason)`` for failure / error / skipped testcases in ``paths``.

    Reasons come from JUnit ``message`` / ``type`` / body text (same slice as
    ``junit_outcome_counts_for_paths``).
    """
    failures: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    skips: list[tuple[str, str]] = []
    if not path.is_file() or not paths:
        return failures, errors, skips
    path_set, dotted_prefixes, java_fqcns = _path_filter_sets(paths)
    root, _ = _junit_xml_roots(path)
    if root is None:
        return failures, errors, skips
    for case, suite_file in _walk_junit_testcases(root):
        nid = harness_test_label(
            case,
            repo_root,
            language=language,
            suite_file=suite_file,
        )
        if not _nodeid_in_test_patch_paths(
            nid,
            path_set,
            dotted_prefixes,
            java_fqcns,
            test_patch_paths=paths,
            test_patch=test_patch,
            language=language,
            native_integration_pytest_root=native_integration_pytest_root,
        ):
            continue
        for child in case:
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag == "failure":
                failures.append((nid, _junit_child_diagnostic_message(child)))
            elif ctag == "error":
                errors.append((nid, _junit_child_diagnostic_message(child)))
            elif ctag == "skipped":
                skips.append((nid, _junit_child_diagnostic_message(child)))
    return failures, errors, skips


def junit_fail_error_skip_messages_limited(
    path: Path,
    repo_root: Path,
    *,
    limit: int = 200,
    language: str = "python",
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """
    Like ``junit_fail_error_skip_messages_for_paths`` but **no path filter**, capped at
    ``limit`` total non-pass events (errors first, then failures, then skips, in document order).
    """
    failures: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    skips: list[tuple[str, str]] = []
    if not path.is_file():
        return failures, errors, skips
    root, _ = _junit_xml_roots(path)
    if root is None:
        return failures, errors, skips
    total = 0
    for case, suite_file in _walk_junit_testcases(root):
        if total >= limit:
            return failures, errors, skips
        nid = harness_test_label(
            case,
            repo_root,
            language=language,
            suite_file=suite_file,
        )
        for child in case:
            if total >= limit:
                return failures, errors, skips
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag == "failure":
                failures.append((nid, _junit_child_diagnostic_message(child)))
                total += 1
            elif ctag == "error":
                errors.append((nid, _junit_child_diagnostic_message(child)))
                total += 1
            elif ctag == "skipped":
                skips.append((nid, _junit_child_diagnostic_message(child)))
                total += 1
    return failures, errors, skips


def junit_outcome_counts_all(case_map: dict[str, str]) -> tuple[int, int, int, int, int]:
    """``(passed, failure, error, skipped, total)`` over every testcase in ``case_map``."""
    passed = failure = error = skipped = 0
    for outcome in case_map.values():
        if outcome == "passed":
            passed += 1
        elif outcome == "failure":
            failure += 1
        elif outcome == "error":
            error += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            passed += 1
    total = passed + failure + error + skipped
    return passed, failure, error, skipped, total


def _run(cmd: list[str], *, cwd: Path, timeout: int, env: Optional[dict[str, str]] = None) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True, timeout=timeout, env=env)


def git_apply(repo: Path, patch_text: str) -> None:
    body = strip_mailbox_to_unified(patch_text)
    if not body.strip():
        return
    tmp = repo / "._taskgen_apply.patch"
    tmp.write_text(body, encoding="utf-8")
    try:
        _run(["git", "apply", "--whitespace=nowarn", str(tmp)], cwd=repo, timeout=120)
    finally:
        tmp.unlink(missing_ok=True)


def _resolve_dotted_pytest_classname(classname: str, repo_root: Path) -> str | None:
    """
    Map pytest JUnit ``classname`` to a repo-relative path for data-file tests.

    Pygments writes ``classname="tests.snippets.lateralus.pipeline.txt"`` for
    ``tests/snippets/lateralus/pipeline.txt`` (empty ``name``).
    """
    if not classname or "/" in classname.replace("\\", "/"):
        return None
    parts = classname.split(".")
    if len(parts) < 2:
        return None
    root = repo_root.resolve()
    for split in range(len(parts) - 1, 0, -1):
        rel = "/".join(parts[:split]) + "/" + ".".join(parts[split:])
        if (root / rel).is_file():
            return rel
    rel = "/".join(parts)
    if (root / rel).is_file():
        return rel
    return None


def _classname_to_pytest_prefix(classname: str, repo_root: Path) -> tuple[str, str]:
    """
    Map JUnit ``classname`` to pytest file path + optional in-file qualifier (class).

    SWE-bench log parsers key results as ``pandas/tests/foo.py::Class::test_name``,
    not ``pandas.tests.foo::test_name``.
    """
    if not classname:
        return "", ""
    cn = classname.replace("\\", "/").strip()
    if " " in cn and "/" not in cn and not cn.endswith(".py"):
        return "", ""
    resolved = _resolve_dotted_pytest_classname(classname, repo_root)
    if resolved:
        return resolved, ""
    if "/" in cn and _is_js_test_relpath(cn):
        resolved = _resolve_repo_test_path(repo_root, cn.lstrip("/"))
        return resolved or cn.lstrip("/"), ""
    root = repo_root.resolve()
    parts = classname.split(".")
    for i in range(len(parts), 0, -1):
        mod = ".".join(parts[:i])
        for suffix in (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
            rel = mod.replace(".", "/") + suffix
            resolved = _resolve_repo_test_path(root, rel)
            if resolved:
                qual = ".".join(parts[i:])
                return resolved, qual.replace(".", "::") if qual else ""
    # No matching source file (e.g. generated tests): best-effort path shape.
    rel = classname.replace(".", "/") + ".py"
    resolved = _resolve_repo_test_path(root, rel)
    return resolved or rel, ""


def junit_case_to_gradle_test_key(case: ET.Element, repo_root: Path) -> str:
    """
    Gradle test log / ``parse_log_gradle_custom`` key: ``fqcn > methodName``.

    Matches SWE-bench Java harness grading (not pytest ``path.py::method``).
    """
    name = (case.attrib.get("name") or "").strip()
    classname = (case.attrib.get("classname") or "").strip()
    if classname:
        return f"{classname} > {name}" if name else classname
    file_a = case.attrib.get("file")
    if file_a:
        rel = Path(file_a)
        try:
            rel = rel.resolve().relative_to(repo_root.resolve())
        except ValueError:
            rel = Path(file_a)
        fqcn = rel.as_posix().removesuffix(".java").replace("/", ".")
        return f"{fqcn} > {name}" if name else fqcn
    return name or classname


def pytest_style_nodeid_to_gradle_test_key(nodeid: str) -> str:
    """Convert pytest-style ``path.py::method()`` to ``fqcn > method``."""
    nid = (nodeid or "").strip()
    if not nid:
        return nid
    if " > " in nid:
        return nid
    if "::" not in nid:
        return nid
    parts = nid.split("::")
    rel = parts[0].replace("\\", "/")
    fqcn = rel.removesuffix(".py").removesuffix(".java").replace("/", ".")
    method = parts[-1].rstrip("()")
    if len(parts) > 2:
        inner = ".".join(parts[1:-1])
        fqcn = f"{fqcn}.{inner}" if inner else fqcn
    return f"{fqcn} > {method}" if method else fqcn


def junit_case_to_mocha_nodeid(
    case: ET.Element,
    repo_root: Path,
    *,
    suite_file: str | None = None,
) -> str:
    """Node id for mocha-junit-reporter (testsuite ``file`` + testcase title)."""
    name = (case.attrib.get("name") or "").strip()
    classname = (case.attrib.get("classname") or "").strip()
    file_a = case.attrib.get("file") or suite_file
    rel_s = ""
    if file_a:
        rel_s = _junit_file_to_repo_relpath(str(file_a), repo_root)
        resolved = _resolve_repo_test_path(repo_root, rel_s)
        if resolved:
            rel_s = resolved
    if not rel_s and classname and _is_js_test_relpath(classname.replace("\\", "/")):
        resolved = _resolve_repo_test_path(repo_root, classname.replace("\\", "/"))
        if resolved:
            rel_s = resolved
    if rel_s:
        if classname and classname != name and not _is_js_test_relpath(classname):
            return f"{rel_s}::{classname}::{name}"
        return f"{rel_s}::{name}"
    if classname:
        return f"{classname}::{name}" if name else classname
    return name


def _rspec_junit_classname_is_dotted_spec_path(classname: str, rel_s: str) -> bool:
    """
    True when ``rspec_junit_formatter`` puts the spec file in ``classname``.

    See ``swebench.harness.log_parsers.junit_xml`` — kept in sync for grading.
    """
    if not classname:
        return False
    if "::" in classname or " " in classname:
        return False
    cn = classname.replace("\\", ".").strip(".")
    if not cn or "/" in cn:
        return False

    if rel_s:
        rel_norm = rel_s.replace("\\", "/").lstrip("./")
        dotted_rel = rel_norm.removesuffix(".rb").replace("/", ".")
        stem = Path(rel_norm).stem
        if cn == dotted_rel or cn == stem:
            return True

    if cn.startswith("spec.") and cn.endswith("_spec"):
        return True
    return False


def junit_case_to_rspec_nodeid(
    case: ET.Element,
    repo_root: Path,
    *,
    suite_file: str | None = None,
) -> str:
    """Node id for ``rspec_junit_formatter`` (repo-relative ``*_spec.rb`` + example name)."""
    name = (case.attrib.get("name") or "").strip()
    classname = (case.attrib.get("classname") or "").strip()
    file_a = case.attrib.get("file") or suite_file
    rel_s = ""
    if file_a:
        rel_s = _junit_file_to_repo_relpath(str(file_a), repo_root)
        resolved = _resolve_repo_test_path(repo_root, rel_s)
        if resolved:
            rel_s = resolved
    if rel_s:
        if name and _rspec_junit_classname_is_dotted_spec_path(classname, rel_s):
            return f"{rel_s}::{name}"
        if classname and name and classname != name and not classname.startswith(rel_s):
            return f"{rel_s}::{classname} {name}".strip()
        if name:
            return f"{rel_s}::{name}"
        if classname:
            return f"{rel_s}::{classname}"
        return rel_s
    if classname:
        return f"{classname}::{name}" if name else classname
    return name


def harness_test_label(
    case: ET.Element,
    repo_root: Path,
    *,
    language: str = "python",
    suite_file: str | None = None,
) -> str:
    """Test case key for FAIL_TO_PASS / PASS_TO_PASS and log grading."""
    lang = (language or "").lower()
    if lang == "java":
        return junit_case_to_gradle_test_key(case, repo_root)
    if lang in ("javascript", "js", "typescript", "ts", "node"):
        return junit_case_to_mocha_nodeid(case, repo_root, suite_file=suite_file)
    if lang in ("ruby", "rb"):
        return junit_case_to_rspec_nodeid(case, repo_root, suite_file=suite_file)
    return junit_case_to_nodeid(case, repo_root)


def junit_case_to_nodeid(case: ET.Element, repo_root: Path) -> str:
    """
    Pytest node id for SWE-bench / ``pytest -rA`` (repo-relative ``.py`` path + ``::``).
    """
    name = case.attrib.get("name", "")
    classname = case.attrib.get("classname", "")
    file_a = case.attrib.get("file")
    rel_s = ""
    qual = ""
    if file_a:
        fp = Path(file_a)
        try:
            rel = fp.resolve().relative_to(repo_root.resolve())
        except ValueError:
            rel = Path(file_a)
        rel_s = _junit_file_to_repo_relpath(rel.as_posix(), repo_root)
        resolved = _resolve_repo_test_path(repo_root, rel_s)
        if resolved:
            rel_s = resolved
        mod_suffix = rel_s
        for ext in (".py",) + _JS_TEST_EXTENSIONS:
            if mod_suffix.endswith(ext):
                mod_suffix = mod_suffix[: -len(ext)].replace("/", ".")
                break
        else:
            mod_suffix = mod_suffix.replace("/", ".")
        if classname.startswith(mod_suffix + "."):
            rest = classname[len(mod_suffix) + 1 :]
            if rest:
                qual = rest.replace(".", "::")
    if not rel_s and classname:
        cn = classname.replace("\\", "/").strip().lstrip("/")
        if "/" in cn or _is_js_test_relpath(cn):
            resolved = _resolve_repo_test_path(repo_root, cn)
            if resolved:
                rel_s = resolved
        if not rel_s:
            rel_s, qual = _classname_to_pytest_prefix(classname, repo_root)
    if rel_s:
        if qual:
            return f"{rel_s}::{qual}::{name}"
        return f"{rel_s}::{name}"
    return f"{classname}::{name}" if classname else name


def swebench_pytest_log_parseable(nodeid: str) -> bool:
    """
    True if SWE-bench ``parse_log_pytest`` can recover *nodeid* from a ``PASSED {nodeid}`` line.

    That parser uses ``line.split()`` and takes the second token, so nodeids must not contain
    whitespace (e.g. parametrization ``[first date is single-digit]`` or
    ``[datetime64[ns, UTC]]``).
    """
    if not nodeid or " " in nodeid:
        return False
    probe = f"PASSED {nodeid}"
    parts = probe.split()
    return len(parts) >= 2 and parts[1] == nodeid


def _pytest_xfail_near_def(text: str, test_name: str) -> bool:
    for m in re.finditer(rf"def {re.escape(test_name)}\s*\(", text):
        start = max(0, m.start() - 600)
        chunk = text[start : m.start()]
        if "@pytest.mark.xfail" in chunk or "pytest.mark.xfail(" in chunk:
            return True
    return False


def _candidate_test_files_for_nodeid(repo_root: Path, nodeid: str) -> list[Path]:
    rel = nodeid.split("::", 1)[0].replace("\\", "/")
    paths: list[Path] = []
    primary = repo_root / rel
    if primary.is_file():
        paths.append(primary)
    if "/tests/extension/" in f"/{rel}":
        base = repo_root / "pandas/tests/extension/base"
        if base.is_dir():
            paths.extend(sorted(base.glob("*.py")))
    return paths


def pytest_marked_xfail_in_repo(repo_root: Path, nodeid: str) -> bool:
    """
    True when the test (or an extension base mixin) is decorated with ``@pytest.mark.xfail``.

    Such tests may XPASS at eval time; SWE-bench's grader does not treat XPASS as success.
    """
    if "::" not in nodeid:
        return False
    test_name = nodeid.split("::")[-1].split("[", 1)[0]
    if not test_name.startswith("test_"):
        return False
    seen: set[Path] = set()
    for path in _candidate_test_files_for_nodeid(repo_root, nodeid):
        if path in seen:
            continue
        seen.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _pytest_xfail_near_def(text, test_name):
            return True
    return False


def swebench_gradable_nodeid(
    nodeid: str,
    repo_root: Path | None = None,
    *,
    for_pass_to_pass: bool = False,
    language: str = "python",
    django_runtests: bool = False,
) -> bool:
    """Node id safe for SWE-bench log grading (optional xfail filter for pytest Python)."""
    if not nodeid or not nodeid.strip():
        return False
    if django_runtests:
        from .swebench_align import django_runtests_gradable_nodeid

        return django_runtests_gradable_nodeid(nodeid)
    if (language or "").lower() == "java":
        key = pytest_style_nodeid_to_gradle_test_key(nodeid)
        return bool(key) and " > " in key
    lang = (language or "").lower()
    if lang in ("javascript", "js", "typescript", "ts", "node"):
        # Vitest/Jest names often contain spaces (e.g. ``suite > case``); path prefix must not.
        head = nodeid.split("::", 1)[0]
        return bool(head.strip()) and " " not in head
    if lang in ("ruby", "rb"):
        # RSpec/Minitest example titles often contain spaces after ``path::``.
        head = nodeid.split("::", 1)[0].replace("\\", "/").strip()
        if not head or " " in head:
            return False
        low = head.lower()
        return low.endswith("_spec.rb") or low.endswith("_test.rb")
    if " " in nodeid:
        return False
    if language == "python":
        if not swebench_pytest_log_parseable(nodeid):
            return False
        if for_pass_to_pass and repo_root is not None and pytest_marked_xfail_in_repo(repo_root, nodeid):
            return False
        return True
    return True


def filter_swebench_gradable_nodeids(
    nodeids: list[str],
    repo_root: Path | None = None,
    *,
    for_pass_to_pass: bool = False,
    language: str = "python",
    django_runtests: bool = False,
) -> tuple[list[str], list[str]]:
    """Return (kept, dropped) node ids suitable for FAIL_TO_PASS / PASS_TO_PASS JSON fields."""
    kept: list[str] = []
    dropped: list[str] = []
    for nid in nodeids:
        label = nid
        if django_runtests:
            from .swebench_align import normalize_django_runtests_test_key

            label = normalize_django_runtests_test_key(nid)
        elif (language or "").lower() == "java":
            label = pytest_style_nodeid_to_gradle_test_key(nid)
        if swebench_gradable_nodeid(
            label,
            repo_root,
            for_pass_to_pass=for_pass_to_pass,
            language=language,
            django_runtests=django_runtests,
        ):
            kept.append(label)
        else:
            dropped.append(nid)
    return kept, dropped


def _case_outcome(case: ET.Element) -> str:
    for child in case:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag in ("failure", "error"):
            return tag
        if tag == "skipped":
            return "skipped"
    return "passed"


def parse_junit(
    path: Path, repo_root: Path, *, language: str = "python"
) -> dict[str, str]:
    root, _ = _junit_xml_roots(path)
    if root is None:
        return {}
    out: dict[str, str] = {}
    for case, suite_file in _walk_junit_testcases(root):
        nid = harness_test_label(
            case,
            repo_root,
            language=language,
            suite_file=suite_file,
        )
        out[nid] = _case_outcome(case)
    return out


def parse_test_status_map(
    path: Path,
    repo_root: Path,
    language: str,
    *,
    result_format: str | None = None,
) -> dict[str, str]:
    """Parse JUnit XML or language-specific test logs into testcase -> outcome."""
    from .languages import get_language_spec
    from .test_log_parsers import parse_log_for_language

    spec = get_language_spec(language)
    fmt = result_format or spec.result_format
    if fmt == "junit":
        return parse_junit(path, repo_root, language=language)
    if path.is_file():
        return parse_log_for_language(path.read_text(encoding="utf-8", errors="replace"), fmt)
    return {}


def test_reported_count(path: Path, language: str, *, result_format: str | None = None) -> int:
    from .languages import get_language_spec
    from .test_log_parsers import parse_log_for_language as _parse_log

    spec = get_language_spec(language)
    fmt = result_format or spec.result_format
    if fmt == "junit":
        return junit_reported_test_count(path)
    if path.is_file():
        return len(_parse_log(path.read_text(encoding="utf-8", errors="replace"), fmt))
    return 0


def junit_reported_test_count(path: Path) -> int:
    """Count ``<testcase>`` nodes (including nested jest-junit suites)."""
    root, _ = _junit_xml_roots(path)
    if root is None:
        return 0
    return sum(1 for _ in _iter_junit_elements(root, "testcase"))
