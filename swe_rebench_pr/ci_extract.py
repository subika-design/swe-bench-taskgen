"""Extract install/test signals from CI workflows and Dockerfiles (repo-first)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_APT_GET_INSTALL_RE = re.compile(
    r"apt-get\s+install\s+(?:[^|\n;]*\s+)?(?:-y|--yes)\s+([^\n|;&\\]+)",
    re.IGNORECASE,
)
_APT_INSTALL_RE = re.compile(
    r"\bapt\s+install\s+(?:[^|\n;]*\s+)?(?:-y|--yes)\s+([^\n|;&\\]+)",
    re.IGNORECASE,
)
_CI_RUN_LINE_RE = re.compile(r"^\s*-\s+run:\s*(.+)$", re.MULTILINE)
_CI_EXPORT_RE = re.compile(
    r"\bexport\s+([A-Za-z_][A-Za-z0-9_]*)=(?:\"([^\"]*)\"|'([^']*)'|(\S+))",
)

_SETUP_PYTHON_RE = re.compile(
    r"python-version:\s*['\"]?([^'\"\n${}]+)",
    re.IGNORECASE,
)
_SETUP_NODE_RE = re.compile(
    r"node-version:\s*['\"]?([^'\"\n${}]+)",
    re.IGNORECASE,
)
_SETUP_GO_RE = re.compile(
    r"go-version:\s*['\"]?([^'\"\n${}]+)",
    re.IGNORECASE,
)
_SETUP_PHP_RE = re.compile(
    r"php-version:\s*['\"]?([^'\"\n${}]+)",
    re.IGNORECASE,
)
_SETUP_RUBY_RE = re.compile(
    r"ruby-version:\s*['\"]?([^'\"\n${}]+)",
    re.IGNORECASE,
)

_FROM_IMAGE_RE = re.compile(
    r"^\s*FROM\s+(?:--\S+\s+)*([^\s:]+(?::[^\s@]+)?)",
    re.IGNORECASE | re.MULTILINE,
)
_DOCKER_RUN_RE = re.compile(r"^\s*RUN\s+(.+)$", re.IGNORECASE | re.MULTILINE)

_INSTALL_SCORES: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\bcomposer\s+install\b", re.I), 12),
    (re.compile(r"\bbundle\s+install\b", re.I), 12),
    (re.compile(r"\bnpm\s+ci\b", re.I), 11),
    (re.compile(r"\bnpm\s+install\b", re.I), 10),
    (re.compile(r"\byarn\s+install\b", re.I), 10),
    (re.compile(r"\bgo\s+mod\s+download\b", re.I), 11),
    (re.compile(r"\bcargo\s+build\b", re.I), 10),
    (re.compile(r"\bpip\s+install\b", re.I), 10),
    (re.compile(r"\bmvn\s+.*(?:package|compile)\b", re.I), 10),
    (re.compile(r"\bgradlew\b.*(?:assemble|compile|build)", re.I), 10),
    (re.compile(r"\bcmake\b", re.I), 8),
    (re.compile(r"\bmeson\s+setup\b", re.I), 9),
)

_TEST_SCORES: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\bpytest\b", re.I), 12),
    (re.compile(r"\bvendor/bin/phpunit\b", re.I), 12),
    (re.compile(r"\bvendor/bin/pest\b", re.I), 12),
    (re.compile(r"\bphp\s+artisan\s+test\b", re.I), 11),
    (re.compile(r"\bbundle\s+exec\s+rspec\b", re.I), 12),
    (re.compile(r"\bbundle\s+exec\s+minitest\b", re.I), 11),
    (re.compile(r"\bcargo\s+test\b", re.I), 12),
    (re.compile(r"\bgo\s+test\b", re.I), 12),
    (re.compile(r"\bnode\s+Makefile(?:\.js)?\s+mocha\b", re.I), 14),
    (re.compile(r"\bnode\s+Makefile(?:\.js)?\s+test\b", re.I), 10),
    (re.compile(r"\bnpx\s+jest\b", re.I), 11),
    (re.compile(r"\bnpx\s+vitest\b", re.I), 11),
    (re.compile(r"\bnpm\s+test\b", re.I), 9),
    (re.compile(r"\byarn\s+test\b", re.I), 9),
    (re.compile(r"\bgradlew\b.*\btest\b", re.I), 11),
    (re.compile(r"\bmvn\s+.*\btest\b", re.I), 10),
    (re.compile(r"\bctest\b", re.I), 10),
    (re.compile(r"\./tests/runtests\.py\b", re.I), 13),
)

_SKIP_RUN_PREFIXES = (
    "echo ",
    "printenv",
    "cd ",
    "export ",
    "set -",
    "git config",
    "git clone",
    "actions/",
    "curl -",
    "wget ",
)


@dataclass
class CiExtractDraft:
    """Structured signals from CI/Docker — merged into ``install_config``."""

    install: str | None = None
    test_cmd: str | None = None
    pre_install: list[str] = field(default_factory=list)
    apt_pkgs: list[str] = field(default_factory=list)
    docker_specs: dict[str, str] = field(default_factory=dict)
    python: str | None = None
    test_env: dict[str, str] = field(default_factory=dict)
    ci_excerpt: str = ""

    def as_merge_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.install:
            out["install"] = self.install
        if self.test_cmd:
            out["test_cmd"] = self.test_cmd
        if self.pre_install:
            out["pre_install"] = list(self.pre_install)
        if self.apt_pkgs:
            out["apt-pkgs"] = list(self.apt_pkgs)
        if self.docker_specs:
            out["docker_specs"] = dict(self.docker_specs)
        if self.python:
            out["python"] = self.python
        if self.test_env:
            out["test_env"] = dict(self.test_env)
        if self.ci_excerpt:
            out["_ci_excerpt"] = self.ci_excerpt
        return out


def _parse_apt_tokens(chunk: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in re.split(r"[\s\\]+", chunk):
        tok = raw.strip().strip("\\").strip("'\"")
        if not tok or tok.startswith("${{") or tok.startswith("$"):
            continue
        if tok in ("-y", "--yes", "sudo", "apt-get", "apt", "install", "--no-install-recommends"):
            continue
        if not re.match(r"^[a-z0-9][a-z0-9+.-]*$", tok, re.IGNORECASE):
            continue
        low = tok.lower()
        if low not in seen:
            seen.add(low)
            out.append(tok)
    return out


def apt_packages_from_ci_workflows(repo: Path, *, max_files: int = 40) -> list[str]:
    """Union Debian packages from ``apt-get install`` / ``apt install`` in GitHub workflows."""
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    seen: set[str] = set()
    out: list[str] = []
    count = 0
    for wf in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
        if count >= max_files:
            break
        count += 1
        try:
            text = wf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in (_APT_GET_INSTALL_RE, _APT_INSTALL_RE):
            for m in pattern.finditer(text):
                for tok in _parse_apt_tokens(m.group(1)):
                    if tok not in seen:
                        seen.add(tok)
                        out.append(tok)
    from .apt_from_log import sanitize_apt_package_names

    return sanitize_apt_package_names(out)


def _workflow_texts(repo: Path, *, max_files: int = 40) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    wf_dir = repo / ".github" / "workflows"
    if wf_dir.is_dir():
        count = 0
        for wf in sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml")):
            if count >= max_files:
                break
            count += 1
            try:
                texts.append((wf.relative_to(repo).as_posix(), wf.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    for name in (".travis.yml", ".circleci/config.yml"):
        p = repo / name
        if p.is_file():
            try:
                texts.append((name, p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return texts


def _score_line(line: str, patterns: tuple[tuple[re.Pattern[str], int], ...]) -> int:
    score = 0
    for pat, pts in patterns:
        if pat.search(line):
            score += pts
    return score


def _normalize_run_line(raw: str) -> str:
    line = raw.strip()
    if "|" in line and line.startswith("|"):
        return ""
    line = line.split("|")[0].strip() if " && " not in line else line
    line = re.sub(r"\s+", " ", line)
    return line


def _collect_run_lines(text: str) -> list[str]:
    lines: list[str] = []
    for m in _CI_RUN_LINE_RE.finditer(text):
        norm = _normalize_run_line(m.group(1))
        if not norm or len(norm) < 4:
            continue
        low = norm.lower()
        if any(low.startswith(p) for p in _SKIP_RUN_PREFIXES):
            continue
        if "${{" in norm:
            continue
        lines.append(norm)
    return lines


def _pick_best_line(candidates: list[str], patterns: tuple[tuple[re.Pattern[str], int], ...]) -> str | None:
    best: tuple[int, str] | None = None
    for line in candidates:
        sc = _score_line(line, patterns)
        if sc < 5:
            continue
        if best is None or sc > best[0]:
            best = (sc, line)
    return best[1] if best else None


def _parse_setup_versions(text: str) -> dict[str, str]:
    specs: dict[str, str] = {}
    m = _SETUP_PYTHON_RE.search(text)
    if m:
        v = m.group(1).strip().strip("'\"")
        if v and not v.startswith("$"):
            specs["_python"] = v.split(".")[0] + "." + (v.split(".")[1] if "." in v else "0")
    m = _SETUP_NODE_RE.search(text)
    if m:
        v = m.group(1).strip().strip("'\"")
        if v and not v.startswith("$"):
            specs["node_version"] = v
    m = _SETUP_GO_RE.search(text)
    if m:
        v = m.group(1).strip().strip("'\"")
        if v and not v.startswith("$"):
            from .go_build import normalize_go_version

            specs["go_version"] = normalize_go_version(v)
    m = _SETUP_PHP_RE.search(text)
    if m:
        v = m.group(1).strip().strip("'\"")
        if v and not v.startswith("$"):
            specs["php_version"] = f"{v}-cli-bookworm" if "-cli" not in v else v
    m = _SETUP_RUBY_RE.search(text)
    if m:
        v = m.group(1).strip().strip("'\"")
        if v and not v.startswith("$"):
            specs["ruby_version"] = f"{v}-bookworm" if "-bookworm" not in v else v
    return specs


def _parse_dockerfile_signals(repo: Path) -> CiExtractDraft:
    draft = CiExtractDraft()
    candidates: list[Path] = []
    for name in ("Dockerfile", "docker/Dockerfile", ".devcontainer/Dockerfile"):
        p = repo / name
        if p.is_file():
            candidates.append(p)
    try:
        for p in sorted((repo / ".devcontainer").glob("**/Dockerfile"))[:3]:
            if p.is_file() and p not in candidates:
                candidates.append(p)
    except OSError:
        pass

    excerpt_parts: list[str] = []
    run_lines: list[str] = []
    for path in candidates[:5]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(repo).as_posix()
        excerpt_parts.append(f"=== {rel} ===\n{text[:4000]}")
        for m in _FROM_IMAGE_RE.finditer(text):
            img = m.group(1).lower()
            if img.startswith("python:"):
                draft.python = img.split(":")[1].split("-")[0]
            elif img.startswith("node:"):
                draft.docker_specs.setdefault("node_version", img.split(":")[1].split("-")[0])
            elif img.startswith("php:"):
                draft.docker_specs.setdefault("php_version", img.split(":")[1])
            elif img.startswith("golang:"):
                from .go_build import normalize_go_version

                tag = img.split(":")[1].split("-")[0]
                draft.docker_specs.setdefault("go_version", normalize_go_version(tag))
            elif img.startswith("rust:"):
                draft.docker_specs.setdefault("rust_version", img.split(":")[1].split("-")[0])
            elif img.startswith("ruby:"):
                draft.docker_specs.setdefault("ruby_version", img.split(":")[1])
        for m in _DOCKER_RUN_RE.finditer(text):
            run = m.group(1).strip()
            if "apt-get install" in run.lower() or " apt install " in run.lower():
                for pat in (_APT_GET_INSTALL_RE, _APT_INSTALL_RE):
                    for am in pat.finditer(run):
                        draft.apt_pkgs.extend(_parse_apt_tokens(am.group(1)))
            run_lines.append(run)

    if run_lines:
        inst = _pick_best_line(run_lines, _INSTALL_SCORES)
        if inst:
            draft.install = inst
        test = _pick_best_line(run_lines, _TEST_SCORES)
        if test:
            draft.test_cmd = test
    if excerpt_parts:
        draft.ci_excerpt = "\n\n".join(excerpt_parts)[:12_000]
    return draft


def extract_ci_draft(repo: Path, *, max_workflow_files: int = 40) -> CiExtractDraft:
    """Parse GitHub Actions / Travis / CircleCI + Dockerfiles into install signals."""
    draft = CiExtractDraft()
    all_runs: list[str] = []
    excerpt_parts: list[str] = []

    for rel, text in _workflow_texts(repo, max_files=max_workflow_files):
        excerpt_parts.append(f"=== {rel} ===\n{text[:6000]}")
        all_runs.extend(_collect_run_lines(text))
        for pattern in (_APT_GET_INSTALL_RE, _APT_INSTALL_RE):
            for m in pattern.finditer(text):
                for tok in _parse_apt_tokens(m.group(1)):
                    if tok not in draft.apt_pkgs:
                        draft.apt_pkgs.append(tok)
        specs = _parse_setup_versions(text)
        if specs.get("_python"):
            draft.python = specs["_python"]
        for k, v in specs.items():
            if k != "_python" and v:
                draft.docker_specs[k] = v
        for m in _CI_EXPORT_RE.finditer(text):
            val = m.group(2) or m.group(3) or m.group(4) or ""
            if val and not val.startswith("${{"):
                draft.test_env[m.group(1)] = val

    draft.apt_pkgs = list(apt_packages_from_ci_workflows(repo, max_files=max_workflow_files))

    if all_runs:
        inst = _pick_best_line(all_runs, _INSTALL_SCORES)
        if inst:
            draft.install = inst
        test = _pick_best_line(all_runs, _TEST_SCORES)
        if test:
            if "pytest" in test.lower() and "-rA" not in test and "-q" not in test:
                test = f"{test} -rA" if "pytest" in test.split()[0:2] else test
            draft.test_cmd = test

    docker_draft = _parse_dockerfile_signals(repo)
    if not draft.install and docker_draft.install:
        draft.install = docker_draft.install
    if not draft.test_cmd and docker_draft.test_cmd:
        draft.test_cmd = docker_draft.test_cmd
    if docker_draft.python and not draft.python:
        draft.python = docker_draft.python
    for k, v in docker_draft.docker_specs.items():
        draft.docker_specs.setdefault(k, v)
    for pkg in docker_draft.apt_pkgs:
        if pkg not in draft.apt_pkgs:
            draft.apt_pkgs.append(pkg)

    excerpt_parts.extend(
        [p for p in (docker_draft.ci_excerpt or "").split("\n\n=== ") if p]
    )
    draft.ci_excerpt = "\n\n".join(excerpt_parts)[:20_000]
    return draft


_CMAKE_DEFINE_RE = re.compile(r"-D[A-Za-z0-9_]+(?:=[^\s\"']+)?")
# Drop CI-only paths and tooling flags unsuitable for SWE-bench env images.
_CMAKE_DEFINE_SKIP_RE = re.compile(
    r"(?i)linuxbrew|/home/runner|/home/|CLANG_TIDY|CURL_CLANG_TIDY|CURL_WERROR|"
    r"CMAKE_C_COMPILER_TARGET|OPENSSL_ROOT_DIR="
)

# Fallback when workflows lack an HTTP/3 + pytest matrix job (e.g. curl ``linux.yml`` H3 c-ares).
DEFAULT_NATIVE_HTTP3_CMAKE_DEFINITIONS: tuple[str, ...] = (
    "-DCMAKE_BUILD_TYPE=Release",
    "-DBUILD_STATIC_LIBS=ON",
    "-DENABLE_DEBUG=ON",
    "-DCURL_USE_OPENSSL=ON",
    "-DUSE_NGTCP2=ON",
    "-DUSE_SSLS_EXPORT=ON",
    "-DENABLE_ARES=ON",
    "-DUSE_PROXY_HTTP3=ON",
)
# Backward-compatible alias.
DEFAULT_CURL_HTTP3_CMAKE_DEFINITIONS = DEFAULT_NATIVE_HTTP3_CMAKE_DEFINITIONS


def _cmake_flag_allowed(flag: str) -> bool:
    raw = flag.strip()
    if not raw.startswith("-D"):
        return False
    if _CMAKE_DEFINE_SKIP_RE.search(raw):
        return False
    return True


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        key = raw.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _workflow_matrix_chunks(text: str) -> list[str]:
    """Split a workflow file into per-matrix-job text blobs (``- name:`` headers)."""
    parts = re.split(r"\n\s*-\s*name:\s*", text)
    return [p for p in parts[1:] if p.strip()]


def _extract_cmake_flags_from_chunk(chunk: str) -> list[str]:
    flags: list[str] = []
    for line in chunk.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-D"):
            continue
        for m in _CMAKE_DEFINE_RE.finditer(stripped):
            flag = m.group(0)
            if _cmake_flag_allowed(flag):
                flags.append(flag)
    return flags


def cmake_definitions_from_ci_for_http3_pytest(
    repo: Path,
    *,
    max_files: int = 40,
) -> list[str]:
    """
    Collect ``-D`` cmake flags from CI matrix jobs that run pytest with HTTP/3 enabled.

    Matches CI matrix jobs (e.g. curl ``linux.yml`` ``address-sanitizer H3 c-ares``) where
    ``install_steps`` includes ``pytest`` and ``generate`` sets ``USE_NGTCP2`` /
    ``USE_PROXY_HTTP3``.
    """
    collected: list[str] = []
    count = 0
    for _rel, text in _workflow_texts(repo, max_files=max_files):
        count += 1
        for chunk in _workflow_matrix_chunks(text):
            low = chunk.lower()
            if "pytest" not in low:
                continue
            if not re.search(r"use_ngtcp2|use_proxy_http3", chunk, re.IGNORECASE):
                continue
            collected.extend(_extract_cmake_flags_from_chunk(chunk))
    return _dedupe_preserve_order(collected)


def ci_all_run_lines(repo: Path, *, max_files: int = 40) -> list[str]:
    """Ordered ``run:`` shell lines from CI workflows (deduped, first-seen order)."""
    all_runs: list[str] = []
    seen: set[str] = set()
    for _rel, text in _workflow_texts(repo, max_files=max_files):
        for line in _collect_run_lines(text):
            if line not in seen:
                seen.add(line)
                all_runs.append(line)
    return all_runs


def ci_excerpt_for_remediation(draft: CiExtractDraft | None, *, max_chars: int = 8000) -> str:
    if draft is None:
        return ""
    return (draft.ci_excerpt or "")[:max_chars]


def merge_ci_draft_into_config(
    cfg: dict[str, Any],
    draft: CiExtractDraft | dict[str, Any],
    *,
    language: str,
) -> dict[str, Any]:
    """Merge CI/Docker signals into *cfg* without clobbering strong heuristics."""
    from .languages import get_language_spec

    if isinstance(draft, CiExtractDraft):
        overlay = draft.as_merge_dict()
    else:
        overlay = dict(draft)

    spec = get_language_spec(language)
    defaults = spec.default_install_config
    out = dict(cfg)

    def _is_default(val: str | None, default_val: str | None) -> bool:
        if not val or not default_val:
            return not bool(val)
        return val.strip() == str(default_val).strip()

    ci_install = overlay.get("install")
    if ci_install and _is_default(str(out.get("install") or ""), str(defaults.get("install") or "")):
        out["install"] = str(ci_install)

    ci_test = overlay.get("test_cmd")
    if ci_test and _is_default(str(out.get("test_cmd") or ""), str(defaults.get("test_cmd") or "")):
        out["test_cmd"] = str(ci_test)
    elif ci_test and not str(out.get("test_cmd") or "").strip():
        out["test_cmd"] = str(ci_test)

    if overlay.get("python") and language == "python":
        if not out.get("python") or str(out.get("python")) in ("3.10", "3.11"):
            out["python"] = str(overlay["python"]).split("-")[0]

    specs = dict(out.get("docker_specs") or {}) if isinstance(out.get("docker_specs"), dict) else {}
    ci_specs = overlay.get("docker_specs")
    if isinstance(ci_specs, dict):
        for k, v in ci_specs.items():
            if v and not specs.get(k):
                specs[k] = str(v)
    if specs:
        out["docker_specs"] = specs

    ci_apt = overlay.get("apt-pkgs")
    if isinstance(ci_apt, list) and ci_apt:
        from .apt_from_log import merge_apt_into_config

        out = merge_apt_into_config(out, [str(x) for x in ci_apt if str(x).strip()])

    ci_pre = overlay.get("pre_install")
    if isinstance(ci_pre, list) and ci_pre:
        pre = list(out.get("pre_install") or [])
        for ln in ci_pre:
            if isinstance(ln, str) and ln.strip() and ln not in pre:
                pre.append(ln.strip())
        out["pre_install"] = pre

    ci_env = overlay.get("test_env")
    if isinstance(ci_env, dict) and ci_env:
        env = dict(out.get("test_env") or {})
        env.update({str(k): str(v) for k, v in ci_env.items()})
        out["test_env"] = env

    if overlay.get("_ci_excerpt"):
        out["_ci_excerpt"] = str(overlay["_ci_excerpt"])

    return out
