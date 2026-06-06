"""Map CI install/test commands to Docker-replayable shell (language-aware)."""

from __future__ import annotations

import re

_PIP_EDITABLE_EXTRA_RE = re.compile(
    r"pip\s+install(?:\s+-e|\s+--editable)\s+[\"']?\.\[[^\]]+\][\"']?",
    re.IGNORECASE,
)
_UV_PIP_FLAGS_TO_DROP = frozenset(
    {
        "--system",
        "--break-system-packages",
        "--python-preference=only-system",
    }
)


def _strip_uv_pip_flags(args: str) -> str:
    tokens = args.split()
    return " ".join(t for t in tokens if t.lower() not in _UV_PIP_FLAGS_TO_DROP)


def _normalize_uv_python_command(s: str) -> str | None:
    """Map ``uv pip …`` / ``uv pip sync`` to conda-replayable ``pip`` install lines."""
    low = s.lower().strip()
    if m := re.match(r"^uv\s+pip\s+install\s+(.+)$", s, re.IGNORECASE):
        return f"pip install {_strip_uv_pip_flags(m.group(1))}"
    if re.search(r"\buv\s+pip\s+sync\b", low):
        return _python_editable_with_extras_fallback("dev", "tests", "test")
    return None


def docker_safe_python_install(cmd: str) -> str:
    """Last-resort rewrite so ``install_config`` never ships raw ``uv`` into Docker."""
    s = str(cmd or "").strip()
    if not s:
        return "pip install -e ."
    if " && " in s:
        parts = [docker_safe_python_install(p.strip()) for p in s.split(" && ") if p.strip()]
        return " && ".join(parts) if parts else "pip install -e ."
    out = normalize_ci_install_command(s, language="python")
    out = re.sub(r"\buv\s+(?=pip\s+install\b)", "", out, flags=re.IGNORECASE)
    out = re.sub(r"^uv\s+run\s+(?:--\s+)?", "", out, flags=re.IGNORECASE)
    return out.strip() or "pip install -e ."


def _python_editable_with_extras_fallback(*groups: str) -> str:
    ordered: list[str] = []
    seen: set[str] = set()
    for name in groups:
        low = name.strip().lower()
        if low and low not in seen:
            seen.add(low)
            ordered.append(low)
    for fallback in ("tests", "test", "dev", "all"):
        if fallback not in seen:
            ordered.append(fallback)
    attempts = [f'pip install -e ".[{g}]"' for g in ordered]
    attempts.append("pip install -e .")
    return " || ".join(attempts)


