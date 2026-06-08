# Repository Evaluator

A [Lazarus](https://lazarus.turing.com/) tool from Turing that scores open-source repositories for suitability as SWE-bench-style training data. It inspects repository structure, CI, local test execution, pull-request history, and (by default) LLM-assisted quality signals.

---

## What it does

For a GitHub, Bitbucket, or GitLab repository, the evaluator reports:

- **Repository metrics** — languages, file counts, lines of code, test frameworks, CI/CD hints
- **Local test run** — auto-detects a test runner and runs tests in a clone (when the toolchain is available)
- **PR filtering** — which merged PRs pass structural rules (test files, change size, linked issues, English text, etc.)
- **LLM layers** (default on) — taxonomy classification, benchmark rubrics on accepted PRs, enterprise/risk collectors, and repo quality checks (vibecode, security, production quality)

Accepted PR URLs can be exported for downstream task generation (for example with `swe-bench-taskgen` in this monorepo).

---

## Requirements

| Requirement | Notes |
|-------------|--------|
| **Python 3.12+** | See `.python-version` (CI uses 3.12.8) |
| **Git** | Clones repos when `--repo-path` is not set |
| **Dependencies** | `pip install -r requirements.txt` |
| **Platform token** | Set in `.env`: `GH_TOKEN` / `GITHUB_TOKEN` (GitHub), `BITBUCKET_TOKEN` (Bitbucket), or `GITLAB_TOKEN` (GitLab). Optional for public GitHub repos but you will hit rate limits without it. |
| **LLM API key** | Required for default runs; set the key for your chosen provider in `.env`. Can be skipped with flags (see below). |
| **Language toolchains** | Needed on `PATH` for local test analysis (see table below) |

---

## Installation

```bash
cd repo-eval-pr-filtering-kit
pip install -r requirements.txt
cp .env.example .env
# Edit .env: GitHub token + LLM API key (see below)
```

---

## Environment (`.env`)

When used inside the **swe-bench-taskgen** monorepo, configure a single root `.env` with `PR_FILTER_*` keys (see monorepo `README.md`). Standalone runs load `pr-filtering-kit/.env` or the monorepo root `.env` automatically.

**All credentials are read from environment variables only** — there is no `--token` flag. For a typical GitHub run you need **two** credentials:

| Variable | Required | Purpose |
|----------|----------|---------|
| `GH_TOKEN` or `GITHUB_TOKEN` | Strongly recommended (GitHub) | GitHub API access (PRs, issues, cloning private repos). Without it, public repos still work but rate limits apply quickly. |
| `BITBUCKET_TOKEN` | Required (Bitbucket) | Bitbucket API access when `--platform bitbucket`. |
| `GITLAB_TOKEN` | Required (GitLab) | GitLab API access when `--platform gitlab`. |
| Provider API key (see table below) | Yes (default mode) | LLM calls for taxonomy, PR rubrics, quality checks, and enterprise collectors. |

Example `.env` for GitHub + OpenAI:

```bash
GH_TOKEN=ghp_...
OPENAI_API_KEY=sk-...
LLM_CONCURRENCY=4
```

For Bitbucket or GitLab, put `BITBUCKET_TOKEN` or `GITLAB_TOKEN` in `.env` and pass `--platform bitbucket` or `--platform gitlab`.

### LLM provider

By default the evaluator calls an LLM. Set **one** provider key in `.env`:

| Provider | `LLM_PROVIDER` | Environment variable | Default model (if `LLM_MODEL` unset) |
|----------|----------------|----------------------|--------------------------------------|
| OpenAI | `openai` (default) | `OPENAI_API_KEY` | `gpt-5.1` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` |
| Google | `google` | `GOOGLE_API_KEY` | `gemini-3-flash-preview` |

Anthropic example:

```bash
GH_TOKEN=ghp_...
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_CONCURRENCY=4
```

Optional tuning (also in `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_MODEL` | *(provider default)* | Override model name |
| `LLM_CONCURRENCY` | `4` | Parallel LLM calls (`--taxonomy-concurrency` defaults to this) |
| `MAX_ACCEPTED_PRS` | `50` | Stop after N rubric goal PRs (`accepted` + `partially_accepted`) |
| `LLM_MAX_RETRIES` | `8` | Retries with exponential backoff |
| `LLM_BACKOFF_BASE_DELAY` | `5.0` | Initial backoff seconds |
| `COST_WARNING_THRESHOLD` | `5` | USD interval for interactive cost warnings |

In the **swe-bench-taskgen** monorepo, set `PR_FILTER_MAX_ACCEPTED_PRS` in the root `.env` (maps to `MAX_ACCEPTED_PRS`).

Typical cost per repository: about **$1–$5** depending on provider, model, and PR count; large repos cost more.

### Run without LLM (faster, static-only)

```bash
python repo_evaluator.py owner/repo \
  --skip-quality-checks \
  --skip-quality-llm \
  --skip-taxonomy \
  --skip-pr-rubrics
```

---

## Usage

### Basic

```bash
# GitHub (owner/repo) — GH_TOKEN read from .env
python repo_evaluator.py curl/curl

# Full GitHub URL
python repo_evaluator.py https://github.com/microsoft/vscode

# Bitbucket — BITBUCKET_TOKEN in .env
python repo_evaluator.py bitbucket:owner/repo --platform bitbucket

# GitLab (supports group/subgroup/repo paths) — GITLAB_TOKEN in .env
python repo_evaluator.py gitlab:group/subgroup/repo --platform gitlab

# Local clone (no auto-clone)
python repo_evaluator.py owner/repo --repo-path /path/to/repo
```

Repo string formats: `owner/repo`, `github:owner/repo`, `bitbucket:owner/repo`, `gitlab:group/.../repo`, or a full HTTPS URL. Platform defaults to **`auto`** (`github` if ambiguous).

### Useful flags

| Flag | Description |
|------|-------------|
| `--json` | Print JSON to stdout (and still write sidecar files unless you only use stdout) |
| `--output PATH` | Write JSON to `PATH`; CSV and PR URL lists go beside it |
| `--max-prs N` | Cap how many PRs to analyze |
| `--start-date YYYY-MM-DD` | Ignore PRs merged before this date |
| `--pr-number N` | Analyze a single PR |
| `--min-test-files`, `--max-non-test-files`, `--min-code-changes` | Override PR filters (defaults from `eval_kit/constants.py`) |
| `--skip-quality-checks` | Skip vibecode / security / production quality |
| `--skip-quality-llm` | Quality checks use static analysis only |
| `--skip-taxonomy` | Skip xAI taxonomy classification |
| `--skip-pr-rubrics` | Skip LLM benchmark rubrics on accepted PRs |
| `--taxonomy-concurrency N` | Parallel taxonomy calls (default: `LLM_CONCURRENCY` or 4) |
| `--platform auto\|github\|bitbucket\|gitlab` | Force platform |

Default PR filter constants (override with CLI flags):

- `MIN_TEST_FILES` = 0  
- `MAX_NON_TEST_FILES` = 100  
- `MIN_PR_CODE_CHANGES` = 1  
- `MAX_TEST_FILES` = 15  
- `MAX_CHANGED_FILES` = 50  
- Linked issue word count: **10–6000** words  
- Up to **50** rubric goal PRs by default (`MAX_ACCEPTED_PRS` / `PR_FILTER_MAX_ACCEPTED_PRS` in `.env`)

---

## Output

Every successful run writes files under `./output/` (default mode) or next to `--output` when you pass `--json --output PATH`.

For a repo like `curl/curl`, expect these files:

| File | Example path | Contents |
|------|--------------|----------|
| Full report JSON | `./output/curl__curl.json` | Complete evaluation: repo metrics, PR analysis, rubric scores, taxonomy, quality checks, etc. |
| CSV export | `./output/curl.csv` | One-row CSV summary for Lazarus submission (repo name only in the filename, not `owner__repo`). |
| Rubric-accepted PR URLs | `./output/curl__curl_accepted.txt` | One PR URL per line — PRs that passed **both** structural filters and LLM rubrics with status `accepted` (issue, patch, and tests all score well enough). |
| Rubric-partially-accepted PR URLs | `./output/curl__curl_partially_accepted.txt` | One PR URL per line — PRs with rubric status `partially_accepted` (no test diff in the PR, but issue and gold-patch rubrics pass). Useful for downstream task generation when test changes are absent. |

The `_accepted.txt` and `_partially_accepted.txt` files are always written alongside the JSON/CSV. They are populated only when PR rubrics run (default); with `--skip-pr-rubrics` both files are created but empty.

With `--json` alone (no `--output`), JSON and sidecar files go to a timestamped directory: `./output_repos_batch_YYYYMMDD_HHMMSS/`.

With `--json --output report.json`, JSON goes to `report.json` and the CSV plus PR URL lists are written beside it (same directory).

Human-readable summary is printed to the terminal when `--json` is not set.

Submit results: upload the CSV via [lazarus.turing.com](https://lazarus.turing.com/) with the information they request. Use the `*_accepted.txt` and `*_partially_accepted.txt` lists as input for downstream task generation (for example with `swe-bench-taskgen` in this monorepo).

---

## Supported languages and test runners

The evaluator **auto-detects** a runner from `eval_kit/test_runners/`. Install the commands for your repo’s language before running.

| Language(s) | Detected runners (priority order) | Typical commands on `PATH` |
|-------------|-----------------------------------|----------------------------|
| Python | pytest, unittest | `python` |
| JavaScript / TypeScript | Jest, Vitest, Mocha, `node:test` | `node`, `npm` |
| Go | `go test` | `go` |
| Rust | `cargo test` | `cargo` |
| Java | Gradle, Maven | `gradle` or `gradlew`, `mvn`, `java` |
| Scala / Kotlin | sbt, Gradle, Maven | `sbt`, `gradle`, `mvn` |
| Ruby | RSpec, Minitest | `ruby`, `bundle` |
| PHP | Pest, PHPUnit | `php`, `composer` |
| C / C++ | Google Test, CMake/CTest, Make | `cmake`, `ctest`, `make`, `g++` |
| C# | .NET Framework (MSBuild), `dotnet test` | `msbuild` (Windows), `dotnet` |
| COBOL | cobol-check | `cobc`, `java` |

If no runner is detected or the runtime is missing, test analysis is skipped and a warning is logged; the repo may score poorly on test-related criteria.

**Example:** [curl/curl](https://github.com/curl/curl) is primarily C with `CMakeLists.txt` — install **CMake** (and usual C build deps) before evaluating.

---

## JavaScript / TypeScript test configuration

For Jest-based repos, optional project-root files improve compatibility:

| File | Purpose |
|------|---------|
| `repo_evaluator_test_env.json` | Extra env vars during tests (always includes `CI=true`) |
| `repo_evaluator_write_empty_json_files.txt` | Paths to create as `{}` if missing |

Environment overrides (instead of files):

- `REPO_EVAL_TEST_ENV_JSON` — JSON object of env vars  
- `REPO_EVAL_WRITE_EMPTY_JSON_FILES` — comma-separated paths  

---

## Why PRs are rejected

Common `rejection_breakdown` keys from PR analysis:

| Key | Meaning |
|-----|---------|
| `fewer_than_min_test_files` | Not enough test files in the PR |
| `more_than_max_non_test_files` | Too many non-test files changed |
| `too_many_test_files` | More than `MAX_TEST_FILES` test files |
| `too_many_changed_files` | More than `MAX_CHANGED_FILES` code files |
| `code_changes_not_sufficient` | Source changes below `--min-code-changes` |
| `difficulty_not_hard` | PR classified as not hard enough |
| `issue_is_a_pr` | Linked “issue” is another PR |
| `issue_is_not_closed` | Linked issue not closed |
| `issue_word_count` | Issue body outside 10–6000 words |
| `content_not_in_english` | Title/body may not be English |
| `rust_embedded_tests` | Rust sources contain embedded `#[test]` |
| `merge_date` | Merged before `--start-date` |
| `bot_pr` | Author is a bot |
| `creation_date` | Missing PR `createdAt` |
| `full_patch_retrieval` | Could not fetch full diff |
| `pr_processing_error` | Exception while processing PR |

Feature-PR analysis may add separate reasons in `feature_rejection_breakdown`.

---

## API tokens

### GitHub

1. Settings → Developer settings → Personal access tokens  
2. Create a token with **`repo`** scope (and `read:org` if needed)  
3. Add `GH_TOKEN=...` to `.env`

### Bitbucket

Repository **Access tokens** with read access; add `BITBUCKET_TOKEN=...` to `.env` and use `--platform bitbucket`.

### GitLab

Personal or project access token with API read access; add `GITLAB_TOKEN=...` to `.env`, use `gitlab:group/.../repo`, and `--platform gitlab`.

---

## Project layout

```
repo-eval-pr-filtering-kit/
  README.md
  repo_evaluator.py          # CLI entry point
  requirements.txt
  .env.example
  eval_kit/
    constants.py               # PR thresholds
    llm_client.py              # LLM providers and retries
    platform_clients.py        # GitHub / Bitbucket / GitLab
    repo_evaluator_helpers.py
    test_runners/              # Per-language test detection and execution
    enterprise_signals/        # PR/repo risk collectors
    task_taxonomy/             # PR taxonomy classification
  tests/
  output/                      # Example evaluator outputs (optional)
```

---

## Development

```bash
pip install -r requirements.txt
pip install pytest pytest-cov
PYTHONPATH=. pytest
```

---

## Limitations

- Requires network access to platform APIs  
- Large repos with many PRs are slow and LLM-heavy  
- Detection heuristics favor common layouts and naming conventions  
- Bitbucket/GitLab parity with GitHub is good but not identical  
- Cloned repos are removed after the run unless you pass `--repo-path`  
- Default mode always uses LLM unless you pass the `--skip-*` flags above  

---

## License

See repository license file. Respect upstream licenses for evaluated repositories.
