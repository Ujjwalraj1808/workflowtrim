"""Repair resource-waste smells in a GitHub Actions workflow file.

repair(text) returns (fixed_text, applied) where applied is a list of (smell_id, location).
Edits are made with ruamel.yaml round-trip mode so comments and key order survive.
Detect-only smells:
  S4 (path filter): a workflow skipped by paths-ignore leaves its required checks "Pending",
     which blocks pull requests from merging (GitHub docs, "Troubleshooting required status checks").
  S7 (full clone): removing fetch-depth: 0 can break tools that need history.
S1 is repaired only when a timeout derived from the repository's run history is given
(savings.timeouts_from_runs); without run history there is no safe value.
Repairs that cannot be made safely from the workflow file alone are skipped and the smell stays
reported: S3 when the job's install command does not show which cache fits, or `cache:` is set
explicitly; S6 when `push` already has a branches-ignore / tags / tags-ignore filter.
"""
import difflib
import functools
import io
import json
import re
import shutil
import subprocess

import yaml as pyyaml
from ruamel.yaml import YAML, YAMLError
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.util import load_yaml_guess_indent

from workflowtrim.rules import SETUP_ACTIONS, cancels, detect

RETENTION_DAYS = 7
CANCEL_PR_ONLY = "${{ github.event_name == 'pull_request' }}"
# pull_request runs share a group per PR; every other run gets its own group, so push runs are
# neither serialized nor replaced while pending (GitHub cancels a pending run in the same group).
CONCURRENCY_GROUP = "${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}"
PIP_REQUIREMENTS = re.compile(r"pip3? install[^\n]*?(?:-r|--requirement)[ =]+([^\s;&|]+)")


def run_commands(steps):
    """The shell commands of a job's `run` steps, without comment lines."""
    lines = "\n".join(str(s.get("run", "")) for s in steps).splitlines()
    return "\n".join(l for l in lines if not l.lstrip().startswith("#"))


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


def s1(wf, timeout):
    """timeout: minutes from the file's run history (savings.timeouts_from_runs)."""
    out = []
    for name, job in _jobs(wf).items():
        if "timeout-minutes" not in job:
            _insert_after(job, "runs-on", "timeout-minutes", timeout)
            out.append(("S1", name))
    return out


def s2(wf, repo, branch):
    """Cancel superseded runs for pull_request events only; push runs (deploys, releases) keep running."""
    on = wf.get("on")
    events = on.keys() if isinstance(on, dict) else ([on] if isinstance(on, str) else on or [])
    if "pull_request" not in events:
        return []
    c = wf.get("concurrency")
    if cancels(c):
        return []
    if isinstance(c, dict):
        c["cancel-in-progress"] = CANCEL_PR_ONLY
        return [("S2", "workflow")]
    block = CommentedMap()
    block["group"] = c if isinstance(c, str) else CONCURRENCY_GROUP
    block["cancel-in-progress"] = CANCEL_PR_ONLY
    if c is None:
        _insert_after(wf, "on", "concurrency", block)
    else:
        wf["concurrency"] = block          # `concurrency: <group>` string form
    return [("S2", "workflow")]


def _cache_input(action, run, earlier_uses):
    """(cache, cache-dependency-path or None) for a setup action, chosen from the job's install
    commands, or None when no choice is safe. setup-node and setup-python fail the job when the
    lock / requirements file for the chosen cache is missing, so only commands that imply that
    file are used: `npm ci` (needs package-lock.json), yarn with --frozen-lockfile / --immutable,
    pnpm only after pnpm/action-setup (setup-node needs pnpm installed), pip with -r <literal file path>.
    Commented-out shell lines are ignored (see run_commands)."""
    if action == "actions/setup-node":
        if re.search(r"\bnpm ci\b", run):
            return "npm", None
        if re.search(r"\byarn\b[^\n]*--(frozen-lockfile|immutable)\b", run):
            return "yarn", None
        if re.search(r"\bpnpm (i|install)\b", run) and any(u.startswith("pnpm/action-setup") for u in earlier_uses):
            return "pnpm", None
    elif action == "actions/setup-python":
        m = PIP_REQUIREMENTS.search(run)
        path = m.group(1).strip("'\"") if m else ""
        # a literal file path only: setup-python does not expand shell or ${{ }} variables, and the
        # lazy match can also catch another tool's -r (`pip install $(jq -r '...' f.json)`)
        if re.fullmatch(r"[\w./-]+", path):
            return "pip", path
    elif action == "actions/setup-java":
        if re.search(r"(^|[\s/])mvnw?\b", run):
            return "maven", None
        if re.search(r"(^|[\s/])gradlew?\b", run):
            return "gradle", None
    elif action == "actions/setup-go":
        if re.search(r"\bgo (build|test|vet|install|run|mod download|generate)\b", run):
            return True, None
    return None


