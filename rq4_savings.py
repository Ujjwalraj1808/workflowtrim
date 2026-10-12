"""RQ4: how much compute do the S1 and S2 smells waste in real run history?

Usage (needs GITHUB_TOKEN, see README):
  python rq4_savings.py            select repositories, download their run history, print the tables
  python rq4_savings.py report     tables only, from the downloaded data (no API calls)

1. Selection (rq4/candidates.csv). Candidates: RQ3 snapshot repositories with an S1 or S2 finding.
   Per stratum (popular / less-popular) they are shuffled with seed SEED and checked in that order;
   a repository is eligible if it has at least MIN_RUNS completed runs in the DAYS days before its
   snapshot date. The first PER_STRATUM eligible repositories per stratum are used, whatever their
   savings turn out to be. A repository whose run history cannot be downloaded (deleted, made
   private) is recorded in rq4/raw/<owner>__<repo>.error.txt and replaced by the next candidate.
2. Download (rq4/raw/<owner>__<repo>.json). The newest MAX_RUNS completed runs in that window and
   their jobs, and for every workflow file with S1 / S2 the date of the last commit that changed it
   up to the snapshot commit. Only the fields the estimate needs are kept.
3. History (stable_since in the raw file). For every smelly file, the detector is run on each version
   committed in the window (and the one before it); the file's runs are used from the date since which
   its S1 jobs and S2 have been as in the snapshot without interruption. A commit that does not change
   them (e.g. an action version bump) does not cut the history.
4. Report (rq4/savings.csv). Filter "smells_unchanged" (main) uses the runs since stable_since;
   filter "file_unchanged" (stricter, for comparison) only the runs after the file's last change.
   S1 counts only the jobs that have the smell (a job with timeout-minutes is not changed by the
   repair). The S1 timeout multiplier is varied (MULTIPLIERS); S2 does not depend on it.
The script can be stopped and run again: selected repositories and downloaded files are kept.
"""
import csv
import json
import os
import random
import sys
import time
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

from workflowtrim.fetch import API, _get_with_backoff, _headers
from workflowtrim.rules import detect, parse
from workflowtrim.savings import _api, _ts, estimate_from_runs, fetch_jobs, fetch_runs, WORKERS

SEED = 2026
PER_STRATUM = 10
MIN_RUNS = 100
DAYS = 90
MAX_RUNS = 500
MULTIPLIERS = (1.25, 1.5, 2.0)
MIN_REMAINING = 700          # API calls kept in reserve: a typical repository needs about 520
OUT = "rq4"
RAW = os.path.join(OUT, "raw")
FIELDS = ["repo", "stratum", "order", "runs_in_window", "eligible"]
FILTERS = {"smells_unchanged": "stable_since", "file_unchanged": "changed"}


