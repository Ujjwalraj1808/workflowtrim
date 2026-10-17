import csv

import pytest

import rq5_score

FIELDS = ["id", "repo", "smell", "pr_url", "opened_on", "outcome", "outcome_on", "first_response_on",
          "maintainer_reason"]


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def test_summary_counts_rates_and_days(tmp_path):
    p = tmp_path / "tracking.csv"
    _write(p, [
        {"id": "M1", "smell": "S1", "pr_url": "u1", "opened_on": "2026-10-16", "outcome": "merged",
         "outcome_on": "2026-10-20", "first_response_on": "2026-10-18"},
        {"id": "M2", "smell": "S1", "pr_url": "u2", "opened_on": "2026-10-16", "outcome": "closed",
         "outcome_on": "2026-10-17", "maintainer_reason": "not needed"},
        {"id": "M3", "smell": "S2", "pr_url": "u3", "opened_on": "2026-10-16", "outcome": "open"},
        {"id": "M4", "smell": "S2", "pr_url": "", "opened_on": ""},          # not sent: ignored
    ])
    rows = rq5_score.load(str(p))
    m = rq5_score.summary(rows)
    assert (m["sent"], m["merged"], m["closed"], m["open"]) == (3, 1, 1, 1)
    assert m["accept"] == pytest.approx(1 / 3) and m["accept_decided"] == 0.5
    assert m["responded"] == 2                      # M1 replied, M2 decided; M3 silent
    assert m["days_response"] == 1.5 and m["days_merge"] == 4


def test_unknown_outcome_is_rejected(tmp_path):
    p = tmp_path / "tracking.csv"
    _write(p, [{"id": "M1", "smell": "S1", "pr_url": "u1", "opened_on": "2026-10-16", "outcome": "accepted"}])
    with pytest.raises(SystemExit):
        rq5_score.load(str(p))
