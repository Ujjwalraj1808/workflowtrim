from rq2_score import kappa


def test_kappa_known_value():
    pairs = [("Y", "Y"), ("Y", "N"), ("N", "N"), ("N", "N")]   # po 0.75, pe 0.5
    assert kappa(pairs) == 0.5


def test_kappa_perfect_and_undefined():
    assert kappa([("Y", "Y"), ("N", "N")]) == 1.0
    assert kappa([("Y", "Y"), ("Y", "Y")]) is None              # one label only
