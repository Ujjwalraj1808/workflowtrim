"""Statistics for the paper: confidence intervals (RQ1, RQ2), popular vs less-popular comparison (RQ1)
and precision-adjusted prevalence.

- Repository shares: 95% Wilson intervals.
- File shares: 95% cluster-bootstrap intervals (repositories resampled, files of a repository are not
  independent), 2,000 resamples, seed 2026.
- Strata: per smell, Fisher's exact test on repositories with the smell, and a Mann-Whitney U test on the
  per-repository share of files with the smell (each repository counts once, whatever its number of files);
  p-values Holm-corrected over the eight smells.
- RQ2: Wilson intervals for precision and recall.
- Precision-adjusted file share = file share x precision of the smell; interval from the same bootstrap
  with the 50 precision labels resampled too. Recall is 0.99, so missed smells are not added back.

Run:  python stats.py      (needs scipy; reads repos.csv, findings.csv, review.csv, rq2/)
"""
import math
from collections import defaultdict

import numpy as np
from scipy.stats import fisher_exact, mannwhitneyu

from rq2_score import read, sheet
from summarize import SMELLS, load_dataset
import csv
import os

SEED = 2026
B = 2000
Z = 1.959964


def wilson(k, n):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    c = (p + Z * Z / (2 * n)) / (1 + Z * Z / n)
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / (1 + Z * Z / n)
    return c - h, c + h


def holm(pvals):
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    adj, running = [0.0] * len(pvals), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvals) - rank) * pvals[i]))
        adj[i] = running
    return adj


def rq2_final():
    """Final RQ2 labels (agreed label, else the resolved one), as rq2_score.py computes them."""
    a, b = read("A"), read("B")
    with open(os.path.join("rq2", "resolved.csv"), newline="", encoding="utf-8-sig") as f:
        resolved = {(r["id"], r["smell"]): r["label"].strip().upper() for r in csv.DictReader(f)}
    final = {k: (a[k] if a[k] == b[k] else resolved[k]) for k in a}
    with open(os.path.join("rq2", "tool_recall.csv"), newline="", encoding="utf-8") as f:
        tool = {(r["id"], s): r[s] for r in csv.DictReader(f) for s in SMELLS}
    return final, tool