def s3(wf, repo, branch):
    out = []
    for name, job in _jobs(wf).items():
        steps = _steps(job)
        if any(str(s.get("uses", "")).startswith("actions/cache") or
               (str(s.get("uses", "")).startswith(SETUP_ACTIONS) and (s.get("with") or {}).get("cache"))
               for s in steps):
            continue
        run = run_commands(steps)
        fixed = False
        for i, s in enumerate(steps):
            uses = str(s.get("uses", ""))
            action = next((a for a in SETUP_ACTIONS if uses.startswith(a)), None)
            if not action or (isinstance(s.get("with"), dict) and "cache" in s["with"]):
                continue   # not a setup action, or cache set explicitly (e.g. `cache: false`): respect it
            choice = _cache_input(action, run, [str(p.get("uses", "")) for p in steps[:i]])
            if choice is None:
                continue
            cache, dependency_path = choice
            if not isinstance(s.get("with"), dict):
                s["with"] = CommentedMap()
            s["with"]["cache"] = cache
            if dependency_path:
                s["with"]["cache-dependency-path"] = dependency_path
            fixed = True
        if fixed:
            out.append(("S3", name))
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
            # only scheduled runs are stopped in forks; push / pull_request runs in forks keep running
            _insert_after(job, "runs-on", "if", f"github.event_name != 'schedule' || github.repository == '{repo}'")
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
    if any(k in push for k in ("branches", "branches-ignore", "tags", "tags-ignore")):
        # branches + branches-ignore is invalid; adding branches to a tags filter makes the
        # workflow also run on every push to the default branch
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


REPAIRS = [s2, s3, s5, s6, s8]   # s1 needs a timeout, see repair()


def repair(text, repo=None, default_branch=None, timeout=None, smells=None):
    """Apply all repairs, or only those in `smells` (e.g. {"S3"}). Returns (fixed_text, applied).
    S1 needs timeout (minutes, from run history), S5 needs repo, S6 needs default_branch.
    Files that are not valid YAML are returned unchanged (the detector skips them too)."""
    try:
        y = _yaml(text)
        wf = y.load(text)
    except YAMLError:
        return text, []
    if not isinstance(wf, dict):
        return text, []
    applied = s1(wf, timeout) if timeout and (smells is None or "S1" in smells) else []
    for fix in REPAIRS:
        if smells is not None and fix.__name__.upper() not in smells:
            continue
        applied.extend(fix(wf, repo, default_branch))
    if not applied:
        return text, []
    buf = io.StringIO()
    y.dump(wf, buf)
    return _minimize(text, buf.getvalue()), applied


def _indent(line):
    return len(line) - len(line.lstrip(" "))


_FAST_LOADER = getattr(pyyaml, "CSafeLoader", pyyaml.SafeLoader)   # libyaml when installed: same result, faster


def _load(text):
    try:
        return True, pyyaml.load(text, Loader=_FAST_LOADER)
    except pyyaml.YAMLError:
        return False, None


