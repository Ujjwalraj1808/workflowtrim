# WorkflowTrim

Automated detection of resource-waste smells in GitHub Actions CI/CD workflows.

WorkflowTrim downloads the `.github/workflows/*.yml` files of a GitHub repository and checks them against a catalogue of eight resource-waste smells: configuration patterns that are valid and functional but cause compute minutes to be consumed without contributing to the workflow's purpose.

**Status:** v0.1 (detector + dataset collection). Automated repair and savings estimation are in progress.

## Smell catalogue

| ID | Smell | Why it wastes resources |
|----|-------|-------------------------|
| S1 | Missing job timeout | A hung job runs for up to 360 minutes (GitHub default) |
| S2 | No concurrency cancellation | Superseded runs keep executing after newer commits are pushed |
| S3 | Missing dependency cache | Dependencies are downloaded and installed from scratch on every run |
| S4 | No path filtering | Full pipeline runs even for documentation-only changes |
| S5 | Unguarded run on forks | Scheduled jobs execute needlessly on every fork |
| S6 | Duplicate triggers | `push` and `pull_request` both fire for the same commit |
| S7 | Unnecessary full clone | `fetch-depth: 0` downloads the entire git history |
| S8 | Over-long artifact retention | Artifacts are stored for 90 days by default |

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

## Building a dataset

```
python collect.py 500      # run the detector on the 500 most-starred active repositories
python summarize.py        # print dataset summary and smell prevalence tables
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
collect.py      dataset collection
summarize.py    prevalence tables from the CSVs
test_rules.py   unit tests
```

## Authors

Ujjwal Raj, Vaibhav Mishra, Rishav Raj  
Guide: Dr. Arvind Kumar  
School of Computer Application and Technology, Galgotias University
