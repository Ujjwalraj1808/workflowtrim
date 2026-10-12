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
    """GET with retries, up to 5 attempts:
    429 or 403 "secondary rate limit": wait Retry-After (else 60s);
    network error (timeout, dropped connection, response cut off) or 5xx server error: wait 30s."""
    for attempt in range(5):
        last = attempt == 4
        try:
            r = requests.get(url, headers=headers, timeout=30)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError):
            if last:
                raise
            print("      network error, retrying in 30s")
            time.sleep(30)
            continue
        if r.status_code == 429 or (r.status_code == 403 and "secondary rate limit" in r.text.lower()):
            if last:
                return r
            wait = int(r.headers.get("Retry-After", 60))
            print(f"      rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        if r.status_code >= 500 and not last:
            print(f"      GitHub server error {r.status_code}, retrying in 30s")
            time.sleep(30)
            continue
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
