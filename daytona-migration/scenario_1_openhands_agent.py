#!/usr/bin/env python3
"""Scenario 1: use the OpenHands agent that ships in every sandbox.

Unlike Daytona, where you provision a sandbox and bring your own agent, every
OpenHands sandbox already runs the OpenHands agent. You can drive that agent at
two levels:

- ``--mode cloud`` (default): high-level Cloud App Server. Start a conversation
  with ``POST /api/v1/app-conversations`` and the Cloud layer injects your
  account's LLM credentials for you. No model key needed.
- ``--mode agent-server``: low-level agent-server. Create the conversation
  directly on the sandbox with ``POST /api/conversations``, supplying your own
  ``agent``/``llm`` block. Returns the id synchronously (no start-task poll).

Both modes stream the run over the agent-server WebSocket and print the agent's
final response.

    export OH_API_KEY=...          # Cloud API key (required)
    export LLM_API_KEY=...         # model key (required only for agent-server mode)
    pip install -r requirements.txt

    python scenario_1_openhands_agent.py --task "Write a hello-world FastAPI app"
    python scenario_1_openhands_agent.py --mode agent-server \
        --llm-model anthropic/claude-sonnet-4-5 --task "Add a /health route"

Env vars: OH_API_KEY, OH_API_BASE, SANDBOX_SPEC_ID, LLM_MODEL, LLM_API_KEY,
LLM_BASE_URL, POLL_TIMEOUT.
"""

import argparse
import os
import sys
import time

import requests
from _common import (
    DEFAULT_CLOUD,
    agent_final_response,
    agent_server_url,
    delete_sandbox,
    run_watch,
    start_sandbox,
    wait_until_running,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--mode",
        choices=["cloud", "agent-server"],
        default="cloud",
        help="High-level Cloud App Server (default) or low-level agent-server.",
    )
    p.add_argument(
        "--task",
        default="Create hello.txt containing the text 'Hello from OpenHands'.",
        help="The instruction to send the agent.",
    )
    p.add_argument("--api-key", default=os.environ.get("OH_API_KEY"))
    p.add_argument("--base-url", default=os.environ.get("OH_API_BASE", DEFAULT_CLOUD))
    p.add_argument("--sandbox-spec-id", default=os.environ.get("SANDBOX_SPEC_ID"))
    p.add_argument(
        "--llm-model",
        default=os.environ.get("LLM_MODEL", "anthropic/claude-sonnet-4-5"),
        help="Model for agent-server mode (ignored in cloud mode).",
    )
    p.add_argument("--llm-api-key", default=os.environ.get("LLM_API_KEY"))
    p.add_argument("--llm-base-url", default=os.environ.get("LLM_BASE_URL"))
    p.add_argument(
        "--poll-timeout",
        type=int,
        default=int(os.environ.get("POLL_TIMEOUT", "180")),
    )
    p.add_argument(
        "--keep",
        action="store_true",
        help="Leave the sandbox running instead of deleting it.",
    )
    return p.parse_args()


def start_via_cloud(
    base_url: str, headers: dict, sandbox_id: str, task: str, timeout: int
) -> str:
    """High-level: POST /api/v1/app-conversations, then poll the start task.

    The Cloud layer injects your account's LLM credentials, so no model key is
    needed. The call is async: it returns a start task whose
    ``app_conversation_id`` appears once provisioning finishes.
    """
    body = {
        "sandbox_id": sandbox_id,
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": task}],
        },
        "title": "daytona-migration scenario 1 (cloud)",
    }
    resp = requests.post(
        f"{base_url}/api/v1/app-conversations", headers=headers, json=body
    )
    resp.raise_for_status()
    task_id = resp.json()["id"]

    deadline = time.monotonic() + timeout
    while True:
        resp = requests.get(
            f"{base_url}/api/v1/app-conversations/start-tasks",
            headers=headers,
            params={"ids": task_id},
        )
        resp.raise_for_status()
        results = resp.json()
        conv_id = results[0].get("app_conversation_id") if results else None
        if conv_id:
            return conv_id
        if time.monotonic() > deadline:
            raise TimeoutError(f"start task {task_id} never returned a conversation id")
        time.sleep(2)


def start_via_agent_server(agent_url: str, session: dict, task: str, llm: dict) -> str:
    """Low-level: POST /api/conversations on the agent-server (synchronous id).

    A direct conversation inherits no Cloud LLM credentials, so we pass our own
    ``agent``/``llm`` block. The ``initial_message`` makes it start running.
    """
    body = {
        "workspace": {"kind": "LocalWorkspace", "working_dir": "/workspace/project"},
        "agent": {"kind": "Agent", "llm": llm},
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": task}],
        },
    }
    resp = requests.post(
        f"{agent_url}/api/conversations", headers=session, json=body, timeout=60
    )
    resp.raise_for_status()
    return resp.json()["id"]


def main() -> None:
    args = parse_args()
    if not args.api_key:
        sys.exit("error: set --api-key or the OH_API_KEY environment variable")
    if args.mode == "agent-server" and not args.llm_api_key:
        sys.exit("error: agent-server mode needs --llm-api-key or LLM_API_KEY")

    headers = {"X-Session-API-Key": args.api_key}

    # 1. Start a sandbox (analogue of daytona.create()).
    sb = start_sandbox(args.base_url, headers, args.sandbox_spec_id)
    sid = sb["id"]
    print("sandbox:", sid, "| mode:", args.mode)

    # 2. Wait for RUNNING.
    sb = wait_until_running(args.base_url, headers, sid, args.poll_timeout)
    agent_url = agent_server_url(sb)
    session_api_key = sb["session_api_key"]
    session = {"X-Session-API-Key": session_api_key}
    print("agent:", agent_url)

    try:
        # 3. Start a conversation with the built-in OpenHands agent.
        if args.mode == "cloud":
            conv_id = start_via_cloud(
                args.base_url, headers, sid, args.task, args.poll_timeout
            )
        else:
            llm: dict = {"model": args.llm_model, "service_id": "agent"}
            if args.llm_api_key:
                llm["api_key"] = args.llm_api_key
            if args.llm_base_url:
                llm["base_url"] = args.llm_base_url
            conv_id = start_via_agent_server(agent_url, session, args.task, llm)
        print("conversation:", conv_id)

        # 4. Stream the run until the agent reaches a terminal state.
        print("\n=== agent activity ===")
        run_watch(agent_url, session_api_key, conv_id)

        # 5. Print the agent's final response.
        answer = agent_final_response(agent_url, session, conv_id)
        if answer:
            print("\n=== final response ===")
            print(answer)
    finally:
        if args.keep:
            print(f"\nSandbox {sid} left running (use --keep=false to delete).")
        else:
            delete_sandbox(args.base_url, headers, sid)
            print(f"\nDeleted sandbox {sid}.")


if __name__ == "__main__":
    main()
