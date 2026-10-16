"""RQ3: are the automatic repairs correct? Runs the repairer on every workflow file of the snapshot.

Usage: python rq3_snapshot.py   (once, downloads the files)
       python rq3_repair.py     (needs actionlint, see requirements.txt)
Reads rq3/snapshot.csv and rq3/files/. For every file, each repairable smell is repaired on its own
(scope S1, S2, ...) and all together (scope "all"), and every repair is checked with repair.check():
parses, smells_removed (at the repaired locations), steps_unchanged, no_new_actionlint_errors.
Scope "all" also checks that a second repair changes nothing (idempotent) and that the repair adds no
smell the original did not have (no_new_smells). S1 is repaired with a fixed timeout of S1_TIMEOUT
minutes: the value does not change any check; how the value is chosen (from run history) is part of RQ4.
Writes
  rq3/findings.csv   every finding with its outcome: repaired / skipped (not safe) / detect_only
  rq3/repairs.csv    one row per (file, scope) that was repaired, with the checks and diff size
  rq3/review.xlsx    REVIEW_PER_SMELL random single-smell repairs per smell (seed SEED) with their diff,
                     for the manual check described in rq3/REVIEW.md. Repairs listed in
                     rq3/pilot_sample.csv (the discarded pilot sample, seed 2026) are not drawn again.
and prints the RQ3 tables. Progress is saved per file in rq3/progress.jsonl, so an interrupted run
continues where it stopped; `python rq3_repair.py --minutes 9` stops after about 9 minutes.
"""
import csv
import difflib
import json
import os
import sys
import time
import random
import re
from collections import Counter, defaultdict
from multiprocessing import Pool

from openpyxl import Workbook
from openpyxl.styles import Alignment
from openpyxl.worksheet.datavalidation import DataValidation

from ruamel.yaml import YAML, YAMLError

from workflowtrim.repair import GRADLE_CACHE_ACTIONS, PIP_REQUIREMENTS, check, diff, repair, run_commands, run_lines
from workflowtrim.rules import SETUP_ACTIONS, detect, parse

OUT = "rq3"
FILES = os.path.join(OUT, "files")
REPAIRABLE = ["S1", "S2", "S3", "S5", "S6", "S8"]
DETECT_ONLY = {"S4", "S7"}
S1_TIMEOUT = 30
SEED = 2027   # 2026 drew the pilot sample, discarded (see rq3/REVIEW.md)
REVIEW_PER_SMELL = 20
CHECKS = ["parses", "smells_removed", "steps_unchanged", "no_new_actionlint_errors"]
# a removed / rewritten line is part of the repair only if it holds a key that the repair edits
INSTALL = re.compile(r"\b(npm (ci|install|i)|yarn|pnpm|pip3? install|poetry install|pipenv install|uv (sync|pip)|"
                     r"mvnw?|gradlew?|go (build|test|vet|install|run|generate|get|mod)|bundle install|composer install)\b")
REPAIR_KEYS = {
    "S1": r"timeout-minutes",
    "S2": r"concurrency|cancel-in-progress|group",
    "S3": r"with|cache",
    "S5": r"if",
    "S6": r"on|push|pull_request|branches",
    "S8": r"with|retention-days",
}


def diff_size(original, fixed, smells):
    """(lines added, lines removed, removed lines that are not part of the repair)."""
    lines = list(difflib.unified_diff(original.splitlines(), fixed.splitlines(), lineterm="", n=0))
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = [l[1:] for l in lines if l.startswith("-") and not l.startswith("---")]
    keys = re.compile(r"^\s*(-\s+)?(%s)\s*(:|$)" % "|".join(REPAIR_KEYS[s] for s in smells))
    return added, len(removed), sum(1 for l in removed if not keys.match(l))


def skip_reason(text, smell, loc):
    """Why a repairable finding was left unrepaired (the repair is skipped when it cannot be made safely)."""
    try:
        YAML().load(text)
    except YAMLError:
        return "file rejected by the YAML round-trip parser (e.g. duplicate keys)"
    wf = parse(text)
    if smell == "S3":
        steps = [st for st in (wf["jobs"][loc].get("steps") or []) if isinstance(st, dict)]
        setup = [st for st in steps if str(st.get("uses", "")).startswith(SETUP_ACTIONS)]
        if not setup:
            return "dependencies installed without a setup action (needs actions/cache)"
        if any(isinstance(st.get("with"), dict) and "cache" in st["with"] for st in setup):
            return "cache input set explicitly"
        if any(isinstance(st.get("with"), dict) and str(st["with"].get("package-manager-cache", "")).lower() == "false"
               for st in setup):
            return "package-manager-cache: false set explicitly"
        if any(str(st.get("uses", "")).startswith(GRADLE_CACHE_ACTIONS) for st in steps):
            return "a Gradle action (setup-gradle / gradle-build-action) already caches"
        run = run_commands(steps)
        if any(d is None and INSTALL.search(c) for d, c in run_lines(wf, wf["jobs"][loc], steps)):
            return "install runs in a directory that is not a literal path (expression or variable)"
        if not INSTALL.search(run):
            return "job installs no dependencies (setup action only: likely a detector false positive)"
        m = PIP_REQUIREMENTS.search(run)
        if (m and not re.fullmatch(r"[\w./-]+", m.group(1).strip("'\""))
                and any(str(st.get("uses", "")).startswith("actions/setup-python") for st in setup)):
            return "pip requirements path is not a literal file path (variable or command substitution)"
        return "install command does not imply a lock / requirements file (e.g. npm install)"
    if smell == "S5":
        return "every job already has an if condition"
    if smell == "S6":
        return "push already has branches-ignore / tags / tags-ignore"
    if smell == "S8":
        return "retention-days set explicitly by the developers"
    return "other"


