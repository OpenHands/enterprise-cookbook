#!/usr/bin/env python3
"""Provision a sandbox, then attach a conversation to it.

End-to-end recipe:

  1. Start an OpenHands Cloud sandbox (no conversation yet).
  2. Wait for it to reach RUNNING and grab its agent-server URL + session key.
  3. Shallow-clone a git repo into the sandbox via the agent server.
  4. Run the repo's ``.openhands/setup.sh`` (the location OpenHands itself uses,
     https://docs.openhands.dev/openhands/usage/customization/repository).
  5. Attach a brand-new conversation to that already-prepared sandbox by passing
     ``sandbox_id`` to ``POST /api/v1/app-conversations``.

The result: an agent conversation that starts up looking at a repo you cloned
and a workspace you set up yourself — handy for pre-warming environments,
custom checkouts, monorepo sub-paths, or running setup that differs from the
default clone-and-go flow.

Everything is configurable via flags or environment variables so you can drop
this into your own tooling unchanged. With just ``OH_API_KEY`` set it clones
this very repo (which ships a tiny ``.openhands/setup.sh``) so you can watch the
whole thing work before pointing it at your own repository.

    export OH_API_KEY=...                     # Cloud API key (required)
    python attach_conversation.py             # clones OpenHands/enterprise-cookbook

    python attach_conversation.py \
        --repo https://github.com/your-org/your-repo \
        --branch main \
        --message "Run the test suite and fix any failures."
"""

import argparse
import os
import sys
import time

import requests


DEFAULT_REPO = "https://github.com/OpenHands/enterprise-cookbook"
DEFAULT_MESSAGE = (
    "The repository has already been cloned into the workspace and its "
    ".openhands/setup.sh has been run. List the files you see and give me a "
    "one-paragraph summary of what this project does."
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Prepare a sandbox (clone + setup.sh) then attach a conversation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("OH_API_KEY"),
        help="Cloud API key (env: OH_API_KEY).",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("OH_API_BASE", "https://app.all-hands.dev"),
        help="Cloud app server base URL (env: OH_API_BASE).",
    )
    p.add_argument(
        "--repo",
        default=os.environ.get("REPO_URL", DEFAULT_REPO),
        help="Git URL to shallow-clone (env: REPO_URL).",
    )
    p.add_argument(
        "--branch",
        default=os.environ.get("REPO_BRANCH"),
        help="Branch to clone (env: REPO_BRANCH; default: repo default).",
    )
    p.add_argument(
        "--depth",
        type=int,
        default=int(os.environ.get("CLONE_DEPTH", "1")),
        help="git clone --depth (env: CLONE_DEPTH).",
    )
    p.add_argument(
        "--workdir",
        default=os.environ.get("WORKDIR", "/workspace"),
        help="Directory the repo is cloned into (env: WORKDIR).",
    )
    p.add_argument(
        "--setup-script",
        default=os.environ.get("SETUP_SCRIPT", ".openhands/setup.sh"),
        help="Repo-relative setup script to run (env: SETUP_SCRIPT).",
    )
    p.add_argument(
        "--message",
        default=os.environ.get("INITIAL_MESSAGE", DEFAULT_MESSAGE),
        help="First message for the attached conversation (env: INITIAL_MESSAGE).",
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
    resp = requests.post(f"{base_url}/api/v1/sandboxes", headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()["id"]


def wait_until_running(base_url: str, headers: dict, sid: str, timeout: int) -> dict:
    """Poll GET /api/v1/sandboxes?id=<id> until status == RUNNING."""
    deadline = time.monotonic() + timeout
    while True:
        resp = requests.get(
            f"{base_url}/api/v1/sandboxes", headers=headers, params={"id": sid}
        )
        resp.raise_for_status()
        results = resp.json()
        if not results or results[0] is None:
            raise ValueError(f"Sandbox {sid} not found")
        sb = results[0]
        print("  sandbox status:", sb["status"])
        if sb["status"] == "RUNNING":
            return sb
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Sandbox {sid} did not reach RUNNING within {timeout}s "
                f"(last status: {sb['status']})"
            )
        time.sleep(3)


def attach_conversation(
    base_url: str, headers: dict, sid: str, message: str, timeout: int
) -> str:
    """POST /api/v1/app-conversations with sandbox_id, then resolve its id.

    The call is asynchronous: it returns a *start task*. We poll
    /api/v1/app-conversations/start-tasks until it yields an app_conversation_id.
    """
    payload = {
        "sandbox_id": sid,
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": message}],
        },
        "title": "clone-and-attach demo",
    }
    resp = requests.post(
        f"{base_url}/api/v1/app-conversations", headers=headers, json=payload
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
        )
        resp.raise_for_status()
        items = resp.json()
        item = items[0] if isinstance(items, list) else items
        print("  start-task status:", item.get("status"))
        conv_id = item.get("app_conversation_id")
    return conv_id


