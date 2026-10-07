#!/usr/bin/env python3
"""Upload a local agent-skills directory into a sandbox, then start a conversation.

End-to-end recipe:

  1. Start an OpenHands Cloud sandbox (no conversation yet).
  2. Wait for it to reach RUNNING and grab its agent-server URL + session key.
  3. Resolve the sandbox user's home so ``~/.openhands/skills`` expands correctly.
  4. tar your local skills directory, upload the single archive to the sandbox
     via the agent server's ``POST /api/file/upload``, then extract it into the
     remote skills directory (recursive copy, no per-file round-trips).
  5. Start a brand-new conversation on that already-prepared sandbox by passing
     ``sandbox_id`` to ``POST /api/v1/app-conversations`` and print its URL.

Why upload to ``~/.openhands/skills``? When OpenHands builds a conversation it
loads *user* skills from ``~/.openhands/skills/`` (alongside public, org, and
project skills). Dropping your skills there *before* the conversation starts
means the agent picks them up for that brand-new conversation — no repo
required. See https://docs.openhands.dev/ for the skills system.

This builds on `clone-and-attach` (../clone-and-attach/), which prepares a
sandbox (clone + setup.sh) and then attaches a conversation. Here we prepare the
sandbox by uploading skills instead.

Everything is configurable via flags or environment variables so you can drop
this into your own tooling unchanged:

    export OH_API_KEY=...                       # Cloud API key (required)
    python upload_skills.py ./my-skills         # upload ./my-skills, start convo

    python upload_skills.py ./my-skills \
        --remote-skills-dir '~/.agents/skills' \
        --message "List the skills you can see and what each one does."

Env vars: OH_API_KEY, OH_API_BASE, REMOTE_SKILLS_DIR, INITIAL_MESSAGE,
CONVERSATION_TITLE, SANDBOX_ID, SANDBOX_SPEC_ID, POLL_TIMEOUT.
"""

import argparse
import io
import os
import shlex
import sys
import tarfile
import time
from pathlib import Path

import requests


DEFAULT_REMOTE_SKILLS_DIR = "~/.openhands/skills"
DEFAULT_MESSAGE = (
    "I just uploaded a set of agent skills into your user skills directory. "
    "List the skills available to you and give a one-line description of what "
    "each one does."
)
# Where the archive lands on the sandbox before we extract it. /tmp is always
# writable and is cleaned up by the script after extraction.
REMOTE_ARCHIVE_PATH = "/tmp/oh-upload-skills.tar.gz"
# Client-side HTTP timeout (seconds) for the quick app/agent server calls, so a
# stalled network connection surfaces as an error instead of hanging forever.
HTTP_TIMEOUT = 60
# Uploads can be larger and slower than the control-plane calls, so give them a
# more generous client-side timeout.
UPLOAD_TIMEOUT = 300


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Upload a local agent-skills directory into a sandbox, then "
        "start a conversation that can use those skills.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "skills_dir",
        help="Local agent-skills directory to upload (its contents are copied "
        "into the remote skills directory).",
    )
    p.add_argument(
        "--api-key",
        # Resolved from $OH_API_KEY in main() rather than bound here, so the
        # key is never rendered as a default in ``--help`` output.
        default=None,
        help="Cloud API key (env: OH_API_KEY).",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("OH_API_BASE", "https://app.all-hands.dev"),
        help="Cloud app server base URL (env: OH_API_BASE).",
    )
    p.add_argument(
        "--remote-skills-dir",
        default=os.environ.get("REMOTE_SKILLS_DIR", DEFAULT_REMOTE_SKILLS_DIR),
        help="Destination skills directory in the sandbox. A leading '~' is "
        "expanded to the sandbox user's home (env: REMOTE_SKILLS_DIR).",
    )
    p.add_argument(
        "--message",
        default=os.environ.get("INITIAL_MESSAGE", DEFAULT_MESSAGE),
        help="First message for the conversation (env: INITIAL_MESSAGE).",
    )
    p.add_argument(
        "--title",
        default=os.environ.get("CONVERSATION_TITLE", "upload-skills demo"),
        help="Title for the new conversation (env: CONVERSATION_TITLE).",
    )
    p.add_argument(
        "--sandbox-id",
        default=os.environ.get("SANDBOX_ID"),
        help="Reuse an existing RUNNING sandbox instead of creating one "
        "(env: SANDBOX_ID).",
    )
    p.add_argument(
        "--sandbox-spec-id",
        default=os.environ.get("SANDBOX_SPEC_ID"),
        help="Optional runtime image spec id (env: SANDBOX_SPEC_ID).",
    )
    p.add_argument(
        "--poll-timeout",
        type=int,
        default=int(os.environ.get("POLL_TIMEOUT", "240")),
        help="Seconds to wait for sandbox / conversation readiness.",
    )
    return p.parse_args()


