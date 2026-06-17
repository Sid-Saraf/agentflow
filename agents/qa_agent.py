#!/usr/bin/env python3
"""
QA Agent — verifies a branch, runs tests, ponytail check, then creates PR.
Triggered by GitHub Actions when ready-for-qa label is applied.

Env vars: OPENAI_API_KEY, GH_TOKEN, ISSUE_NUMBER, GITHUB_REPOSITORY
"""
from __future__ import annotations
import os
import sys
import json
import subprocess
from pathlib import Path
from openai import OpenAI
from base import gh, bash, read_file, list_files, gpt

REPO_ROOT = Path(os.environ.get("GITHUB_WORKSPACE", "."))

SYSTEM = """You are a QA engineer verifying that a GitHub issue was implemented correctly.

Your job:
1. Read the issue acceptance criteria
2. Read the changed files
3. Run the test suite
4. Verify each acceptance criterion is met
5. Run a final check for anything missing

Be thorough. A shipped bug is worse than a delayed feature."""

PONYTAIL_PROMPT = """Review this PR diff ONLY for over-engineering. Be concise.

Diff:
{diff}

Return JSON: {{"verdict": "clean"|"needs_simplification", "issues": [{{"location": "file:line", "problem": "...", "fix": "..."}}]}}"""

QA_REVIEW_PROMPT = """You are reviewing this implementation against the issue requirements.

Issue:
{issue}

Changed files:
{files}

Test output:
{test_output}

Return JSON:
{{
  "verdict": "pass"|"fail",
  "summary": "2-3 sentence summary",
  "passed_criteria": ["criteria that are met"],
  "failed_criteria": ["criteria NOT met"],
  "missing_tests": ["test cases that should be added"],
  "pr_description": "markdown PR description with: what changed, how to test, closes #N"
}}"""


def get_changed_files() -> str:
    _, diff_stat = bash("git diff main --stat", cwd=str(REPO_ROOT))
    _, files = bash("git diff main --name-only", cwd=str(REPO_ROOT))
    content = diff_stat + "\n\n"
    for f in files.splitlines()[:10]:
        file_content = read_file(f, str(REPO_ROOT))
        content += f"\n--- {f} ---\n{file_content[:500]}\n"
    return content


def run_tests() -> tuple[bool, str]:
    """Auto-detect and run the test suite."""
    root = str(REPO_ROOT)
    # Python
    if (REPO_ROOT / "pytest.ini").exists() or (REPO_ROOT / "pyproject.toml").exists() or list(REPO_ROOT.rglob("test_*.py")):
        code, out = bash("python -m pytest -x -q 2>&1 | tail -30", cwd=root, timeout=120)
        return code == 0, out
    # Node
    if (REPO_ROOT / "package.json").exists():
        code, out = bash("npm test -- --passWithNoTests 2>&1 | tail -30", cwd=root, timeout=120)
        return code == 0, out
    return True, "No test suite detected"


def run_ponytail(diff: str) -> str:
    raw = gpt("Over-engineering reviewer.", PONYTAIL_PROMPT.format(diff=diff[:8000]), json_mode=True)
    result = json.loads(raw)
    if result["verdict"] == "needs_simplification" and result.get("issues"):
        return "\n".join(f"- {i['location']}: {i['problem']} → {i['fix']}" for i in result["issues"])
    return ""


def verify(issue_number: str, title: str, body: str) -> bool:
    gh(f"issue edit {issue_number} --add-label qa-in-progress --remove-label ready-for-qa")
    print(f"QA Agent: issue #{issue_number} — {title}")

    # Run tests
    print("\n[1/4] Running tests...")
    tests_passed, test_output = run_tests()
    print(f"Tests: {'PASS' if tests_passed else 'FAIL'}")
    print(test_output[:300])

    # Ponytail
    print("\n[2/4] Ponytail over-engineering check...")
    _, diff = bash("git diff main", cwd=str(REPO_ROOT))
    ponytail_findings = run_ponytail(diff) if diff.strip() else ""
    print(f"Ponytail: {'issues found' if ponytail_findings else 'clean ✓'}")

    # Changed files
    print("\n[3/4] Reading implementation...")
    changed = get_changed_files()

    # QA review
    print("\n[4/4] Verifying acceptance criteria...")
    raw = gpt(
        SYSTEM,
        QA_REVIEW_PROMPT.format(issue=body, files=changed[:4000], test_output=test_output[:1000]),
        json_mode=True,
    )
    review = json.loads(raw)
    verdict = review["verdict"]
    print(f"Verdict: {verdict.upper()}")
    print(f"Summary: {review['summary']}")

    if verdict == "fail" or not tests_passed:
        # Report back to issue
        fail_comment = (
            f"## QA Failed ❌\n\n{review['summary']}\n\n"
            f"**Failed criteria:**\n" + "\n".join(f"- {c}" for c in review.get("failed_criteria", [])) +
            f"\n\n**Missing tests:**\n" + "\n".join(f"- {t}" for t in review.get("missing_tests", [])) +
            (f"\n\n**Ponytail findings:**\n{ponytail_findings}" if ponytail_findings else "") +
            "\n\nLabel reset to `ready-for-build` for rework."
        )
        gh(f'issue comment {issue_number} --body "{fail_comment.replace(chr(34), chr(39))}"')
        gh(f"issue edit {issue_number} --add-label qa-failed --remove-label qa-in-progress")
        # Reset to ready-for-build so builder picks it up again
        gh(f"issue edit {issue_number} --add-label ready-for-build --remove-label qa-failed")
        return False

    # Create PR
    branch = f"agent/issue-{issue_number}"
    pr_body = review["pr_description"]
    if ponytail_findings:
        pr_body += f"\n\n### Ponytail Notes\n{ponytail_findings}"
    pr_body_escaped = pr_body.replace('"', "'")

    pr_url = gh(
        f'pr create --repo {os.environ.get("GITHUB_REPOSITORY", "")} '
        f'--title "feat: {title}" '
        f'--body "{pr_body_escaped}" '
        f'--base main --head {branch}'
    )

    gh(f"issue edit {issue_number} --add-label qa-passed --remove-label qa-in-progress")
    gh(f'issue comment {issue_number} --body "QA passed ✅ PR: {pr_url}"')
    print(f"\nPR created: {pr_url}")
    return True


if __name__ == "__main__":
    issue_number = os.environ.get("ISSUE_NUMBER") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not issue_number:
        print("ISSUE_NUMBER required")
        sys.exit(1)

    data = json.loads(subprocess.run(
        ["gh", "issue", "view", issue_number, "--json", "title,body"],
        capture_output=True, text=True
    ).stdout)

    success = verify(issue_number, data["title"], data["body"])
    sys.exit(0 if success else 1)
