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
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from .fetch import API, _get_with_backoff, _headers

TIMEOUT_MULTIPLIER = 1.5  # S1 timeout = multiplier x P95 successful job duration (RQ4: vary 1.25/1.5/2.0)
MIN_SAMPLES = 20          # successful jobs needed before a timeout is derived
MIN_TIMEOUT = 10          # minutes; short jobs vary a lot relative to their length
GITHUB_DEFAULT_TIMEOUT = 360
RATES = {"linux": 0.006, "windows": 0.010, "macos": 0.062}   # USD per minute, GitHub-hosted standard runners (list price since 1 Jan 2026)
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


def fetch_runs(repo, days=90, max_runs=MAX_RUNS, until=None):
    """Most recent completed runs (newest first) within `days`, at most max_runs.
    until: "YYYY-MM-DD", end of the window (default: now), e.g. the date of a workflow snapshot."""
    end = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=timezone.utc) if until else datetime.now(timezone.utc)
    since = (end - timedelta(days=days)).strftime("%Y-%m-%d")
    created = f"{since}..{until}" if until else f">={since}"
    runs = []
    page = 1
    while len(runs) < max_runs:
        data = _api(f"{API}/repos/{repo}/actions/runs?status=completed&per_page=100"
                    f"&page={page}&created={created}")
        batch = data.get("workflow_runs", [])
        runs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return runs[:max_runs]


def fetch_jobs(repo, run_id):
    """All jobs of a run (large matrix runs have more than one page)."""
    jobs, page = [], 1
    while True:
        data = _api(f"{API}/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100&page={page}")
        jobs.extend(data.get("jobs", []))
        if len(jobs) >= data.get("total_count", 0) or not data.get("jobs"):
            return jobs
        page += 1


def job_name_pattern(name):
    """Regex for the names GitHub gives the runs of a workflow job whose display name is `name`
    (the job's `name:` or, without one, its key): ${{ }} expressions are filled in at run time and
    matrix jobs get a " (values)" suffix."""
    parts = re.split(r"\$\{\{.*?\}\}", name)
    return re.compile(".+".join(re.escape(p) for p in parts) + r"( \(.*\))?")


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


def estimate_from_runs(runs, jobs_by_run, smells_by_file, multiplier=TIMEOUT_MULTIPLIER, s1_job_names=None):
    """runs: run dicts from the API; jobs_by_run: {run_id: [job dicts]};
    smells_by_file: {"ci.yml": {"S1", "S2"}}.
    s1_job_names: optional {"ci.yml": [display names of the jobs with S1]}; when given, S1 counts only
    those jobs (a job that already has timeout-minutes is not changed by the repair).
    Returns {"runs", "from", "to", "timeouts": {file: minutes}, "S1": {...}, "S2": {...},
    "minutes", "cost_usd" (S1 and S2 together; minutes both would save are counted once),
    "job_minutes" (all finished jobs)}."""
    patterns = {f: [job_name_pattern(n) for n in names] for f, names in (s1_job_names or {}).items()}
    completed = []
    for run in runs:
        jobs = []
        for j in jobs_by_run.get(run["id"], []):
            if j.get("started_at") and j.get("completed_at"):
                start, end = _ts(j["started_at"]), _ts(j["completed_at"])
                if end > start:
                    jobs.append({"start": start, "end": end, "conclusion": j.get("conclusion"),
                                 "rate": RATES[_os(j)], "name": j.get("name", "")})
        if not jobs:
            continue
        head_repo = (run.get("head_repository") or {}).get("full_name") or ""
        completed.append({"file": run["path"].rsplit("/", 1)[-1], "branch": (head_repo, run["head_branch"]),
                          "event": run["event"], "start": min(j["start"] for j in jobs),
                          "created": _ts(run["created_at"]), "jobs": jobs})

    timeouts = timeouts_from_runs(completed, multiplier)
    cut = {}   # id(job) -> earliest time a repair would have stopped it (S1 and S2 can both apply)
    s1_jobs, s1_min, s1_cost = 0, 0.0, 0.0
    for run in completed:
        timeout = timeouts.get(run["file"])
        if timeout is None or "S1" not in smells_by_file.get(run["file"], ()):
            continue
        for j in run["jobs"]:
            if s1_job_names is not None and not any(p.fullmatch(j["name"]) for p in patterns.get(run["file"], [])):
                continue
            if j["conclusion"] in ("failure", "cancelled", "timed_out"):
                minutes = (j["end"] - j["start"]).total_seconds() / 60
                if minutes > timeout:
                    stop = j["start"] + timedelta(minutes=timeout)
                    cut[id(j)] = (min(stop, cut[id(j)][0]) if id(j) in cut else stop, j)
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
                    cut[id(j)] = (min(cur["start"], cut[id(j)][0]) if id(j) in cut else cur["start"], j)
                    minutes = (j["end"] - max(j["start"], cur["start"])).total_seconds() / 60
                    wasted += minutes
                    s2_cost += minutes * j["rate"]
            if wasted > 0:
                s2_overlaps += 1
                s2_min += wasted

    # together: each job's minutes after its earliest stop (or after its start, if it started later),
    # so minutes both repairs save count once
    total_min = sum((j["end"] - max(stop, j["start"])).total_seconds() / 60 for stop, j in cut.values())
    total_cost = sum((j["end"] - max(stop, j["start"])).total_seconds() / 60 * j["rate"] for stop, j in cut.values())
    dates = [r["created"] for r in completed]
    return {
        "runs": len(completed),
        "from": min(dates).strftime("%Y-%m-%d") if dates else None,
        "to": max(dates).strftime("%Y-%m-%d") if dates else None,
        "timeouts": timeouts,
        "S1": {"jobs": s1_jobs, "minutes": round(s1_min, 1), "cost_usd": round(s1_cost, 2)},
        "S2": {"overlaps": s2_overlaps, "minutes": round(s2_min, 1), "cost_usd": round(s2_cost, 2)},
        "minutes": round(total_min, 1),
        "cost_usd": round(total_cost, 2),
        "job_minutes": round(sum((j["end"] - j["start"]).total_seconds() / 60
                                 for run in completed for j in run["jobs"]), 1),
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
