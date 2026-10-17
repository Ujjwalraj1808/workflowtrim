"""Fork re-run study: measure, in real runs, the job minutes that the S3 (dependency cache) repair saves.

The savings of S3 cannot be estimated from run history (RQ4 only covers S1 and S2), so the original and the
repaired workflow are run side by side in a fork owned by the person running this script:

  plan     choose candidate workflow files (fork/plan.csv): files whose S3 finding was repaired in RQ3, the
           reviewed-correct ones first, then a random sample (seed 2029); release, publish and deploy
           workflows are left out because they need secrets. One file per repository. Edit the file to drop rows.
  setup    for every planned row: fork the repository (default branch only), enable Actions in the fork,
           disable all workflows of the fork, and add two workflow files to the fork's default branch:
             .github/workflows/wt-base.yml  the current upstream file
             .github/workflows/wt-fix.yml   the same file after the S3 repair of the current tool
           In both, the triggers are replaced by `workflow_dispatch` only, so the two arms differ only in
           the cache lines and nothing runs by itself.
  run      dispatch the two arms alternately (base, fix, base, fix, ...), ROUNDS times each, waiting for each
           run to finish. The first fix run fills the cache (cold); the later ones read it (warm).
  report   per repository: median job minutes of successful base runs and of warm fix runs, the difference
           and its share; overall: per-repository changes and a Wilcoxon signed-rank test across repositories.
           Writes fork/runs.csv and fork/report.csv.

Forks of public repositories are public, so their GitHub-hosted minutes are free. Delete the forks yourself
afterwards (Settings > Danger zone), the script never deletes anything.

Run:  GITHUB_TOKEN=... python fork_study.py plan|setup|run|report
The token needs the `repo` and `workflow` scopes (classic token), because it creates forks and workflow files.
"""
import base64
import csv
import json
import os
import random
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from io import StringIO

import requests

from workflowtrim.fetch import API, _headers
from workflowtrim.repair import _yaml, repair
from workflowtrim.savings import fetch_jobs
from ruamel.yaml.comments import CommentedMap

OUT = "fork"
SEED = 2029
SAMPLE = 30
ROUNDS = 5
POLL = 20                 # seconds between checks of a running workflow
RUN_LIMIT = 90 * 60       # seconds a run may take before it is recorded as timed out
SKIP_NAMES = re.compile(r"release|publish|deploy", re.I)
ARMS = {"base": "wt-base.yml", "fix": "wt-fix.yml"}


def call(method, path, body=None):
    """One GitHub REST call; returns the decoded JSON (None for an empty answer). Errors stop the script."""
    r = requests.request(method, f"{API}/{path}", headers=_headers(), json=body, timeout=60)
    if r.status_code >= 400:
        raise SystemExit(f"{method} {path}: HTTP {r.status_code} {r.text[:200]}")
    return r.json() if r.text.strip() else None


def now():
    return datetime.now(timezone.utc)


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- arms
def dispatch_only(text, name):
    """The workflow with `on: workflow_dispatch` as its only trigger and `name` as its name."""
    y = _yaml(text)
    wf = y.load(text)
    wf["on"] = CommentedMap([("workflow_dispatch", None)])
    if "name" in wf:
        wf["name"] = name
    else:
        wf.insert(0, "name", name)
    buf = StringIO()
    y.dump(wf, buf)
    return buf.getvalue()


def arms(text, repo, branch):
    """(base text, fix text) or None when the current tool makes no S3 repair on this file."""
    fixed, applied = repair(text, repo=repo, default_branch=branch, smells={"S3"})
    if not applied:
        return None
    return dispatch_only(text, "wt-base"), dispatch_only(fixed, "wt-fix")


