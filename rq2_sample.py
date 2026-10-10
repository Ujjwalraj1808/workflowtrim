"""RQ2 sample: draw the detector-accuracy sample and write one labelling workbook per annotator.

Usage: python rq2_sample.py
Needs repos.csv, findings.csv (and review.csv) and GITHUB_TOKEN. Writes into rq2/:
  files/              snapshot of every sampled workflow file (annotators label these, not the live repo)
  annotator_A.xlsx    two sheets: "recall" = 100 random workflow files, every smell labelled Y/N
  annotator_B.xlsx      without seeing the detector output; "precision" = 50 findings per smell, TP/FP
  tool_recall.csv     the detector's output on the recall files (annotators must not open it)
Both workbooks are identical; each annotator fills in their own, independently. Labelling rules: ANNOTATION.md.
The sample is fixed by SEED. Files are re-downloaded now and the detector is re-run on the snapshot,
so a finding is only used if it is still present in the current file.
"""
import csv
import os
import random
import requests
from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from summarize import SMELLS, load_dataset
from workflowtrim.fetch import API, _get_with_backoff, _headers
from workflowtrim.rules import detect, parse

SEED = 2026
PER_SMELL = 50
RECALL_FILES = 100
OUT = "rq2"
_snapshots = {}


def snapshot(repo, file):
    """Text of .github/workflows/<file> at the default branch now, saved under rq2/files/. None if gone."""
    if (repo, file) not in _snapshots:
        r = requests.get(f"https://raw.githubusercontent.com/{repo}/HEAD/.github/workflows/{file}", timeout=30)
        text = r.text if r.status_code == 200 else None
        if text is not None:
            with open(local_path(repo, file), "w", encoding="utf-8", newline="") as f:
                f.write(text)
        _snapshots[(repo, file)] = text
    return _snapshots[(repo, file)]


def local_path(repo, file):
    return os.path.join(OUT, "files", f"{repo.replace('/', '__')}__{file}")


def url(repo, file):
    return f"https://github.com/{repo}/blob/HEAD/.github/workflows/{file}"


def precision_sample(findings, rng):
    rows = []
    for smell in SMELLS:
        pool = [f for f in findings if f["smell"] == smell]
        rng.shuffle(pool)
        taken = 0
        for f in pool:
            text = snapshot(f["repo"], f["file"])
            if text is None or (smell, f["location"]) not in {(s, l) for s, l, _ in detect(text)}:
                continue  # file deleted or changed since collection
            rows.append(f)
            taken += 1
            if taken == PER_SMELL:
                break
        print(f"  precision {smell}: {taken} findings")
    return rows


def recall_sample(repos, rng):
    """Uniform over workflow files: pick a repo weighted by its number of workflow files, then a file."""
    weights = [int(r["n_workflows"]) for r in repos]
    chosen = []
    while len(chosen) < RECALL_FILES:
        repo = rng.choices(repos, weights)[0]["repo"]
        listing = _get_with_backoff(f"{API}/repos/{repo}/contents/.github/workflows", _headers())
        if listing.status_code != 200:
            continue
        names = sorted(i["name"] for i in listing.json()
                       if i["type"] == "file" and i["name"].endswith((".yml", ".yaml")))
        if not names:
            continue
        file = rng.choice(names)
        text = snapshot(repo, file)
        if (repo, file) in chosen or text is None or parse(text) is None:
            continue
        chosen.append((repo, file))
    print(f"  recall: {len(chosen)} files")
    return chosen


def write_workbook(path, recall, precision):
    wb = Workbook()
    ws = wb.active
    ws.title = "recall"
    ws.append(["id", "repo", "file", "local_file", "github_url", *SMELLS, "note"])
    for i, (repo, file) in enumerate(recall, 1):
        ws.append([f"R{i:03d}", repo, file, local_path(repo, file), url(repo, file)] + [""] * (len(SMELLS) + 1))
    yn = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
    ws.add_data_validation(yn)
    yn.add(f"F2:{chr(ord('F') + len(SMELLS) - 1)}{len(recall) + 1}")

    ws = wb.create_sheet("precision")
    ws.append(["id", "repo", "file", "local_file", "github_url", "smell", "location", "message", "label", "note"])
    for i, f in enumerate(precision, 1):
        ws.append([f"P{i:03d}", f["repo"], f["file"], local_path(f["repo"], f["file"]), url(f["repo"], f["file"]),
                   f["smell"], f["location"], f["message"], "", ""])
    tpfp = DataValidation(type="list", formula1='"TP,FP"', allow_blank=True)
    ws.add_data_validation(tpfp)
    tpfp.add(f"I2:I{len(precision) + 1}")
    wb.save(path)


def main():
    if not os.environ.get("GITHUB_TOKEN"):
        raise SystemExit("Set GITHUB_TOKEN first (see README).")
    if os.path.exists(os.path.join(OUT, "annotator_A.xlsx")):
        raise SystemExit("rq2/annotator_A.xlsx already exists: the sample is drawn once. Delete rq2/ to redraw.")
    os.makedirs(os.path.join(OUT, "files"), exist_ok=True)
    _, repos, findings = load_dataset()
    rng = random.Random(SEED)
    recall = recall_sample(repos, rng)
    precision = precision_sample(findings, rng)

    with open(os.path.join(OUT, "tool_recall.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "repo", "file", *SMELLS])
        for i, (repo, file) in enumerate(recall, 1):
            found = {s for s, _, _ in detect(snapshot(repo, file))}
            w.writerow([f"R{i:03d}", repo, file] + ["Y" if s in found else "N" for s in SMELLS])
    for who in "AB":
        write_workbook(os.path.join(OUT, f"annotator_{who}.xlsx"), recall, precision)
    print(f"Done -> {OUT}/annotator_A.xlsx, {OUT}/annotator_B.xlsx (label the recall sheet first)")


if __name__ == "__main__":
    main()
