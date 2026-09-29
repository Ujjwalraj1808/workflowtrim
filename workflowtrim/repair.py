"""Repair resource-waste smells in a GitHub Actions workflow file.

repair(text) returns (fixed_text, applied) where applied is a list of (smell_id, location).
Edits are made with ruamel.yaml round-trip mode so comments and key order survive.
S7 (full clone) is detect-only: removing fetch-depth: 0 can break tools that need history.
"""
import difflib
import io

import yaml as pyyaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.util import load_yaml_guess_indent

from workflowtrim.rules import SETUP_ACTIONS, detect

TIMEOUT_MINUTES = 30
RETENTION_DAYS = 7
DOC_PATHS = ["docs/**", "**.md"]
CACHE_INPUT = {"actions/setup-node": "npm", "actions/setup-python": "pip",
               "actions/setup-java": "maven", "actions/setup-go": True}


def _yaml(text):
    """Round-trip loader that keeps quotes and the file's own indentation style."""
    _, _, bsi = load_yaml_guess_indent(text)  # how far a `- ` dash sits inside its parent key
    bsi = bsi or 0
    indents = [len(l) - len(l.lstrip(" ")) for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    mapping = min((i for i in indents if i > 0), default=2)
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=mapping, sequence=bsi + 2, offset=bsi)
    return y


def _jobs(wf):
    jobs = wf.get("jobs") or {}
    return {n: j for n, j in jobs.items() if isinstance(j, dict) and "uses" not in j}


def _steps(job):
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def _on_map(wf):
    """Return the `on` block as a mapping, converting `on: push` / `on: [a, b]` forms in place."""
    on = wf.get("on")
    if isinstance(on, dict):
        return on
    m = CommentedMap()
    for ev in ([on] if isinstance(on, str) else (on or [])):
        m[ev] = None
    wf["on"] = m
    return m


def _insert_after(cmap, after_key, key, value):
    keys = list(cmap.keys())
    pos = keys.index(after_key) + 1 if after_key in keys else len(keys)
    cmap.insert(pos, key, value)


def _flow_list(items):
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def s1(wf, repo, branch):
    out = []
    for name, job in _jobs(wf).items():
        if "timeout-minutes" not in job:
            _insert_after(job, "runs-on", "timeout-minutes", TIMEOUT_MINUTES)
            out.append(("S1", name))
    return out


def s2(wf, repo, branch):
    on = wf.get("on")
    events = on.keys() if isinstance(on, dict) else ([on] if isinstance(on, str) else on or [])
    if not ("push" in events or "pull_request" in events):
        return []
    c = wf.get("concurrency")
    if isinstance(c, dict) and c.get("cancel-in-progress") is True:
        return []
    block = CommentedMap()
    block["group"] = "${{ github.workflow }}-${{ github.ref }}"
    block["cancel-in-progress"] = True
    if isinstance(c, dict):
        c["cancel-in-progress"] = True
    else:
        _insert_after(wf, "on", "concurrency", block)
    return [("S2", "workflow")]


def s3(wf, repo, branch):
    out = []
    for name, job in _jobs(wf).items():
        steps = _steps(job)
        if any(str(s.get("uses", "")).startswith("actions/cache") or
               (str(s.get("uses", "")).startswith(SETUP_ACTIONS) and (s.get("with") or {}).get("cache"))
               for s in steps):
            continue
        for s in steps:
            uses = str(s.get("uses", ""))
            action = next((a for a in CACHE_INPUT if uses.startswith(a)), None)
            if action:
                if not isinstance(s.get("with"), dict):
                    s["with"] = CommentedMap()
                s["with"]["cache"] = CACHE_INPUT[action]
                out.append(("S3", name))
                break
    return out


def s4(wf, repo, branch):
    out = []
    on = wf.get("on")
    events = on.keys() if isinstance(on, dict) else ([on] if isinstance(on, str) else on or [])
    if not ("push" in events or "pull_request" in events):
        return []
    on = _on_map(wf)
    for ev in ("push", "pull_request"):
        if ev in on:
            cfg = on[ev]
            if not isinstance(cfg, dict):
                cfg = CommentedMap()
                on[ev] = cfg
            if "paths" not in cfg and "paths-ignore" not in cfg:
                cfg["paths-ignore"] = _flow_list(DOC_PATHS)
                out.append(("S4", f"on.{ev}"))
    return out


def s5(wf, repo, branch):
    on = wf.get("on")
    events = on.keys() if isinstance(on, dict) else ([on] if isinstance(on, str) else on or [])
    if "schedule" not in events or not repo:
        return []
    jobs = _jobs(wf)
    if not jobs or any("github.repository" in str(j.get("if", "")) for j in jobs.values()):
        return []
    out = []
    for name, job in jobs.items():
        if "if" not in job:
            _insert_after(job, "runs-on", "if", f"github.repository == '{repo}'")
            out.append(("S5", name))
    return out


def s6(wf, repo, branch):
    on = wf.get("on")
    events = on.keys() if isinstance(on, dict) else ([on] if isinstance(on, str) else on or [])
    if not ("push" in events and "pull_request" in events) or not branch:
        return []
    on = _on_map(wf)
    push = on["push"]
    if not isinstance(push, dict):
        push = CommentedMap()
        on["push"] = push
    if "branches" in push:
        return []
    push.insert(0, "branches", _flow_list([branch]))
    return [("S6", "on.push")]


def s8(wf, repo, branch):
    out = []
    for name, job in _jobs(wf).items():
        for s in _steps(job):
            if not str(s.get("uses", "")).startswith("actions/upload-artifact"):
                continue
            with_ = s.get("with")
            days = with_.get("retention-days") if isinstance(with_, dict) else None
            if days is not None:
                try:
                    days = int(days)
                except (TypeError, ValueError):
                    continue
                if days <= 30:
                    continue
            if not isinstance(with_, dict):
                with_ = CommentedMap()
                s["with"] = with_
            with_["retention-days"] = RETENTION_DAYS
            out.append(("S8", name))
    return out


REPAIRS = [s1, s2, s3, s4, s5, s6, s8]


def repair(text, repo=None, default_branch=None):
    """Apply all repairs. Returns (fixed_text, applied). S5 needs repo, S6 needs default_branch."""
    y = _yaml(text)
    wf = y.load(text)
    if not isinstance(wf, dict):
        return text, []
    applied = []
    for fix in REPAIRS:
        applied.extend(fix(wf, repo, default_branch))
    if not applied:
        return text, []
    buf = io.StringIO()
    y.dump(wf, buf)
    return buf.getvalue(), applied


def diff(original, fixed, name="workflow.yml"):
    return "".join(difflib.unified_diff(original.splitlines(True), fixed.splitlines(True),
                                        f"a/{name}", f"b/{name}"))


def _skeleton(text):
    wf = pyyaml.safe_load(text)
    return [(job, [(s.get("uses"), s.get("run")) for s in (cfg.get("steps") or []) if isinstance(s, dict)])
            for job, cfg in ((wf.get("jobs") or {}).items()) if isinstance(cfg, dict)]


def check(original, fixed, applied):
    """RQ3 safety checks: parses, repaired smells are gone, jobs/steps unchanged."""
    try:
        pyyaml.safe_load(fixed)
        parses = True
    except pyyaml.YAMLError:
        parses = False
    remaining = {s for s, _, _ in detect(fixed)} if parses else set()
    fixed_smells = {s for s, _ in applied}
    return {
        "parses": parses,
        "smells_removed": parses and not (fixed_smells & remaining),
        "steps_unchanged": parses and _skeleton(original) == _skeleton(fixed),
    }
