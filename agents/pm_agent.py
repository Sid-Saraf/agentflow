#!/usr/bin/env python3
"""
PM Agent — breaks a feature into epics and implementation-ready GitHub issues.

Usage:
    python agents/pm_agent.py "Add Google Places geocoding with Nominatim fallback"
    python agents/pm_agent.py --repo owner/name "feature description"
    python agents/pm_agent.py --from-spec spec.md
"""
from __future__ import annotations
import sys
import json
import argparse
import subprocess
from pathlib import Path
from base import gpt, gh, current_repo, ensure_labels

CODEBASE_SUMMARY_PROMPT = """Summarize this codebase in 200 words for an engineer writing GitHub issues.
Cover: what it does, main components, tech stack, key files. Be concrete."""

BREAKDOWN_PROMPT = """You are a product manager breaking down a feature into GitHub issues.

Target repo context:
{context}

Feature: {feature}

Break this into 1-3 epics, each with 2-5 implementation issues.
Return JSON:
{{
  "epics": [
    {{
      "title": "Epic: <name>",
      "description": "What this epic covers and why",
      "issues": [
        {{
          "title": "imperative title max 60 chars",
          "body": "## Problem\\n...\\n## Solution\\n...\\n## Acceptance Criteria\\n- [ ] ...\\n## Files to Touch\\n- path/to/file\\n## Tests Required\\n- [ ] test description",
          "labels": ["ready-for-build"],
          "estimate": "S|M|L"
        }}
      ]
    }}
  ]
}}

Rules:
- Each issue must be independently implementable
- Acceptance criteria must be testable checkboxes
- Files to touch must be real paths from the codebase
- Smallest possible scope — no speculation, no future-proofing
- S = <2hrs, M = 2-4hrs, L = 4-8hrs for an agent"""


def read_codebase(path: str = ".") -> str:
    """Quick codebase scan for context."""
    files = []
    for ext in ["*.py", "*.ts", "*.tsx", "*.js", "*.md"]:
        files.extend(Path(path).rglob(ext))

    # Read key files (README, main entry points, up to 4000 chars total)
    content = ""
    for f in sorted(files)[:20]:
        if any(skip in str(f) for skip in [".git", "node_modules", "__pycache__", ".next"]):
            continue
        try:
            text = f.read_text()[:300]
            content += f"\n--- {f} ---\n{text}\n"
            if len(content) > 4000:
                break
        except Exception:
            pass
    return content


def create_epic_and_issues(epic: dict, repo: str) -> None:
    epic_url = gh(f'issue create --repo {repo} --title "{epic["title"]}" --body "{epic["description"]}" --label "epic"')
    print(f"  Epic: {epic_url}")

    for issue in epic["issues"]:
        estimate = issue.get("estimate", "M")
        body = (issue["body"] + f"\n\n**Estimate:** {estimate}\n**Part of:** {epic['title']}").replace('"', "'")
        labels = ",".join(issue.get("labels", ["ready-for-build"]))
        url = gh(f'issue create --repo {repo} --title "{issue["title"]}" --body "{body}" --label "{labels}"')
        print(f"    Issue ({estimate}): {url}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("feature", nargs="?", help="Feature description")
    parser.add_argument("--repo", help="GitHub repo (owner/name)")
    parser.add_argument("--from-spec", help="Read feature from a spec file")
    args = parser.parse_args()

    repo = args.repo or current_repo()
    ensure_labels(repo)

    if args.from_spec:
        feature = Path(args.from_spec).read_text()
    elif args.feature:
        feature = args.feature
    else:
        print("Describe the feature to break down:")
        feature = sys.stdin.read().strip()

    print(f"\nPM Agent — {repo}")
    print(f"Feature: {feature[:100]}...\n")

    # Scan codebase for context
    print("Scanning codebase...")
    raw_context = read_codebase()
    context = gpt(CODEBASE_SUMMARY_PROMPT, raw_context) if raw_context else "New project, no existing code."

    print("Breaking down into epics and issues...")
    raw = gpt(
        BREAKDOWN_PROMPT.format(context=context, feature=feature),
        "Generate the breakdown now.",
        json_mode=True,
        max_tokens=6000,
    )
    breakdown = json.loads(raw)

    total_issues = sum(len(e["issues"]) for e in breakdown["epics"])
    print(f"\nPlan: {len(breakdown['epics'])} epic(s), {total_issues} issue(s)\n")

    for epic in breakdown["epics"]:
        print(f"\nEpic: {epic['title']}")
        for issue in epic["issues"]:
            print(f"  [{issue.get('estimate','M')}] {issue['title']}")

    print()
    confirm = input("Create these issues on GitHub? [Y/n] ").strip().lower()
    if confirm in ("n", "no"):
        print("Cancelled.")
        return

    print("\nCreating issues...")
    for epic in breakdown["epics"]:
        create_epic_and_issues(epic, repo)

    print(f"\nDone. {total_issues} issues created with label: ready-for-build")
    print(f"View: https://github.com/{repo}/issues?q=label:ready-for-build")


if __name__ == "__main__":
    main()
