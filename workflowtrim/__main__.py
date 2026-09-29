"""Usage: python -m workflowtrim owner/repo [--json]"""
import json
import sys
from workflowtrim.fetch import fetch_workflows
from workflowtrim.rules import detect


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    repo = sys.argv[1]
    as_json = "--json" in sys.argv

    files = fetch_workflows(repo)
    if not files:
        print(f"{repo}: no workflow files found")
        return

    results = []
    for name, text in files:
        for smell, location, message in detect(text):
            results.append({"repo": repo, "file": name, "smell": smell,
                            "location": location, "message": message})

    if as_json:
        print(json.dumps(results, indent=2))
        return

    print(f"{repo}: {len(files)} workflow file(s), {len(results)} finding(s)\n")
    for r in results:
        print(f"{r['smell']}  {r['file']:<28} {r['location']:<20} {r['message']}")


if __name__ == "__main__":
    main()