# --- Agent server (runs shell commands inside the sandbox) --------------------


def agent_server_url(sandbox: dict) -> str:
    url = next(
        (u["url"] for u in sandbox["exposed_urls"] if u["name"] == "AGENT_SERVER"),
        None,
    )
    if not url:
        raise ValueError(f"AGENT_SERVER URL not found in sandbox {sandbox['id']}")
    return url


def run_command(
    agent_url: str, session: dict, cmd: str, cwd: str | None = None, timeout: int = 300
) -> dict:
    """POST /api/bash/execute_bash_command and return the raw result dict."""
    body: dict = {"command": cmd, "timeout": timeout}
    if cwd:
        body["cwd"] = cwd
    resp = requests.post(
        f"{agent_url}/api/bash/execute_bash_command", headers=session, json=body
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


def repo_dir_name(repo_url: str) -> str:
    """Derive the clone directory name from a git URL."""
    return repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")


def main() -> None:
    args = parse_args()
    if not args.api_key:
        sys.exit("error: set --api-key or the OH_API_KEY environment variable")
    headers = {"X-Session-API-Key": args.api_key}

    # 1-2. Create (or reuse) a sandbox and wait until it is RUNNING.
    sid = args.sandbox_id or start_sandbox(args.base_url, headers, args.sandbox_spec_id)
    print("sandbox:", sid)
    sb = wait_until_running(args.base_url, headers, sid, args.poll_timeout)

    agent_url = agent_server_url(sb)
    session = {"X-Session-API-Key": sb["session_api_key"]}
    print("agent:", agent_url)

    clone_path = f"{args.workdir.rstrip('/')}/{repo_dir_name(args.repo)}"

    # 3. Shallow-clone the repo into the sandbox.
    print(f"\n=== shallow clone {args.repo} -> {clone_path} ===")
    branch = f"--branch {args.branch} " if args.branch else ""
    report(
        "git clone",
        run_command(
            agent_url,
            session,
            f"rm -rf {clone_path} && "
            f"git clone --depth {args.depth} {branch}{args.repo} {clone_path}",
        ),
    )

    # 4. Run the repo's setup script from its conventional location.
    print(f"\n=== run {args.setup_script} ===")
    report(
        "setup script",
        run_command(
            agent_url,
            session,
            f"if [ -f {args.setup_script} ]; then bash {args.setup_script}; "
            f"else echo 'no {args.setup_script} in this repo — skipping'; fi",
            cwd=clone_path,
        ),
    )

    # 5. Attach a conversation to the prepared sandbox.
    print("\n=== attach conversation ===")
    conv_id = attach_conversation(
        args.base_url, headers, sid, args.message, args.poll_timeout
    )
    print("\nConversation attached to your prepared sandbox:")
    print(f"  {args.base_url}/conversations/{conv_id}")


if __name__ == "__main__":
    main()