# ---------------------------------------------------------------- plan
def plan():
    with open(os.path.join("rq3", "findings.csv"), newline="", encoding="utf-8") as f:
        repaired = sorted({(r["repo"], r["file"]) for r in csv.DictReader(f)
                           if r["smell"] == "S3" and r["outcome"] == "repaired"})
    with open(os.path.join("rq3", "review_resolved.csv"), newline="", encoding="utf-8") as f:
        correct = [(r["repo"], r["file"]) for r in csv.DictReader(f) if r["smell"] == "S3" and r["label"] == "correct"]
    rest = [k for k in repaired if k not in correct]
    random.Random(SEED).shuffle(rest)
    rows, repos = [], set()
    for source, keys in (("reviewed", correct), ("random", rest)):
        for repo, file in keys:
            if len(rows) >= SAMPLE:
                break
            if repo in repos or SKIP_NAMES.search(file):
                continue
            repos.add(repo)
            rows.append({"repo": repo, "file": file, "source": source})
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "plan.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "file", "source"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} workflow files planned in {OUT}/plan.csv; remove rows you do not want, then run setup")


# ---------------------------------------------------------------- state
def load_state():
    p = os.path.join(OUT, "state.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def save_state(state):
    with open(os.path.join(OUT, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


def put_file(fork, path, text, branch):
    """Create or replace a file on a branch of the fork."""
    try:
        sha = call("GET", f"repos/{fork}/contents/{path}?ref={branch}")["sha"]
    except SystemExit:                       # not there yet
        sha = None
    body = {"message": f"WorkflowTrim fork study: {path}", "branch": branch,
            "content": base64.b64encode(text.encode()).decode()}
    if sha:
        body["sha"] = sha
    call("PUT", f"repos/{fork}/contents/{path}", body)


# ---------------------------------------------------------------- setup
def setup():
    with open(os.path.join(OUT, "plan.csv"), newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    state = load_state()
    for row in rows:
        repo, file = row["repo"], row["file"]
        if repo in state:
            continue
        info = call("GET", f"repos/{repo}")
        branch = info["default_branch"]
        data = call("GET", f"repos/{repo}/contents/.github/workflows/{file}?ref={branch}")
        text = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        pair = arms(text, repo, branch)
        if pair is None:
            print(f"skip {repo} {file}: the current tool makes no S3 repair on the current file")
            state[repo] = {"file": file, "status": "no repair"}
            save_state(state)
            continue
        fork = call("POST", f"repos/{repo}/forks", {"default_branch_only": True})["full_name"]
        for _ in range(30):                       # forking is asynchronous
            try:
                call("GET", f"repos/{fork}")
                break
            except SystemExit:
                time.sleep(10)
        call("PUT", f"repos/{fork}/actions/permissions", {"enabled": True, "allowed_actions": "all"})
        for wf in call("GET", f"repos/{fork}/actions/workflows?per_page=100").get("workflows", []):
            if wf["state"] == "active" and os.path.basename(wf["path"]) not in ARMS.values():
                call("PUT", f"repos/{fork}/actions/workflows/{wf['id']}/disable")
        for arm, text_arm in zip(ARMS, pair):
            put_file(fork, f".github/workflows/{ARMS[arm]}", text_arm, branch)
        time.sleep(5)
        for arm in ARMS.values():
            call("PUT", f"repos/{fork}/actions/workflows/{arm}/enable")
        state[repo] = {"file": file, "fork": fork, "branch": branch, "status": "ready", "runs": []}
        save_state(state)
        print(f"ready {repo} -> {fork}")


# ---------------------------------------------------------------- run
def dispatch_and_wait(fork, branch, arm, known):
    """Dispatch one run of an arm and wait until it completes. Returns the run (dict) or None on timeout."""
    started = now()
    call("POST", f"repos/{fork}/actions/workflows/{ARMS[arm]}/dispatches", {"ref": branch})
    run = None
    while (now() - started).total_seconds() < RUN_LIMIT:
        time.sleep(POLL)
        if run is None:
            runs = call("GET", f"repos/{fork}/actions/workflows/{ARMS[arm]}/runs"
                               f"?event=workflow_dispatch&per_page=10")["workflow_runs"]
            new = [r for r in runs if r["id"] not in known
                   and (_ts(r["created_at"]) - started).total_seconds() > -60]
            if not new:
                continue
            run = min(new, key=lambda r: r["created_at"])
        run = call("GET", f"repos/{fork}/actions/runs/{run['id']}")
        if run["status"] == "completed":
            return run
    return None


def run(rounds=ROUNDS):
    state = load_state()
    for repo, s in state.items():
        if s.get("status") != "ready":
            continue
        done = {(r["arm"], r["round"]) for r in s["runs"]}
        for k in range(1, rounds + 1):
            for arm in ARMS:
                if (arm, k) in done:
                    continue
                known = {r["run_id"] for r in s["runs"]}
                r = dispatch_and_wait(s["fork"], s["branch"], arm, known)
                s["runs"].append({"arm": arm, "round": k, "run_id": r["id"] if r else None,
                                  "conclusion": r["conclusion"] if r else "timed out"})
                save_state(state)
                print(f"{repo} round {k} {arm}: {s['runs'][-1]['conclusion']}")


# ---------------------------------------------------------------- report
def job_minutes(jobs):
    return sum((_ts(j["completed_at"]) - _ts(j["started_at"])).total_seconds() / 60
               for j in jobs if j.get("started_at") and j.get("completed_at") and j.get("conclusion") == "success")


def summarise(runs):
    """runs: list of {arm, round, conclusion, minutes}. Base: successful runs; fix: successful runs after the
    first fix run (which fills the cache). Returns None when either arm has fewer than two such runs."""
    base = [r["minutes"] for r in runs if r["arm"] == "base" and r["conclusion"] == "success"]
    fixes = sorted((r for r in runs if r["arm"] == "fix"), key=lambda r: r["round"])
    warm = [r["minutes"] for r in fixes[1:] if r["conclusion"] == "success"]
    cold = fixes[0]["minutes"] if fixes and fixes[0]["conclusion"] == "success" else None
    if len(base) < 2 or len(warm) < 2:
        return None
    b, w = statistics.median(base), statistics.median(warm)
    return {"base_runs": len(base), "warm_runs": len(warm), "base_median": round(b, 2),
            "fix_cold": None if cold is None else round(cold, 2), "fix_warm_median": round(w, 2),
            "saved": round(b - w, 2), "saved_pct": round(100 * (b - w) / b, 1) if b else None}


def report():
    state = load_state()
    rows, results = [], []
    for repo, s in state.items():
        if s.get("status") != "ready":
            continue
        runs = []
        for r in s["runs"]:
            minutes = job_minutes(fetch_jobs(s["fork"], r["run_id"])) if r["run_id"] else 0.0
            runs.append({**r, "minutes": minutes})
            rows.append({"repo": repo, "file": s["file"], **r, "minutes": round(minutes, 2)})
        m = summarise(runs)
        if m:
            results.append({"repo": repo, "file": s["file"], **m})
    with open(os.path.join(OUT, "runs.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["repo", "file", "arm", "round", "run_id", "conclusion", "minutes"])
        w.writeheader()
        w.writerows(rows)
    fields = ["repo", "file", "base_runs", "warm_runs", "base_median", "fix_cold", "fix_warm_median", "saved",
              "saved_pct"]
    with open(os.path.join(OUT, "report.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    for r in results:
        print(f"  {r['repo']:<45} base {r['base_median']:>7} min  warm fix {r['fix_warm_median']:>7} min  "
              f"saved {r['saved']:>6} min ({r['saved_pct']}%)")
    if len(results) >= 6:
        from scipy.stats import wilcoxon
        p = wilcoxon([r["base_median"] for r in results], [r["fix_warm_median"] for r in results]).pvalue
        print(f"\n{len(results)} repositories, median saving "
              f"{statistics.median(r['saved_pct'] for r in results)}% of job minutes per run, Wilcoxon p = {p:.3g}")
    else:
        print(f"\n{len(results)} repositories with enough successful runs (at least 6 needed for the test)")


if __name__ == "__main__":
    if not os.environ.get("GITHUB_TOKEN"):
        raise SystemExit("Set GITHUB_TOKEN (classic token with the repo and workflow scopes).")
    steps = {"plan": plan, "setup": setup, "run": run, "report": report}
    if len(sys.argv) != 2 or sys.argv[1] not in steps:
        raise SystemExit("usage: python fork_study.py plan|setup|run|report")
    steps[sys.argv[1]]()