def _merge(old, new):
    """Lines of a changed block: lines whose content is unchanged keep their original text; added lines
    are shifted by the indentation difference of the closest kept line above them."""
    out, shift = [], 0
    key = lambda l: "".join(l.split())           # lines equal up to whitespace keep their original text
    sm = difflib.SequenceMatcher(None, [key(l) for l in old], [key(l) for l in new], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                out.append(old[i])
                if new[j].strip():
                    shift = _indent(old[i]) - _indent(new[j])
        else:
            for line in new[j1:j2]:
                out.append(" " * max(0, _indent(line) + shift) + line.lstrip(" ") if line.strip() else line)
            out.extend(l for l in old[i1:i2] if not l.strip())   # keep blank lines (no data)
    return out


def _minimize(original, fixed):
    """ruamel rewrites some unrelated formatting (indentation style, spaces in flow collections, trailing
    spaces, blank lines). Put back the original text of every changed block whose original form gives
    exactly the same YAML data as the repaired file, so the diff shows only the repair."""
    ok, target = _load(fixed)
    if not ok:
        return fixed
    a, b = original.splitlines(), fixed.splitlines()
    end = "\n" if original.endswith("\n") else ""
    whole = "\n".join(_merge(a, b)) + end        # whole file at once: keeps a re-indented list consistent
    chosen = []                                   # (old block, new block) per changed hunk
    pieces = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        pieces.append(a[i1:i2] if op == "equal" else None)
        if op != "equal":
            chosen.append((len(pieces) - 1, a[i1:i2], b[j1:j2]))
    current = {k: new for k, _, new in chosen}

    def render():
        lines = [l for k, p in enumerate(pieces) for l in (p if p is not None else current[k])]
        return "\n".join(lines) + end

    for k, old, new in chosen:
        for candidate in (old, _merge(old, new)):
            if candidate == current[k]:
                continue
            keep = current[k]
            current[k] = candidate
            if _load(render()) == (True, target):
                break
            current[k] = keep
    candidates = [render()] + ([whole] if _load(whole) == (True, target) else [])
    return min(candidates, key=lambda t: _changed_lines(original, t))


def _changed_lines(original, fixed):
    return sum(1 for l in difflib.unified_diff(original.splitlines(), fixed.splitlines(), lineterm="", n=0)
               if l[:1] in "+-" and l[:3] not in ("+++", "---"))


def diff(original, fixed, name="workflow.yml"):
    return "".join(difflib.unified_diff(original.splitlines(True), fixed.splitlines(True),
                                        f"a/{name}", f"b/{name}"))


def _skeleton(text):
    wf = pyyaml.safe_load(text)
    return [(job, [(s.get("uses"), s.get("run")) for s in (cfg.get("steps") or []) if isinstance(s, dict)])
            for job, cfg in ((wf.get("jobs") or {}).items()) if isinstance(cfg, dict)]


@functools.lru_cache(maxsize=4096)
def actionlint_errors(text):
    """Set of (kind, message) that actionlint reports for a workflow text.
    Line numbers are left out because a repair shifts lines."""
    exe = shutil.which("actionlint")
    if exe is None:
        raise RuntimeError("actionlint not found on PATH: pip install -r requirements.txt")
    out = subprocess.run([exe, "-format", "{{json .}}", "-"], input=text, capture_output=True,
                         text=True, encoding="utf-8").stdout
    return {(e["kind"], e["message"]) for e in json.loads(out or "[]")}


def check(original, fixed, applied):
    """RQ3 safety checks: parses, repaired smells are gone, jobs/steps unchanged,
    and the repair adds no actionlint error that the original did not already have."""
    try:
        pyyaml.safe_load(fixed)
        parses = True
    except pyyaml.YAMLError:
        parses = False
    # compare per location: a smell left in a job that was not repaired (skipped as unsafe) does not
    # count against the repairs that were made. S2 / S5 are reported for the whole workflow.
    remaining = {(s, loc) for s, loc, _ in detect(fixed)} if parses else set()
    still_there = [(s, loc) for s, loc in applied if (s, loc) in remaining or (s, "workflow") in remaining]
    return {
        "parses": parses,
        "smells_removed": parses and not still_there,
        "steps_unchanged": parses and _skeleton(original) == _skeleton(fixed),
        "no_new_actionlint_errors": not (actionlint_errors(fixed) - actionlint_errors(original)),
    }