def normalize_ci_install_command(line: str, *, language: str = "python") -> str:
    """
    Translate modern CI package-manager install lines into pip/npm commands
    that replay inside SWE-bench Docker env images.
    """
    s = line.strip()
    if not s or s.startswith("#"):
        return s
    lang = language.strip().lower()
    low = s.lower()

    if lang in ("python", "py"):
        if " && " in s:
            parts = [
                normalize_ci_install_command(part.strip(), language=language)
                for part in s.split(" && ")
                if part.strip()
            ]
            return " && ".join(parts)
        if uv := _normalize_uv_python_command(s):
            return uv
        if _PIP_EDITABLE_EXTRA_RE.search(s) or (
            re.search(r"\bpip\s+install\b", low)
            and not re.search(r"\buv\s+pip\s+install\b", low)
        ):
            return s
        if re.search(r"\bpdm\s+sync\b", low):
            return _python_editable_with_extras_fallback("dev", "tests", "test")
        if re.search(r"\bpdm\s+install\b", low):
            groups: list[str] = []
            if re.search(r"(?:-G|--group)\s+dev\b", low) or re.search(r"\s--dev\b", low):
                groups.append("dev")
            if re.search(r"(?:-G|--group)\s+tests?\b", low):
                groups.append("tests")
            if re.search(r"(?:-G|--group)\s+test\b", low):
                groups.append("test")
            return _python_editable_with_extras_fallback(*(groups or ("dev", "tests", "test")))
        if re.search(r"\buv\s+sync\b", low):
            groups = []
            if "--all-extras" in low or "--all-groups" in low:
                groups.extend(["dev", "tests", "test"])
            elif m := re.search(r"--extra[s]?\s+([A-Za-z0-9_-]+)", s):
                groups.append(m.group(1))
            elif m := re.search(r"--group\s+([A-Za-z0-9_-]+)", s):
                groups.append(m.group(1))
            return _python_editable_with_extras_fallback(*(groups or ("dev", "tests", "test")))
        if re.search(r"\bpoetry\s+install\b", low):
            groups: list[str] = []
            with_m = re.search(r"--with\s+([A-Za-z0-9_, -]+)", s)
            if with_m:
                for part in re.split(r"[,\s]+", with_m.group(1)):
                    if part.strip():
                        groups.append(part.strip())
            e_m = re.search(r"-E\s+([A-Za-z0-9_-]+)", s)
            if e_m:
                groups.append(e_m.group(1))
            extra_m = re.search(r"--extras?\s+([A-Za-z0-9_-]+)", s)
            if extra_m:
                groups.append(extra_m.group(1))
            if groups:
                return _python_editable_with_extras_fallback(*groups)
            if "--no-root" in low:
                return "pip install ."
            return "pip install -e ."
        if re.search(r"\bpython\s+-m\s+pip\s+install\b", low):
            return s
        if re.search(r"\btox\s+-e\b", low):
            env_m = re.search(r"-e\s+([A-Za-z0-9_,]+)", s)
            if env_m:
                envs = [e.strip() for e in env_m.group(1).split(",") if e.strip()]
                return _python_editable_with_extras_fallback(*envs)
            return _python_editable_with_extras_fallback("dev", "tests", "test")
        if re.search(r"\bnox\s+-", low):
            session_m = re.search(r"-(?:s|session)\s+([A-Za-z0-9_-]+)", s)
            if session_m:
                return _python_editable_with_extras_fallback(session_m.group(1), "tests", "test")
            return _python_editable_with_extras_fallback("tests", "test", "dev")
        if re.search(r"\bhatch\s+run\b", low):
            return _python_editable_with_extras_fallback("dev", "tests", "test")
        if re.search(r"\bmake\s+test\b", low):
            return "pip install -e ."
        return s

    if lang in ("javascript", "js", "typescript", "ts"):
        if re.search(r"\bpnpm\s+install\b", low):
            if "--frozen-lockfile" in low or "pnpm i --frozen-lockfile" in low:
                return "npm ci"
            if re.search(r"(?:-r|--filter|--workspace-root)\b", low) or "workspace" in low:
                return "npm ci || npm install"
            return "npm install"
        if re.search(r"\blerna\s+bootstrap\b", low):
            return "npm install || npm ci"
        if re.search(r"\byarn\s+install\b", low):
            if re.search(r"(?:-W|--workspace|--focus)\b", s) or "workspaces" in low:
                return "yarn install || npm ci || npm install"
            return "yarn install || npm install"
        if re.search(r"\bcorepack\s+enable\b", low):
            return "corepack enable || true"
        if re.search(r"\bbun\s+install\b", low):
            return "npm install"
        return s

    if lang in ("go", "golang"):
        if re.search(r"\bgo\s+mod\s+download\b", low):
            return s
        if re.search(r"\bgo\s+get\b", low):
            return "go mod download"
        return s

    if lang in ("java",):
        if re.search(r"\./mvnw\b", low) and "install" in low:
            return "./mvnw -q -DskipTests package || ./mvnw -q -DskipTests compile"
        if re.search(r"\./gradlew\b", low) and re.search(r"\b(?:assemble|build|compile|clean)\b", low):
            return _normalize_gradlew_build_command(s)
        return s

    if lang in ("php",):
        if re.search(r"\bcomposer\s+install\b", low) or re.search(r"\bbin/composer\s+install\b", low):
            return _normalize_composer_install_command(s)
        return s

    if lang in ("ruby", "rb"):
        if re.search(r"\bbundle\s+install\b", low):
            return s
        return s

    return s


