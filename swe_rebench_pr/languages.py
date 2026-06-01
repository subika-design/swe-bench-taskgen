"""Language registry for multi-language SWE-rebench task generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .patch_sanitize import is_junk_patch_path

SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "c",
    "go",
    "java",
    "javascript",
    "php",
    "python",
    "ruby",
    "rust",
)

LANGUAGE_ALIASES: dict[str, str] = {
    "c": "c",
    "go": "go",
    "golang": "go",
    "java": "java",
    "javascript": "javascript",
    "js": "javascript",
    "node": "javascript",
    "typescript": "javascript",
    "ts": "javascript",
    "php": "php",
    "python": "python",
    "py": "python",
    "ruby": "ruby",
    "rb": "ruby",
    "rust": "rust",
    "rs": "rust",
}


@dataclass(frozen=True)
class LanguageSpec:
    id: str
    extensions: tuple[str, ...]
    path_markers: tuple[str, ...]
    filename_patterns: tuple[str, ...]
    docker_image: str
    result_format: str  # junit | gotest_log | cargo_log | maven_log
    default_install_config: dict[str, Any]


def normalize_language(lang: str) -> str:
    key = lang.strip().lower()
    if key not in LANGUAGE_ALIASES:
        supported = ", ".join(SUPPORTED_LANGUAGES)
        raise ValueError(f"Unsupported language {lang!r}. Use one of: {supported}")
    return LANGUAGE_ALIASES[key]


def _spec(
    lang_id: str,
    extensions: tuple[str, ...],
    path_markers: tuple[str, ...],
    filename_patterns: tuple[str, ...],
    docker_image: str,
    result_format: str,
    install_config: dict[str, Any],
) -> LanguageSpec:
    cfg = dict(install_config)
    cfg["language"] = lang_id
    cfg["docker_image"] = docker_image
    return LanguageSpec(
        id=lang_id,
        extensions=extensions,
        path_markers=path_markers,
        filename_patterns=filename_patterns,
        docker_image=docker_image,
        result_format=result_format,
        default_install_config=cfg,
    )


LANGUAGE_SPECS: dict[str, LanguageSpec] = {
    "python": _spec(
        "python",
        (".py",),
        ("tests/", "/tests/", "/testing/", "/test/", "/spec/", "/specs/", "__tests__"),
        ("test_", "conftest.py"),
        "python:3.11-bookworm",
        "junit",
        {
            "python": "3.11",
            "install": "pip install -e .",
            "test_cmd": "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider",
            "pre_install": [
                "apt-get update -qq",
                "apt-get install -y --no-install-recommends git build-essential",
            ],
            "pip_packages": ["pip", "wheel", "setuptools", "pytest"],
            "post_install": [],
            "pytest_plugins": [],
        },
    ),
    "go": _spec(
        "go",
        (".go",),
        ("/testdata/",),
        ("_test.go",),
        "golang:1.22-bookworm",
        "gotest_log",
        {
            "install": "go mod download",
            "test_cmd": "go test -v -count=1",
            "pre_install": [
                "apt-get update -qq",
                "apt-get install -y --no-install-recommends git ca-certificates",
            ],
            "post_install": [],
        },
    ),
    "java": _spec(
        "java",
        (".java",),
        ("/src/test/", "/test/java/", "/tests/"),
        ("test.java", "tests.java"),
        "maven:3.9-eclipse-temurin-17",
        "junit",
        {
            "install": "mvn -q -DskipTests package || mvn -q -DskipTests compile",
            "test_cmd": "mvn -q test",
            "pre_install": [
                "apt-get update -qq",
                "apt-get install -y --no-install-recommends git",
            ],
            "post_install": [],
        },
    ),
    "javascript": _spec(
        "javascript",
        (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
        ("/__tests__/", "/tests/", "/test/"),
        (".test.", ".spec."),
        "node:20-bookworm",
        "junit",
        {
            "install": "npm ci || npm install",
            "test_cmd": (
                "npx jest --ci --forceExit --reporters=default --reporters=jest-junit "
                "--outputFile=__JUNIT_OUT__"
            ),
            "pre_install": [
                "apt-get update -qq",
                "apt-get install -y --no-install-recommends git",
                "export ELECTRON_SKIP_BINARY_DOWNLOAD=1",
            ],
            "post_install": [
                'export PATH="$(pwd)/node_modules/.bin:$PATH"',
                "npm install --save-dev jest-junit 2>/dev/null || true",
            ],
        },
    ),
    "php": _spec(
        "php",
        (".php",),
        ("/tests/", "/test/"),
        ("test.php", "tests.php"),
        "php:8.2-cli-bookworm",
        "junit",
        {
            "install": "composer install --no-interaction --prefer-dist || true",
            "test_cmd": "vendor/bin/phpunit",
            "pre_install": [
                "apt-get update -qq",
                "apt-get install -y --no-install-recommends git unzip",
                "curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer",
            ],
            "post_install": [],
        },
    ),
    "ruby": _spec(
        "ruby",
        (".rb",),
        ("/spec/", "/tests/", "/test/"),
        ("_spec.rb", "_test.rb"),
        "ruby:3.2-bookworm",
        "junit",
        {
            "install": "bundle install || true",
            "test_cmd": "bundle exec rspec",
            "pre_install": [
                "apt-get update -qq",
                "apt-get install -y --no-install-recommends git build-essential",
                "gem install bundler -N",
            ],
            "post_install": [],
        },
    ),
    "rust": _spec(
        "rust",
        (".rs",),
        ("/tests/", "/benches/"),
        ("_test.rs",),
        "rust:1.75-bookworm",
        "cargo_log",
        {
            "install": "cargo build --tests || cargo build",
            "test_cmd": "cargo test --no-fail-fast",
            "pre_install": [
                "apt-get update -qq",
                "apt-get install -y --no-install-recommends git build-essential pkg-config libssl-dev",
            ],
            "post_install": [],
        },
    ),
    "c": _spec(
        "c",
        (".c", ".h", ".cc", ".cpp", ".hpp"),
        ("/tests/", "/test/"),
        ("_test.c", "_test.cpp", "test_"),
        "gcc:12-bookworm",
        "junit",
        {
            "install": "mkdir -p build && cd build && cmake .. && cmake --build .",
            "test_cmd": "cd build && ctest --output-on-failure",
            "pre_install": [
                "apt-get update -qq",
                "apt-get install -y --no-install-recommends git build-essential cmake",
            ],
            "post_install": [],
        },
    ),
}


def get_language_spec(language: str) -> LanguageSpec:
    return LANGUAGE_SPECS[normalize_language(language)]


def _path_has_test_marker(low: str, marker: str) -> bool:
    """Match git diff paths like ``__tests__/foo.js`` and ``src/__tests__/foo.js``."""
    m = marker.strip("/")
    if not m:
        return False
    if marker in low:
        return True
    if low.startswith(f"{m}/"):
        return True
    return f"/{m}/" in low


def is_pygments_golden_output(path: str) -> bool:
    """Pygments examplefiles write ``*.output`` golden files — not pytest targets."""
    return path.replace("\\", "/").lower().endswith(".output")


def is_pygments_data_test_path(path: str) -> bool:
    """Pygments collects ``tests/snippets/*.txt`` and ``tests/examplefiles/*`` via pytest plugins."""
    p = path.replace("\\", "/")
    low = p.lower()
    if is_pygments_golden_output(p):
        return False
    return low.startswith("tests/snippets/") or low.startswith("tests/examplefiles/")


def filter_python_pytest_targets(paths: Iterable[str]) -> list[str]:
    """Drop golden ``.output`` artifacts and other non-runnable pytest paths."""
    out: list[str] = []
    for raw in paths:
        p = raw.replace("\\", "/")
        if is_pygments_golden_output(p):
            continue
        out.append(p)
    return out


def is_test_path(path: str, spec: LanguageSpec) -> bool:
    p = path.replace("\\", "/")
    low = p.lower()
    if spec.id == "python" and is_pygments_data_test_path(p):
        return True
    if not any(low.endswith(ext) for ext in spec.extensions):
        return False
    if low.startswith("tests/") or low.startswith("test/"):
        return True
    if any(_path_has_test_marker(low, m) for m in spec.path_markers):
        return True
    base = low.rsplit("/", 1)[-1]
    return any(pat in base for pat in spec.filename_patterns)


def is_javascript_snapshot_artifact(path: str) -> bool:
    """Drop web-test-runner / Jest snapshot outputs mistaken for runnable tests."""
    p = path.replace("\\", "/").lower()
    if "/__snapshots__/" in p:
        return True
    if p.endswith(".snap.js") or p.endswith(".snap.ts") or p.endswith(".snap.jsx"):
        return True
    return False


def filter_javascript_test_targets(paths: Iterable[str]) -> list[str]:
    return [p for p in paths if not is_javascript_snapshot_artifact(p)]


def collect_test_targets(
    language: str,
    patch: str,
    test_patch: str,
) -> list[str]:
    spec = get_language_spec(language)
    paths: set[str] = set()
    for block in (patch, test_patch):
        for m in re.finditer(r"^diff --git a/(\S+) b/\1$", block, re.MULTILINE):
            path = m.group(1)
            if is_test_path(path, spec):
                paths.add(path)
    result = sorted(paths)
    if language == "javascript":
        result = filter_javascript_test_targets(result)
    elif language == "python":
        result = filter_python_pytest_targets(result)
    return result


def collect_test_targets_from_test_patch(language: str, test_patch: str) -> list[str]:
    spec = get_language_spec(language)
    paths: set[str] = set()
    for m in re.finditer(r"^diff --git a/(\S+) b/\1$", test_patch, re.MULTILINE):
        path = m.group(1)
        if is_test_path(path, spec):
            paths.add(path)
    result = sorted(paths)
    if language == "javascript":
        result = filter_javascript_test_targets(result)
    elif language == "python":
        result = filter_python_pytest_targets(result)
    return result


def detect_language_from_paths(paths: Iterable[str]) -> str | None:
    scores: dict[str, int] = {lang: 0 for lang in SUPPORTED_LANGUAGES}
    for path in paths:
        for lang in SUPPORTED_LANGUAGES:
            if is_test_path(path, get_language_spec(lang)):
                scores[lang] += 1
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] < 1:
        return None
    tied = [lang for lang, n in scores.items() if n == best[1]]
    if len(tied) > 1:
        return None
    return best[0]


def detect_language_from_patches(patch: str, test_patch: str) -> str | None:
    paths: list[str] = []
    for block in (patch, test_patch):
        paths.extend(m.group(1) for m in re.finditer(r"^diff --git a/(\S+) b/\1$", block, re.MULTILINE))
    return detect_language_from_paths(paths)


def _paths_from_patches(patch: str, test_patch: str) -> list[str]:
    paths: list[str] = []
    for block in (patch, test_patch):
        for m in re.finditer(r"^diff --git a/(\S+) b/\1$", block, re.MULTILINE):
            path = m.group(1)
            if not is_junk_patch_path(path):
                paths.append(path)
    return paths


def detect_language_from_changed_paths(patch: str, test_patch: str) -> str | None:
    """Infer language from all changed file extensions (not only test paths)."""
    scores: dict[str, int] = {lang: 0 for lang in SUPPORTED_LANGUAGES}
    for path in _paths_from_patches(patch, test_patch):
        low = path.replace("\\", "/").lower()
        for lang in SUPPORTED_LANGUAGES:
            spec = get_language_spec(lang)
            if any(low.endswith(ext) for ext in spec.extensions):
                scores[lang] += 1
                break
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] < 1:
        return None
    tied = [lang for lang, n in scores.items() if n == best[1]]
    return best[0] if len(tied) == 1 else None


def detect_language_from_repo_build_markers(repo: Path) -> str | None:
    """Strong signal from top-level build files (Gradle, go.mod, etc.)."""
    if (repo / "gradlew").is_file() or (repo / "build.gradle").is_file() or (repo / "build.gradle.kts").is_file():
        return "java"
    if (repo / "pom.xml").is_file():
        return "java"
    if (repo / "go.mod").is_file():
        return "go"
    if (repo / "Cargo.toml").is_file():
        return "rust"
    if (repo / "composer.json").is_file():
        return "php"
    if (repo / "Gemfile").is_file():
        return "ruby"
    # Python before package.json — Django and many Python repos ship a root package.json for JS tooling.
    if (repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file() or (repo / "setup.cfg").is_file():
        return "python"
    if (repo / "package.json").is_file():
        return "javascript"
    return None


def detect_language_from_repo(repo: Path, *, max_files: int = 8000) -> str | None:
    """Guess primary language from repo markers and test file paths."""
    from_build = detect_language_from_repo_build_markers(repo)
    if from_build:
        return from_build

    scores: dict[str, int] = {lang: 0 for lang in SUPPORTED_LANGUAGES}
    if (repo / "go.mod").is_file():
        scores["go"] += 50
    if (repo / "Cargo.toml").is_file():
        scores["rust"] += 50
    if (repo / "CMakeLists.txt").is_file():
        scores["c"] += 30
    if (repo / "pyproject.toml").is_file() or (repo / "setup.py").is_file():
        scores["python"] += 40
    count = 0
    try:
        for path in repo.rglob("*"):
            if not path.is_file():
                continue
            count += 1
            if count > max_files:
                break
            rel = path.relative_to(repo).as_posix()
            for lang in SUPPORTED_LANGUAGES:
                if is_test_path(rel, get_language_spec(lang)):
                    scores[lang] += 1
    except OSError:
        pass
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] < 1:
        return None
    tied = [lang for lang, n in scores.items() if n == best[1]]
    return best[0] if len(tied) == 1 else None
