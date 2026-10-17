import csv
import difflib
import os

import fork_study

WF = ("name: CI\non:\n  push:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
      "      - uses: actions/checkout@v4\n      - uses: actions/setup-node@v4\n        with:\n"
      "          node-version: 20\n      - run: npm ci\n      - run: npm test\n")


def test_arms_differ_only_in_name_and_cache():
    base, fix = fork_study.arms(WF, "acme/app", "main")
    for text, name in ((base, "wt-base"), (fix, "wt-fix")):
        assert f"name: {name}\n" in text
        assert "workflow_dispatch" in text and "push" not in text and "pull_request" not in text
    changed = [l for l in difflib.ndiff(base.splitlines(), fix.splitlines()) if l[:2] in ("- ", "+ ")]
    assert sorted(changed) == ["+           cache: npm", "+ name: wt-fix", "- name: wt-base"]


def test_no_arms_without_s3_repair():
    assert fork_study.arms(WF.replace("npm ci", "echo hi").replace("npm test", "echo"), "acme/app", "main") is None


def test_summarise_uses_warm_fix_runs_only():
    runs = [{"arm": "base", "round": k, "conclusion": "success", "minutes": m} for k, m in ((1, 10), (2, 12), (3, 11))]
    runs += [{"arm": "fix", "round": k, "conclusion": "success", "minutes": m} for k, m in ((1, 13), (2, 6), (3, 7))]
    m = fork_study.summarise(runs)
    assert (m["base_median"], m["fix_cold"], m["fix_warm_median"], m["saved"], m["saved_pct"]) == (11, 13, 6.5, 4.5, 40.9)
    runs[-1]["conclusion"] = "failure"          # only one warm fix run left
    assert fork_study.summarise(runs) is None


def test_plan_reviewed_first_one_per_repo_no_release(tmp_path, monkeypatch):
    os.makedirs(tmp_path / "rq3")
    with open(tmp_path / "rq3" / "findings.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["repo", "file", "smell", "location", "outcome", "skip_reason"])
        for repo, file in (("a/x", "ci.yml"), ("a/x", "lint.yml"), ("b/y", "release.yml"), ("c/z", "test.yml"),
                           ("d/w", "build.yml")):
            w.writerow([repo, file, "S3", "job", "repaired", ""])
        w.writerow(["e/v", "ci.yml", "S3", "job", "skipped", "no install"])
    with open(tmp_path / "rq3" / "review_resolved.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "smell", "repo", "file", "label_A", "label_B", "label"])
        w.writerow(["M1", "S3", "d/w", "build.yml", "correct", "correct", "correct"])
    monkeypatch.chdir(tmp_path)
    fork_study.plan()
    with open("fork/plan.csv") as f:
        rows = list(csv.DictReader(f))
    assert rows[0] == {"repo": "d/w", "file": "build.yml", "source": "reviewed"}
    assert sorted(r["repo"] for r in rows) == ["a/x", "c/z", "d/w"]       # one per repo, no release workflow


def test_run_alternates_arms_and_records_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("fork")
    fork_study.save_state({"acme/app": {"file": "ci.yml", "fork": "me/app", "branch": "main", "status": "ready",
                                        "runs": []}})
    created = []

    def call(method, path, body=None):
        if method == "POST" and path.endswith("/dispatches"):
            created.append({"id": len(created) + 1, "created_at": fork_study.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "arm": path.split("/")[-2]})
            return None
        if "/runs?" in path:
            arm = path.split("/")[-2]
            return {"workflow_runs": [r for r in created if r["arm"] == arm]}
        run_id = int(path.rsplit("/", 1)[1])
        return {"id": run_id, "status": "completed", "conclusion": "success"}

    monkeypatch.setattr(fork_study, "call", call)
    monkeypatch.setattr(fork_study.time, "sleep", lambda s: None)
    fork_study.run(rounds=2)
    runs = fork_study.load_state()["acme/app"]["runs"]
    assert [(r["arm"], r["round"], r["run_id"]) for r in runs] == [("base", 1, 1), ("fix", 1, 2), ("base", 2, 3),
                                                                    ("fix", 2, 4)]
    fork_study.run(rounds=2)                    # already done: nothing is dispatched again
    assert len(created) == 4
