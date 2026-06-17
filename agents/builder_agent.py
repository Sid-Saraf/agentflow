#!/usr/bin/env python3
"""
Builder Agent — implements a GitHub issue autonomously.
Triggered by GitHub Actions when ready-for-build label is applied.

Env vars: OPENAI_API_KEY, GH_TOKEN, ISSUE_NUMBER, GITHUB_REPOSITORY
"""
from __future__ import annotations
import os
import sys
import json
import subprocess
from pathlib import Path
from openai import OpenAI
from base import gh, git, bash, read_file, write_file, list_files, gpt

REPO_ROOT = Path(os.environ.get("GITHUB_WORKSPACE", "."))

SYSTEM = """You are a senior software engineer implementing GitHub issues.

Rules:
- Read files before editing them
- Implement ALL acceptance criteria — no shortcuts
- Write tests for every new code path
- Run tests before finishing
- Keep changes minimal and focused — no gold-plating (ponytail will catch over-engineering)
- Follow existing code style exactly
- Commit when done: git add -A && git commit -m "feat: <title> (closes #N)"
"""

PONYTAIL_PROMPT = """Review this diff ONLY for over-engineering. Look for:
- Reinvented standard library functions
- Unnecessary abstractions or wrapper classes
- Speculative flexibility (config for things that will never change)
- Unneeded dependencies
- Dead code or unused parameters

Diff:
{diff}

Return JSON: {{"issues": [{{"file": "...", "line": N, "problem": "...", "fix": "..."}}], "verdict": "clean"|"needs_simplification"}}
Only flag genuine over-engineering. Be ruthless but fair."""


tools = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file", "description": "Write content to a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "run_bash", "description": "Run a shell command",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]},
    }},
    {"type": "function", "function": {
        "name": "list_files", "description": "List files in a directory",
        "parameters": {"type": "object", "properties": {"directory": {"type": "string"}}, "required": ["directory"]},
    }},
]


def dispatch(name: str, args: dict) -> str:
    root = str(REPO_ROOT)
    if name == "read_file":
        return read_file(args["path"], root)
    elif name == "write_file":
        write_file(args["path"], args["content"], root)
        return f"Written: {args['path']}"
    elif name == "run_bash":
        code, out = bash(args["command"], cwd=root, timeout=args.get("timeout", 120))
        return f"exit={code}\n{out}"
    elif name == "list_files":
        return "\n".join(list_files(args["directory"], root))
    return f"Unknown: {name}"


def run_ponytail_check() -> str:
    """Check the current diff for over-engineering."""
    _, diff = bash("git diff HEAD", cwd=str(REPO_ROOT))
    if not diff.strip():
        return "clean"
    raw = gpt("You are a code reviewer focused on over-engineering.",
              PONYTAIL_PROMPT.format(diff=diff[:8000]), json_mode=True)
    result = json.loads(raw)
    if result["verdict"] == "needs_simplification" and result.get("issues"):
        issues_text = "\n".join(f"- {i['file']}:{i['line']}: {i['problem']} → {i['fix']}"
                                for i in result["issues"])
        return f"OVER-ENGINEERING FOUND:\n{issues_text}"
    return "clean"


def implement(issue_number: str, title: str, body: str) -> bool:
    client = OpenAI()
    branch = f"agent/issue-{issue_number}"
    bash(f"git checkout -b {branch}", cwd=str(REPO_ROOT))
    gh(f"issue edit {issue_number} --add-label build-in-progress --remove-label ready-for-build")

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Implement issue #{issue_number}: {title}\n\n{body}\n\nStart by listing files, then read the relevant ones, implement, test, commit."},
    ]

    for turn in range(50):
        resp = client.chat.completions.create(model="gpt-4o", max_tokens=4096, tools=tools, messages=messages)
        msg = resp.choices[0].message
        messages.append(msg)

        if msg.content:
            print(f"[{turn+1}] {msg.content[:150]}")

        if not msg.tool_calls:
            break

        results = []
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments)
            print(f"  → {call.function.name}({str(args)[:60]})")
            result = dispatch(call.function.name, args)
            print(f"     {result[:100]}")
            results.append({"role": "tool", "tool_call_id": call.id, "content": result})
        messages.extend(results)

    # Ponytail check
    print("\n[Ponytail] Checking for over-engineering...")
    ponytail_result = run_ponytail_check()
    if ponytail_result != "clean":
        print(ponytail_result)
        # Ask agent to fix it
        messages.append({"role": "user", "content": f"Ponytail found over-engineering issues. Please fix them:\n{ponytail_result}"})
        for turn in range(15):
            resp = client.chat.completions.create(model="gpt-4o", max_tokens=4096, tools=tools, messages=messages)
            msg = resp.choices[0].message
            messages.append(msg)
            if not msg.tool_calls:
                break
            results = []
            for call in msg.tool_calls:
                args = json.loads(call.function.arguments)
                result = dispatch(call.function.name, args)
                results.append({"role": "tool", "tool_call_id": call.id, "content": result})
            messages.extend(results)
    else:
        print("[Ponytail] Clean ✓")

    # Check if anything was committed
    status = bash("git status --short", cwd=str(REPO_ROOT))[1]
    if not status.strip() and "nothing to commit" in bash("git status", cwd=str(REPO_ROOT))[1]:
        gh(f"issue edit {issue_number} --add-label needs-human --remove-label build-in-progress")
        gh(f'issue comment {issue_number} --body "Builder agent made no changes. Needs human review."')
        return False

    bash(f"git push origin {branch}", cwd=str(REPO_ROOT))
    gh(f"issue edit {issue_number} --add-label ready-for-qa --remove-label build-in-progress")
    gh(f'issue comment {issue_number} --body "Builder agent finished. Branch: `{branch}`. Moving to QA."')
    print(f"Done. Branch pushed: {branch}")
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

    success = implement(issue_number, data["title"], data["body"])
    sys.exit(0 if success else 1)
