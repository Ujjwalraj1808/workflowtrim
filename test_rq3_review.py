from rq3_review_score import kappa, wilson


def test_kappa_perfect_and_chance():
    po, k = kappa(["correct", "incorrect"], ["correct", "incorrect"])
    assert po == 1 and k == 1
    po, k = kappa(["correct"] * 4, ["correct"] * 4)
    assert po == 1 and k is None          # all labels identical: kappa undefined


def test_kappa_known_value():
    a = ["correct", "correct", "incorrect", "incorrect"]
    b = ["correct", "incorrect", "incorrect", "incorrect"]
    po, k = kappa(a, b)                   # po 0.75, pe 0.5 -> kappa 0.5
    assert po == 0.75 and abs(k - 0.5) < 1e-9


def test_wilson_interval_contains_estimate():
    lo, hi = wilson(100, 120)
    assert lo < 100 / 120 < hi
    assert round(lo, 3) == 0.757 and round(hi, 3) == 0.889
