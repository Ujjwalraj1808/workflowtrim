# WorkflowTrim

Automated detection and repair of resource-waste smells in GitHub Actions CI/CD workflows.

WorkflowTrim downloads the `.github/workflows/*.yml` files of a GitHub repository and checks them against a catalogue of eight resource-waste smells: configuration patterns that are valid and functional but cause compute minutes to be consumed without contributing to the workflow's purpose.

**Status:** v0.5 — detector (v0.4 rules), dataset of 1,000 searched / 817 included repositories, RQ2 detector evaluation, automated repair with four safety checks, savings estimation from run history (S1 and S2), and a web UI. v0.5 repair changes: S2 gives non-pull-request runs their own concurrency group, S3 picks the cache from the install command and skips unsafe cases, S5 guards scheduled runs only, S6 is skipped when `push` already has another ref filter, invalid YAML is left unchanged.

## Smell catalogue

| ID | Smell | Why it wastes resources | Repair |
|----|-------|-------------------------|--------|
| S1 | Missing job timeout | A hung job runs for up to 360 minutes (GitHub default) | add `timeout-minutes` = max(10, 1.5 × P95 of the file's successful job durations); needs 20+ successful jobs in the run history, otherwise detect-only |
| S2 | No PR run cancellation | Superseded pull-request runs keep executing after newer commits are pushed | add `concurrency` with group `${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref \|\| github.run_id }}` and `cancel-in-progress: ${{ github.event_name == 'pull_request' }}`: each PR shares one group, every other run gets its own, so push runs (deploys, releases) are neither cancelled nor queued behind each other |
| S3 | Missing dependency cache | Dependencies are downloaded and installed from scratch on every run | add the `cache:` input that matches the job's install command: `npm ci` → npm, yarn with `--frozen-lockfile`/`--immutable` → yarn, `pnpm install` after `pnpm/action-setup` → pnpm, `pip install -r <file>` → pip + `cache-dependency-path`, `mvn`/`mvnw` → maven, `gradle`/`gradlew` → gradle, `go build/test/…` → true. Skipped when the command does not imply the lock file (setup-node fails without it) or `cache:` is set explicitly |
| S4 | No path filtering | Full pipeline runs even for documentation-only changes | detect only: a workflow skipped by `paths-ignore` leaves required checks "Pending" and blocks merging |
| S5 | Unguarded scheduled run on forks | Scheduled jobs run on forks that enable scheduled workflows (disabled by default on forks; the minutes are the fork's) | add `if: github.event_name != 'schedule' \|\| github.repository == 'owner/repo'` to jobs without an `if` (push / pull_request runs in forks keep running) |
| S6 | Duplicate triggers | `push` and `pull_request` both fire for the same commit | restrict `push` to the default branch; skipped when `push` already has `branches-ignore`, `tags` or `tags-ignore` (adding `branches` would be invalid or would start runs on every default-branch push) |
| S7 | Unnecessary full clone | `fetch-depth: 0` downloads the entire git history | detect only (manual review) |
| S8 | Over-long artifact retention (storage) | Artifacts are stored for 90 days by default; this wastes storage, not compute minutes | set `retention-days: 7` |

## Installation

Requires Python 3.10+.

```
pip install -r requirements.txt
```

A GitHub personal access token is required (unauthenticated requests are limited to 60 per hour). Create one at https://github.com/settings/tokens with no scopes selected, then set it as an environment variable:

```
# Windows CMD
set GITHUB_TOKEN=ghp_xxxxx

# Windows PowerShell
$env:GITHUB_TOKEN="ghp_xxxxx"

# macOS / Linux
export GITHUB_TOKEN=ghp_xxxxx
```

## Usage

Analyse a single repository:

```
python -m workflowtrim owner/repo
python -m workflowtrim owner/repo --json
```

Example output:

```
pallets/flask: 5 workflow file(s), 17 finding(s)

S1  tests.yaml                   tests                no timeout-minutes (default 360 min)
S3  tests.yaml                   tests                actions/setup-python without cache input
S4  pre-commit.yaml              on.push              no paths / paths-ignore filter
```

## Web UI

```
streamlit run app.py
```

Enter a repository, click **Analyze**: summary metrics, a smell distribution chart, the findings table, per-file repairs shown as a unified diff with four safety checks (repaired YAML parses, the reported smells are gone, jobs and steps are unchanged, no actionlint error that the original did not already have), and the estimated waste from the repository's run history (see below). Each fixed file can be downloaded.

## Savings estimation

`workflowtrim/savings.py` estimates the compute minutes and cost that the detected smells caused, from the repository's real GitHub Actions run history:

- **Data:** the most recent 300 completed runs from the last 90 days (`GET /actions/runs`), and the jobs of each run (`GET /actions/runs/{id}/jobs`). Job-level `started_at` / `completed_at` are used because the run-level `updated_at` is not a completion time (it changes, for example, when a pull request is closed).
- **S2:** for consecutive `pull_request` runs of the same workflow file on the same branch, the job-minutes of the earlier run that were still executing after the next run started, i.e. what the S2 repair would have stopped.
- **S1:** per workflow file, timeout = max(10, ceil(1.5 × P95 of successful job durations)), from at least 20 successful jobs (the same value the S1 repair writes). For failed, cancelled or timed-out jobs longer than it, the minutes beyond it. The multiplier is a parameter (`estimate_from_runs(..., multiplier=...)`) for sensitivity analysis.
- Only workflow files in which the smell was detected are counted.
- **Cost:** minutes × GitHub-hosted runner rate by OS, taken from the job labels (Linux $0.008, Windows $0.016, macOS $0.08 per minute).
- S3, S4 and S5 are not estimated: they need data the run history does not provide (per-run changed files, fork runs, install-step timings).

Jobs are fetched with 8 parallel requests. In the web UI, results are cached on disk per repository per day, so analysing the same repository again on the same day is instant, also after a Streamlit restart (clear with **⋮ → Clear cache**).

## Building a dataset

Two strata per language, 100 searched repositories each (5 languages x 2 strata = 1,000 searched):

```
python collect.py 100 Python popular          # stars >= 100, most-starred first
python collect.py 100 Python less-popular     # stars 100..1000, most recently updated first
# ... repeat both lines for JavaScript, TypeScript, Go, Java
python summarize.py                           # dataset construction, summary, prevalence tables
```

All repositories were pushed in 2026 and are neither archived nor forks. `collect.py` writes `repos.csv` (every searched repository, with an `excluded` reason if it is left out) and `findings.csv` (one row per finding). Exclusion reasons: `no_build_manifest` (no build/package manifest for the language in the repository root, e.g. curated lists and documentation-only repositories) and `no_workflows`. `summarize.py` analyses only included repositories and reports the exclusions. The script can be re-run safely (repositories already in `repos.csv` are skipped) and waits for the API rate-limit reset instead of stopping; the full 1,000 takes a few hours.

## RQ2: detector accuracy

```
python rq2_sample.py     # once: draws the fixed sample (seed 2026) and writes rq2/annotator_A.xlsx, rq2/annotator_B.xlsx
python rq2_score.py      # after labelling: kappa + agreement, disagreements, then precision / recall / F1
```

The sample has 100 random workflow files (sheet `recall`, every smell labelled Y/N without seeing the detector output) and 50 detector findings per smell (sheet `precision`, labelled TP/FP). Sampled files are saved in `rq2/files/` so labels refer to a fixed snapshot. Two annotators label independently following [ANNOTATION.md](ANNOTATION.md); disagreements are resolved in `rq2/resolved.csv`.

## RQ3: repair correctness

```
python rq3_snapshot.py               # once: snapshot of every included repository's workflow files (needs git)
python rq3_repair.py                 # repairs every file, runs the safety checks, writes the RQ3 tables
python rq3_repair.py --minutes 9     # same, stopping after 9 minutes; run again to continue
```

`rq3_snapshot.py` makes a sparse, shallow clone of each included repository and copies `.github/workflows/*.yml|yaml` to `rq3/files/`; `rq3/snapshot.csv` records the commit SHA and date of each snapshot. `rq3_repair.py` repairs each smell in isolation and all smells together (S1 with a fixed 30-minute timeout, since no run history is used here; S4 and S7 are detect-only), and checks that the result parses, the repaired smell is gone, jobs and steps are unchanged, actionlint reports no new error, a second repair changes nothing and no new smell appears. Progress is saved in `rq3/progress.jsonl`, so the run can be stopped and continued.

Outputs: `rq3/findings.csv` (every finding: repaired, skipped with reason, or detect-only), `rq3/repairs.csv` (checks and diff size per file and scope) and `rq3/review.xlsx` (a fixed random sample of 20 diffs per repairable smell for manual review, see [rq3/REVIEW.md](rq3/REVIEW.md)).

## Running the tests

```
python -m pytest -q
```

## Project structure

```
workflowtrim/
  __main__.py   command-line entry point
  fetch.py      downloads workflow files via the GitHub REST API
  rules.py      one detection function per smell (S1-S8)
  repair.py     one repair function per repairable smell; ruamel.yaml round-trip keeps comments and formatting
  savings.py    waste estimation (S1, S2) from run history
app.py          Streamlit web UI (analyze + repair + savings)
collect.py      dataset collection (strata, exclusion criteria)
review.csv      manual review of the automatic exclusions (repo, excluded, note)
rq2_sample.py   RQ2 sample and labelling workbooks
rq2_score.py    RQ2 agreement and precision / recall / F1
ANNOTATION.md   RQ2 annotation protocol
rq3_snapshot.py RQ3 snapshot of the dataset's workflow files
rq3_repair.py   RQ3 repair coverage, safety checks and review sample
summarize.py    prevalence tables from the CSVs
test_rules.py   detector tests
test_repair.py  repairer tests
test_savings.py savings estimator tests
test_collect.py exclusion rule tests
test_rq2.py     kappa tests
```

## Authors

Ujjwal Raj 
Guide: Dr. Arvind Kumar  
School of Computer Application and Technology, Galgotias University