def main():
    _, repos, findings = load_dataset()
    rng = np.random.default_rng(SEED)
    names = [r["repo"] for r in repos]
    stratum = {r["repo"]: r["stratum"] for r in repos}
    parseable = np.array([int(r["n_workflows"]) - int(r["n_unparseable"]) for r in repos])
    idx = {n: i for i, n in enumerate(names)}
    files = {s: defaultdict(set) for s in SMELLS}
    for f in findings:
        files[f["smell"]][f["repo"]].add(f["file"])
    # per repo: number of files with the smell
    k = {s: np.array([len(files[s].get(n, ())) for n in names]) for s in SMELLS}
    has = {s: k[s] > 0 for s in SMELLS}
    pop = np.array([stratum[n] == "popular" for n in names])
    n_repos, n_files = len(names), int(parseable.sum())
    boot = rng.integers(0, n_repos, size=(B, n_repos))          # same resamples for every smell

    final, tool = rq2_final()
    prec_labels = {s: np.array([v == "TP" for key, v in final.items() if sheet(key) == "precision" and key[1] == s])
                   for s in SMELLS}

    print(f"RQ1 prevalence with 95% intervals ({n_repos} repositories, {n_files} parseable files)")
    print(f"  {'smell':<6}{'repos %':>9}{'  95% CI':<17}{'files %':>9}{'  95% CI (cluster)':<20}"
          f"{'adj. files %':>13}{'  95% CI':<16}")
    for s in SMELLS:
        r_k = int(has[s].sum())
        lo, hi = wilson(r_k, n_repos)
        share = k[s].sum() / n_files
        bs = k[s][boot].sum(axis=1) / parseable[boot].sum(axis=1)
        flo, fhi = np.percentile(bs, [2.5, 97.5])
        pl = prec_labels[s]
        pb = pl[rng.integers(0, len(pl), size=(B, len(pl)))].mean(axis=1)
        adj = share * pl.mean()
        alo, ahi = np.percentile(bs * pb, [2.5, 97.5])
        print(f"  {s:<6}{100 * r_k / n_repos:>9.1f}  [{100 * lo:5.1f}, {100 * hi:5.1f}]   "
              f"{100 * share:>7.1f}  [{100 * flo:5.1f}, {100 * fhi:5.1f}]      "
              f"{100 * adj:>9.1f}  [{100 * alo:5.1f}, {100 * ahi:5.1f}]")

    print("\nPopular vs less-popular")
    mw = mannwhitneyu(parseable[pop], parseable[~pop], alternative="two-sided")
    print(f"  workflow files per repo: median {np.median(parseable[pop]):.0f} vs {np.median(parseable[~pop]):.0f}"
          f" (Mann-Whitney p = {mw.pvalue:.2g})")
    rows, p_repo, p_file = [], [], []
    for s in SMELLS:
        a1, n1 = int(has[s][pop].sum()), int(pop.sum())
        a2, n2 = int(has[s][~pop].sum()), int((~pop).sum())
        p_repo.append(fisher_exact([[a1, n1 - a1], [a2, n2 - a2]]).pvalue)
        share = np.divide(k[s], parseable, out=np.zeros(n_repos), where=parseable > 0)
        ok = parseable > 0
        p_file.append(mannwhitneyu(share[pop & ok], share[~pop & ok], alternative="two-sided").pvalue)
        rows.append((s, 100 * a1 / n1, 100 * a2 / n2, 100 * share[pop & ok].mean(), 100 * share[~pop & ok].mean()))
    hr, hf = holm(p_repo), holm(p_file)
    print(f"  {'smell':<6}{'repos pop%':>11}{'less%':>8}{'p (Holm)':>10}{'mean file share pop%':>22}{'less%':>8}{'p (Holm)':>10}")
    for (s, rp, rl, fp, fl), a, b in zip(rows, hr, hf):
        print(f"  {s:<6}{rp:>11.1f}{rl:>8.1f}{a:>10.3g}{fp:>22.1f}{fl:>8.1f}{b:>10.3g}")

    print("\nRQ2 with 95% Wilson intervals")
    tp_all = n_all = 0
    for s in SMELLS:
        pl = prec_labels[s]
        tp, n = int(pl.sum()), len(pl)
        tp_all, n_all = tp_all + tp, n_all + n
        lo, hi = wilson(tp, n)
        actual = [key for key, v in final.items() if sheet(key) == "recall" and key[1] == s and v == "Y"]
        found = sum(tool[key] == "Y" for key in actual)
        rlo, rhi = wilson(found, len(actual))
        print(f"  {s:<6}precision {tp}/{n} = {tp / n:.2f} [{lo:.2f}, {hi:.2f}]   "
              f"recall {found}/{len(actual)} = {found / len(actual):.2f} [{rlo:.2f}, {rhi:.2f}]")
    lo, hi = wilson(tp_all, n_all)
    actual = [key for key, v in final.items() if sheet(key) == "recall" and v == "Y"]
    found = sum(tool[key] == "Y" for key in actual)
    rlo, rhi = wilson(found, len(actual))
    print(f"  all   precision {tp_all}/{n_all} = {tp_all / n_all:.2f} [{lo:.2f}, {hi:.2f}]   "
          f"recall {found}/{len(actual)} = {found / len(actual):.2f} [{rlo:.2f}, {rhi:.2f}]")
    counts = {s: sum(1 for f in findings if f["smell"] == s) for s in SMELLS}
    weighted = sum(counts[s] * prec_labels[s].mean() for s in SMELLS) / sum(counts.values())
    print(f"  precision weighted by each smell's share of the {sum(counts.values())} findings: {weighted:.2f}")


if __name__ == "__main__":
    main()
