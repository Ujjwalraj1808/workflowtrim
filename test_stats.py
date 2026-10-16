from stats import holm, wilson


def test_holm_matches_hand_computation():
    # p sorted 0.01, 0.02, 0.04 -> 0.03, 0.04, 0.04 (monotone), returned in input order
    assert [round(x, 6) for x in holm([0.04, 0.01, 0.02])] == [0.04, 0.03, 0.04]


def test_holm_caps_at_one():
    assert holm([0.6, 0.9]) == [1.0, 1.0]


def test_wilson_known_values():
    lo, hi = wilson(320, 400)
    assert round(lo, 2) == 0.76 and round(hi, 2) == 0.84
    lo, hi = wilson(50, 50)
    assert hi == 1.0 or round(hi, 6) == 1.0
    assert round(lo, 2) == 0.93
