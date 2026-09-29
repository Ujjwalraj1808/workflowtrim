# WorkflowTrim (v0.1 — Detector)

GitHub repo ki `.github/workflows/*.yml` files download karke 8 resource-waste smells (S1–S8) detect karta hai.

## Setup (pehli baar, step by step)

1. **Python install karo** — python.org se 3.10+ download karo. Install karte waqt **"Add Python to PATH"** tick karna mat bhoolna.
2. Ye folder (`workflowtrim`) kahin rakho, phir terminal / Command Prompt usi folder mein kholo.
3. Libraries install karo:
   ```
   pip install -r requirements.txt
   ```
4. **GitHub token banao** (zaroori — bina token sirf 60 requests/hour milti hain):
   - GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token
   - Koi scope tick mat karo (public repos ke liye zarurat nahi) → Generate → token copy karo
   - Terminal mein set karo:
     - Windows CMD: `set GITHUB_TOKEN=ghp_xxxxx`
     - Windows PowerShell: `$env:GITHUB_TOKEN="ghp_xxxxx"`
     - Mac/Linux: `export GITHUB_TOKEN=ghp_xxxxx`

## Chalana

```
python -m workflowtrim pallets/flask
python -m workflowtrim pallets/flask --json     # JSON output (dataset banane ke liye)
```

Output example:
```
pallets/flask: 4 workflow file(s), 3 finding(s)

S1  tests.yaml                   tests                no timeout-minutes (default 360 min)
S3  tests.yaml                   tests                actions/setup-python without cache input
```

## Tests chalana

```
python -m pytest -q
```

## Files

- `workflowtrim/fetch.py` — GitHub API se workflow files laata hai
- `workflowtrim/rules.py` — 8 smell rules (S1–S8), har rule ek function
- `workflowtrim/__main__.py` — command-line entry
- `test_rules.py` — unit tests

## Smells

| ID | Smell |
|----|-------|
| S1 | Job pe `timeout-minutes` nahi |
| S2 | `concurrency` + `cancel-in-progress: true` nahi |
| S3 | setup-node/python/java/go ya npm/pip install bina cache ke |
| S4 | push/pull_request pe `paths` / `paths-ignore` nahi |
| S5 | `schedule` trigger bina `if: github.repository == ...` guard ke |
| S6 | push (bina branches filter) + pull_request dono — double run |
| S7 | `actions/checkout` with `fetch-depth: 0` |
| S8 | `upload-artifact` bina `retention-days` ya > 30 |

## Agla step

- Repairer (`repair.py`) — ruamel.yaml se fix karke diff banana
- Savings estimator — `/actions/runs` API se run history
- Dataset script — 500+ repos pe `--json` chala ke CSV banana
