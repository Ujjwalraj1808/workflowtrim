"""RQ5 developer study: what happened to the repair pull requests (pr/tracking.csv).

tracking.csv has one row per pull request that was sent:
  id, repo, smell, pr_url, opened_on, outcome, outcome_on, first_response_on, maintainer_reason
outcome is "merged", "closed" (closed without merging) or "open" (no decision yet); dates are YYYY-MM-DD,
empty when it did not happen. Rows without a pr_url (not sent) are ignored.

Reported per smell and overall:
  - acceptance rate = merged / sent, with a 95% Wilson interval;
  - acceptance among decided = merged / (merged + closed);
  - response rate = pull requests with any maintainer response (first_response_on, or a decision) / sent;
  - median days from opening to the first response and to the merge;
  - the maintainers' reasons for closing, as written in the sheet.

Run:  python rq5_score.py
"""
import csv
import os
import statistics
from datetime import date

from rq3_review_score import wilson

TRACKING = os.path.join("pr", "tracking.csv")
SMELLS = ["S1", "S2", "S3", "S5", "S6", "S8"]
OUTCOMES = {"merged", "closed", "open"}


def day(s):
    return date.fromisoformat(s.strip()) if s and s.strip() else None


def load(path=TRACKING):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("pr_url") or "").strip()]
    for r in rows:
        r["outcome"] = (r.get("outcome") or "open").strip().lower() or "open"
        if r["outcome"] not in OUTCOMES:
            raise SystemExit(f"{r['id']}: outcome must be merged, closed or open, not {r['outcome']!r}")
        r["opened"], r["decided"] = day(r["opened_on"]), day(r.get("outcome_on"))
        r["responded"] = day(r.get("first_response_on")) or (r["decided"] if r["outcome"] != "open" else None)
    return rows


def summary(rows):
    n = len(rows)
    merged = sum(r["outcome"] == "merged" for r in rows)
    closed = sum(r["outcome"] == "closed" for r in rows)
    responded = sum(r["responded"] is not None for r in rows)
    to_response = [(r["responded"] - r["opened"]).days for r in rows if r["responded"]]
    to_merge = [(r["decided"] - r["opened"]).days for r in rows if r["outcome"] == "merged" and r["decided"]]
    return {"sent": n, "merged": merged, "closed": closed, "open": n - merged - closed,
            "accept": merged / n if n else None, "ci": wilson(merged, n) if n else None,
            "accept_decided": merged / (merged + closed) if merged + closed else None,
            "responded": responded,
            "days_response": statistics.median(to_response) if to_response else None,
            "days_merge": statistics.median(to_merge) if to_merge else None}


def fmt(x, pct=False):
    if x is None:
        return "-"
    return f"{100 * x:.1f}%" if pct else f"{x:g}"


def main():
    rows = load()
    print(f"RQ5: {len(rows)} pull requests sent")
    print(f"  {'smell':<6}{'sent':>5}{'merged':>8}{'closed':>8}{'open':>6}{'accepted':>10}  {'95% CI':<16}"
          f"{'of decided':>11}{'responded':>11}{'days to reply':>15}{'days to merge':>15}")
    for s in SMELLS + ["all"]:
        part = rows if s == "all" else [r for r in rows if r["smell"] == s]
        if not part:
            continue
        m = summary(part)
        ci = f"[{100 * m['ci'][0]:.1f}, {100 * m['ci'][1]:.1f}]"
        print(f"  {s:<6}{m['sent']:>5}{m['merged']:>8}{m['closed']:>8}{m['open']:>6}{fmt(m['accept'], True):>10}  "
              f"{ci:<16}{fmt(m['accept_decided'], True):>11}{m['responded']:>11}"
              f"{fmt(m['days_response']):>15}{fmt(m['days_merge']):>15}")
    reasons = [(r["id"], r["repo"], r["smell"], r["maintainer_reason"].strip())
               for r in rows if r["outcome"] == "closed" and (r.get("maintainer_reason") or "").strip()]
    if reasons:
        print("\nReasons given when a pull request was closed")
        for i, repo, s, why in reasons:
            print(f"  {i} {s} {repo}: {why}")


if __name__ == "__main__":
    main()
