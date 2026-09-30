# WorkflowTrim

Automated detection and repair of resource-waste smells in GitHub Actions CI/CD workflows.

WorkflowTrim downloads the `.github/workflows/*.yml` files of a GitHub repository and checks them against a catalogue of eight resource-waste smells: configuration patterns that are valid and functional but cause compute minutes to be consumed without contributing to the workflow's purpose.

**Status:** v0.3 — detector, 500-repository dataset, automated repair with safety checks, savings estimation from run history (S1 and S2), and a web UI.

## Smell catalogue

| ID | Smell | Why it wastes resources | Repair |
|----|-------|-------------------------|--------|
| S1 | Missing job timeout | A hung job runs for up to 360 minutes (GitHub default) | add `timeout-minutes: 30` |
| S2 | No concurrency cancellation | Superseded runs keep executing after newer commits are pushed | add `concurrency` group with `cancel-in-progress: true` |
| S3 | Missing dependency cache | Dependencies are downloaded and installed from scratch on every run | add `cache:` input to `setup-*` actions |
| S4 | No path filtering | Full pipeline runs even for documentation-only changes | add `paths-ignore: [docs/**, '**.md']` |
| S5 | Unguarded run on forks | Scheduled jobs execute needlessly on every fork | add `if: github.repository == 'owner/repo'` |
| S6 | Duplicate triggers | `push` and `pull_request` both fire for the same commit | restrict `push` to the default branch |
| S7 | Unnecessary full clone | `fetch-depth: 0` downloads the entire git history | detect only (manual review) |
| S8 | Over-long artifact retention | Artifacts are stored for 90 days by default | set `retention-days: 7` |

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

Enter a repository, click **Analyze**: summary metrics, a smell distribution chart, the findings table, per-file repairs shown as a unified diff with three safety checks (repaired YAML parses, the reported smells are gone, jobs and steps are unchanged), and the estimated waste from the repository's run history (see below). Each fixed file can be downloaded.

## Savings estimation

`workflowtrim/savings.py` estimates the compute minutes and cost that the detected smells caused, from the repository's real GitHub Actions run history:

- **Data:** the most recent 300 completed runs from the last 90 days (`GET /actions/runs`), and the jobs of each run (`GET /actions/runs/{id}/jobs`). Job-level `started_at` / `completed_at` are used because the run-level `updated_at` is not a completion time (it changes, for example, when a pull request is closed).
- **S2:** for consecutive `push` / `pull_request` runs of the same workflow file on the same branch, the job-minutes of the earlier run that were still executing after the next run started, i.e. what `cancel-in-progress: true` would have stopped.
- **S1:** for failed, cancelled or timed-out jobs longer than 30 minutes, the minutes beyond 30 (the same default the S1 repair adds).
- Only workflow files in which the smell was detected are counted.
- **Cost:** minutes × GitHub-hosted runner rate by OS, taken from the job labels (Linux $0.008, Windows $0.016, macOS $0.08 per minute).
- S3, S4 and S5 are not estimated: they need data the run history does not provide (per-run changed files, fork runs, install-step timings).

Jobs are fetched with 8 parallel requests. In the web UI, results are cached on disk per repository per day, so analysing the same repository again on the same day is instant, also after a Streamlit restart (clear with **⋮ → Clear cache**).

## Building a dataset

```
python collect.py 100 Python      # 100 most-starred active repositories of one language
python collect.py 100 JavaScript  # ... repeat for TypeScript, Go, Java
python summarize.py               # print dataset summary and smell prevalence tables
```

`collect.py` writes `repos.csv` (one row per repository) and `findings.csv` (one row per finding). It can be re-run safely: repositories already present in `repos.csv` are skipped. With a token, 500 repositories take roughly 15-20 minutes.

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
collect.py      dataset collection
summarize.py    prevalence tables from the CSVs
test_rules.py   detector tests
test_repair.py  repairer tests
test_savings.py savings estimator tests
```

## Authors

Ujjwal Raj 
Guide: Dr. Arvind Kumar  
School of Computer Application and Technology, Galgotias University
