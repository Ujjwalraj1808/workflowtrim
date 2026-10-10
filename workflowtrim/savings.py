"""Estimate compute minutes and cost wasted by smells, from a repository's run history.

Reads the most recent MAX_RUNS completed runs (GET /actions/runs) and, for each,
its jobs (GET /actions/runs/{id}/jobs) to get real job start/end times and the
runner OS. Run-level updated_at is NOT a completion time (it changes when a PR is
closed), so jobs are required for correct durations.

  S2  jobs of a pull_request run still running when the next pull_request run of the
      same workflow file on the same head branch (same head repository, so PRs from
      different forks that all use `main` are not mixed) started
      = job-minutes the S2 repair (cancel-in-progress for pull_request only) would have saved
  S1  per workflow file, timeout = max(MIN_TIMEOUT, ceil(TIMEOUT_MULTIPLIER x P95 of its
      successful job durations)), needs MIN_SAMPLES successful jobs; the S1 repair writes the
      same value. Failed / cancelled / timed-out jobs longer than it
      = minutes beyond the timeout that it would have cut

S3, S4, S5 need extra data (commit file lists, fork runs, install-step times)
and are not estimated here.
"""
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from .fetch import API, _get_with_backoff, _headers

TIMEOUT_MULTIPLIER = 1.5  # S1 timeout = multiplier x P95 successful job duration (RQ4: vary 1.25/1.5/2.0)
MIN_SAMPLES = 20          # successful jobs needed before a timeout is derived
MIN_TIMEOUT = 10          # minutes; short jobs vary a lot relative to their length
GITHUB_DEFAULT_TIMEOUT = 360
RATES = {"linux": 0.008, "windows": 0.016, "macos": 0.08}   # USD per minute, GitHub-hosted
MAX_RUNS = 300            # one extra API call per run (jobs)
WORKERS = 8               # parallel jobs requests


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _api(url):
    r = _get_with_backoff(url, _headers())
    if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
        raise SystemExit("GitHub API rate limit reached. Set GITHUB_TOKEN (see README) and retry.")
    r.raise_for_status()
    return r.json()


def fetch_runs(repo, days=90, max_runs=MAX_RUNS):
    """Most recent completed runs (newest first) within `days`, at most max_runs."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    runs = []
    page = 1
    while len(runs) < max_runs:
        data = _api(f"{API}/repos/{repo}/actions/runs?status=completed&per_page=100"
                    f"&page={page}&created=>={since}")
        batch = data.get("workflow_runs", [])
        runs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return runs[:max_runs]


def fetch_jobs(repo, run_id):
    return _api(f"{API}/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100").get("jobs", [])


def _os(job):
    labels = " ".join(job.get("labels") or []).lower()
    if "windows" in labels:
        return "windows"
    if "macos" in labels:
        return "macos"
    return "linux"


def timeouts_from_runs(completed, multiplier=TIMEOUT_MULTIPLIER):
    """{file: timeout minutes} for files with at least MIN_SAMPLES successful jobs.
    Files whose timeout would not be below GitHub's 360-minute default are left out."""
    durations = {}
    for run in completed:
        for j in run["jobs"]:
            if j["conclusion"] == "success":
                durations.setdefault(run["file"], []).append((j["end"] - j["start"]).total_seconds() / 60)
    out = {}
    for file, d in durations.items():
        if len(d) < MIN_SAMPLES:
            continue
        d.sort()
        p95 = d[math.ceil(0.95 * len(d)) - 1]           # nearest-rank percentile
        timeout = max(MIN_TIMEOUT, math.ceil(multiplier * p95))
        if timeout < GITHUB_DEFAULT_TIMEOUT:
            out[file] = timeout
    return out


def estimate_from_runs(runs, jobs_by_run, smells_by_file, multiplier=TIMEOUT_MULTIPLIER):
    """runs: run dicts from the API; jobs_by_run: {run_id: [job dicts]};
    smells_by_file: {"ci.yml": {"S1", "S2"}}.
    Returns {"runs", "from", "to", "timeouts": {file: minutes}, "S1": {...}, "S2": {...},
    "minutes", "cost_usd"}."""
    completed = []
    for run in runs:
        jobs = []
        for j in jobs_by_run.get(run["id"], []):
            if j.get("started_at") and j.get("completed_at"):
                start, end = _ts(j["started_at"]), _ts(j["completed_at"])
                if end > start:
                    jobs.append({"start": start, "end": end, "conclusion": j.get("conclusion"),
                                 "rate": RATES[_os(j)]})
        if not jobs:
            continue
        head_repo = (run.get("head_repository") or {}).get("full_name") or ""
        completed.append({"file": run["path"].rsplit("/", 1)[-1], "branch": (head_repo, run["head_branch"]),
                          "event": run["event"], "start": min(j["start"] for j in jobs),
                          "created": _ts(run["created_at"]), "jobs": jobs})

    timeouts = timeouts_from_runs(completed, multiplier)
    s1_jobs, s1_min, s1_cost = 0, 0.0, 0.0
    for run in completed:
        timeout = timeouts.get(run["file"])
        if timeout is None or "S1" not in smells_by_file.get(run["file"], ()):
            continue
        for j in run["jobs"]:
            if j["conclusion"] in ("failure", "cancelled", "timed_out"):
                minutes = (j["end"] - j["start"]).total_seconds() / 60
                if minutes > timeout:
                    s1_jobs += 1
                    s1_min += minutes - timeout
                    s1_cost += (minutes - timeout) * j["rate"]

    s2_overlaps, s2_min, s2_cost = 0, 0.0, 0.0
    groups = {}
    for run in completed:
        if "S2" in smells_by_file.get(run["file"], ()) and run["event"] == "pull_request":
            groups.setdefault((run["file"], run["branch"]), []).append(run)
    for group in groups.values():
        group.sort(key=lambda r: r["start"])
        for prev, cur in zip(group, group[1:]):
            wasted = 0.0
            for j in prev["jobs"]:
                if j["end"] > cur["start"]:
                    minutes = (j["end"] - max(j["start"], cur["start"])).total_seconds() / 60
                    wasted += minutes
                    s2_cost += minutes * j["rate"]
            if wasted > 0:
                s2_overlaps += 1
                s2_min += wasted

    dates = [r["created"] for r in completed]
    return {
        "runs": len(completed),
        "from": min(dates).strftime("%Y-%m-%d") if dates else None,
        "to": max(dates).strftime("%Y-%m-%d") if dates else None,
        "timeouts": timeouts,
        "S1": {"jobs": s1_jobs, "minutes": round(s1_min, 1), "cost_usd": round(s1_cost, 2)},
        "S2": {"overlaps": s2_overlaps, "minutes": round(s2_min, 1), "cost_usd": round(s2_cost, 2)},
        "minutes": round(s1_min + s2_min, 1),
        "cost_usd": round(s1_cost + s2_cost, 2),
    }


def estimate(repo, smells_by_file, days=90, max_runs=MAX_RUNS, progress=None):
    """progress: optional callback(done, total) called after each run's jobs are fetched."""
    runs = [r for r in fetch_runs(repo, days, max_runs) if r["conclusion"] != "skipped"]
    jobs_by_run = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_jobs, repo, run["id"]): run["id"] for run in runs}
        for i, fut in enumerate(as_completed(futures), 1):   # progress runs in the caller's thread
            jobs_by_run[futures[fut]] = fut.result()
            if progress:
                progress(i, len(runs))
    return estimate_from_runs(runs, jobs_by_run, smells_by_file)
