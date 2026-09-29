from workflowtrim.repair import repair, check
from workflowtrim.rules import detect
from test_rules import SMELLY, CLEAN


def smells(text):
    return {s for s, _, _ in detect(text)}


def test_repair_removes_all_repairable_smells():
    fixed, applied = repair(SMELLY, repo="acme/app", default_branch="main")
    assert smells(fixed) == {"S7"}  # S7 is detect-only
    assert {s for s, _ in applied} == {"S1", "S2", "S3", "S4", "S6", "S8"}


def test_repair_passes_safety_checks():
    fixed, applied = repair(SMELLY, repo="acme/app", default_branch="main")
    assert check(SMELLY, fixed, applied) == {"parses": True, "smells_removed": True, "steps_unchanged": True}


def test_clean_workflow_is_untouched():
    fixed, applied = repair(CLEAN)
    assert applied == []
    assert fixed == CLEAN


def test_s6_skipped_without_default_branch():
    _, applied = repair(SMELLY)
    assert "S6" not in {s for s, _ in applied}


def test_comments_survive():
    text = "name: CI  # top\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest  # keep\n    steps:\n      - run: echo hi\n"
    fixed, _ = repair(text)
    assert "# top" in fixed and "# keep" in fixed
