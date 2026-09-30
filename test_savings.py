from workflowtrim.savings import estimate_from_runs


def run(rid, file, created, branch="main", event="push"):
    return {"id": rid, "path": f".github/workflows/{file}", "head_branch": branch, "event": event,
            "status": "completed", "conclusion": "success", "created_at": f"2026-09-01T{created}Z"}


def job(start, end, conclusion="success", labels=("ubuntu-latest",)):
    return {"started_at": f"2026-09-01T{start}Z", "completed_at": f"2026-09-01T{end}Z",
            "conclusion": conclusion, "labels": list(labels)}


def test_s2_overlap_sums_job_minutes_after_next_run_starts():
    runs = [run(1, "ci.yml", "10:00:00"), run(2, "ci.yml", "10:10:00")]
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


def test_s1_counts_failed_jobs_beyond_timeout_with_os_rate():
    runs = [run(1, "ci.yml", "10:00:00")]
    jobs = {1: [job("10:00:00", "10:50:00", "failure", ["windows-latest"]),  # 20 min saved @0.016
                job("10:00:00", "10:20:00", "cancelled"),                    # under 30 -> nothing
                job("10:00:00", "11:00:00", "success")]}                     # success -> nothing
    r = estimate_from_runs(runs, jobs, {"ci.yml": {"S1"}})
    assert r["S1"] == {"jobs": 1, "minutes": 20.0, "cost_usd": 0.32}


def test_file_without_smell_and_runs_without_finished_jobs_skipped():
    runs = [run(1, "ci.yml", "10:00:00"), run(2, "ci.yml", "10:10:00")]
    jobs = {1: [job("10:00:00", "10:50:00", "failure")],
            2: [{"started_at": "2026-09-01T10:10:00Z", "completed_at": None, "conclusion": None}]}
    r = estimate_from_runs(runs, jobs, {"ci.yml": set()})
    assert r["runs"] == 1 and r["minutes"] == 0
