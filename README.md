# swe-bench-taskgen

Monorepo pipeline: **`owner/repo` → filtered PRs → SWE-rebench JSONL tasks**.

## Quick start (3 commands)

```bash
cp .env.example .env          # edit once: GH_TOKEN + PR_FILTER_* + TASKGEN_* keys
./setup.sh                      # install both subprojects
./run_pipeline.sh curl/curl     # run pipeline
```

Preflight before spending on LLM calls:

```bash
./run_pipeline.sh --check
```

## Pipeline

```text
owner/repo
    │
    ▼  pr-filtering-kit  (PR_FILTER_* LLM in .env)
    ▼
output/owner__repo/accepted.txt
    │
    ▼  pr-to-swe-task-jsonl  (TASKGEN_* LLM in .env)
    ▼
output/owner__repo/tasks.jsonl
```

## Output layout

Each run writes to **`output/owner__repo/`**:

| File | Contents |
|------|----------|
| `report.json` | Full PR filtering evaluation |
| `accepted.txt` | Rubric-accepted PR URLs |
| `partially_accepted.txt` | Partially accepted PR URLs |
| `tasks.jsonl` | Final SWE-rebench tasks |
| `owner.csv` | CSV summary (from report sidecar) |

## Configuration (single `.env`)

Copy `.env.example` to `.env` at the **monorepo root only**. No per-subfolder `.env` copies.

| Prefix | Used by | Purpose |
|--------|---------|---------|
| `GH_TOKEN` | Both | GitHub API + `gh` CLI |
| `PR_FILTER_*` | pr-filtering-kit | Rubrics, taxonomy, quality checks |
| `PR_FILTER_MAX_ACCEPTED_PRS` | pr-filtering-kit | Stop after N rubric goal PRs (default: 50) |
| `TASKGEN_*` | pr-to-swe-task-jsonl | Patch split, install recipe, Docker remediation |

**Use different providers per step** — no key confusion:

```bash
PR_FILTER_LLM_PROVIDER=openai
PR_FILTER_OPENAI_API_KEY=sk-...

TASKGEN_LLM_MODEL=claude-opus-4-6
TASKGEN_ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
# Standard run
./run_pipeline.sh owner/repo

# Include partially-accepted PRs when accepted list is empty
./run_pipeline.sh owner/repo --include-partially-accepted

# Forward flags to task generator
./run_pipeline.sh owner/repo -- --language rust --docker-timeout 10800

# Makefile shortcuts
make setup
make check
make pipeline REPO=curl/curl
```

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| Python 3.10+ | 3.12+ recommended for pr-filtering-kit |
| `.env` | `GH_TOKEN`, `PR_FILTER_*`, `TASKGEN_*` API keys |
| `gh` CLI | `gh auth login` for task generation |
| Docker | Running daemon for test discovery (step 2) |
| Language toolchains | As needed per repo (Rust: `cargo`, etc.) |

## Subprojects

| Directory | Role | Docs |
|-----------|------|------|
| [`pr-filtering-kit/`](pr-filtering-kit/) | PR filtering + LLM rubrics | [README](pr-filtering-kit/README.md) |
| [`pr-to-swe-task-jsonl/`](pr-to-swe-task-jsonl/) | PR URLs → JSONL tasks | [README](pr-to-swe-task-jsonl/README.md) |

## Cost and runtime

- **Step 1:** ~$1–5/repo (LLM rubrics; scales with PR count)
- **Step 2:** Docker + LLM per accepted PR (slow for large lists)

## Development

```bash
cd pr-filtering-kit && PYTHONPATH=. pytest
cd pr-to-swe-task-jsonl && pytest
```