def evaluate(job):
    """All results for one file: (findings rows, repair rows, review candidates)."""
    repo, branch, name, path = job
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    found = [(s, loc) for s, loc, _ in detect(text)]
    found_set = set(found)
    findings, repairs, candidates = [], [], []
    remaining_by_smell = {}
    for smell in REPAIRABLE:
        if not any(s == smell for s, _ in found):
            continue
        fixed, applied = repair(text, repo=repo, default_branch=branch, timeout=S1_TIMEOUT, smells={smell})
        remaining_by_smell[smell] = {(x, l) for x, l, _ in detect(fixed)} if applied else found_set
        if applied:
            c = check(text, fixed, applied)
            repairs.append([repo, name, smell, len(applied), *[c[k] for k in CHECKS], "", "",
                            *diff_size(text, fixed, [smell])])
            candidates.append((repo, name, smell, diff(text, fixed, name)))
    for s, loc in found:
        # repaired = the finding is gone after the repair (also when a YAML anchor shares the fixed step)
        reason = ""
        if s in DETECT_ONLY:
            outcome = "detect_only"
        elif (s, loc) not in remaining_by_smell[s]:
            outcome = "repaired"
        else:
            outcome = "skipped"                  # reason is filled in by main() (cheap, no re-run needed)
        findings.append([repo, name, s, loc, outcome, reason])
    fixed, applied = repair(text, repo=repo, default_branch=branch, timeout=S1_TIMEOUT)
    if applied:
        c = check(text, fixed, applied)
        again = repair(fixed, repo=repo, default_branch=branch, timeout=S1_TIMEOUT)[1]
        new = {(x, l) for x, l, _ in detect(fixed)} - found_set
        repairs.append([repo, name, "all", len(applied), *[c[k] for k in CHECKS], not again, not new,
                        *diff_size(text, fixed, sorted({s for s, _ in applied}))])
    return findings, repairs, candidates, parse(text) is not None


def pct(a, b):
    return f"{a * 100 / b:.1f}%" if b else "-"


