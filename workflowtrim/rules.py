"""Resource-waste smell rules. Each rule takes a parsed workflow dict and returns a list of findings."""
import yaml

SETUP_ACTIONS = ("actions/setup-node", "actions/setup-python", "actions/setup-java", "actions/setup-go")
INSTALL_CMDS = ("npm ci", "npm install", "yarn install", "pip install", "mvn ", "gradle", "go build", "go test")


def parse(text):
    try:
        wf = yaml.safe_load(text)
    except yaml.YAMLError:
        return None  # malformed YAML (e.g. tab characters) -> skip this file
    if not isinstance(wf, dict):
        return None
    # PyYAML (YAML 1.1) parses the bare key `on:` as boolean True
    if True in wf:
        wf["on"] = wf.pop(True)
    wf["on"] = _normalize_triggers(wf.get("on"))
    return wf


def _normalize_triggers(on):
    """Return triggers as {event_name: config_dict_or_None}."""
    if on is None:
        return {}
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {e: None for e in on}
    return dict(on)


def _jobs(wf):
    jobs = wf.get("jobs") or {}
    # reusable-workflow calls (job has `uses:`) have no steps/timeout of their own
    return {name: job for name, job in jobs.items() if isinstance(job, dict) and "uses" not in job}


def _steps(job):
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def _uses(step, prefix):
    return str(step.get("uses", "")).startswith(prefix)


def s1_missing_timeout(wf):
    return [("S1", job, "no timeout-minutes (default 360 min)")
            for job, cfg in _jobs(wf).items() if "timeout-minutes" not in cfg]


def s2_no_concurrency_cancel(wf):
    on = wf["on"]
    if not ("push" in on or "pull_request" in on):
        return []

    def cancels(c):
        return isinstance(c, dict) and c.get("cancel-in-progress") is True

    if cancels(wf.get("concurrency")):
        return []
    if all(cancels(cfg.get("concurrency")) for cfg in _jobs(wf).values()) and _jobs(wf):
        return []
    return [("S2", "workflow", "no concurrency group with cancel-in-progress: true")]


def s3_missing_cache(wf):
    out = []
    for job, cfg in _jobs(wf).items():
        steps = _steps(cfg)
        has_cache = any(_uses(s, "actions/cache") or
                        (_uses(s, SETUP_ACTIONS) and (s.get("with") or {}).get("cache"))
                        for s in steps)
        if has_cache:
            continue
        for s in steps:
            if _uses(s, SETUP_ACTIONS):
                out.append(("S3", job, f"{s['uses']} without cache input"))
                break
            if not s.get("uses") and any(c in str(s.get("run", "")) for c in INSTALL_CMDS):
                out.append(("S3", job, "dependency install step without actions/cache"))
                break
    return out


def s4_no_path_filter(wf):
    out = []
    for ev in ("push", "pull_request"):
        if ev in wf["on"]:
            cfg = wf["on"][ev] or {}
            if "paths" not in cfg and "paths-ignore" not in cfg:
                out.append(("S4", f"on.{ev}", "no paths / paths-ignore filter"))
    return out


def s5_unguarded_fork_run(wf):
    if "schedule" not in wf["on"]:
        return []
    jobs = _jobs(wf)
    guarded = any("github.repository" in str(cfg.get("if", "")) for cfg in jobs.values())
    if guarded or not jobs:
        return []
    return [("S5", "workflow", "schedule trigger without `if: github.repository == ...` guard")]


def s6_duplicate_triggers(wf):
    on = wf["on"]
    if "push" in on and "pull_request" in on:
        push = on["push"] or {}
        if "branches" not in push:
            return [("S6", "on.push", "push has no branches filter while pull_request is also enabled")]
    return []


def s7_full_clone(wf):
    out = []
    for job, cfg in _jobs(wf).items():
        for s in _steps(cfg):
            if _uses(s, "actions/checkout") and (s.get("with") or {}).get("fetch-depth") == 0:
                out.append(("S7", job, "actions/checkout with fetch-depth: 0 (full clone)"))
    return out


def s8_artifact_retention(wf):
    out = []
    for job, cfg in _jobs(wf).items():
        for s in _steps(cfg):
            if _uses(s, "actions/upload-artifact"):
                days = (s.get("with") or {}).get("retention-days")
                if days is None:
                    out.append(("S8", job, "upload-artifact without retention-days (default 90)"))
                    continue
                try:
                    days = int(days)
                except (TypeError, ValueError):
                    continue  # expression like ${{ ... }} -> cannot judge, skip
                if days > 30:
                    out.append(("S8", job, f"upload-artifact retention-days={days} (>30)"))
    return out


RULES = [s1_missing_timeout, s2_no_concurrency_cancel, s3_missing_cache, s4_no_path_filter,
         s5_unguarded_fork_run, s6_duplicate_triggers, s7_full_clone, s8_artifact_retention]


def detect(text):
    """Run all rules on one workflow file. Returns list of (smell_id, location, message)."""
    wf = parse(text)
    if wf is None:
        return []
    findings = []
    for rule in RULES:
        findings.extend(rule(wf))
    return findings
