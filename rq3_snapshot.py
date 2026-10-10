"""RQ3 snapshot: download the workflow files of every included repository.

Usage: python rq3_snapshot.py          (needs git on PATH; no GitHub token needed)
For each repository in the dataset (repos.csv + review.csv, see summarize.load_dataset) it makes a
shallow, sparse clone of the default branch, keeps only .github/workflows/*.yml|*.yaml and writes
  rq3/files/<owner>__<repo>__<file>   the workflow files
  rq3/snapshot.csv                    repo, status, default_branch, commit, n_files, date
The commit SHA makes the snapshot reproducible. Safe to re-run: repos already in snapshot.csv are skipped.
"""
import csv
import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from summarize import load_dataset

OUT = "rq3"
FILES = os.path.join(OUT, "files")
SNAPSHOT_CSV = os.path.join(OUT, "snapshot.csv")
COLUMNS = ["repo", "status", "default_branch", "commit", "n_files", "date"]
WORKERS = 8


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=600)


def snapshot(repo):
    tmp = tempfile.mkdtemp(prefix="rq3_")
    try:
        r = git("clone", "--depth", "1", "--filter=blob:none", "--sparse", "--no-tags", "-q",
                f"https://github.com/{repo}.git", tmp)
        if r.returncode != 0:
            return [repo, "clone_failed", "", "", 0, date.today().isoformat()]
        git("sparse-checkout", "set", ".github/workflows", cwd=tmp)
        branch = git("symbolic-ref", "--short", "HEAD", cwd=tmp).stdout.strip()
        commit = git("rev-parse", "HEAD", cwd=tmp).stdout.strip()
        wf_dir = os.path.join(tmp, ".github", "workflows")
        names = sorted(n for n in (os.listdir(wf_dir) if os.path.isdir(wf_dir) else [])
                       if n.endswith((".yml", ".yaml")) and os.path.isfile(os.path.join(wf_dir, n)))
        for n in names:
            shutil.copyfile(os.path.join(wf_dir, n), os.path.join(FILES, f"{repo.replace('/', '__')}__{n}"))
        return [repo, "ok" if names else "no_workflows", branch, commit, len(names), date.today().isoformat()]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    os.makedirs(FILES, exist_ok=True)
    _, repos, _ = load_dataset()
    done = set()
    if os.path.exists(SNAPSHOT_CSV):
        with open(SNAPSHOT_CSV, newline="", encoding="utf-8") as f:
            done = {r["repo"] for r in csv.DictReader(f)}
    todo = [r["repo"] for r in repos if r["repo"] not in done]
    new = not os.path.exists(SNAPSHOT_CSV)
    with open(SNAPSHOT_CSV, "a", newline="", encoding="utf-8") as f, ThreadPoolExecutor(WORKERS) as pool:
        w = csv.writer(f)
        if new:
            w.writerow(COLUMNS)
        for i, row in enumerate(pool.map(snapshot, todo), 1):
            w.writerow(row)
            f.flush()
            if i % 50 == 0 or row[1] != "ok":
                print(f"{i:>4}/{len(todo)}  {row[0]:<50} {row[1]} {row[4]} files")
    print(f"Done -> {SNAPSHOT_CSV}, {FILES}/")


if __name__ == "__main__":
    main()
