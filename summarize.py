"""Print paper Table 3 (dataset summary) and Table 4 (smell prevalence) from the CSVs.

Usage: python summarize.py
"""
import csv
from collections import Counter, defaultdict

SMELLS = {
    "S1": "Missing job timeout", "S2": "No concurrency cancellation",
    "S3": "Missing dependency cache", "S4": "No path filtering",
    "S5": "Unguarded run on forks", "S6": "Duplicate triggers",
    "S7": "Unnecessary full clone", "S8": "Over-long artifact retention",
}


def main():
    with open("repos.csv", newline="", encoding="utf-8") as f:
        repos = [r for r in csv.DictReader(f) if int(r["n_workflows"]) > 0]
    with open("findings.csv", newline="", encoding="utf-8") as f:
        findings = list(csv.DictReader(f))

    n_repos = len(repos)
    n_files = sum(int(r["n_workflows"]) for r in repos)
    langs = Counter(r["language"] for r in repos).most_common(5)

    print("Table 3 - Dataset summary")
    print(f"  Repositories (with >=1 workflow): {n_repos}")
    print(f"  Workflow files:                   {n_files}")
    print(f"  Findings:                         {len(findings)}")
    print("  Top languages:                    " +
          ", ".join(f"{l or 'n/a'} {c * 100 / n_repos:.0f}%" for l, c in langs))

    repos_by_smell = defaultdict(set)
    files_by_smell = defaultdict(set)
    for f in findings:
        repos_by_smell[f["smell"]].add(f["repo"])
        files_by_smell[f["smell"]].add((f["repo"], f["file"]))
    any_repo = {f["repo"] for f in findings}

    print("\nTable 4 - Prevalence")
    print(f"  {'Smell':<34}{'Repos %':>10}{'Files %':>10}")
    for sid, name in SMELLS.items():
        print(f"  {sid} {name:<31}{len(repos_by_smell[sid]) * 100 / n_repos:>9.1f}%"
              f"{len(files_by_smell[sid]) * 100 / n_files:>9.1f}%")
    print(f"  {'At least one smell':<34}{len(any_repo) * 100 / n_repos:>9.1f}%")


if __name__ == "__main__":
    main()
