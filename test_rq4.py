from rq4_savings import analyse


def _run(rid, file, created, event="pull_request"):
    return {"id": rid, "path": f".github/workflows/{file}", "event": event, "conclusion": "success",
            "head_branch": "fix", "created_at": created, "head_repository": {"full_name": "a/b"}}


def _job(start, end):
    return [{"name": "build", "started_at": start, "completed_at": end, "conclusion": "success",
             "labels": ["ubuntu-latest"]}]


def test_only_runs_after_the_files_last_change_are_used():
    raw = {"changed": {"ci.yml": "2026-09-01T12:00:00Z"},
           "runs": [_run(1, "ci.yml", "2026-09-01T10:00:00Z"),        # older workflow version
                    _run(2, "ci.yml", "2026-09-02T10:00:00Z"),
                    _run(3, "ci.yml", "2026-09-02T10:10:00Z"),
                    _run(4, "other.yml", "2026-09-02T10:10:00Z")],    # no S1 / S2 in this file
           "jobs": {"1": _job("2026-09-01T10:00:00Z", "2026-09-01T10:30:00Z"),
                    "2": _job("2026-09-02T10:00:00Z", "2026-09-02T10:20:00Z"),
                    "3": _job("2026-09-02T10:10:00Z", "2026-09-02T10:30:00Z"),
                    "4": _job("2026-09-02T10:10:00Z", "2026-09-02T10:30:00Z")}}
    e = analyse(raw, {"ci.yml": {"S2"}}, {}, 1.5)
    assert e["runs"] == 2 and e["job_minutes"] == 40.0
    assert e["S2"] == {"overlaps": 1, "minutes": 10.0, "cost_usd": 0.08}
    assert e["days"] == 1.0


def test_job_names_use_name_or_key(tmp_path, monkeypatch):
    import rq4_savings
    (tmp_path / "rq3" / "files").mkdir(parents=True)
    (tmp_path / "rq3" / "files" / "a__b__ci.yml").write_text(
        "on: push\njobs:\n  test:\n    name: Unit tests\n    runs-on: x\n  lint:\n    runs-on: x\n  bad: 3\n")
    monkeypatch.chdir(tmp_path)
    assert set(rq4_savings.job_names("a/b", "ci.yml", {"test", "lint", "bad"})) == {"bad", "lint", "Unit tests"}


def test_signature_keeps_only_s1_jobs_and_s2():
    from rq4_savings import signature
    text = ("on: [push, pull_request]\njobs:\n  a:\n    runs-on: x\n    steps: [{run: echo}]\n"
            "  b:\n    runs-on: x\n    timeout-minutes: 5\n    steps: [{run: echo}]\n")
    assert signature(text) == (frozenset({"a"}), True)
    assert signature(None) is None


def test_stable_since_walks_back_while_the_smells_are_unchanged():
    from rq4_savings import stable_since
    same, other = (frozenset({"a"}), True), (frozenset(), True)
    start = "2026-07-12T00:00:00Z"
    # newest first: snapshot version, a version-bump commit (same smells), then the commit that added the smells
    versions = [("2026-09-20T00:00:00Z", same), ("2026-09-01T00:00:00Z", same), ("2026-08-01T00:00:00Z", other)]
    assert stable_since(versions, same, same, start) == "2026-09-01T00:00:00Z"
    # every version in the window has the same smells, and so had the one before the window
    assert stable_since(versions[:2], same, same, start) == start
    # ... but the one before the window did not
    assert stable_since(versions[:2], other, same, start) == "2026-09-01T00:00:00Z"
    # the file was not changed in the window
    assert stable_since([], same, same, start) == start


def test_analyse_uses_stable_since_when_present():
    raw = {"changed": {"ci.yml": "2026-09-02T00:00:00Z"}, "stable_since": {"ci.yml": "2026-09-01T00:00:00Z"},
           "runs": [_run(1, "ci.yml", "2026-09-01T10:00:00Z")],
           "jobs": {"1": _job("2026-09-01T10:00:00Z", "2026-09-01T10:30:00Z")}}
    assert analyse(raw, {"ci.yml": {"S2"}}, {}, 1.5)["runs"] == 1
    assert analyse(raw, {"ci.yml": {"S2"}}, {}, 1.5, since="changed")["runs"] == 0
