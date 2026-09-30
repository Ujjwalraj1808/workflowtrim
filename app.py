"""WorkflowTrim web UI (screen 1: analyze a repository).

Run:  set GITHUB_TOKEN=ghp_...   then   streamlit run app.py
"""
import pandas as pd
import requests
import streamlit as st
from workflowtrim.fetch import API, _headers, fetch_workflows
from workflowtrim.repair import check, diff, repair
from workflowtrim.rules import detect
from workflowtrim.savings import estimate

SMELL_NAMES = {
    "S1": "Missing job timeout",
    "S2": "No concurrency cancellation",
    "S3": "Missing dependency cache",
    "S4": "No path filtering",
    "S5": "Unguarded run on forks",
    "S6": "Duplicate triggers",
    "S7": "Unnecessary full clone",
    "S8": "Over-long artifact retention",
}


def analyze(repo):
    """Returns (files, findings_df, repairs).
    findings_df columns: file, smell, name, location, message.
    repairs: {file: (fixed_text, applied, checks)} for files with at least one fix."""
    files = fetch_workflows(repo)
    branch = requests.get(f"{API}/repos/{repo}", headers=_headers(), timeout=30).json().get("default_branch")
    rows = [(name, smell, SMELL_NAMES[smell], loc, msg)
            for name, text in files for smell, loc, msg in detect(text)]
    df = pd.DataFrame(rows, columns=["file", "smell", "name", "location", "message"])
    repairs = {}
    for name, text in files:
        fixed, applied = repair(text, repo=repo, default_branch=branch)
        if applied:
            repairs[name] = (fixed, applied, check(text, fixed, applied))
    return files, df, repairs


st.set_page_config(page_title="WorkflowTrim", layout="wide")
st.title("WorkflowTrim")
st.caption("Find resource-waste smells in a repository's GitHub Actions workflows")

repo = st.text_input("GitHub repository (owner/name)", value="fastify/fastify")
if st.button("Analyze", type="primary") and repo.strip():
    with st.spinner(f"Fetching workflows of {repo}..."):
        files, df, repairs = analyze(repo.strip())

    if not files:
        st.warning("No workflow files found in .github/workflows/")
        st.stop()

    smells_by_file = df.groupby("file")["smell"].apply(set).to_dict()
    bar = st.progress(0, text="Reading run history...")
    savings = estimate(repo.strip(), smells_by_file, days=90,
                       progress=lambda i, n: bar.progress(i / n, text=f"Reading jobs of run {i}/{n}..."))
    bar.empty()

    repairable = sum(len(applied) for _, applied, _ in repairs.values())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Workflow files", len(files))
    c2.metric("Smells found", len(df))
    c3.metric("Auto-repairable", repairable)
    c4.metric(f"Est. waste ({savings['runs']} runs)", f"${savings['cost_usd']}", f"{savings['minutes']:.0f} min")

    left, right = st.columns([1, 2])
    with left:
        st.subheader("Smells by type")
        counts = df["smell"].value_counts().reindex(SMELL_NAMES.keys(), fill_value=0)
        st.bar_chart(counts)
    with right:
        st.subheader("Findings")
        st.dataframe(df, width="stretch", hide_index=True)

    st.subheader("Repairs")
    st.caption("S7 (full clone) is reported but not repaired: removing the full history can break tools that need it.")
    for name, text in files:
        if name not in repairs:
            continue
        fixed, applied, checks = repairs[name]
        ok = all(checks.values())
        with st.expander(f"{name} — {len(applied)} fixes {'✓' if ok else '✗'}"):
            st.write(", ".join(f"{s} ({loc})" for s, loc in applied))
            st.write(" · ".join(f"{'✓' if v else '✗'} {k.replace('_', ' ')}" for k, v in checks.items()))
            st.code(diff(text, fixed, name), language="diff")
            st.download_button("Download fixed YAML", fixed, file_name=name, mime="text/yaml", key=name)

    st.subheader("Savings (estimated)")
    st.caption(f"From {savings['runs']} completed runs ({savings['from']} to {savings['to']}), job-level timing. "
               "Only S1 and S2 are estimated from run history; cost uses GitHub-hosted runner rates by OS.")
    s1, s2 = savings["S1"], savings["S2"]
    st.dataframe(pd.DataFrame([
        ("S2", "Job-minutes cancel-in-progress would have stopped", s2["overlaps"], s2["minutes"], s2["cost_usd"]),
        ("S1", "Failed/cancelled jobs longer than 30 min", s1["jobs"], s1["minutes"], s1["cost_usd"]),
    ], columns=["smell", "what", "count", "minutes wasted", "cost (USD)"]), width="stretch", hide_index=True)
