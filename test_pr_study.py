import base64
import csv
import os
from datetime import datetime, timedelta, timezone

import pr_study

WF = ("on:\n  push:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
      "      - run: make test\n")


def _ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _setup(tmp_path, rows, files):
    os.makedirs(tmp_path / "rq3" / "files")
    with open(tmp_path / "rq3" / "review_resolved.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "smell", "repo", "file", "label_A", "label_B", "label"])
        w.writeheader()
        w.writerows(rows)
    with open(tmp_path / "rq3" / "snapshot.csv", "w", newline="") as f:
        f.write("repo,status,default_branch,commit,n_files,date\n")
    for name, text in files.items():
        (tmp_path / "rq3" / "files" / name).write_text(text)


def _fake_github(monkeypatch, current, durations):
    now = datetime.now(timezone.utc)
    runs = [{"id": i, "path": ".github/workflows/ci.yml", "event": "push", "head_branch": "main",
             "head_repository": {"full_name": "acme/app"}, "created_at": _ts(now - timedelta(days=i + 1))}
            for i in range(len(durations))]
    jobs = {i: [{"started_at": _ts(now - timedelta(days=i + 1)),
                 "completed_at": _ts(now - timedelta(days=i + 1) + timedelta(minutes=d)),
                 "conclusion": "success", "name": "test", "labels": ["ubuntu-latest"]}]
            for i, d in enumerate(durations)}

    def gh(path):
        if path == "repos/acme/app":
            return {"default_branch": "main", "archived": False}
        if path.startswith("repos/acme/app/contents/.github/workflows/ci.yml"):
            return {"content": base64.b64encode(current.encode()).decode()}
        if path.startswith("repos/acme/app/pulls"):
            return [{"merged_at": _ts(now - timedelta(days=3))}]
        if path.startswith("repos/acme/app/actions/workflows/ci.yml/runs"):
            return {"workflow_runs": runs}
        raise pr_study.requests.HTTPError(response=type("R", (), {"status_code": 404})())

    monkeypatch.setattr(pr_study, "gh", gh)
    monkeypatch.setattr(pr_study, "fetch_jobs", lambda repo, run_id: jobs[run_id])


def test_s1_candidate_gets_timeout_from_history(tmp_path, monkeypatch):
    _setup(tmp_path, [{"id": "M001", "smell": "S1", "repo": "acme/app", "file": "ci.yml", "label_A": "correct",
                       "label_B": "correct", "label": "correct"}], {"acme__app__ci.yml": WF})
    _fake_github(monkeypatch, WF, [10] * 25)          # 25 successful 10-minute jobs -> 1.5 x 10 = 15
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    pr_study.main()
    with open("pr/candidates.csv") as f:
        row = next(csv.DictReader(f))
    assert row["status"] == "kept" and row["timeout"] == "15"
    assert "timeout-minutes: 15" in open("pr/M001/ci.yml").read()
    body = open("pr/M001/body.md").read()
    assert "15 minutes" in body and "(25 successful jobs)" in body


def test_changed_file_and_short_history_are_skipped(tmp_path, monkeypatch):
    _setup(tmp_path, [{"id": "M001", "smell": "S1", "repo": "acme/app", "file": "ci.yml", "label_A": "correct",
                       "label_B": "correct", "label": "correct"}], {"acme__app__ci.yml": WF})
    _fake_github(monkeypatch, WF.replace("make test", "make check"), [10] * 25)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    pr_study.main()
    with open("pr/candidates.csv") as f:
        assert next(csv.DictReader(f))["reason"] == "workflow file changed since the snapshot"
    _fake_github(monkeypatch, WF, [10] * 5)           # fewer than 20 successful jobs
    pr_study.main()
    with open("pr/candidates.csv") as f:
        assert next(csv.DictReader(f))["reason"].startswith("not enough successful job history")
