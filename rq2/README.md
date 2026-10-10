# RQ2 data: detector accuracy

Labelled sample used to measure the detector's precision and recall (detector rules v0.4, `workflowtrim/rules.py`). Labelling rules: [../ANNOTATION.md](../ANNOTATION.md). Scores: `python rq2_score.py` from the repository root.

## Files

| File | Content |
| --- | --- |
| `files/` | Snapshot of the 479 sampled workflow files, downloaded on 1 Oct 2026 by `rq2_sample.py` (seed 2026). Labels refer to these snapshots, not to the live repositories. |
| `tool_recall.csv` | Detector output for the 100 recall files (Y/N per smell). Not shown to the annotators. |
| `annotator_A.xlsx` | Annotator A. Sheet `recall`: 100 files × S1–S8, Y/N. Sheet `precision`: 50 detector findings per smell, TP/FP. |
| `annotator_B.xlsx` | Annotator B, second pass (the independent labels used for Cohen's kappa). |
| `annotator_B_pass1.xlsx` | Annotator B, first pass, kept for transparency; not used (see below). |
| `annotator_B_corrected_LLM_assisted.xlsx` | Annotator B's later revision, made after the two label sets had been compared; kept as a record, not used for kappa. |
| `resolved.csv` | Final label for each of the 120 A/B disagreements (31 recall, 89 precision), agreed in discussion. |

## Process and disclosures

- Both annotators labelled the same items independently, recall sheet first (without the detector output), then precision sheet.
- Annotator A had a calibration round on the recall sheet (feedback on the protocol) before labelling the precision sheet.
- Annotator B's first pass misread the protocol (labels given where a smell's precondition was absent; pooled κ 0.35). The protocol was clarified (section "First check the precondition" in ANNOTATION.md, no definition changed) and B relabelled the full sample.
- Free-text `note` cells of annotator B were drafted with an LLM; all labels were assigned by the annotators.
- Both annotators knew the detector's rules.
- In `annotator_B.xlsx`, the `github_url` cells of R044 and R045 had been overwritten by mistake with R043's URL; they were restored to the correct URLs. Labels were not changed (they refer to the `local_file` snapshot, which was correct).
- `local_file` paths use Windows separators (`rq2\files\…`); they are informational only.

## Results (from `rq2_score.py`)

| | Recall sheet | Precision sheet |
| --- | --- | --- |
| Cohen's κ (pooled) | 0.88 | 0.40 |
| Agreement | 96% | 78% |

| Smell | Precision | Recall |
| --- | --- | --- |
| S1 | 50/50 = 1.00 | 75/75 |
| S2 | 46/50 = 0.92 | 22/22 |
| S3 | 28/50 = 0.56 | 12/13 |
| S4 | 25/50 = 0.50 | 16/16 |
| S5 | 50/50 = 1.00 | 11/11 |
| S6 | 43/50 = 0.86 | 2/2 |
| S7 | 38/50 = 0.76 | 8/8 |
| S8 | 40/50 = 0.80 | 9/9 |
| All | 320/400 = 0.80 (95% CI 0.76–0.84) | 155/156 = 0.99 |

F1 (all) = 0.89.
