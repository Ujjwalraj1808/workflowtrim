"""Print the paper's dataset and prevalence tables from repos.csv and findings.csv.

Usage: python summarize.py
Only repos with an empty `excluded` column are analysed (see collect.py).
review.csv (columns: repo, excluded) holds the manual review of the automatic exclusions:
a row overrides that repo's `excluded` value ("" = include, or a reason such as content_only).
File percentages use parseable workflow files as the denominator (unparseable YAML has no findings).
"""
import csv
import os
from collections import Counter, defaultdict

SMELLS = {
    "S1": "Missing job timeout", "S2": "No PR run cancellation",
    "S3": "Missing dependency cache", "S4": "No path filtering",
    "S5": "Unguarded scheduled run on forks", "S6": "Duplicate triggers",
    "S7": "Unnecessary full clone", "S8": "Over-long artifact retention (storage)",
}
STRATA = ("popular", "less-popular")


def pct(part, whole):
    return f"{part * 100 / whole:.1f}%" if whole else "-"


def load_dataset():
    """(all_repos, included_repos, included_findings), with review.csv applied.
    Prints whether review.csv was used."""
    with open("repos.csv", newline="", encoding="utf-8") as f:
        all_repos = list(csv.DictReader(f))
    with open("findings.csv", newline="", encoding="utf-8") as f:
        all_findings = list(csv.DictReader(f))
    if os.path.exists("review.csv"):
        with open("review.csv", newline="", encoding="utf-8") as f:
            review = {r["repo"]: r["excluded"].strip() for r in csv.DictReader(f)}
        unknown = set(review) - {r["repo"] for r in all_repos}
        if unknown:
            raise SystemExit(f"review.csv has repos that are not in repos.csv: {sorted(unknown)}")
        for r in all_repos:
            r["excluded"] = review.get(r["repo"], r["excluded"])
        print(f"Manual review: {len(review)} repos overridden from review.csv\n")
    else:
        print("Manual review: review.csv not found, automatic exclusions only\n")
    repos = [r for r in all_repos if not r["excluded"]]
    included = {r["repo"] for r in repos}
    return all_repos, repos, [f for f in all_findings if f["repo"] in included]


def main():
    all_repos, repos, findings = load_dataset()

    print("Table 2 - Dataset construction (repos)")
    print(f"  {'':<30}" + "".join(f"{s:>14}" for s in STRATA) + f"{'total':>10}")
    rows = [("searched", lambda r: True)] + \
           [(f"excluded: {reason}", lambda r, x=reason: r["excluded"] == x)
            for reason in sorted({r["excluded"] for r in all_repos if r["excluded"]})] + \
           [("included", lambda r: not r["excluded"])]
    for label, keep in rows:
        counts = [sum(1 for r in all_repos if r["stratum"] == s and keep(r)) for s in STRATA]
        print(f"  {label:<30}" + "".join(f"{c:>14}" for c in counts) + f"{sum(counts):>10}")

    n_repos = len(repos)
    n_files = sum(int(r["n_workflows"]) for r in repos)
    n_unparseable = sum(int(r["n_unparseable"]) for r in repos)
    n_parseable = n_files - n_unparseable
    langs = Counter(r["language"] for r in repos).most_common()
    print("\nTable 3 - Dataset summary (included repos)")
    print(f"  Repositories:           {n_repos}")
    print(f"  Workflow files:         {n_files} ({n_unparseable} unparseable YAML, excluded from file %)")
    print(f"  Findings:               {len(findings)}")
    print("  Languages:              " + ", ".join(f"{l or 'n/a'} {c}" for l, c in langs))

    stratum_of = {r["repo"]: r["stratum"] for r in repos}
    repos_by_smell = defaultdict(set)
    files_by_smell = defaultdict(set)
    for f in findings:
        repos_by_smell[f["smell"]].add(f["repo"])
        files_by_smell[f["smell"]].add((f["repo"], f["file"]))
    repos_by_smell["any"] = {f["repo"] for f in findings}
    n_stratum = Counter(stratum_of.values())

    print("\nTable 4 - Prevalence (share of repos, and of parseable workflow files)")
    print(f"  {'Smell':<44}{'Repos':>8}{'Files':>8}" + "".join(f"{s:>14}" for s in STRATA))
    for sid, name in [*SMELLS.items(), ("any", "")]:
        label = "At least one smell" if sid == "any" else f"{sid} {name}"
        files = pct(len(files_by_smell[sid]), n_parseable) if sid != "any" else ""
        by_stratum = [pct(sum(1 for r in repos_by_smell[sid] if stratum_of[r] == s), n_stratum[s]) for s in STRATA]
        print(f"  {label:<44}{pct(len(repos_by_smell[sid]), n_repos):>8}{files:>8}"
              + "".join(f"{v:>14}" for v in by_stratum))

    lang_of = {r["repo"]: r["language"] for r in repos}
    n_lang = Counter(lang_of.values())
    lang_names = [l for l, _ in langs]
    print("\nTable 5 - Prevalence by language (share of repos)")
    print(f"  {'Smell':<8}" + "".join(f"{l or 'n/a':>12}" for l in lang_names))
    for sid in [*SMELLS, "any"]:
        vals = [pct(sum(1 for r in repos_by_smell[sid] if lang_of[r] == l), n_lang[l]) for l in lang_names]
        print(f"  {sid:<8}" + "".join(f"{v:>12}" for v in vals))


if __name__ == "__main__":
    main()