def load():
    with open("rq3/snapshot.csv", newline="", encoding="utf-8") as f:
        snap = {r["repo"]: r for r in csv.DictReader(f) if r["status"] == "ok"}
    with open("repos.csv", newline="", encoding="utf-8") as f:
        stratum = {r["repo"]: r["stratum"] for r in csv.DictReader(f)}
    smells = defaultdict(lambda: defaultdict(set))       # repo -> file -> {S1, S2}
    s1_jobs = defaultdict(lambda: defaultdict(set))      # repo -> file -> {job keys}
    with open("rq3/findings.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["smell"] in ("S1", "S2") and r["repo"] in snap:
                smells[r["repo"]][r["file"]].add(r["smell"])
                if r["smell"] == "S1":
                    s1_jobs[r["repo"]][r["file"]].add(r["location"])
    return snap, stratum, smells, s1_jobs


def wait_for_rate_limit(min_remaining=MIN_REMAINING):
    core = _api(f"{API}/rate_limit")["resources"]["core"]     # does not count against the limit
    if core["remaining"] < min_remaining:
        wait = max(0, core["reset"] - time.time()) + 5
        print(f"  {core['remaining']} API calls left, waiting {wait / 60:.0f} min for the reset")
        time.sleep(wait)


def window(snap_row):
    until = snap_row["date"]
    since = (datetime.strptime(until, "%Y-%m-%d") - timedelta(days=DAYS)).strftime("%Y-%m-%d")
    return since, until


def failed_downloads():
    return {n[:-len(".error.txt")].replace("__", "/", 1) for n in os.listdir(RAW) if n.endswith(".error.txt")}


def select(snap, stratum, smells):
    """Eligible repositories in selection order, without failed downloads; checks more candidates
    until each stratum has PER_STRATUM of them."""
    failed = failed_downloads()
    path = os.path.join(OUT, "candidates.csv")
    rows = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    checked = {r["repo"] for r in rows}
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        if not rows:
            w.writeheader()
        for group in ("popular", "less-popular"):
            order = sorted(r for r in smells if stratum.get(r) == group)
            random.Random(SEED).shuffle(order)
            for i, repo in enumerate(order, 1):
                if sum(1 for r in rows if r["stratum"] == group and r["eligible"] == "yes"
                       and r["repo"] not in failed) >= PER_STRATUM:
                    break
                if repo in checked:
                    continue
                since, until = window(snap[repo])
                try:
                    n = _api(f"{API}/repos/{repo}/actions/runs?status=completed&per_page=1"
                             f"&created={since}..{until}")["total_count"]
                except Exception as e:                # Actions disabled, repository gone, ...
                    print(f"  {repo}: {e}")
                    n = 0
                row = {"repo": repo, "stratum": group, "order": i, "runs_in_window": n,
                       "eligible": "yes" if n >= MIN_RUNS else "no"}
                w.writerow(row)
                f.flush()
                rows.append(row)
                print(f"  {group:<12} #{i:<4} {repo:<45} {n:>6} runs  {row['eligible']}")
    return [r["repo"] for r in rows if r["eligible"] == "yes" and r["repo"] not in failed]


def last_change(repo, file, commit):
    """Date of the last commit up to the snapshot commit that changed the file; None if unknown
    (e.g. the commit was removed by a force-push): the file's runs are then not used."""
    path = urllib.parse.quote(f".github/workflows/{file}")
    try:
        data = _api(f"{API}/repos/{repo}/commits?path={path}&sha={commit}&per_page=1")
    except requests.HTTPError as e:
        print(f"      {file}: last change unknown ({e.response.status_code}), its runs are not used")
        return None
    return data[0]["commit"]["committer"]["date"] if data else None


def signature(text):
    """What the estimate depends on: the jobs with S1, and whether S2 is present. None: no file."""
    if text is None:
        return None
    found = detect(text)
    return frozenset(loc for s, loc, _ in found if s == "S1"), any(s == "S2" for s, _, _ in found)


def stable_since(versions, before, target, window_start):
    """versions: [(commit date, signature)] of the file's commits in the window, newest first;
    before: signature of the version before the window. Date since which the file has had the target
    signature without interruption (None if the newest version differs)."""
    for i, (_, sig) in enumerate(versions):
        if sig != target:
            return versions[i - 1][0] if i else None
    if versions and before != target:
        return versions[-1][0]
    return window_start


def file_at(repo, file, sha):
    path = urllib.parse.quote(f".github/workflows/{file}")
    r = _get_with_backoff(f"{API}/repos/{repo}/contents/{path}?ref={sha}",
                          {**_headers(), "Accept": "application/vnd.github.raw"})
    if r.status_code == 404:
        return None
    if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
        raise SystemExit("GitHub API rate limit reached.")
    r.raise_for_status()
    return r.text


def history(repo, file, commit, window_start):
    """stable_since for one file, from its commits up to the snapshot commit."""
    path = urllib.parse.quote(f".github/workflows/{file}")
    url = f"{API}/repos/{repo}/commits?path={path}&sha={commit}&per_page=100"
    try:
        commits, page = [], 1
        while True:
            data = _api(f"{url}&since={window_start}&page={page}")
            commits += data
            if len(data) < 100:
                break
            page += 1
        versions = [(c["commit"]["committer"]["date"], signature(file_at(repo, file, c["sha"]))) for c in commits]
        prev = _api(f"{url}&until={window_start}")[:1]
        before = signature(file_at(repo, file, prev[0]["sha"])) if prev else None
    except requests.HTTPError as e:
        print(f"      {file}: history unknown ({e.response.status_code}), its runs are not used")
        return None
    with open(os.path.join("rq3", "files", f"{repo.replace('/', '__')}__{file}"), encoding="utf-8") as f:
        target = signature(f.read())
    return stable_since(versions, before, target, window_start)


def download(repo, snap_row, files):
    since, until = window(snap_row)
    runs = [r for r in fetch_runs(repo, DAYS, MAX_RUNS, until) if r["conclusion"] != "skipped"]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        jobs = dict(zip((r["id"] for r in runs), pool.map(lambda r: fetch_jobs(repo, r["id"]), runs)))
    return {
        "repo": repo, "window": [since, until], "commit": snap_row["commit"],
        "changed": {f: last_change(repo, f, snap_row["commit"]) for f in sorted(files)},
        "runs": [{"id": r["id"], "path": r.get("path") or "", "event": r["event"], "conclusion": r["conclusion"],
                  "head_branch": r["head_branch"], "created_at": r["created_at"],
                  "head_repository": {"full_name": (r.get("head_repository") or {}).get("full_name")}}
                 for r in runs],
        "jobs": {str(rid): [{k: j.get(k) for k in ("name", "started_at", "completed_at", "conclusion", "labels")}
                            for j in js] for rid, js in jobs.items()},
    }


def job_names(repo, file, keys):
    """Display names of the S1 jobs: `name:` if set, else the job key."""
    with open(os.path.join("rq3", "files", f"{repo.replace('/', '__')}__{file}"), encoding="utf-8") as f:
        jobs = (parse(f.read()) or {}).get("jobs") or {}
    names = []
    for k in keys:
        job = jobs.get(k)
        names.append(str((job.get("name") if isinstance(job, dict) else None) or k))
    return sorted(names)


def analyse(raw, smells_by_file, s1_job_names, multiplier, since="stable_since"):
    """Estimate for the runs of each smelly file created after raw[since][file]
    (stable_since: since its smells are as in the snapshot; changed: since its last change)."""
    changed = {f: _ts(d) for f, d in (raw.get(since) or raw["changed"]).items() if d}
    runs = [r for r in raw["runs"] if (f := r["path"].rsplit("/", 1)[-1]) in changed
            and _ts(r["created_at"]) >= changed[f]]
    jobs = {r["id"]: raw["jobs"][str(r["id"])] for r in runs}
    est = estimate_from_runs(runs, jobs, smells_by_file, multiplier, s1_job_names)
    created = [_ts(r["created_at"]) for r in runs]
    est["days"] = round(max(1.0, (max(created) - min(created)).total_seconds() / 86400), 1) if created else 0
    return est


def report(stratum, smells, s1_jobs):
    rows = []
    for name in sorted(n for n in os.listdir(RAW) if n.endswith(".json")):
        with open(os.path.join(RAW, name), encoding="utf-8") as f:
            raw = json.load(f)
        repo = raw["repo"]
        names = {file: job_names(repo, file, keys) for file, keys in s1_jobs[repo].items()}
        for filter_, key in FILTERS.items():
            for m in MULTIPLIERS:
                e = analyse(raw, smells[repo], names, m, key)
                per30 = 30 / e["days"] if e["days"] else 0
                rows.append({"repo": repo, "stratum": stratum[repo], "filter": filter_, "multiplier": m, "runs": e["runs"],
                             "from": e["from"], "to": e["to"], "days": e["days"], "job_minutes": e["job_minutes"],
                             "s1_jobs": e["S1"]["jobs"], "s1_minutes": e["S1"]["minutes"],
                             "s2_overlaps": e["S2"]["overlaps"], "s2_minutes": e["S2"]["minutes"],
                             "waste_minutes": e["minutes"],
                             "waste_pct": round(100 * e["minutes"] / e["job_minutes"], 2) if e["job_minutes"] else 0,
                             "cost_usd": e["cost_usd"], "minutes_per_30d": round(e["minutes"] * per30, 1),
                             "cost_usd_per_30d": round(e["cost_usd"] * per30, 2)})
    failed = sorted(failed_downloads())
    if failed:
        print(f"\n  Download failed, replaced by the next candidate: {', '.join(failed)}")
    if not rows:
        print("  No downloaded repositories yet.")
        return
    with open(os.path.join(OUT, "savings.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    main_rows = [r for r in rows if r["filter"] == "smells_unchanged"]
    print(f"\nTable 8 - Waste in run history (S1 multiplier 1.5, runs since the smells are unchanged), "
          f"{len(main_rows) // len(MULTIPLIERS)} repositories")
    print(f"  {'repo':<40} {'stratum':<12} {'runs':>5} {'days':>5} {'job min':>9} {'S1 min':>8} "
          f"{'S2 min':>8} {'waste %':>8} {'min/30d':>8} {'$/30d':>7}")
    for r in (r for r in main_rows if r["multiplier"] == 1.5):
        print(f"  {r['repo']:<40} {r['stratum']:<12} {r['runs']:>5} {r['days']:>5} {r['job_minutes']:>9} "
              f"{r['s1_minutes']:>8} {r['s2_minutes']:>8} {r['waste_pct']:>7}% {r['minutes_per_30d']:>8} "
              f"{r['cost_usd_per_30d']:>7}")
    print("\nSensitivity, all repositories (S1 timeout = multiplier x P95; filter of the runs used)")
    for filter_, m in [(f, m) for f in FILTERS for m in MULTIPLIERS]:
        sel = [r for r in rows if r["multiplier"] == m and r["filter"] == filter_]
        job_min = sum(r["job_minutes"] for r in sel)
        s1, s2 = sum(r["s1_minutes"] for r in sel), sum(r["s2_minutes"] for r in sel)
        waste = sum(r["waste_minutes"] for r in sel)
        print(f"  {filter_:<17} x{m:<5} runs {sum(r['runs'] for r in sel):>5}  S1 {s1:>9.1f} min   S2 {s2:>9.1f} min   together {waste:>9.1f} min = {100 * waste / job_min if job_min else 0:5.2f}% "
              f"of {job_min:.0f} job-minutes   repos with any waste: {sum(1 for r in sel if r['waste_minutes'] > 0)}/{len(sel)}")


def raw_path(repo):
    return os.path.join(RAW, repo.replace("/", "__") + ".json")


def main():
    snap, stratum, smells, s1_jobs = load()
    os.makedirs(RAW, exist_ok=True)
    if sys.argv[1:] != ["report"]:
        if not os.environ.get("GITHUB_TOKEN"):
            raise SystemExit("Set GITHUB_TOKEN first (see README).")
        wait_for_rate_limit()
        while True:                                          # again after a failed download: replacement
            selected = select(snap, stratum, smells)
            todo = [r for r in selected if not os.path.exists(raw_path(r))]
            if not todo:
                break
            for repo in todo:
                wait_for_rate_limit()
                print(f"  [{selected.index(repo) + 1}/{len(selected)}] downloading {repo} ...")
                try:
                    try:
                        raw = download(repo, snap[repo], smells[repo])
                    except SystemExit:                       # limit reached mid-download (large matrix runs)
                        wait_for_rate_limit(min_remaining=10**9)  # wait for the reset, then start it again
                        raw = download(repo, snap[repo], smells[repo])
                except requests.HTTPError as e:              # repository deleted / made private / Actions disabled
                    with open(raw_path(repo)[:-len(".json")] + ".error.txt", "w", encoding="utf-8") as f:
                        f.write(f"{e}\n")
                    print(f"      download failed ({e}), the next candidate replaces it")
                    continue
                with open(raw_path(repo) + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(raw, f)
                os.replace(raw_path(repo) + ".tmp", raw_path(repo))   # a stopped download leaves no half file
                print(f"      {len(raw['runs'])} runs")
        for repo in select(snap, stratum, smells):              # history of the smelly files (step 3)
            with open(raw_path(repo), encoding="utf-8") as f:
                raw = json.load(f)
            if "stable_since" in raw:
                continue
            wait_for_rate_limit()
            print(f"  history of {repo} ({len(raw['changed'])} files) ...")
            start = raw["window"][0] + "T00:00:00Z"
            try:
                raw["stable_since"] = {f: history(repo, f, raw["commit"], start) for f in sorted(raw["changed"])}
            except SystemExit:                               # limit reached: wait for the reset, then again
                wait_for_rate_limit(min_remaining=10**9)
                raw["stable_since"] = {f: history(repo, f, raw["commit"], start) for f in sorted(raw["changed"])}
            with open(raw_path(repo) + ".tmp", "w", encoding="utf-8") as f:
                json.dump(raw, f)
            os.replace(raw_path(repo) + ".tmp", raw_path(repo))
    report(stratum, smells, s1_jobs)


if __name__ == "__main__":
    main()
