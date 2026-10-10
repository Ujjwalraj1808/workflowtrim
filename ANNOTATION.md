# RQ2 annotation protocol

Fixed before labelling starts; do not change it while labelling. Two annotators label the same sample independently and do not discuss items until both have finished.

Clarification added 2026-10-09, after annotator B's first pass: section "First check the precondition" below. It makes explicit what the definitions table already required; no definition was changed. Annotator B's first-pass workbook is kept as `rq2/annotator_B_pass1.xlsx`.

## Material

`python rq2_sample.py` writes `rq2/annotator_A.xlsx` and `rq2/annotator_B.xlsx` (identical) and a snapshot of every sampled workflow file in `rq2/files/`. **Label the snapshot file** (column `local_file`), not the live repository, which may have changed. Do not open `rq2/tool_recall.csv`.

1. **Sheet `recall` first** (100 random workflow files). For each file and each smell S1–S8, write `Y` if the smell is present anywhere in the file by the definition below, otherwise `N`. You have not seen the detector's output for these files; judge from the YAML only.
2. **Then sheet `precision`** (50 detector findings per smell). For each finding, write `TP` if the reported smell is really present at the reported location by the definition below, otherwise `FP`.
3. Use `note` for anything unclear (one short sentence). Aim for about 1–2 minutes per item; if an item needs outside knowledge you do not have, decide from what the file shows and write a note.

## First check the precondition

Every smell needs something to be in the file before it can be present. **If the precondition is missing, the label is `N` (recall) or `FP` (precision)**, whatever else the file contains.

| Smell | Precondition: the file must contain |
| --- | --- |
| S1 | at least one job with `steps:` (not only reusable-workflow jobs with job-level `uses:`) |
| S2 | `pull_request` under `on:` |
| S3 | a step that downloads or installs dependencies (npm / yarn / pnpm / pip / poetry / uv / Maven / Gradle / Go modules …) |
| S4 | `push` or `pull_request` under `on:` |
| S5 | `schedule` under `on:` |
| S6 | both `push` and `pull_request` under `on:` |
| S7 | `actions/checkout` with `fetch-depth: 0` |
| S8 | `actions/upload-artifact` |

Only when the precondition holds, apply the "Present" / "Not present" columns of the definitions table.

## Definitions

A smell is present when the workflow, as written, wastes runner compute or storage in the way described **and** nothing else in the file prevents that waste. Only what is visible in the file counts; repository settings you cannot see are ignored (note them if relevant).

| Smell | Present (Y / TP) when | Not present (N / FP), for example |
| --- | --- | --- |
| S1 Missing job timeout | A job that runs steps on a runner has no `timeout-minutes`, so a hung job can run up to 360 minutes. | The job is a reusable-workflow call (`uses:` at job level); every step of the job has its own `timeout-minutes`. |
| S2 No PR run cancellation | The workflow runs on `pull_request` events that can supersede each other (new commits to the PR), and superseded runs are not cancelled. | `concurrency` with `cancel-in-progress` covers the PR runs (workflow level, or on every job); a cancel action (e.g. `styfle/cancel-workflow-action`) does it; `pull_request.types` contains only events that cannot repeat for new commits (e.g. `closed`, `labeled`, `opened`). |
| S3 Missing dependency cache | A job downloads or installs dependencies with a package manager / build tool (npm, yarn, pnpm, pip, poetry, Maven, Gradle, Go modules) and caches nothing. | A cache is in place: `actions/cache`, the setup action's `cache:` input, or caching that the setup action does by default (e.g. `actions/setup-go` v4 or later caches by default); the job installs nothing (setup action used only to run a tool); dependencies come pre-installed in the container image. |
| S4 No path filtering | A `push` / `pull_request` trigger has no `paths` / `paths-ignore`, and the workflow does work that is pointless when only documentation changes (build, test, lint of code). A `branches` filter is not a path filter. | The workflow must run on every change (e.g. checks all files: spelling, links, licence, security scan; labeller; CLA; release; docs build). |
| S5 Unguarded scheduled run on forks | The workflow has a `schedule` trigger and its jobs have no condition that stops them in forks. | A job condition that excludes forks, e.g. `github.repository == 'owner/repo'`, `github.repository_owner == 'owner'`, `github.event.repository.fork == false`, including on a job that all other jobs depend on (`needs:`). |
| S6 Duplicate triggers | Both `push` and `pull_request` are enabled and `push` is not limited, so a commit to a PR branch in the same repository runs the workflow twice. | `push` is limited to some branches (`branches`, `branches-ignore`) or to `tags` only; the two triggers run different jobs (job-level `if` on `github.event_name`). |
| S7 Unnecessary full clone | `actions/checkout` uses `fetch-depth: 0` and nothing in the job needs the full git history. | The job uses history: version or changelog from git (`git describe`, setuptools-scm, GitVersion, nbgv, semantic-release, release-please, changesets, goreleaser), commit or diff checks over many commits (commitlint, changed-files against the base branch, `git diff base...HEAD`, gitleaks history scan), SonarQube, `git log` / `git blame` based tools. |
| S8 Over-long artifact retention | `actions/upload-artifact` keeps artifacts for more than 30 days (no `retention-days` means the 90-day default). | The artifact is clearly meant to be kept long (release or nightly builds published for users, compliance reports), or `retention-days` is 30 or less. |

## After labelling

1. `python rq2_score.py` prints Cohen's kappa and % agreement and writes the disagreements to `rq2/disagreements.csv`.
2. Both annotators discuss each disagreement using the definitions above, fill in `label`, and save the file as `rq2/resolved.csv`. If a definition turns out to be ambiguous, record it in the paper's threats; do not change the labels you already agreed on.
3. `python rq2_score.py` again prints precision, recall and F1 per smell.
