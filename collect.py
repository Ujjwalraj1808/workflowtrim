"""Collect a dataset: search popular GitHub repos, run the detector on each, save CSVs.

Usage: python collect.py 100 Python
       python collect.py 100          (no language filter)
Writes repos.csv (one row per repo) and findings.csv (one row per finding).
Safe to re-run: repos already in repos.csv are skipped.
"""
import csv
import os
import sys
import time
import requests
from workflowtrim.fetch import fetch_workflows, _headers, API
from workflowtrim.rules import detect

REPOS_CSV = "repos.csv"
FINDINGS_CSV = "findings.csv"
QUERY = "stars:>=100 pushed:>2026-01-01 archived:false fork:false"


def search_repos(n, language):
    """Yield (full_name, stars, language) for up to n repos, most-starred first."""
    query = QUERY + (f" language:{language}" if language else "")
    page = 1
    seen = 0
    while seen < n and page <= 10:  # search API caps at 1000 results
        r = requests.get(f"{API}/search/repositories", headers=_headers(), timeout=30,
                         params={"q": query, "sort": "stars", "per_page": 100, "page": page})
        r.raise_for_status()
        for item in r.json()["items"]:
            if seen >= n:
                return
            yield item["full_name"], item["stargazers_count"], item["language"] or ""
            seen += 1
        page += 1
        time.sleep(2)  # search API allows 30 requests/minute


def fetch_with_retry(repo, tries=3):
    """fetch_workflows, but retry on network errors. Returns None if all tries fail."""
    for attempt in range(1, tries + 1):
        try:
            return fetch_workflows(repo)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            print(f"      network error on {repo} ({type(e).__name__}), attempt {attempt}/{tries}")
            time.sleep(10)
    return None


def load_done():
    if not os.path.exists(REPOS_CSV):
        return set()
    with open(REPOS_CSV, newline="", encoding="utf-8") as f:
        return {row["repo"] for row in csv.DictReader(f)}


def main():
    if not os.environ.get("GITHUB_TOKEN"):
        raise SystemExit("Set GITHUB_TOKEN first (see README).")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    language = sys.argv[2] if len(sys.argv) > 2 else ""
    done = load_done()
    new_repos = new_findings = 0

    with open(REPOS_CSV, "a", newline="", encoding="utf-8") as rf, \
         open(FINDINGS_CSV, "a", newline="", encoding="utf-8") as ff:
        repos_w = csv.writer(rf)
        find_w = csv.writer(ff)
        if not done:
            repos_w.writerow(["repo", "stars", "language", "n_workflows", "n_findings"])
            find_w.writerow(["repo", "file", "smell", "location", "message"])

        for repo, stars, lang in search_repos(n, language):
            if repo in done:
                continue
            files = fetch_with_retry(repo)
            if files is None:
                print(f"      SKIP {repo} (not saved, will retry on next run)")
                continue
            findings = [(name, *f) for name, text in files for f in detect(text)]
            repos_w.writerow([repo, stars, lang, len(files), len(findings)])
            for name, smell, location, message in findings:
                find_w.writerow([repo, name, smell, location, message])
            rf.flush(); ff.flush()
            new_repos += 1
            new_findings += len(findings)
            print(f"{new_repos:>4}  {repo:<45} {len(files)} files  {len(findings)} findings")

    print(f"\nDone: {new_repos} new repos, {new_findings} new findings -> {REPOS_CSV}, {FINDINGS_CSV}")


if __name__ == "__main__":
    main()
