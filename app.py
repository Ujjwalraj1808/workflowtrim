"""WorkflowTrim web UI (screen 1: analyze a repository).

Run:  set GITHUB_TOKEN=ghp_...   then   streamlit run app.py
"""
import pandas as pd
import streamlit as st
from workflowtrim.fetch import fetch_workflows
from workflowtrim.rules import detect

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
DETECT_ONLY = {"S7"}


def analyze(repo):
    """Returns (files, findings_df). findings_df columns: file, smell, name, location, message."""
    files = fetch_workflows(repo)
    rows = [(name, smell, SMELL_NAMES[smell], loc, msg)
            for name, text in files for smell, loc, msg in detect(text)]
    df = pd.DataFrame(rows, columns=["file", "smell", "name", "location", "message"])
    return files, df


st.set_page_config(page_title="WorkflowTrim", layout="wide")
st.title("WorkflowTrim")
st.caption("Find resource-waste smells in a repository's GitHub Actions workflows")

repo = st.text_input("GitHub repository (owner/name)", value="fastify/fastify")
if st.button("Analyze", type="primary") and repo.strip():
    with st.spinner(f"Fetching workflows of {repo}..."):
        files, df = analyze(repo.strip())

    if not files:
        st.warning("No workflow files found in .github/workflows/")
        st.stop()

    repairable = int((~df["smell"].isin(DETECT_ONLY)).sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Workflow files", len(files))
    c2.metric("Smells found", len(df))
    c3.metric("Auto-repairable", repairable)

    left, right = st.columns([1, 2])
    with left:
        st.subheader("Smells by type")
        counts = df["smell"].value_counts().reindex(SMELL_NAMES.keys(), fill_value=0)
        st.bar_chart(counts)
    with right:
        st.subheader("Findings")
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.subheader("Workflow files")
    for name, text in files:
        with st.expander(name):
            st.code(text, language="yaml")
