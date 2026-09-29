"""Download all workflow YAML files of a GitHub repository using the REST API."""
import os
import time
import requests

API = "https://api.github.com"


def _headers():
    h = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get_with_backoff(url, headers):
    """GET, but on 429 wait (Retry-After, else 60s) and retry, up to 5 times."""
    for _ in range(5):
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 429:
            return r
        wait = int(r.headers.get("Retry-After", 60))
        print(f"      429 rate limited, waiting {wait}s")
        time.sleep(wait)
    return r


def fetch_workflows(repo):
    """repo = 'owner/name'. Returns list of (filename, yaml_text)."""
    url = f"{API}/repos/{repo}/contents/.github/workflows"
    r = _get_with_backoff(url, _headers())
    if r.status_code == 404:
        return []
    if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
        raise SystemExit("GitHub API rate limit reached. Set GITHUB_TOKEN (see README) and retry.")
    r.raise_for_status()
    files = []
    for item in r.json():
        if item["type"] == "file" and item["name"].endswith((".yml", ".yaml")):
            raw = _get_with_backoff(item["url"], {**_headers(), "Accept": "application/vnd.github.raw"})
            raw.raise_for_status()
            files.append((item["name"], raw.text))
    return files
