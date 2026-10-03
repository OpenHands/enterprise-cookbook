#!/usr/bin/env python3
"""Scenario 2: run Claude Code or Codex inside an OpenHands sandbox (ACP).

Here OpenHands is just the managed, isolated infrastructure — the actual coding
agent is an external CLI (Claude Code, Codex, or Gemini CLI) bridged in over the
Agent Client Protocol (ACP). The OpenHands agent launches the CLI as a
subprocess instead of calling an LLM itself. These CLIs are already installed in
the default sandbox image, so nothing needs provisioning.

You select the backend with an ``agent`` block whose ``kind`` is ``ACPAgent``
(``acp_server`` + optional ``acp_model``), and you pass the provider credential
through the conversation ``secrets`` channel keyed by the provider's env-var
name — NOT the agent's ``llm`` field (ACP ignores it).

    export OH_API_KEY=...              # Cloud API key (required)
    export ANTHROPIC_API_KEY=...       # for --provider claude-code
    # or OPENAI_API_KEY / GEMINI_API_KEY for codex / gemini-cli
    pip install -r requirements.txt

    python scenario_2_acp_agent.py --provider claude-code --task "Refactor utils.py"
    python scenario_2_acp_agent.py --provider codex --acp-model gpt-5.5 \
        --task "Add unit tests"

Env vars: OH_API_KEY, OH_API_BASE, SANDBOX_SPEC_ID, ANTHROPIC_API_KEY,
OPENAI_API_KEY, GEMINI_API_KEY, POLL_TIMEOUT.
"""

import argparse
import os
import sys

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


# provider key -> the env var whose value is its credential. These are the
# credential env-var names from the ACP_PROVIDERS registry in openhands-sdk.
PROVIDER_CREDENTIAL_ENV = {
    "claude-code": "ANTHROPIC_API_KEY",
    "codex": "OPENAI_API_KEY",
    "gemini-cli": "GEMINI_API_KEY",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--provider",
        choices=sorted(PROVIDER_CREDENTIAL_ENV),
        default="claude-code",
        help="Which ACP backend to launch inside the sandbox.",
    )
    p.add_argument(
        "--acp-model",
        default=None,
        help="Model id for the ACP server (e.g. 'sonnet', 'gpt-5.5'). "
        "Leave blank to use the provider default.",
    )
    p.add_argument(
        "--task",
        default="Create hello.txt containing the text 'Hello from ACP'.",
        help="The instruction to send the ACP agent.",
    )
    p.add_argument(
        "--credential",
        default=None,
        help="Provider API key. Defaults to the provider's env var "
        "(ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY).",
    )
    p.add_argument("--api-key", default=os.environ.get("OH_API_KEY"))
    p.add_argument("--base-url", default=os.environ.get("OH_API_BASE", DEFAULT_CLOUD))
    p.add_argument("--sandbox-spec-id", default=os.environ.get("SANDBOX_SPEC_ID"))
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


def start_acp_conversation(
    agent_url: str,
    session: dict,
    task: str,
    acp_server: str,
    acp_model: str | None,
    credential_env: str,
    credential: str,
) -> str:
    """POST /api/conversations with an ACPAgent block + credential in secrets.

    The ``llm`` block is a required placeholder used only for cost attribution;
    the ACP subprocess makes its own model calls. The real credential travels in
    ``secrets`` under the provider's env-var name.
    """
    agent: dict = {
        "kind": "ACPAgent",
        "acp_server": acp_server,
        "llm": {"model": "acp-placeholder", "service_id": "agent"},
    }
    if acp_model:
        agent["acp_model"] = acp_model

    body = {
        "workspace": {"kind": "LocalWorkspace", "working_dir": "/workspace/project"},
        "agent": agent,
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": task}],
        },
        "secrets": {credential_env: credential},
    }
    resp = requests.post(
        f"{agent_url}/api/conversations", headers=session, json=body, timeout=60
    )
    resp.raise_for_status()
    return resp.json()["id"]


def switch_acp_model(
    agent_url: str, session: dict, conversation_id: str, model: str
) -> None:
    """POST /api/conversations/{id}/switch_acp_model — swap the model at runtime."""
    resp = requests.post(
        f"{agent_url}/api/conversations/{conversation_id}/switch_acp_model",
        headers=session,
        json={"model": model},
    )
    resp.raise_for_status()


def main() -> None:
    args = parse_args()
    if not args.api_key:
        sys.exit("error: set --api-key or the OH_API_KEY environment variable")

    credential_env = PROVIDER_CREDENTIAL_ENV[args.provider]
    credential = args.credential or os.environ.get(credential_env)
    if not credential:
        sys.exit(
            f"error: provider '{args.provider}' needs a credential; set "
            f"--credential or the {credential_env} environment variable"
        )

    headers = {"X-Session-API-Key": args.api_key}

    # 1. Start a sandbox (ACP CLIs are pre-installed in the default image).
    sb = start_sandbox(args.base_url, headers, args.sandbox_spec_id)
    sid = sb["id"]
    print("sandbox:", sid, "| provider:", args.provider)

    # 2. Wait for RUNNING.
    sb = wait_until_running(args.base_url, headers, sid, args.poll_timeout)
    agent_url = agent_server_url(sb)
    session_api_key = sb["session_api_key"]
    session = {"X-Session-API-Key": session_api_key}
    print("agent:", agent_url)

    try:
        # 3. Create a conversation backed by the external ACP agent.
        conv_id = start_acp_conversation(
            agent_url,
            session,
            args.task,
            args.provider,
            args.acp_model,
            credential_env,
            credential,
        )
        print("conversation:", conv_id)

        # 4. Stream the run until the ACP agent reaches a terminal state.
        print("\n=== acp agent activity ===")
        run_watch(agent_url, session_api_key, conv_id)

        # 5. Print the final response.
        answer = agent_final_response(agent_url, session, conv_id)
        if answer:
            print("\n=== final response ===")
            print(answer)
    finally:
        if args.keep:
            print(f"\nSandbox {sid} left running (omit --keep to delete).")
        else:
            delete_sandbox(args.base_url, headers, sid)
            print(f"\nDeleted sandbox {sid}.")


if __name__ == "__main__":
    main()
