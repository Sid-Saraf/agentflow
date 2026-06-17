"""Shared utilities for all agents."""
from __future__ import annotations
import os
import subprocess
import json
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# Search for .env in: agentflow root, CWD, CWD/backend, parent dirs
for _env in [
    Path(__file__).parent.parent / ".env",
    Path.cwd() / ".env",
    Path.cwd() / "backend" / ".env",
    Path.home() / ".env",
]:
    if _env.exists():
        load_dotenv(_env)

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o")
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def gpt(system: str, user: str, json_mode: bool = False, max_tokens: int = 4000) -> str:
    client = _get_client()
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        **kwargs,
    )
    return resp.choices[0].message.content


def gpt_stream(system: str, messages: list) -> str:
    """Streaming chat for conversational agents."""
    client = _get_client()
    full = ""
    with client.chat.completions.stream(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "system", "content": system}] + messages,
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full += text
    print()
    return full


def gh(cmd: str, check: bool = True) -> str:
    result = subprocess.run(f"gh {cmd}", shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"gh {cmd} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def git(cmd: str, cwd: str | None = None) -> str:
    result = subprocess.run(f"git {cmd}", shell=True, capture_output=True, text=True, cwd=cwd)
    return result.stdout.strip()


def bash(cmd: str, cwd: str | None = None, timeout: int = 120) -> tuple[int, str]:
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    output = result.stdout + ("\nSTDERR: " + result.stderr if result.stderr else "")
    return result.returncode, output.strip()


def read_file(path: str, root: str = ".") -> str:
    p = Path(root) / path
    return p.read_text() if p.exists() else f"[not found: {path}]"


def write_file(path: str, content: str, root: str = ".") -> None:
    p = Path(root) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def list_files(directory: str = ".", root: str = ".") -> list[str]:
    p = Path(root) / directory
    if not p.exists():
        return []
    return sorted(str(f.relative_to(root)) for f in p.rglob("*") if f.is_file()
                  and ".git" not in f.parts and "node_modules" not in f.parts
                  and "__pycache__" not in f.parts)


def current_repo() -> str:
    """Get owner/repo for the current directory."""
    return gh("repo view --json nameWithOwner -q .nameWithOwner")


def ensure_labels(repo: str) -> None:
    """Create agent pipeline labels if they don't exist."""
    labels = [
        ("ready-for-build", "0075ca", "Agent should implement this"),
        ("build-in-progress", "e4e669", "Builder agent is working"),
        ("ready-for-qa", "d4edda", "Builder done, QA should verify"),
        ("qa-in-progress", "c5e8d1", "QA agent running"),
        ("qa-passed", "0e8a16", "QA verified, PR created"),
        ("qa-failed", "d73a4a", "QA failed, needs rework"),
        ("needs-human", "b60205", "Agent stuck, human needed"),
        ("epic", "5319e7", "High-level epic"),
    ]
    for name, color, desc in labels:
        subprocess.run(
            f'gh label create "{name}" --color "{color}" --description "{desc}" --repo {repo}',
            shell=True, capture_output=True
        )