# --- Cloud app server (manages the sandbox + conversation lifecycle) ----------


def start_sandbox(base_url: str, headers: dict, spec_id: str | None) -> str:
    """POST /api/v1/sandboxes -> new sandbox id."""
    params = {"sandbox_spec_id": spec_id} if spec_id else None
    resp = requests.post(
        f"{base_url}/api/v1/sandboxes",
        headers=headers,
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def wait_until_running(base_url: str, headers: dict, sid: str, timeout: int) -> dict:
    """Poll GET /api/v1/sandboxes?id=<id> until status == RUNNING."""
    deadline = time.monotonic() + timeout
    while True:
        resp = requests.get(
            f"{base_url}/api/v1/sandboxes",
            headers=headers,
            params={"id": sid},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results or results[0] is None:
            raise ValueError(f"Sandbox {sid} not found")
        sb = results[0]
        print("  sandbox status:", sb["status"])
        if sb["status"] == "RUNNING":
            return sb
        if sb["status"] in ("ERROR", "MISSING"):
            raise RuntimeError(f"Sandbox {sid} entered status {sb['status']}")
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Sandbox {sid} did not reach RUNNING within {timeout}s "
                f"(last status: {sb['status']})"
            )
        time.sleep(3)


def start_conversation(
    base_url: str, headers: dict, sid: str, message: str, title: str, timeout: int
) -> str:
    """POST /api/v1/app-conversations with sandbox_id, then resolve its id.

    The call is asynchronous: it returns an AppConversationStartTask. We poll
    /api/v1/app-conversations/start-tasks until it yields an app_conversation_id
    (or reports ERROR).
    """
    payload = {
        "sandbox_id": sid,
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": message}],
        },
        "title": title,
    }
    resp = requests.post(
        f"{base_url}/api/v1/app-conversations",
        headers=headers,
        json=payload,
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    task = resp.json()
    conv_id = task.get("app_conversation_id")
    task_id = task["id"]

    deadline = time.monotonic() + timeout
    while not conv_id:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Start task {task_id} never produced a conversation")
        time.sleep(3)
        resp = requests.get(
            f"{base_url}/api/v1/app-conversations/start-tasks",
            headers=headers,
            params={"ids": task_id},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json()
        if isinstance(items, list):
            if not items:
                # Nothing back yet; keep polling until the deadline.
                continue
            item = items[0]
        else:
            item = items
        status = item.get("status")
        print("  start-task status:", status)
        if status == "ERROR":
            detail = item.get("detail") or "unknown error"
            raise RuntimeError(f"Start task {task_id} failed: {detail}")
        conv_id = item.get("app_conversation_id")
    return conv_id


# --- Agent server (file ops + shell inside the sandbox) -----------------------


def agent_server_url(sandbox: dict) -> str:
    url = next(
        (u["url"] for u in sandbox["exposed_urls"] if u["name"] == "AGENT_SERVER"),
        None,
    )
    if not url:
        raise ValueError(f"AGENT_SERVER URL not found in sandbox {sandbox['id']}")
    return url


def remote_home(agent_url: str, session: dict) -> str:
    """GET /api/file/home -> the sandbox user's home directory."""
    resp = requests.get(
        f"{agent_url}/api/file/home", headers=session, timeout=HTTP_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()["home"]


def resolve_remote_dir(path: str, home: str) -> str:
    """Expand a leading '~' against the sandbox home and normalise the path."""
    if path == "~":
        return home
    if path.startswith("~/"):
        path = f"{home.rstrip('/')}/{path[2:]}"
    return path.rstrip("/")


def upload_archive(
    agent_url: str, session: dict, archive: bytes, remote_path: str
) -> None:
    """POST /api/file/upload?path=<absolute> with a multipart file body."""
    resp = requests.post(
        f"{agent_url}/api/file/upload",
        headers=session,
        params={"path": remote_path},
        files={"file": (Path(remote_path).name, archive, "application/gzip")},
        timeout=UPLOAD_TIMEOUT,
    )
    resp.raise_for_status()


def run_command(agent_url: str, session: dict, cmd: str, timeout: int = 120) -> dict:
    """POST /api/bash/execute_bash_command and return the raw result dict."""
    resp = requests.post(
        f"{agent_url}/api/bash/execute_bash_command",
        headers=session,
        json={"command": cmd, "timeout": timeout},
        # Let the client read outlast the server-side command timeout so we don't
        # abort a command that is still legitimately running.
        timeout=timeout + 30,
    )
    resp.raise_for_status()
    return resp.json()


def report(label: str, result: dict) -> None:
    print(f"$ {label}  (exit={result.get('exit_code')})")
    out = (result.get("stdout") or "").rstrip()
    err = (result.get("stderr") or "").rstrip()
    if out:
        print(out)
    if err:
        print("[stderr]", err)
    if result.get("exit_code"):
        raise RuntimeError(f"command failed ({label}): exit {result['exit_code']}")


# --- Local helpers ------------------------------------------------------------


def make_tarball(skills_dir: Path) -> bytes:
    """tar.gz the *contents* of skills_dir (so children land in the target dir)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # arcname="." tars the directory's contents rather than the directory
        # itself, so `tar -x -C <target>` merges them into <target>.
        tar.add(skills_dir, arcname=".")
    return buf.getvalue()


def summarise_local_skills(skills_dir: Path) -> None:
    """Print a quick inventory so you can see what is being uploaded."""
    skill_mds = sorted(skills_dir.rglob("SKILL.md"))
    loose_mds = sorted(p for p in skills_dir.rglob("*.md") if p.name != "SKILL.md")
    print(f"  AgentSkills (SKILL.md): {len(skill_mds)}")
    for p in skill_mds[:10]:
        print(f"    - {p.parent.relative_to(skills_dir)}")
    if len(skill_mds) > 10:
        print(f"    ... and {len(skill_mds) - 10} more")
    print(f"  loose .md skills:       {len(loose_mds)}")
    for p in loose_mds[:10]:
        print(f"    - {p.relative_to(skills_dir)}")
    if len(loose_mds) > 10:
        print(f"    ... and {len(loose_mds) - 10} more")
    if not skill_mds and not loose_mds:
        print(
            "  warning: no SKILL.md or *.md files found — uploading anyway, but "
            "the agent may not detect any skills."
        )


# --- Main ---------------------------------------------------------------------


def main() -> None:
    args = parse_args()
    api_key = args.api_key or os.environ.get("OH_API_KEY")
    if not api_key:
        sys.exit("error: set --api-key or the OH_API_KEY environment variable")

    skills_dir = Path(args.skills_dir).expanduser().resolve()
    if not skills_dir.is_dir():
        sys.exit(f"error: skills directory not found: {skills_dir}")

    print(f"local skills dir: {skills_dir}")
    summarise_local_skills(skills_dir)

    headers = {"X-Session-API-Key": api_key}

    # 1-2. Create (or reuse) a sandbox and wait until it is RUNNING.
    sid = args.sandbox_id or start_sandbox(args.base_url, headers, args.sandbox_spec_id)
    print("sandbox:", sid)
    sb = wait_until_running(args.base_url, headers, sid, args.poll_timeout)

    agent_url = agent_server_url(sb)
    session = {"X-Session-API-Key": sb["session_api_key"]}
    print("agent:", agent_url)

    # 3. Resolve the remote skills directory (expand a leading '~').
    home = remote_home(agent_url, session)
    target_dir = resolve_remote_dir(args.remote_skills_dir, home)
    print(f"\n=== upload skills -> {target_dir} ===")

    # 4a. tar the local directory's contents and upload the single archive.
    archive = make_tarball(skills_dir)
    print(f"  uploading {len(archive)} bytes -> {REMOTE_ARCHIVE_PATH}")
    upload_archive(agent_url, session, archive, REMOTE_ARCHIVE_PATH)

    # 4b. Extract into the target dir (recursive copy), then clean up + list.
    q_archive = shlex.quote(REMOTE_ARCHIVE_PATH)
    q_target = shlex.quote(target_dir)
    report(
        "extract skills",
        run_command(
            agent_url,
            session,
            f"mkdir -p {q_target} && "
            f"tar -xzf {q_archive} -C {q_target} && "
            f"rm -f {q_archive} && "
            f"echo 'installed skills:' && "
            # Use a portable `find` (no GNU-only `-printf`): cd into the target
            # so `find .` yields paths relative to it, then strip the leading
            # './' and indent. Works on both GNU and BSD/macOS find.
            f"(cd {q_target} && find . -maxdepth 2 -name SKILL.md 2>/dev/null "
            f"| sed 's|^\\./||;s|^|  |'); true",
        ),
    )

    # 5. Start a conversation on the prepared sandbox.
    print("\n=== start conversation ===")
    conv_id = start_conversation(
        args.base_url, headers, sid, args.message, args.title, args.poll_timeout
    )
    print("\nConversation started on your skills-loaded sandbox:")
    print(f"  {args.base_url}/conversations/{conv_id}")


if __name__ == "__main__":
    main()
