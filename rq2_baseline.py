"""Baseline: the detector of Khatami et al. (SCAM 2024, gha-ci-detector 0.0.3) on the RQ2 recall sample.

Four of its 22 smells correspond to ours:  #10 jobs without timeouts -> S1,
#5 stop running workflows when there is a newer commit in the PR -> S2, #16 avoid running CI when no source code
changed -> S4, #1 avoid executing scheduled workflows on forks -> S5. Its #21 (use the setup action's cache
parameter instead of actions/cache) is a different smell from our S3, so S3, S6, S7 and S8 have no counterpart.

  python rq2_baseline.py run     needs gha-ci-detector installed (pip install gha_ci_detector-0.0.3-py3-none-any.whl
                                 from the replication package, doi:10.5281/zenodo.12207164); writes
                                 rq2/baseline_khatami.csv (Y/N per file and smell, or the parse error)
  python rq2_baseline.py reimpl  the same four checks transcribed from its smell_detector.py (no install needed);
                                 writes rq2/baseline_khatami_reimpl.csv
  python rq2_baseline.py score   file-level precision, recall and F1 of both detectors on the 100 recall files,
                                 against the resolved annotator labels (rq2_score.py); uses the transcribed
                                 checks (`score-original` uses the output of `run` instead)

The package pins yamllint 1.35.1 and ruamel.yaml 0.18.6 (poetry.lock): with yamllint 1.38 its first check fails
on every file ("rules should be a mapping"), so install those versions.

Both detectors are scored against our smell definitions, which is what the annotators labelled.
"""
import csv
import os
import sys

OUT = "rq2"
MAP = {"S1": "10", "S2": "5", "S4": "16", "S5": "1"}


def run():
    from gha_ci_detector.Runner import Runner
    from gha_ci_detector.Workflow import Workflow
    with open(os.path.join(OUT, "tool_recall.csv"), newline="", encoding="utf-8") as f:
        files = [(r["id"], r["repo"], r["file"]) for r in csv.DictReader(f)]
    rows = []
    for rid, repo, file in files:
        path = os.path.join(OUT, "files", f"{repo.replace('/', '__')}__{file}")
        text = open(path, encoding="utf-8", errors="replace").read()
        row = {"id": rid, "error": ""}
        try:
            numbers = {s.split(".")[0] for s in Runner(Workflow(text, file)).run_all()}
        except Exception as e:                  # the baseline crashes on some files: recorded, counted as N
            numbers, row["error"] = set(), f"{type(e).__name__}: {e}"[:120]
        row.update({s: "Y" if n in numbers else "N" for s, n in MAP.items()})
        rows.append(row)
    with open(os.path.join(OUT, "baseline_khatami.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", *MAP, "error"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} files, {sum(1 for r in rows if r['error'])} with errors -> {OUT}/baseline_khatami.csv")


CI_WORDS = ["lint", "build", "test", "compile", "style", "ci", "codeql", "cypress"]
FORK_GUARDS = ["github.repository", "github.repository_owner", "repo.full_name "]


def reimpl_smells(text, name):
    """The four checks of gha-ci-detector 0.0.3 (smell_detector.py) that match our smells, transcribed:
    action_should_have_timeout (#10), stop_workflows_for_old_commit (#5), running_ci_when_nothing_changed (#16)
    and scheduled_workflows_on_forks (#1). Same parser (ruamel.yaml round trip, YAML 1.2)."""
    import io
    from ruamel.yaml import YAML
    wf = YAML().load(io.StringIO(text))
    jobs = [j for j in (wf.get("jobs") or {}).values()]
    on = wf.get("on")
    found = set()
    if any("steps" in j and "timeout-minutes" not in j for j in jobs):
        found.add("10")
    if "concurrency" not in wf and isinstance(on, dict) and ("pull_request" in on or "pull_request_target" in on):
        found.add("5")
    if any(w in name.lower() for w in CI_WORDS) or any(w in str(wf).lower() for w in CI_WORDS):
        if "on" in wf and isinstance(on, dict) and "push" in on:
            push = on["push"]
            if not (isinstance(push, dict) and ("paths" in push or "paths-ignore" in push)):
                found.add("16")
        elif "on" not in wf:
            found.add("16")
    if isinstance(on, dict) and "schedule" in on:
        if any(j.get("if") is None or not any(w in j["if"] for w in FORK_GUARDS) for j in jobs):
            found.add("1")
    return found


def reimpl():
    """Like run(), with the transcribed checks; writes rq2/baseline_khatami_reimpl.csv."""
    with open(os.path.join(OUT, "tool_recall.csv"), newline="", encoding="utf-8") as f:
        files = [(r["id"], r["repo"], r["file"]) for r in csv.DictReader(f)]
    rows = []
    for rid, repo, file in files:
        path = os.path.join(OUT, "files", f"{repo.replace('/', '__')}__{file}")
        numbers = reimpl_smells(open(path, encoding="utf-8", errors="replace").read(), file)
        rows.append({"id": rid, **{s: "Y" if n in numbers else "N" for s, n in MAP.items()}, "error": ""})
    with open(os.path.join(OUT, "baseline_khatami_reimpl.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", *MAP, "error"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} files -> {OUT}/baseline_khatami_reimpl.csv")


def prf(pred, truth):
    tp = sum(p and t for p, t in zip(pred, truth))
    fp = sum(p and not t for p, t in zip(pred, truth))
    fn = sum(t and not p for p, t in zip(pred, truth))
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else None
    return tp, fp, fn, p, r, f1


def final_labels():
    """Resolved recall labels {(id, smell): 'Y'/'N'}, as in rq2_score.py."""
    from rq2_score import read
    a, b = read("A"), read("B")
    with open(os.path.join(OUT, "resolved.csv"), newline="", encoding="utf-8-sig") as f:
        resolved = {(r["id"], r["smell"]): r["label"].strip().upper() for r in csv.DictReader(f)}
    return {k: (a[k] if a[k] == b[k] else resolved[k]) for k in a if k[0].startswith("R")}


def score(baseline="baseline_khatami_reimpl.csv"):
    truth = final_labels()
    with open(os.path.join(OUT, "tool_recall.csv"), newline="", encoding="utf-8") as f:
        ours = {r["id"]: r for r in csv.DictReader(f)}
    with open(os.path.join(OUT, baseline), newline="", encoding="utf-8") as f:
        base = {r["id"]: r for r in csv.DictReader(f)}
    ids = sorted(ours)
    fmt = lambda x: "  -  " if x is None else f"{x:.2f}"
    print(f"{'smell':<6}{'actual':>7}  {'tool':<12}{'TP':>4}{'FP':>4}{'FN':>4}{'P':>7}{'R':>7}{'F1':>7}")
    for s in MAP:
        t = [truth[(i, s)] == "Y" for i in ids]
        for name, src in (("WorkflowTrim", ours), ("Khatami", base)):
            tp, fp, fn, p, r, f1 = prf([src[i][s] == "Y" for i in ids], t)
            print(f"{s:<6}{sum(t):>7}  {name:<12}{tp:>4}{fp:>4}{fn:>4}{fmt(p):>7}{fmt(r):>7}{fmt(f1):>7}")


if __name__ == "__main__":
    {"run": run, "reimpl": reimpl, "score": score,
     "score-original": lambda: score("baseline_khatami.csv")}[sys.argv[1]]()
