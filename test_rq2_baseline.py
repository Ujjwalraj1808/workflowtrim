from rq2_baseline import prf, reimpl_smells

WF = ("on:\n  push:\n  pull_request:\n  schedule:\n    - cron: '0 0 * * *'\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
      "    steps:\n      - run: make test\n  docs:\n    if: github.repository == 'a/b'\n    runs-on: ubuntu-latest\n"
      "    timeout-minutes: 5\n    steps:\n      - run: make docs\n")


def test_transcribed_checks():
    assert reimpl_smells(WF, "ci.yml") == {"10", "5", "16", "1"}
    # string trigger form and a concurrency key without cancellation are not flagged by #5
    assert "5" not in reimpl_smells("on: pull_request\njobs:\n  a:\n    steps:\n      - run: x\n", "a.yml")
    assert "5" not in reimpl_smells(WF.replace("jobs:", "concurrency:\n  group: g\n  cancel-in-progress: false\njobs:"),
                                    "ci.yml")
    # #16 looks at push only
    assert "16" not in reimpl_smells(WF.replace("  push:\n", "  push:\n    paths: ['src/**']\n"), "ci.yml")


def test_prf():
    assert prf([True, True, False, False], [True, False, True, False]) == (1, 1, 1, 0.5, 0.5, 0.5)
    assert prf([False, False], [False, False])[3:] == (None, None, None)
