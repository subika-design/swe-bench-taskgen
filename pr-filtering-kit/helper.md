# Lazarus repo evaluator — helper notes

Project: [Lazarus](https://lazarus.turing.com/) (Turing **Repository Evaluator** in this kit).

---

## Evaluation steps (what the tool does, in order)

These steps match **`repo_evaluator.py`** / **`main()`**. Some steps can be skipped with flags (see README).

### A. Inside `RepoEvaluator.evaluate()` (PR pipeline)

PRs are fetched in **adaptive batches** until internal targets are met or limits hit. **Per batch:**

1. **Repository-level analysis** (first call only)  
   Scans the tree: languages, structure, tests/CI signals, activity, issue counts via API, etc.

2. **PR gate (hard filters)**  
   Each candidate PR is checked against fixed rules (tests touched, file counts, linked issue closed, not a bot, issue word count, enough code change, patch retrieval, etc.). Failures log a **rejection reason** (see table below).

3. **Feature PR classification** (runs on every gate-passing PR in the same pass)  
   **Separate from rubrics.** The tool scores whether the change looks like a substantial **feature-style** task (`classify_feature_pr`). Results feed **`feature_accepted` / `feature_accepted_prs`** and rejection counts—they **do not remove** a PR from the gate-pass list.

4. **PR rubrics / fairness (LLM)** (unless **`--skip-pr-rubrics`**)  
   Runs **`QualityEvaluator`** on each PR in **`accepted_prs`** (gate passers). Produces per-PR scores and **`rubric_accepted`** (see [PR rubrics](#pr-rubrics-benchmark-quality)). This is **not** the same as feature classification.

### B. After `evaluate()` returns (`main()`)

Runs **after** the report object is built; quality is **not** interleaved with the PR gate inside `evaluate()`.

6. **Quality checks (LLM + static)** (unless **`--skip-quality-checks`**)  
   **Repository-level** pass on the clone: vibecode, security, production quality, plus static variants (`run_all_quality_checks`). Uses **`--skip-quality-llm`** to skip LLM portions only. This does **not** decide which PRs pass the gate or rubrics.

7. **Taxonomy** (unless **`--skip-taxonomy`**)  
   Classifies **`accepted_prs`** from the report (xAI taxonomy).

8. **Fairness evaluator** (conditional)  
   If rubrics ran and there are entries with **`rubric_accepted`**, an extra fairness pass may run on that subset (see `main()` after taxonomy).

9. **Report**  
   Console summary and **`--json`** / **`--output`** / CSV-style fields. For Lazarus submission, follow their site instructions.

**Useful CLI knobs:** `--max-prs`, `--start-date`, `--pr-number`, `--skip-quality-checks`, `--skip-quality-llm`, `--skip-taxonomy`, `--skip-pr-rubrics`.

---

## What the counts mean (don’t conflate these)

| Concept | Meaning |
| -------- | ------- |
| **`pass_first_filter` / gate pass** | Count of PRs that passed **hard filters only** (and sit in **`pass_first_filter_prs`** in JSON when emitted). |
| **`accepted` / `accepted_prs`** | PRs that passed **hard filters** (same set rubrics run on). |
| **`feature_accepted`** | How many gate-passing PRs the **feature classifier** labeled as feature-like. Independent of **`rubric_accepted`**. |
| **`rubric_accepted`** | Rubric outcome: **`accepted`**, **`partially_accepted`** (no test diff but non-test rubrics pass), or **`rejected`**. Goal counting uses accepted + partially_accepted. |

---

## Feature classification vs PR rubrics

| | **Feature classification** | **PR rubrics (`QualityEvaluator`)** |
| --- | --- | --- |
| **Purpose** | Tag PRs as “feature-like” for reporting / taxonomy-style signals. | Judge whether the **issue + patch + tests** form a **good benchmark** (clarity, alignment, tests too strict/loose). |
| **Drops PRs from `accepted_prs`?** | No. | No—**`rubric_accepted`** is a status string on the row; it does not delete the PR from lists. |
| **Where** | Inside **`PRAnalyzer.analyze_prs`**, right after a PR passes the gate. | **`_run_pr_rubrics`**, on gate **`accepted_prs`**. |

---

## PR rubrics (benchmark quality)

Rubrics use an LLM with the merged **problem statement**, **source diff**, and **test diff** (`eval_kit/quality_evaluator.py`). **Lower scores are better** (0–3). Summary of each **trimmed** dimension stored on the report:

| Rubric key | What it measures |
| ---------- | ---------------- |
| **`issue_clarity`** | Is the **problem statement** explicit about expected behavior and acceptance criteria? 0 = fully clear … 3 = extremely unclear (e.g. nothing actionable without external context). |
| **`gold_patch_clarity`** | How **readable and structured** is the **production code change** (the “gold” patch)? 0 = clear … 3 = unreadable / chaotic. |
| **`test_clarity`** | Are **tests** easy to read and intent obvious (names, setup, what is asserted)? 0 = clear … 3 = unclear noise. |
| **`gold_patch_to_issue_alignment`** | Does the **code patch** match the issue: scope and substance (atomic vs under/over-scoped vs unrelated)? Same scale as “patch-to-issue alignment” in the prompt. |
| **`test_to_issue_alignment`** | Do **tests** actually validate what the issue asks for, without missing the core behavior or testing the wrong thing? 0 = perfect alignment … 3 = poor / unrelated. |
| **`false_negatives`** | Risk that **tests reject valid alternative implementations** (over-specific assertions). 0 = generalized … 3 = only near-identical solutions pass. |
| **`false_positives`** | Risk that **broken or incomplete solutions still pass**. 0 = strict enough … 3 = overly permissive. |

**`rubric_accepted` statuses** (see `_rubric_acceptance_status` in **`repo_evaluator.py`**):

- **`accepted`**: all scored rubric dimensions pass (no score 3, at most two scores of 2).
- **`partially_accepted`**: no test diff (`tests_missing`); only issue + gold-patch rubrics are scored and they pass the same rule. Test rubrics are omitted, not forced to 3.
- **`rejected`**: rubric rule failed, or evaluation error.

**Note:** The full LLM prompt may also score **`task_difficulty`**; trimmed rubrics omit test dimensions when there is no test diff.

---

## PR rejection reasons (plain language)

| Reason                        | Simple meaning                                                         |
| ----------------------------- | ---------------------------------------------------------------------- |
| `issue_is_not_closed`         | The linked issue is still open; they want it closed first.             |
| `fewer_than_min_test_files`   | The PR doesn’t touch enough test files (by their rules).               |
| `difficulty_not_hard`         | The PR is too small (not enough files changed).                        |
| `too_many_test_files`         | The PR touches too many test files (too big / noisy).                  |
| `bot_pr`                      | The PR is from a bot, not a human.                                     |
| `issue_word_count`            | The linked issue text is too short or too long (or the link is wrong). |
| `code_changes_not_sufficient` | The PR barely changes code (e.g. almost empty diff).                   |

More reasons are documented in the kit **`README.md`** (“Why PRs Get Rejected”).
