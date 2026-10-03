"""Shared sandbox-lifecycle helpers for the Daytona migration scenarios.

Both ``scenario_1_openhands_agent.py`` and ``scenario_2_acp_agent.py`` need the
same Cloud App Server plumbing: create a sandbox, wait for ``RUNNING``, find the
agent-server URL, watch the event WebSocket, and clean up. These are the direct
analogues of ``daytona.create()`` / ``daytona.get()`` / ``daytona.delete()``.

See ``start-sandbox`` and ``react-to-state-websocket`` for standalone versions
of this same flow.
"""

import asyncio
import json
import time

import requests
import websockets


DEFAULT_CLOUD = "https://app.all-hands.dev"

# execution_status values that mean the conversation has stopped running.
TERMINAL_STATES = {"finished", "error", "stuck"}


def start_sandbox(base_url: str, headers: dict, spec_id: str | None = None) -> dict:
    """POST /api/v1/sandboxes -> SandboxInfo (initially STARTING).

    The analogue of ``daytona.create()``.
    """
    params = {"sandbox_spec_id": spec_id} if spec_id else None
    resp = requests.post(f"{base_url}/api/v1/sandboxes", headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def wait_until_running(
    base_url: str, headers: dict, sandbox_id: str, timeout: int = 180
) -> dict:
    """Poll batch-get-by-id until status == RUNNING (analogue of polling state).

    ``session_api_key`` and ``exposed_urls`` are ``null`` until RUNNING.
    """
    deadline = time.monotonic() + timeout
    while True:
        resp = requests.get(
            f"{base_url}/api/v1/sandboxes", headers=headers, params={"id": sandbox_id}
        )
        resp.raise_for_status()
        results = resp.json()
        if not results or results[0] is None:
            raise ValueError(f"Sandbox {sandbox_id} not found")
        sb = results[0]
        print("  sandbox status:", sb["status"])
        if sb["status"] == "RUNNING":
            return sb
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Sandbox {sandbox_id} did not reach RUNNING within {timeout}s "
                f"(last status: {sb['status']})"
            )
        time.sleep(3)


def agent_server_url(sandbox: dict) -> str:
    """Pick the AGENT_SERVER entry from the sandbox's exposed_urls."""
    url = next(
        (u["url"] for u in sandbox["exposed_urls"] if u["name"] == "AGENT_SERVER"),
        None,
    )
    if not url:
        raise ValueError(f"AGENT_SERVER URL not found in sandbox {sandbox['id']}")
    return url


def delete_sandbox(base_url: str, headers: dict, sandbox_id: str) -> None:
    """DELETE the sandbox (id goes in both the path and a query param).

    The analogue of ``daytona.delete(sandbox)``.
    """
    requests.delete(
        f"{base_url}/api/v1/sandboxes/{sandbox_id}",
        headers=headers,
        params={"sandbox_id": sandbox_id},
    )


async def watch_states(
    agent_url: str, session_api_key: str, conversation_id: str
) -> None:
    """Stream the conversation's events and return on a terminal status.

    Connects to ``wss://{agent}/sockets/events/{id}?resend_mode=all`` and
    authenticates with a first-message ``auth`` frame (keeps the key out of the
    URL and proxy logs). ``resend_mode=all`` replays events produced between
    create and connect, closing that race without polling.
    """
    ws_base = agent_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_base}/sockets/events/{conversation_id}?resend_mode=all"

    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"type": "auth", "session_api_key": session_api_key}))
        while True:
            event = json.loads(await ws.recv())
            kind = event.get("kind")
            if kind == "ConversationStateUpdateEvent":
                if event.get("key") == "execution_status":
                    status = event.get("value")
                    print(f"  >> execution_status: {status}")
                    if status in TERMINAL_STATES:
                        return
            elif kind:
                # Surface other events (messages, actions, errors) for context.
                print(f"  .. {kind}")


def run_watch(agent_url: str, session_api_key: str, conversation_id: str) -> None:
    """Blocking wrapper around :func:`watch_states`."""
    asyncio.run(watch_states(agent_url, session_api_key, conversation_id))


def agent_final_response(agent_url: str, session: dict, conversation_id: str) -> str:
    """GET the agent's final response text, if the endpoint is available."""
    resp = requests.get(
        f"{agent_url}/api/conversations/{conversation_id}/agent_final_response",
        headers=session,
    )
    if resp.status_code != 200:
        return ""
    data = resp.json()
    if isinstance(data, dict):
        return data.get("content") or data.get("text") or json.dumps(data)
    return str(data)
