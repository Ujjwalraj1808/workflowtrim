"""Additional analyses reported in the journal version.

  - RQ1 co-occurrence: distinct smells per parseable workflow file and per repository, and the most frequent
    pairs of smells in one file (included repositories, findings.csv).
  - RQ4 uncertainty: per-repository waste shares and a bootstrap interval (10,000 resamples of repositories,
    seed 2026) for the pooled share and for the mean of the shares (rq4/savings.csv, filter smells_unchanged,
    multiplier 1.5), and a Mann-Whitney U test of the per-repository shares between the strata.

Run:  python jss_extra.py
"""
import collections
import csv
import itertools
import os
import statistics

import numpy as np
from scipy.stats import mannwhitneyu

import summarize


def cooccurrence():
    _, repos, findings = summarize.load_dataset()
    n_files = sum(int(r["n_workflows"]) - int(r["n_unparseable"]) for r in repos)
    by_file, by_repo = collections.defaultdict(set), collections.defaultdict(set)
    for f in findings:
        by_file[(f["repo"], f["file"])].add(f["smell"])
        by_repo[f["repo"]].add(f["smell"])
    per_file = [len(v) for v in by_file.values()] + [0] * (n_files - len(by_file))
    per_repo = [len(by_repo.get(r["repo"], ())) for r in repos]
    pairs = collections.Counter(p for v in by_file.values() for p in itertools.combinations(sorted(v), 2))
    print(f"RQ1 co-occurrence ({n_files} parseable files, {len(repos)} repositories)")
    print(f"  files with at least one smell: {n_files - per_file.count(0)} "
          f"({100 * (n_files - per_file.count(0)) / n_files:.1f}%)")
    print(f"  distinct smells per file: median {statistics.median(per_file):g}, mean {statistics.mean(per_file):.2f}, "
          f"three or more in {100 * sum(x >= 3 for x in per_file) / n_files:.1f}% of files")
    print(f"  distinct smells per repository: median {statistics.median(per_repo):g}; "
          f"distribution {sorted(collections.Counter(per_repo).items())}")
    for (a, b), k in pairs.most_common(6):
        print(f"  {a}+{b} in one file: {k} files ({100 * k / n_files:.1f}%)")


def savings_uncertainty():
    with open(os.path.join("rq4", "savings.csv"), newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["filter"] == "smells_unchanged" and r["multiplier"] == "1.5"]
    jm = np.array([float(r["job_minutes"]) for r in rows])
    waste = np.array([float(r["waste_minutes"]) for r in rows])
    share = 100 * waste / jm
    rng = np.random.default_rng(2026)
    pooled, mean = [], []
    for _ in range(10000):
        i = rng.integers(0, len(rows), len(rows))
        pooled.append(100 * waste[i].sum() / jm[i].sum())
        mean.append(share[i].mean())
    lo, hi = np.percentile(pooled, [2.5, 97.5])
    mlo, mhi = np.percentile(mean, [2.5, 97.5])
    pop = [s for s, r in zip(share, rows) if r["stratum"] == "popular"]
    less = [s for s, r in zip(share, rows) if r["stratum"] != "popular"]
    print(f"\nRQ4 uncertainty ({len(rows)} repositories)")
    print(f"  pooled share {100 * waste.sum() / jm.sum():.2f}% [bootstrap 95% {lo:.2f}, {hi:.2f}]")
    print(f"  per-repository share: median {np.median(share):.2f}%, 75th percentile {np.percentile(share, 75):.2f}%, "
          f"mean {share.mean():.2f}% [bootstrap 95% {mlo:.2f}, {mhi:.2f}], max {share.max():.2f}%")
    print(f"  strata, Mann-Whitney U on per-repository shares: p = {mannwhitneyu(pop, less).pvalue:.2f}")


if __name__ == "__main__":
    cooccurrence()
    savings_uncertainty()