def _normalize_gradlew_build_command(line: str) -> str:
    """Map CI ``./gradlew clean build`` to a Docker-replayable compile install."""
    low = line.lower()
    parts = ["chmod +x ./gradlew 2>/dev/null || true"]
    if "./gradlew --stop" in low or re.search(r"\./gradlew\s+--stop", low):
        parts.insert(0, "./gradlew --stop 2>/dev/null || true")
    if "clean" in low and "build" in low:
        parts.append(
            "./gradlew --no-daemon clean build -x test --continue "
            "|| ./gradlew --no-daemon classes --continue"
        )
    elif "build" in low:
        parts.append(
            "./gradlew --no-daemon build -x test --continue "
            "|| ./gradlew --no-daemon classes --continue"
        )
    elif "assemble" in low:
        parts.append("./gradlew --no-daemon assemble -x test --continue || true")
    elif "compile" in low:
        parts.append("./gradlew --no-daemon classes --continue || true")
    else:
        parts.append("./gradlew --no-daemon build -x test --continue || true")
    return " && ".join(parts)


def _normalize_composer_install_command(line: str) -> str:
    """Preserve Composer install flags; expand ``$COMPOSER_FLAGS`` when present."""
    s = line.strip()
    flags = "--ansi --no-interaction --no-progress --prefer-dist"
    if "$COMPOSER_FLAGS" in s or "${COMPOSER_FLAGS}" in s:
        s = s.replace("$COMPOSER_FLAGS", flags).replace("${COMPOSER_FLAGS}", flags)
    elif "composer install" in s.lower() and "--no-interaction" not in s.lower():
        s = re.sub(r"\bcomposer\s+install\b", f"composer install {flags}", s, count=1, flags=re.I)
    return s


def compose_ci_install_sequence(steps: list[str], *, language: str) -> str:
    """
    Join ordered CI install/build steps into one replayable shell command.

    Skips duplicate ``chmod +x ./gradlew`` when a later step already compiles.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in steps:
        cmd = normalize_ci_install_command(str(raw).strip(), language=language)
        if not cmd or cmd in seen:
            continue
        seen.add(cmd)
        normalized.append(cmd)

    if not normalized:
        return ""

    if language.strip().lower() == "java":
        has_gradle_build = any(
            "./gradlew" in c and re.search(r"\b(?:build|assemble|classes|compile)\b", c, re.I)
            for c in normalized
        )
        if has_gradle_build:
            normalized = [
                c
                for c in normalized
                if c != "chmod +x ./gradlew 2>/dev/null || true"
                or not has_gradle_build
            ]
            # Keep chmod bundled inside gradle build steps; drop standalone chmod-only.
            filtered: list[str] = []
            for c in normalized:
                if c == "chmod +x ./gradlew 2>/dev/null || true" and any(
                    "chmod +x ./gradlew" in x and "./gradlew --no-daemon" in x for x in normalized
                ):
                    continue
                filtered.append(c)
            normalized = filtered or normalized

    return " && ".join(normalized)


def normalize_ci_test_command(line: str, *, language: str = "python") -> str:
    """Light normalization for CI test commands."""
    s = line.strip()
    if not s:
        return s
    lang = language.strip().lower()
    low = s.lower()
    if lang in ("javascript", "js", "typescript", "ts"):
        if re.search(r"\bpnpm\s+(run\s+)?test\b", low):
            return re.sub(r"\bpnpm\b", "npm run", s, count=1)
        if re.search(r"\bbun\s+(run\s+)?test\b", low):
            return re.sub(r"\bbun\b", "npm run", s, count=1)
    if lang in ("python", "py") and re.search(r"^uv\s+run\s+", low):
        inner = re.sub(r"^uv\s+run\s+(?:--\s+)?", "", s, count=1, flags=re.IGNORECASE)
        return normalize_ci_test_command(inner.strip(), language=language)
    if lang in ("python", "py") and "pytest" in low:
        if "-rA" not in s and "-q" not in s and "pytest" in s.split()[0:2]:
            return f"{s} -rA"
    if lang in ("python", "py") and re.search(r"\btox\s+-e\b", low):
        env_m = re.search(r"-e\s+([A-Za-z0-9_,]+)", s)
        if env_m:
            env = env_m.group(1).split(",")[0].strip()
            return f"pytest --no-header -rA --tb=line --color=no -p no:cacheprovider tests/ || pytest -rA"
        return "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider"
    if lang in ("python", "py") and re.search(r"\bnox\s+-", low):
        return "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider"
    if lang in ("javascript", "js", "typescript", "ts"):
        if re.search(r"\byarn\s+workspaces\s+run\s+test\b", low):
            return re.sub(r"\byarn\s+workspaces\s+run\s+test\b", "npm run test", s, count=1, flags=re.I)
    return s