def main():
    with open(os.path.join(OUT, "snapshot.csv"), newline="", encoding="utf-8") as f:
        snap = {r["repo"]: r for r in csv.DictReader(f)}
    prefixes = sorted(((r.replace("/", "__") + "__", r) for r in snap), key=lambda x: -len(x[0]))
    jobs = []
    for name in sorted(os.listdir(FILES)):
        prefix, repo = next((p, r) for p, r in prefixes if name.startswith(p))
        jobs.append((repo, snap[repo]["default_branch"], name[len(prefix):], os.path.join(FILES, name)))

    progress = os.path.join(OUT, "progress.jsonl")
    done = {}
    if os.path.exists(progress):
        with open(progress, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue                     # line cut off by an interruption: redo that file
                done[rec["path"]] = rec
    todo = [j for j in jobs if j[3] not in done]
    limit = float(sys.argv[sys.argv.index("--minutes") + 1]) * 60 if "--minutes" in sys.argv else None
    start = time.time()
    print(f"  {len(done)} files done earlier, {len(todo)} to go")
    if todo:
        with open(progress, "a", encoding="utf-8") as f, Pool(maxtasksperchild=200) as pool:
            for job, result in zip(todo, pool.imap(evaluate, todo, chunksize=4)):
                rec = dict(zip(("findings", "repairs", "candidates", "parseable"), result), path=job[3])
                f.write(json.dumps(rec) + "\n")
                f.flush()
                done[job[3]] = rec
                if len(done) % 500 == 0:
                    print(f"  {len(done)}/{len(jobs)} files")
                if limit and time.time() - start > limit:
                    pool.terminate()
                    break
    if len(done) < len(jobs):
        print(f"  stopped at {len(done)}/{len(jobs)} files; run again to continue")
        return
    findings, repairs, candidates, parseable = [], [], [], 0
    for j in jobs:
        rec = done[j[3]]
        if any(r[4] == "skipped" for r in rec["findings"]):
            with open(j[3], encoding="utf-8", errors="replace") as f:
                text = f.read()
            for r in rec["findings"]:
                if r[4] == "skipped":
                    r[5] = skip_reason(text, r[2], r[3])
        findings += rec["findings"]
        repairs += rec["repairs"]
        candidates += [tuple(c) for c in rec["candidates"]]
        parseable += rec["parseable"]

    with open(os.path.join(OUT, "findings.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["repo", "file", "smell", "location", "outcome", "skip_reason"])
        w.writerows(findings)
    with open(os.path.join(OUT, "repairs.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["repo", "file", "scope", "n_applied", *CHECKS, "idempotent", "no_new_smells",
                    "lines_added", "lines_removed", "unrelated_lines_removed"])
        w.writerows(repairs)

    print(f"\nRQ3 snapshot: {len(snap)} repos, {len(jobs)} workflow files ({len(jobs) - parseable} unparseable), "
          f"{len(findings)} findings")
    out = Counter((r[2], r[4]) for r in findings)
    print("\nTable 6 - Repair coverage (findings)")
    print(f"  {'Smell':<7}{'findings':>9}{'repaired':>10}{'skipped':>9}{'repaired %':>12}")
    for s in [*REPAIRABLE, *sorted(DETECT_ONLY)]:
        n = sum(v for (x, _), v in out.items() if x == s)
        if s in DETECT_ONLY:
            print(f"  {s:<7}{n:>9}{'detect-only':>31}")
        else:
            print(f"  {s:<7}{n:>9}{out[(s, 'repaired')]:>10}{out[(s, 'skipped')]:>9}{pct(out[(s, 'repaired')], n):>12}")

    reasons = Counter((r[2], r[5]) for r in findings if r[4] == "skipped")
    if reasons:
        print("\n  Skipped findings by reason:")
        for (smell, why), n in sorted(reasons.items()):
            print(f"    {smell}  {n:>5}  {why}")

    by_scope = defaultdict(list)
    for r in repairs:
        by_scope[r[2]].append(r)
    print("\nTable 7 - Repair correctness (automatic checks, share of repaired files)")
    print(f"  {'Scope':<7}{'files':>7}" + "".join(f"{c[:14]:>16}" for c in CHECKS) + f"{'idempotent':>12}{'no new smell':>14}{'only repair lines':>19}")
    for scope in [*REPAIRABLE, "all"]:
        rows = by_scope[scope]
        if not rows:
            continue
        cells = [pct(sum(1 for r in rows if r[4 + i]), len(rows)) for i in range(len(CHECKS))]
        extra = [pct(sum(1 for r in rows if r[8]), len(rows)), pct(sum(1 for r in rows if r[9]), len(rows))] \
            if scope == "all" else ["", ""]
        only = pct(sum(1 for r in rows if r[12] == 0), len(rows))
        print(f"  {scope:<7}{len(rows):>7}" + "".join(f"{c:>16}" for c in cells) + f"{extra[0]:>12}{extra[1]:>14}{only:>19}")
    failed = [r for r in repairs if not all(r[4:8]) or (r[2] == "all" and not all(r[8:10]))]
    print(f"\n  Repairs failing a check: {len(failed)}" + (f" -> see rq3/repairs.csv" if failed else ""))

    with open(os.path.join(OUT, "pilot_sample.csv"), newline="", encoding="utf-8") as f:
        pilot = {(r["repo"], r["file"], r["smell"]) for r in csv.DictReader(f)}
    rng = random.Random(SEED)
    wb = Workbook()
    ws = wb.active
    ws.title = "review"
    # file_url / repo_url point at the snapshot commit, so reviewers see the same file and repository
    ws.append(["id", "repo", "file", "smell", "diff", "label", "note", "file_url", "repo_url"])
    n = 0
    for smell in REPAIRABLE:
        pool_ = sorted((c for c in candidates if c[2] == smell and tuple(c[:3]) not in pilot),
                       key=lambda c: (c[0], c[1]))
        for repo, name, s, d in rng.sample(pool_, min(REVIEW_PER_SMELL, len(pool_))):
            n += 1
            commit = snap[repo]["commit"]
            ws.append([f"M{n:03d}", repo, name, s, d[:32000], "", "",
                       f"https://github.com/{repo}/blob/{commit}/.github/workflows/{name}",
                       f"https://github.com/{repo}/tree/{commit}"])
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=cell.column_letter in "EG", vertical="top")
    for col, width in zip("ABCDEFGHI", (7, 24, 24, 6, 90, 11, 40, 14, 14)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "B2"
    dv = DataValidation(type="list", formula1='"correct,incorrect,unsure"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"F2:F{n + 1}")
    wb.save(os.path.join(OUT, "review.xlsx"))
    print(f"  Manual review sample: {n} repairs -> {OUT}/review.xlsx (protocol: {OUT}/REVIEW.md)")


if __name__ == "__main__":
    main()
