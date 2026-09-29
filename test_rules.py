from workflowtrim.rules import detect

SMELLY = """
name: CI
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-node@v4
      - run: npm ci
      - uses: actions/upload-artifact@v4
        with:
          name: dist
"""

CLEAN = """
name: CI
on:
  push:
    branches: [main]
    paths-ignore: ['docs/**', '**.md']
  pull_request:
    paths-ignore: ['docs/**', '**.md']
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          cache: npm
      - run: npm ci
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          retention-days: 7
"""

SCHEDULED = """
on:
  schedule:
    - cron: '0 0 * * *'
jobs:
  nightly:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - run: echo hi
"""


def ids(text):
    return sorted({f[0] for f in detect(text)})


def test_smelly_workflow_triggers_expected_rules():
    assert ids(SMELLY) == ["S1", "S2", "S3", "S4", "S6", "S7", "S8"]


def test_clean_workflow_has_no_findings():
    assert detect(CLEAN) == []


def test_schedule_without_guard_is_s5():
    assert ids(SCHEDULED) == ["S5"]


def test_schedule_with_guard_is_clean():
    guarded = SCHEDULED.replace("runs-on: ubuntu-latest",
                                "runs-on: ubuntu-latest\n    if: github.repository == 'me/repo'")
    assert ids(guarded) == []


def test_reusable_workflow_job_is_skipped():
    text = "on: push\njobs:\n  call:\n    uses: org/repo/.github/workflows/x.yml@main\n"
    assert "S1" not in ids(text)
