import pytest
from datetime import datetime, timedelta, timezone

from workflowtrim import savings
from workflowtrim.savings import estimate_from_runs, timeouts_from_runs


def run(rid, file, created, branch="main", event="push"):
    return {"id": rid, "path": f".github/workflows/{file}", "head_branch": branch, "event": event,
            "status": "completed", "conclusion": "success", "created_at": f"2026-09-01T{created}Z"}


def job(start, end, conclusion="success", labels=("ubuntu-latest",), name="build"):
    return {"started_at": f"2026-09-01T{start}Z", "completed_at": f"2026-09-01T{end}Z",
            "conclusion": conclusion, "labels": list(labels), "name": name}


def test_s2_overlap_sums_job_minutes_after_next_run_starts():
    runs = [run(1, "ci.yml", "10:00:00", event="pull_request"), run(2, "ci.yml", "10:10:00", event="pull_request")]
    jobs = {1: [job("10:00:00", "10:20:00"),                 # 10 min after run 2 starts
                job("10:00:00", "10:15:00"),                 # 5 min
                job("10:00:00", "10:05:00")],                # ended before -> 0
            2: [job("10:10:00", "10:30:00")]}
    r = estimate_from_runs(runs, jobs, {"ci.yml": {"S2"}})
    assert r["S2"] == {"overlaps": 1, "minutes": 15.0, "cost_usd": round(15 * 0.008, 2)}
    assert r["from"] == "2026-09-01" and r["runs"] == 2


def test_s2_ignores_other_branches_events_and_files():
    runs = [run(1, "ci.yml", "10:00:00"), run(2, "ci.yml", "10:10:00", branch="dev"),
            run(3, "ci.yml", "10:12:00", event="schedule"), run(4, "other.yml", "10:12:00")]
    jobs = {i: [job("10:00:00", "10:30:00")] for i in (1, 2, 3, 4)}
    assert estimate_from_runs(runs, jobs, {"ci.yml": {"S2"}})["S2"]["overlaps"] == 0


def test_s2_ignores_push_runs():
    runs = [run(1, "ci.yml", "10:00:00"), run(2, "ci.yml", "10:10:00")]      # push, same branch
    jobs = {1: [job("10:00:00", "10:30:00")], 2: [job("10:10:00", "10:30:00")]}
    assert estimate_from_runs(runs, jobs, {"ci.yml": {"S2"}})["S2"]["overlaps"] == 0


def test_s1_counts_failed_jobs_beyond_history_timeout_with_os_rate():
    runs = [run(1, "ci.yml", "10:00:00")]
    jobs = {1: [job("10:00:00", "10:20:00")] * 20 +                          # P95 20 min -> timeout 30
               [job("10:00:00", "10:50:00", "failure", ["windows-latest"]),  # 20 min saved @0.016
                job("10:00:00", "10:20:00", "cancelled")]}                   # under 30 -> nothing
    r = estimate_from_runs(runs, jobs, {"ci.yml": {"S1"}})
    assert r["timeouts"] == {"ci.yml": 30}
    assert r["S1"] == {"jobs": 1, "minutes": 20.0, "cost_usd": 0.32}


def _completed(minutes, n, file="ci.yml"):
    start = datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    return [{"file": file, "jobs": [{"start": start, "end": start + timedelta(minutes=minutes),
                                     "conclusion": "success"} for _ in range(n)]}]


def test_timeout_needs_min_samples_has_floor_and_skips_360():
    assert timeouts_from_runs(_completed(20, 19)) == {}               # too few samples
    assert timeouts_from_runs(_completed(2, 20)) == {"ci.yml": 10}    # floor
    assert timeouts_from_runs(_completed(20, 20), 2.0) == {"ci.yml": 40}
    assert timeouts_from_runs(_completed(300, 20)) == {}              # 450 >= 360 -> no timeout


def test_file_without_smell_and_runs_without_finished_jobs_skipped():
    runs = [run(1, "ci.yml", "10:00:00"), run(2, "ci.yml", "10:10:00")]
    jobs = {1: [job("10:00:00", "10:50:00", "failure")],
            2: [{"started_at": "2026-09-01T10:10:00Z", "completed_at": None, "conclusion": None}]}
    r = estimate_from_runs(runs, jobs, {"ci.yml": set()})
    assert r["runs"] == 1 and r["minutes"] == 0


def test_s2_pull_requests_from_different_forks_are_not_mixed():
    def pr(rid, created, fork):
        r = run(rid, "ci.yml", created, event="pull_request")
        r["head_repository"] = {"full_name": f"{fork}/repo"}
        return r
    runs = [pr(1, "10:00:00", "alice"), pr(2, "10:10:00", "bob")]           # both head_branch "main"
    jobs = {1: [job("10:00:00", "10:20:00")], 2: [job("10:10:00", "10:30:00")]}
    assert estimate_from_runs(runs, jobs, {"ci.yml": {"S2"}})["S2"]["overlaps"] == 0
    runs[1]["head_repository"] = {"full_name": "alice/repo"}               # same fork branch: superseded
    assert estimate_from_runs(runs, jobs, {"ci.yml": {"S2"}})["S2"]["overlaps"] == 1


