"""Collect the dataset: search GitHub repos, run the detector on each, save CSVs.

Usage: python collect.py 100 Python popular
       python collect.py 100 Python less-popular
Languages: Python, JavaScript, TypeScript, Go, Java. Strata (see STRATA):
  popular       stars >= 100, most-starred first
  less-popular  stars 100..1000, most recently updated first (not ordered by stars)
Both: pushed in 2026, not archived, not a fork.

Writes repos.csv (one row per searched repo, excluded ones included with a reason) and
findings.csv (one row per finding). summarize.py uses only rows with an empty `excluded`.
Exclusion reasons, checked in this order:
  no_build_manifest  no build/package manifest in the repository root (curated lists,
                     docs, books and other content-only repositories)
  no_workflows       no .yml/.yaml file in .github/workflows
Safe to re-run: repos already in repos.csv are skipped. When the API rate limit is almost
used up, the script waits for the reset instead of stopping.
"""
import csv
import os
import sys
import time
import requests
from workflowtrim.fetch import fetch_workflows, _headers, API
from workflowtrim.rules import detect, parse

REPOS_CSV = "repos.csv"
FINDINGS_CSV = "findings.csv"
REPO_COLUMNS = ["repo", "stars", "language", "stratum", "n_workflows", "n_unparseable", "n_findings", "excluded"]
BASE_QUERY = "pushed:>2026-01-01 archived:false fork:false"
STRATA = {"popular": ("stars:>=100", "stars"), "less-popular": ("stars:100..1000", "updated")}
BAZEL = {"WORKSPACE", "WORKSPACE.bazel", "MODULE.bazel"}   # monorepos build with Bazel at the root
MANIFESTS = {
    "Python": {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"} | BAZEL,
    "JavaScript": {"package.json", "deno.json", "deno.jsonc"} | BAZEL,
    "TypeScript": {"package.json", "deno.json", "deno.jsonc"} | BAZEL,
    "Go": {"go.mod", "go.work"} | BAZEL,
    "Java": {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
             "build.xml"} | BAZEL,
}
RATE_LIMIT_RESERVE = 100   # wait for the reset when fewer core API requests than this remain


def search_repos(n, language, stratum):
    """Yield (full_name, stars, language) for up to n repos of one stratum."""
    stars, sort = STRATA[stratum]
    query = f"{stars} {BASE_QUERY} language:{language}"
    page = 1
    seen = 0
    while seen < n and page <= 10:  # search API caps at 1000 results
        r = requests.get(f"{API}/search/repositories", headers=_headers(), timeout=30,
                         params={"q": query, "sort": sort, "order": "desc", "per_page": 100, "page": page})
        r.raise_for_status()
        for item in r.json()["items"]:
            if seen >= n:
                return
            yield item["full_name"], item["stargazers_count"], item["language"] or ""
            seen += 1
        page += 1
        time.sleep(2)  # search API allows 30 requests/minute


def root_files(repo):
    r = requests.get(f"{API}/repos/{repo}/contents/", headers=_headers(), timeout=30)
    r.raise_for_status()
    return {item["name"] for item in r.json()}


def exclusion(language, root, n_workflows):
    """Exclusion reason for a repo, or "" if it is included."""
    if not (root & MANIFESTS[language]):
        return "no_build_manifest"
    if n_workflows == 0:
        return "no_workflows"
    return ""


def wait_for_rate_limit():
    """GET /rate_limit does not count against the limit."""
    core = requests.get(f"{API}/rate_limit", headers=_headers(), timeout=30).json()["resources"]["core"]
    if core["remaining"] < RATE_LIMIT_RESERVE:
        wait = max(0, core["reset"] - time.time()) + 5
        print(f"      rate limit: {core['remaining']} requests left, waiting {wait / 60:.0f} min for the reset")
        time.sleep(wait)


def with_retry(fn, repo, tries=3):
    """fn(repo), but retry on network errors. Returns None if all tries fail."""
    for attempt in range(1, tries + 1):
        try:
            return fn(repo)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            print(f"      network error on {repo} ({type(e).__name__}), attempt {attempt}/{tries}")
            time.sleep(10)
    return None


def load_done():
    if not os.path.exists(REPOS_CSV):
        return set()
    with open(REPOS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != REPO_COLUMNS:
            raise SystemExit(f"{REPOS_CSV} has the old column format. Rename it and {FINDINGS_CSV} "
                             "(e.g. repos_v1.csv, findings_v1.csv) and run again.")
        return {row["repo"] for row in reader}


def main():
    if not os.environ.get("GITHUB_TOKEN"):
        raise SystemExit("Set GITHUB_TOKEN first (see README).")
    if len(sys.argv) != 4 or sys.argv[2] not in MANIFESTS or sys.argv[3] not in STRATA:
        raise SystemExit(f"Usage: python collect.py N LANGUAGE STRATUM\n"
                         f"  LANGUAGE: {', '.join(MANIFESTS)}\n  STRATUM: {', '.join(STRATA)}")
    n, language, stratum = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    done = load_done()
    new_repos = new_findings = 0

    with open(REPOS_CSV, "a", newline="", encoding="utf-8") as rf, \
         open(FINDINGS_CSV, "a", newline="", encoding="utf-8") as ff:
        repos_w = csv.writer(rf)
        find_w = csv.writer(ff)
        if not done:
            repos_w.writerow(REPO_COLUMNS)
            find_w.writerow(["repo", "file", "smell", "location", "message"])

        for repo, stars, lang in search_repos(n, language, stratum):
            if repo in done:
                continue
            wait_for_rate_limit()
            root = with_retry(root_files, repo)
            files = with_retry(fetch_workflows, repo)
            if root is None or files is None:
                print(f"      SKIP {repo} (not saved, will retry on next run)")
                continue
            findings = [(name, *f) for name, text in files for f in detect(text)]
            unparseable = sum(parse(text) is None for _, text in files)
            excluded = exclusion(language, root, len(files))
            repos_w.writerow([repo, stars, lang, stratum, len(files), unparseable, len(findings), excluded])
            for name, smell, location, message in findings:
                find_w.writerow([repo, name, smell, location, message])
            rf.flush(); ff.flush()
            done.add(repo)
            new_repos += 1
            new_findings += len(findings)
            print(f"{new_repos:>4}  {repo:<45} {len(files)} files  {len(findings)} findings"
                  + (f"  [excluded: {excluded}]" if excluded else ""))

    print(f"\nDone: {new_repos} new repos, {new_findings} new findings -> {REPOS_CSV}, {FINDINGS_CSV}")


if __name__ == "__main__":
    main()
