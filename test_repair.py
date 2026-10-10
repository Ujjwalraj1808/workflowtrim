from workflowtrim.repair import repair, check
from workflowtrim.rules import detect
from test_rules import SMELLY, CLEAN


def smells(text):
    return {s for s, _, _ in detect(text)}


def test_repair_removes_all_repairable_smells():
    fixed, applied = repair(SMELLY, repo="acme/app", default_branch="main", timeout=25)
    assert smells(fixed) == {"S4", "S7"}  # S4 and S7 are detect-only
    assert {s for s, _ in applied} == {"S1", "S2", "S3", "S6", "S8"}
    assert "timeout-minutes: 25" in fixed


def test_repair_passes_safety_checks():
    fixed, applied = repair(SMELLY, repo="acme/app", default_branch="main", timeout=25)
    assert check(SMELLY, fixed, applied) == {"parses": True, "smells_removed": True, "steps_unchanged": True,
                                             "no_new_actionlint_errors": True}


def test_s1_not_repaired_without_timeout_from_history():
    fixed, applied = repair(SMELLY, repo="acme/app", default_branch="main")
    assert "S1" not in {s for s, _ in applied} and "timeout-minutes" not in fixed


def test_s2_cancels_pull_request_runs_only():
    fixed, _ = repair(SMELLY)
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in fixed
    push_only = SMELLY.replace("on: [push, pull_request]", "on: [push]")
    assert "S2" not in {s for s, _ in repair(push_only)[1]}


def test_s2_keeps_existing_concurrency_group():
    text = SMELLY.replace("jobs:", "concurrency: deploy-${{ github.ref }}\njobs:", 1)
    fixed, applied = repair(text)
    assert "group: deploy-${{ github.ref }}" in fixed and "S2" not in smells(fixed)


def test_check_flags_new_actionlint_error():
    broken = SMELLY.replace("runs-on: ubuntu-latest", "runs-on: ubuntu-latest\n    timeout-minutes: abc")
    assert check(SMELLY, broken, [])["no_new_actionlint_errors"] is False


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


def _job(steps, on="push"):
    return f"on: {on}\njobs:\n  t:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n" + \
        "".join(f"      {s}\n" for s in steps)


def test_s2_push_runs_get_their_own_concurrency_group():
    fixed, _ = repair(SMELLY)
    assert "github.event_name == 'pull_request' && github.ref || github.run_id" in fixed


def test_s3_cache_matches_the_install_command():
    fixed, applied = repair(_job(["- uses: actions/setup-node@v4", "- run: yarn install --frozen-lockfile"]))
    assert "cache: yarn" in fixed and applied == [("S3", "t")]
    fixed, _ = repair(_job(["- uses: actions/setup-java@v4", "- run: ./gradlew build"]))
    assert "cache: gradle" in fixed
    fixed, _ = repair(_job(["- uses: actions/setup-python@v5", "- run: pip install -r requirements-dev.txt"]))
    assert "cache: pip" in fixed and "cache-dependency-path: requirements-dev.txt" in fixed


def test_s3_skipped_when_cache_choice_is_not_safe():
    for steps in (["- uses: actions/setup-python@v5", "- run: python scripts/check.py"],   # installs nothing
                  ["- uses: actions/setup-node@v4", "- run: npm install"],                 # lock file not implied
                  ["- uses: actions/setup-node@v4", "- run: pnpm install"],                # pnpm not set up first
                  ["- uses: actions/setup-go@v5", "  with: {cache: false}", "- run: go test ./..."]):  # explicit opt-out
        assert "S3" not in {s for s, _ in repair(_job(steps))[1]}


def test_s3_pnpm_after_pnpm_setup():
    fixed, _ = repair(_job(["- uses: pnpm/action-setup@v4", "- uses: actions/setup-node@v4", "- run: pnpm install"]))
    assert "cache: pnpm" in fixed


def test_s5_guard_only_stops_scheduled_runs():
    fixed, applied = repair(_job(["- run: echo hi"], on="{schedule: [{cron: '0 0 * * *'}], push: {}}"), repo="acme/app")
    assert ("S5", "t") in applied
    assert "github.event_name != 'schedule' || github.repository == 'acme/app'" in fixed
    assert "S5" not in smells(fixed)


def test_s6_not_repaired_when_push_has_another_ref_filter():
    for push in ("{branches-ignore: [gh-pages]}", "{tags: ['v*']}"):
        text = _job(["- run: echo hi"], on=f"{{push: {push}, pull_request: {{}}}}")
        fixed, applied = repair(text, default_branch="main")
        assert "S6" not in {s for s, _ in applied} and "branches: [main]" not in fixed


def test_invalid_yaml_is_returned_unchanged():
    text = "on: push\njobs:\n\tt:\n    runs-on: x\n"
    assert repair(text) == (text, [])


def test_smells_removed_is_checked_per_repaired_location():
    text = ("on: push\njobs:\n"
            "  a:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n"
            "      - uses: actions/setup-node@v4\n      - run: npm ci\n"
            "  b:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n"
            "      - uses: actions/setup-node@v4\n      - run: npm install\n")      # b: not safe to repair
    fixed, applied = repair(text)
    assert applied == [("S3", "a")] and ("S3", "b") in {(s, l) for s, l, _ in detect(fixed)}
    assert check(text, fixed, applied)["smells_removed"] is True
    assert check(text, text, [("S3", "a")])["smells_removed"] is False                 # not actually fixed


def test_repair_can_be_limited_to_some_smells():
    fixed, applied = repair(SMELLY, repo="acme/app", default_branch="main", timeout=25, smells={"S3"})
    assert {s for s, _ in applied} == {"S3"} and "timeout-minutes" not in fixed
    _, applied = repair(SMELLY, repo="acme/app", default_branch="main", timeout=25, smells={"S1", "S8"})
    assert {s for s, _ in applied} == {"S1", "S8"}


def _changed(a, b):
    import difflib
    return [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
            if l[:1] in "+-" and l[:3] not in ("+++", "---")]


def test_repair_diff_keeps_unrelated_formatting():
    text = ("on:\n  push:\n    branches: [ main ]   \n\n"
            "jobs:\n  a:\n    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n        os: [ linux, mac ]\n"
            "    steps:\n    - uses: actions/checkout@v4\n    - run: |\n        echo hi")      # no final newline
    fixed, applied = repair(text, timeout=20)
    assert applied == [("S1", "a")]
    assert _changed(text, fixed) == ["+    timeout-minutes: 20"]
    assert not fixed.endswith("\n")


def test_repair_diff_keeps_blank_lines_in_list_triggers():
    text = "on:\n  - push\n  - pull_request\n\njobs:\n  a:\n    runs-on: x\n    timeout-minutes: 5\n    steps:\n      - run: echo\n"
    fixed, _ = repair(text, default_branch="main", smells={"S6"})
    assert "\n\njobs:" in fixed and "branches: [main]" in fixed
