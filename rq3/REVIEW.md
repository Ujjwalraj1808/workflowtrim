# RQ3 manual review protocol

The automatic checks in `rq3_repair.py` show that a repaired file parses, that the repaired smell is gone, that jobs and steps are unchanged and that actionlint reports no new error. They cannot show that the workflow still **behaves** as the developers intended. This manual review covers that.

`rq3/review.xlsx` holds a fixed random sample (seed 2026) of 20 single-smell repairs per repairable smell (S1, S2, S3, S5, S6, S8; fewer if a smell has fewer repairs), each with its unified diff. Label every row before looking at any other row's label, ideally by two reviewers independently (copy the file), then compare.

## Label

| Label | When |
| --- | --- |
| `correct` | The diff does what the smell's repair intends **and** the workflow otherwise behaves exactly as before: same triggers (except the intended S6 restriction), same jobs and steps run on the same events, same results. Formatting-only differences do not matter. |
| `incorrect` | The diff changes behaviour beyond the intended repair, or the repair could make the workflow fail at run time. Examples: a job that would now be killed during a normal run (S1), a run that should finish being cancelled (S2), a cache input that does not match the project's package manager or lock file (S3), scheduled or other runs skipped that should run (S5), the workflow no longer running for pushes it should run for (S6), an artifact that is needed for longer than 7 days (S8). Write which, in `note`. |
| `unsure` | Deciding needs information outside the file (repository settings, branch protection, release process). Write what is missing in `note`. |

## Per smell: what to look at

- **S1** (`timeout-minutes` added): in RQ3 the value is a fixed 30; judge only that the line is placed on the right job and nothing else changes. Whether 30 is long enough is answered by the history-based value in RQ4.
- **S2** (`concurrency` added or changed): pull_request runs of the same PR share a group and cancel each other; other events get their own group (`github.run_id`) and are never cancelled or queued. If the file already had a `concurrency` block, check that its group was kept.
- **S3** (`cache:` added to a setup action): the cache type must match the install command in the same job, and the file it needs (lock file, requirements file) must plausibly exist.
- **S5** (`if:` added): only scheduled runs are skipped outside the upstream repository.
- **S6** (`push.branches` added): pushes to the default branch still run; other branches are covered by `pull_request`. Incorrect if the workflow is also meant to run on pushes to other branches without a pull request (for example release branches).
- **S8** (`retention-days: 7`): incorrect if the artifact is clearly used later (release assets, nightly builds downloaded by users, reports kept for audits).

Report per smell: correct / incorrect / unsure counts, and the reasons given for `incorrect`.