def test_s1_counts_only_jobs_that_have_the_smell():
    # a job that already has timeout-minutes is not changed by the S1 repair, so it saves nothing
    runs = [run(1, "ci.yml", "10:00:00")]
    jobs = {1: [job("10:00:00", "10:20:00", name="test (ubuntu, 3.12)")] * 20 +      # timeout 30
               [job("10:00:00", "10:50:00", "failure", name="test (ubuntu, 3.13)"),  # 20 min saved
                job("10:00:00", "10:50:00", "failure", name="lint"),                 # has a timeout
                job("10:00:00", "10:45:00", "failure", name="Build windows-latest")]}  # 15 min saved
    r = estimate_from_runs(runs, jobs, {"ci.yml": {"S1"}}, s1_job_names={"ci.yml": ["test", "Build ${{ matrix.os }}"]})
    assert r["S1"]["jobs"] == 2 and r["S1"]["minutes"] == 35.0


def test_job_name_pattern():
    assert savings.job_name_pattern("test").fullmatch("test (ubuntu, 3.12)")
    assert not savings.job_name_pattern("test").fullmatch("test-e2e")
    assert savings.job_name_pattern("Build ${{ matrix.os }}").fullmatch("Build macos-14")


def test_job_minutes_counts_all_finished_jobs():
    runs = [run(1, "ci.yml", "10:00:00"), run(2, "other.yml", "10:00:00")]
    jobs = {1: [job("10:00:00", "10:20:00")], 2: [job("10:00:00", "10:05:00")]}
    assert estimate_from_runs(runs, jobs, {"ci.yml": {"S2"}})["job_minutes"] == 25.0


def test_fetch_jobs_follows_pages(monkeypatch):
    pages = {1: {"total_count": 150, "jobs": [{"id": i} for i in range(100)]},
             2: {"total_count": 150, "jobs": [{"id": i} for i in range(100, 150)]}}
    monkeypatch.setattr(savings, "_api", lambda url: pages[int(url.rsplit("page=", 1)[1])])
    assert len(savings.fetch_jobs("a/b", 1)) == 150


def test_fetch_runs_until_limits_the_window(monkeypatch):
    urls = []
    monkeypatch.setattr(savings, "_api", lambda url: urls.append(url) or {"workflow_runs": []})
    savings.fetch_runs("a/b", days=90, until="2026-10-10")
    assert "created=2026-07-12..2026-10-10" in urls[0]


@pytest.mark.parametrize("error", ["ConnectTimeout", "ChunkedEncodingError"])
def test_get_retries_after_a_network_error(monkeypatch, error):
    import requests
    from workflowtrim import fetch
    calls = []

    def flaky(url, headers, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise getattr(requests.exceptions, error)("network")
        return ok
    ok = type("Response", (), {"status_code": 200})()
    monkeypatch.setattr(fetch.requests, "get", flaky)
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    assert fetch._get_with_backoff("u", {}) is ok and len(calls) == 3


@pytest.mark.parametrize("status, text", [(502, "Bad Gateway"), (503, ""),
                                          (403, "You have exceeded a secondary rate limit")])
def test_get_retries_server_errors_and_secondary_rate_limit(monkeypatch, status, text):
    from workflowtrim import fetch
    responses = [type("R", (), {"status_code": status, "text": text, "headers": {}})(),
                 type("R", (), {"status_code": 200, "text": "", "headers": {}})()]
    monkeypatch.setattr(fetch.requests, "get", lambda url, headers, timeout: responses.pop(0))
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    assert fetch._get_with_backoff("u", {}).status_code == 200


def test_minutes_saved_by_both_repairs_are_counted_once():
    # a cancelled pull_request job runs 10:00-11:00; timeout 30 (S1 saves 10:30-11:00) and the next
    # run starts 10:20 (S2 saves 10:20-11:00): together 40 minutes, not 30 + 40
    runs = [run(1, "ci.yml", "10:00:00", event="pull_request"), run(2, "ci.yml", "10:20:00", event="pull_request")]
    jobs = {1: [job("10:00:00", "10:20:00")] * 20 + [job("10:00:00", "11:00:00", "cancelled")],
            2: [job("10:20:00", "10:25:00")]}
    r = estimate_from_runs(runs, jobs, {"ci.yml": {"S1", "S2"}})
    assert r["timeouts"] == {"ci.yml": 30}
    assert r["S1"]["minutes"] == 30.0
    assert r["S2"]["minutes"] == 40.0             # the 20 successful jobs ended when run 2 started
    assert r["minutes"] == 40.0
    assert r["cost_usd"] == round(40 * 0.008, 2)


def test_together_counts_a_job_that_started_after_the_next_run_only_from_its_start():
    # job of run 1 waits in the queue and starts 10:15, after run 2 started (10:10): S2 saves 10:15-10:30
    runs = [run(1, "ci.yml", "10:00:00", event="pull_request"), run(2, "ci.yml", "10:10:00", event="pull_request")]
    jobs = {1: [job("10:00:00", "10:05:00"), job("10:15:00", "10:30:00")],
            2: [job("10:10:00", "10:20:00")]}
    r = estimate_from_runs(runs, jobs, {"ci.yml": {"S2"}})
    assert r["S2"]["minutes"] == 15.0
    assert r["minutes"] == 15.0
