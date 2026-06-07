"""PHP/Composer helpers for SWE-bench harness Docker image builds."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .apt_from_log import ensure_base_build_apt, merge_apt_into_config, remediate_apt_install_from_log

DEFAULT_PHP_VERSION = "8.2-cli-bookworm"

_PHP_APT_BASE = ("unzip", "libzip-dev", "libxml2-dev", "libcurl4-openssl-dev", "libicu-dev")
_PHP_EXT_INSTALL_RE = re.compile(r"\bdocker-php-ext-install\b")
_PHPUNIT_BIN_RE = re.compile(
    r"""([\w./-]*(?:vendor/bin/|bin/)(?:phpunit|simple-phpunit|pest))\b""",
    re.I,
)
_PHPUNIT_MISSING_RE = re.compile(
    r"""(?:vendor/bin/|tools/[\w-]+/bin/|vendor-bin/[\w-]+/bin/)phpunit(?:['":\s]|$).*?(?:no such file|not found|cannot find)""",
    re.I,
)
_MAKEFILE_PHPUNIT_RE = re.compile(r"^PHPUNIT\s*=\s*(.+)$", re.MULTILINE | re.I)
_PHPUNIT_GLOB_PATTERNS = (
    "tools/*/bin/phpunit",
    "tools/*/bin/simple-phpunit",
    "vendor-bin/*/bin/phpunit",
    "vendor-bin/*/bin/simple-phpunit",
    "tools/*/vendor/bin/phpunit",
    "tools/*/vendor/bin/simple-phpunit",
    "vendor-bin/*/vendor/bin/phpunit",
    "vendor-bin/*/vendor/bin/simple-phpunit",
)
_DOCKER_PHP_EXT_NAMES: dict[str, str] = {
    "intl": "intl",
    "zip": "zip",
    "curl": "curl",
    "mbstring": "mbstring",
    "xml": "xml",
    "xmlreader": "xmlreader",
    "xmlwriter": "xmlwriter",
    "bcmath": "bcmath",
    "gd": "gd",
    "soap": "soap",
    "xsl": "xsl",
    "sodium": "sodium",
    "bz2": "bz2",
    "ldap": "ldap",
    "ffi": "ffi",
    "sqlite3": "pdo_sqlite",
    "pdo_mysql": "pdo_mysql",
    "pdo_pgsql": "pdo_pgsql",
}


def _normalize_php_version(raw: str) -> str:
    v = raw.strip().lstrip("v").replace(" ", "")
    if not v:
        return DEFAULT_PHP_VERSION
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", v.replace("^", "").split(",")[0])
    if m:
        major, minor = int(m.group(1)), int(m.group(2))
        # Official ``php:*-cli-bookworm`` tags start at 8.2; 8.0/8.1 use bullseye or are absent.
        if major == 8 and minor < 2:
            return "8.2-cli-bookworm"
        patch = m.group(3)
        if patch:
            return f"{major}.{minor}-cli-bookworm"
        return f"{major}.{minor}-cli-bookworm"
    return DEFAULT_PHP_VERSION


def resolve_php_version_for_repo(repo: Path) -> str | None:
    composer = repo / "composer.json"
    if not composer.is_file():
        return None
    try:
        data = json.loads(composer.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    req = data.get("require") or {}
    if isinstance(req, dict) and req.get("php"):
        return _normalize_php_version(str(req["php"]))
    return None


def ensure_php_docker_specs(
    cfg: dict[str, Any],
    *,
    repo: Path | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    lang = str(language or cfg.get("language") or "").lower()
    if lang not in ("", "php"):
        return cfg
    out = dict(cfg)
    specs = dict(out.get("docker_specs") or {}) if isinstance(out.get("docker_specs"), dict) else {}
    if not specs.get("php_version"):
        pv: str | None = None
        if repo is not None:
            pv = resolve_php_version_for_repo(repo)
        specs["php_version"] = pv or DEFAULT_PHP_VERSION
    out["docker_specs"] = specs
    return out


def ensure_php_pre_install_order(cfg: dict[str, Any]) -> dict[str, Any]:
    """
    Run Debian packages before ``docker-php-ext-install`` (extensions need -dev libs).
    """
    pre = list(cfg.get("pre_install") or [])
    if not pre:
        return cfg
    ext_lines = [ln for ln in pre if _PHP_EXT_INSTALL_RE.search(str(ln))]
    if not ext_lines:
        return cfg
    other = [ln for ln in pre if ln not in ext_lines]
    out = dict(cfg)
    out["pre_install"] = other + ext_lines
    return out


def php_install_config_for_repo(
    repo: Path,
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .languages import get_language_spec

    cfg = dict(base or get_language_spec("php").default_install_config)
    cfg["language"] = "php"
    cfg = ensure_php_docker_specs(cfg, repo=repo, language="php")
    from .manifest_extract import composer_ext_apt_packages

    cfg = merge_apt_into_config(cfg, list(_PHP_APT_BASE) + composer_ext_apt_packages(repo))
    pre = list(cfg.get("pre_install") or [])
    ext_cmd = php_docker_ext_install_cmd(repo)
    if ext_cmd and ext_cmd not in pre:
        pre.append(ext_cmd)
    if pre:
        cfg["pre_install"] = pre
    cfg["install"] = "composer install --no-interaction --prefer-dist --no-progress"
    from .ci_extract import ci_all_run_lines

    runner, test_cmd = php_test_cmd_for_repo(repo, ci_runs=ci_all_run_lines(repo))
    cfg["php_test_runner"] = runner
    cfg["test_cmd"] = test_cmd
    return ensure_php_pre_install_order(cfg)


def _composer_json(repo: Path) -> dict[str, Any] | None:
    composer = repo / "composer.json"
    if not composer.is_file():
        return None
    try:
        data = json.loads(composer.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def repo_uses_symfony_phpunit_bridge(repo: Path) -> bool:
    """True when Composer declares Symfony PHPUnit Bridge (``simple-phpunit``)."""
    data = _composer_json(repo)
    if not data:
        return False
    for section in ("require-dev", "require"):
        block = data.get(section) or {}
        if isinstance(block, dict) and block.get("symfony/phpunit-bridge"):
            return True
    scripts = data.get("scripts") or {}
    if isinstance(scripts, dict):
        blob = json.dumps(scripts)
        if "simple-phpunit" in blob:
            return True
    return False


def composer_required_php_extensions(repo: Path) -> list[str]:
    """``ext-*`` names from ``composer.json`` ``require`` (without ``ext-`` prefix)."""
    data = _composer_json(repo)
    if not data:
        return []
    reqs = data.get("require") or {}
    if not isinstance(reqs, dict):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for key in reqs:
        if not isinstance(key, str) or not key.lower().startswith("ext-"):
            continue
        ext = key[4:].lower()
        if ext and ext not in seen:
            seen.add(ext)
            out.append(ext)
    return out


# Extensions already compiled in ``harness/dockerfiles/php.py`` base image.
PHP_BASE_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {"gd", "zip", "gmp", "ftp", "curl", "pcntl", "json"}
)


def php_extensions_to_install(
    required: Iterable[str],
    *,
    extra_from_ci: Iterable[str] | None = None,
) -> list[str]:
    """Return docker-php-ext names not already in the PHP harness base image."""
    names: list[str] = []
    seen: set[str] = set()
    for raw in list(required or []) + list(extra_from_ci or []):
        ext = str(raw).strip().lower()
        if not ext or ext in PHP_BASE_IMAGE_EXTENSIONS:
            continue
        name = _DOCKER_PHP_EXT_NAMES.get(ext, ext)
        if name in PHP_BASE_IMAGE_EXTENSIONS or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def php_docker_ext_install_cmd_for_extensions(exts: list[str]) -> str:
    if not exts:
        return ""
    return f"docker-php-ext-install -j$(nproc) {' '.join(exts)}"


def php_docker_ext_install_cmd(repo: Path, *, ci_extensions: Iterable[str] | None = None) -> str:
    """``docker-php-ext-install`` for Composer ``ext-*`` + CI setup-php extensions."""
    required = composer_required_php_extensions(repo)
    to_install = php_extensions_to_install(required, extra_from_ci=ci_extensions)
    return php_docker_ext_install_cmd_for_extensions(to_install)


def _composer_json_declares_bamarni_bin(data: dict[str, Any]) -> bool:
    for section in ("require-dev", "require"):
        block = data.get(section) or {}
        if isinstance(block, dict) and block.get("bamarni/composer-bin-plugin"):
            return True
    plugins = data.get("config") or {}
    if isinstance(plugins, dict):
        allow = plugins.get("allow-plugins") or {}
        if isinstance(allow, dict) and allow.get("bamarni/composer-bin-plugin"):
            return True
    return False


def repo_uses_composer_bin_plugin(repo: Path) -> bool:
    """True when Composer uses bamarni/composer-bin-plugin (tools in subdirs)."""
    data = _composer_json(repo)
    if data and _composer_json_declares_bamarni_bin(data):
        return True
    return (repo / "vendor-bin").is_dir() or (repo / "tools").is_dir()


def bamarni_bin_target_directory(repo: Path) -> str:
    """``extra.bamarni-bin.target-directory`` (default ``vendor-bin``)."""
    data = _composer_json(repo)
    if not data:
        return "vendor-bin"
    extra = data.get("extra") or {}
    bamarni = extra.get("bamarni-bin") if isinstance(extra, dict) else {}
    if isinstance(bamarni, dict):
        target = str(bamarni.get("target-directory") or "").strip().strip("/")
        if target:
            return target
    return "vendor-bin"


def bamarni_bin_links_enabled(repo: Path) -> bool:
    data = _composer_json(repo)
    if not data:
        return True
    extra = data.get("extra") or {}
    bamarni = extra.get("bamarni-bin") if isinstance(extra, dict) else {}
    if isinstance(bamarni, dict) and "bin-links" in bamarni:
        return bool(bamarni.get("bin-links"))
    return True


def inferred_composer_bin_phpunit_rel(repo: Path, *, namespace: str = "phpunit") -> str | None:
    """
    Infer bamarni composer-bin PHPUnit path from ``composer.json`` (no install required).

    ``bin-links: false`` → ``{target}/phpunit/bin/phpunit``;
    ``bin-links: true`` → ``{target}/phpunit/vendor/bin/phpunit``.
    """
    data = _composer_json(repo)
    if not data or not _composer_json_declares_bamarni_bin(data):
        return None
    target = bamarni_bin_target_directory(repo)
    ns = namespace.strip().strip("/") or "phpunit"
    if bamarni_bin_links_enabled(repo):
        return f"{target}/{ns}/vendor/bin/phpunit"
    return f"{target}/{ns}/bin/phpunit"


def _normalize_phpunit_bin_rel(raw: str) -> str:
    rel = raw.strip().strip('"').strip("'").lstrip("./")
    return rel.replace("\\", "/")


def _glob_phpunit_bins(repo: Path) -> list[Path]:
    hits: list[Path] = []
    seen: set[str] = set()
    for pattern in _PHPUNIT_GLOB_PATTERNS:
        for p in repo.glob(pattern):
            if not p.is_file():
                continue
            key = p.relative_to(repo).as_posix()
            if key not in seen:
                seen.add(key)
                hits.append(p)
    hits.sort(key=lambda p: (len(p.parts), str(p)))
    return hits


def discover_phpunit_bin_from_makefile(repo: Path) -> tuple[str, str] | None:
    """
    Parse ``PHPUNIT = ...`` from Makefile-style files.

    Returns ``(bin_rel, extra_args)`` e.g. ``("tools/phpunit/bin/phpunit", "-c .")``.
    """
    for name in ("Makefile", "GNUmakefile", "makefile"):
        mf = repo / name
        if not mf.is_file():
            continue
        try:
            text = mf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _MAKEFILE_PHPUNIT_RE.search(text)
        if not m:
            continue
        raw = m.group(1).strip().split("#", 1)[0].strip()
        if not raw:
            continue
        parts = raw.split()
        if not parts:
            continue
        bin_rel = _normalize_phpunit_bin_rel(parts[0])
        if not bin_rel.endswith(("phpunit", "simple-phpunit", "pest")):
            continue
        extra = " ".join(parts[1:])
        if (repo / bin_rel).is_file() or "/" in bin_rel:
            return bin_rel, extra
    return None


def discover_phpunit_bin_rel(repo: Path, *, ci_runs: Iterable[str] | None = None) -> str | None:
    """
    Locate PHPUnit (or simple-phpunit / pest) relative to repo root.

    Handles composer-bin layouts like ``tools/phpunit/bin/phpunit`` and
    ``tools/phpunit/vendor/bin/phpunit``.
    """
    root_phpunit = repo / "vendor" / "bin" / "phpunit"
    if root_phpunit.is_file():
        return "vendor/bin/phpunit"
    root_simple = repo / "vendor" / "bin" / "simple-phpunit"
    if root_simple.is_file():
        return "vendor/bin/simple-phpunit"

    makefile = discover_phpunit_bin_from_makefile(repo)
    if makefile:
        return makefile[0]

    hits = _glob_phpunit_bins(repo)
    if hits:
        return hits[0].relative_to(repo).as_posix()

    data = _composer_json(repo)
    if data:
        scripts = data.get("scripts") or {}
        if isinstance(scripts, dict):
            for raw in scripts.values():
                text = (
                    raw
                    if isinstance(raw, str)
                    else " ".join(str(x) for x in raw)
                    if isinstance(raw, list)
                    else json.dumps(raw)
                )
                m = _PHPUNIT_BIN_RE.search(text)
                if m:
                    rel = _normalize_phpunit_bin_rel(m.group(1))
                    if (repo / rel).is_file():
                        return rel

    inferred = inferred_composer_bin_phpunit_rel(repo)
    if inferred:
        return inferred

    for line in ci_runs or []:
        m = _PHPUNIT_BIN_RE.search(str(line))
        if m:
            rel = _normalize_phpunit_bin_rel(m.group(1))
            if (repo / rel).is_file():
                return rel

    return None


def discover_phpunit_invocation(
    repo: Path,
    *,
    ci_runs: Iterable[str] | None = None,
) -> tuple[str, str] | None:
    """Return ``(bin_rel, extra_args)`` for PHPUnit invocation."""
    makefile = discover_phpunit_bin_from_makefile(repo)
    if makefile:
        return makefile
    rel = discover_phpunit_bin_rel(repo, ci_runs=ci_runs)
    if rel:
        return rel, ""
    return None


def php_test_cmd_with_bin(bin_rel: str, *, extra_args: str = "") -> str:
    flags = str(extra_args or "").strip()
    if flags:
        return f"{bin_rel} {flags} --log-junit __JUNIT_OUT__"
    return f"{bin_rel} --log-junit __JUNIT_OUT__"


def php_test_cmd_for_repo(
    repo: Path,
    *,
    ci_runs: Iterable[str] | None = None,
) -> tuple[str, str]:
    """
    Return ``(php_test_runner, test_cmd)`` with ``__JUNIT_OUT__`` placeholder.

    Prefers Symfony ``simple-phpunit``, then Laravel ``artisan test``, then discovered phpunit.
    """
    from .repo_detect import repo_uses_artisan_phpunit

    if repo_uses_artisan_phpunit(repo):
        discovered = discover_phpunit_bin_rel(repo, ci_runs=ci_runs) or "vendor/bin/phpunit"
        return (
            "artisan",
            "php artisan test --log-junit __JUNIT_OUT__ 2>/dev/null "
            f"|| {php_test_cmd_with_bin(discovered)}",
        )
    if repo_uses_symfony_phpunit_bridge(repo):
        bin_rel = discover_phpunit_bin_rel(repo, ci_runs=ci_runs) or "vendor/bin/simple-phpunit"
        if "simple-phpunit" not in bin_rel:
            bin_rel = "vendor/bin/simple-phpunit"
        return (
            "simple-phpunit",
            f"{bin_rel} --log-junit __JUNIT_OUT__ 2>/dev/null "
            f"|| {bin_rel} --log-junit __JUNIT_OUT__",
        )
    invocation = discover_phpunit_invocation(repo, ci_runs=ci_runs)
    if invocation:
        bin_rel, extra = invocation
    else:
        bin_rel, extra = "vendor/bin/phpunit", ""
    cmd = php_test_cmd_with_bin(bin_rel, extra_args=extra)
    return (
        "phpunit",
        f"{cmd} 2>/dev/null || {cmd}",
    )


def log_indicates_php_composer_or_phpunit_failure(log: str) -> bool:
    low = (log or "").lower()
    return any(
        m in low
        for m in (
            "composer install",
            "your requirements could not be resolved",
            "ext-intl",
            "ext-zip",
            "simple-phpunit",
            "phpunit",
            "class not found",
        )
    )


def php_junit_nodeid_in_test_patch_paths(nodeid: str, paths: list[str]) -> bool:
    """Match PHPUnit JUnit node ids to ``test_patch`` file paths."""
    if not nodeid or not paths:
        return False
    from .diff_split import _nodeid_leading_relpath

    head = _nodeid_leading_relpath(nodeid).replace("\\", "/").strip().lstrip("/")
    path_set: set[str] = set()
    for raw in paths:
        rel = raw.replace("\\", "/").strip().lstrip("/")
        if not rel:
            continue
        path_set.add(rel)
        path_set.add(Path(rel).name)
        if rel.endswith(".php"):
            stem = Path(rel).stem
            path_set.add(stem)
            path_set.add(rel.replace("/", "\\"))
        if rel.endswith("Test.php"):
            fq = rel[:-4].replace("/", "\\")
            path_set.add(fq.replace("\\", "."))
            path_set.add(fq)
    if head in path_set:
        return True
    head_name = Path(head).name if "/" in head else head
    if head_name in path_set:
        return True
    head_norm = head.replace("\\", "/")
    for p in path_set:
        if not p or "/" not in p:
            continue
        if head_norm == p or head_norm.endswith("/" + p) or p.endswith(head_norm):
            return True
        if head_norm.endswith(p.split("/")[-1]):
            return True
    if "::" in nodeid:
        class_part = nodeid.split("::", 1)[0].strip()
        class_path = class_part.replace(".", "/") + ".php"
        class_tail = class_part.rsplit(".", 1)[-1] + ".php"
        for p in path_set:
            if p == class_path or p.endswith(class_path) or p.endswith(class_tail):
                return True
            if class_tail in p:
                return True
    return False


def log_indicates_php_phpunit_missing(log: str) -> bool:
    low = (log or "").lower()
    if _PHPUNIT_MISSING_RE.search(log or ""):
        return True
    if "phpunit" not in low:
        return False
    return any(
        tok in low
        for tok in ("no such file", "not found", "cannot find", "no such file or directory")
    )


def phpunit_bin_from_test_cmd(test_cmd: str) -> str | None:
    """First PHPUnit executable token from ``test_cmd``."""
    raw = str(test_cmd or "").strip()
    if not raw:
        return None
    head = raw.split("||", 1)[0].strip()
    head = re.split(r"\s2>/dev/null\b", head, maxsplit=1)[0].strip()
    first = head.split()[0] if head.split() else ""
    first = _normalize_phpunit_bin_rel(first)
    if first.endswith(("phpunit", "simple-phpunit", "pest")):
        return first
    return None


def php_runtime_deps_present_check_shell(repo_dir: str) -> str:
    """Bash guard: true when a PHPUnit binary appears present under *repo_dir*."""
    qrepo = repo_dir if repo_dir.startswith("/") else f'"{repo_dir}"'
    return f"""\
  if [[ -x {qrepo}/vendor/bin/phpunit ]] || [[ -x {qrepo}/vendor/bin/simple-phpunit ]]; then
    return 0
  fi
  if compgen -G {qrepo}/tools/*/bin/phpunit >/dev/null 2>&1 \\
     || compgen -G {qrepo}/tools/*/bin/simple-phpunit >/dev/null 2>&1 \\
     || compgen -G {qrepo}/vendor-bin/*/bin/phpunit >/dev/null 2>&1 \\
     || compgen -G {qrepo}/vendor-bin/*/bin/simple-phpunit >/dev/null 2>&1; then
    return 0
  fi
  if find {qrepo} -path '*/vendor/bin/phpunit' -executable 2>/dev/null | head -1 | grep -q .; then
    return 0
  fi
  if find {qrepo} \\( -path '*/tools/*/bin/phpunit' -o -path '*/vendor-bin/*/bin/phpunit' \\) \\
     -executable 2>/dev/null | head -1 | grep -q .; then
    return 0
  fi"""


def remediate_php_install_from_log(cfg: dict[str, Any], log: str, *, repo: Path | None = None) -> dict[str, Any]:
    out = remediate_apt_install_from_log(cfg, log)
    out = merge_apt_into_config(out, list(_PHP_APT_BASE))
    if repo is not None:
        from .ci_extract import ci_all_run_lines
        from .manifest_extract import composer_ext_apt_packages

        out = merge_apt_into_config(out, composer_ext_apt_packages(repo))
        ci_runs = ci_all_run_lines(repo)
        runner, test_cmd = php_test_cmd_for_repo(repo, ci_runs=ci_runs)
        if log_indicates_php_phpunit_missing(log):
            invocation = discover_phpunit_invocation(repo, ci_runs=ci_runs)
            if invocation:
                discovered, extra = invocation
                runner = (
                    "simple-phpunit"
                    if "simple-phpunit" in discovered
                    else "phpunit"
                )
                cmd = php_test_cmd_with_bin(discovered, extra_args=extra)
                test_cmd = f"{cmd} 2>/dev/null || {cmd}"
        out["php_test_runner"] = runner
        out["test_cmd"] = test_cmd
        out["install"] = "composer install --no-interaction --prefer-dist --no-progress"
        ext_cmd = php_docker_ext_install_cmd(repo)
        if ext_cmd:
            pre = list(out.get("pre_install") or [])
            if ext_cmd not in pre:
                pre.append(ext_cmd)
            out["pre_install"] = pre
    return ensure_php_pre_install_order(out)


def repo_has_bin_composer(repo: Path) -> bool:
    return (repo / "bin" / "composer").is_file() or (repo / "bin" / "composer.bat").is_file()


def count_composer_install_in_ci_runs(runs: Iterable[str]) -> int:
    n = 0
    for line in runs:
        low = str(line).lower()
        if re.search(r"\bcomposer\s+install\b", low) or re.search(r"\bbin/composer\s+install\b", low):
            n += 1
    return n


def self_hosting_composer_install_cmd(*, test_env: dict[str, str] | None = None) -> str:
    flags = str((test_env or {}).get("COMPOSER_FLAGS") or "").strip()
    if not flags:
        flags = "--ansi --no-interaction --no-progress --prefer-dist"
    return (
        f"composer install {flags} && "
        f"bin/composer install {flags}"
    )


def merge_php_ci_signals_into_config(
    cfg: dict[str, Any],
    *,
    php_extensions: list[str] | None = None,
    php_tools: list[str] | None = None,
    test_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Apply setup-php / workflow env signals to ``install_config``."""
    out = dict(cfg)
    if test_env:
        env = dict(out.get("test_env") or {})
        env.update({str(k): str(v) for k, v in test_env.items()})
        out["test_env"] = env

    pre = list(out.get("pre_install") or [])
    ci_exts = list(php_extensions or [])
    ext_cmd = php_docker_ext_install_cmd_for_extensions(
        php_extensions_to_install([], extra_from_ci=ci_exts)
    )
    if ext_cmd and ext_cmd not in pre:
        pre.append(ext_cmd)

    tools = [str(t).lower() for t in (php_tools or [])]
    if any("composer" in t for t in tools):
        snap = (
            "curl -sS https://getcomposer.org/installer | "
            "php -- --install-dir=/usr/local/bin --filename=composer 2>/dev/null || true"
        )
        if snap not in pre:
            pre.append(snap)
    if pre:
        out["pre_install"] = pre
    return ensure_php_docker_specs(out, language="php")


def apply_self_hosting_composer_install(
    cfg: dict[str, Any],
    repo: Path,
    *,
    ci_runs: Iterable[str] | None = None,
) -> dict[str, Any]:
    """When repo ships ``bin/composer``, use two-step bootstrap install (CI pattern)."""
    if not repo_has_bin_composer(repo):
        return cfg
    runs = list(ci_runs or [])
    if runs and count_composer_install_in_ci_runs(runs) < 1:
        return cfg
    out = dict(cfg)
    env = dict(out.get("test_env") or {})
    out["install"] = self_hosting_composer_install_cmd(test_env=env)
    return out


def merge_php_harness_fields_after_llm(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if str(before.get("language") or "").lower() != "php" and "phpunit" not in str(
        before.get("test_cmd") or ""
    ).lower():
        return after
    out = dict(after)
    for key in (
        "install",
        "test_cmd",
        "pre_install",
        "post_install",
        "apt-pkgs",
        "docker_specs",
        "language",
        "php_test_runner",
        "eval_commands",
    ):
        if before.get(key) and not out.get(key):
            out[key] = before[key]
    from .harness_guards import restore_test_cmd_if_invalid

    out = restore_test_cmd_if_invalid(before, out, language="php")
    tc_before = str(before.get("test_cmd") or "")
    tc_after = str(out.get("test_cmd") or "")
    if "phpunit" in tc_before.lower() and "pytest" in tc_after.lower():
        out["test_cmd"] = tc_before
    return ensure_php_docker_specs(out, language="php")
