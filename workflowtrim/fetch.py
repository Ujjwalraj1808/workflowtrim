"""Download all workflow YAML files of a GitHub repository using the REST API."""
import os
import requests

API = "https://api.github.com"


def _headers():
    h = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def fetch_workflows(repo):
    """repo = 'owner/name'. Returns list of (filename, yaml_text)."""
    url = f"{API}/repos/{repo}/contents/.github/workflows"
    r = requests.get(url, headers=_headers(), timeout=30)
    if r.status_code == 404:
        return []
    if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
        raise SystemExit("GitHub API rate limit reached. Set GITHUB_TOKEN (see README) and retry.")
    r.raise_for_status()
    files = []
    for item in r.json():
        if item["type"] == "file" and item["name"].endswith((".yml", ".yaml")):
            raw = requests.get(item["download_url"], headers=_headers(), timeout=30)
            raw.raise_for_status()
            files.append((item["name"], raw.text))
    return files
