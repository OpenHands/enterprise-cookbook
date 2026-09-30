#!/usr/bin/env python3
"""Override the OpenHands system prompt for one conversation.

The Cloud app server accepts a ``system_prompt`` field on
``POST /api/v1/app-conversations``. When set, it *replaces* the default
static system prompt for that conversation. Per-conversation dynamic
context (repo context, loaded skills, custom secrets, current datetime)
is still appended automatically, so the custom prompt composes with
whatever runtime context the sandbox provides.

Flow:

  1. Start a conversation with ``system_prompt`` set
     (``POST /api/v1/app-conversations``). The call is asynchronous and
     returns a *start task*; poll ``/api/v1/app-conversations/start-tasks``
     until it hands back an ``app_conversation_id``.
  2. Fetch the first ``SystemPromptEvent`` from the Cloud events endpoint
     (``GET /api/v1/conversation/{id}/events/search?kind__eq=SystemPromptEvent``)
     and confirm ``event["system_prompt"]["text"]`` matches what we sent.
  3. Clean up: ``DELETE`` the conversation and its sandbox.

Env / flags:

    export OH_API_KEY=...                     # Cloud API key (required)
    python custom_system_prompt.py            # default research-assistant prompt
    python custom_system_prompt.py --prompt "You are a pirate. Reply in yarrs."
    python custom_system_prompt.py --keep     # skip cleanup, print URLs to inspect
    python custom_system_prompt.py \
        --base-url https://app.beta.staging.all-hands-testing.dev  # OHE instance
"""

import argparse
import os
import sys
import time

import requests


DEFAULT_PROMPT = (
    "You are a research assistant. Answer questions concisely and accurately. "
    "When writing code, focus on clarity and simplicity."
)
DEFAULT_MESSAGE = "In one short sentence, tell me what your role is."


def _raise_for_status(resp: requests.Response) -> None:
    """Like ``resp.raise_for_status()`` but prints the server's error body.

    ``raise_for_status`` on its own only surfaces the status line, which
    hides the server-side validation detail (``{"detail": [...]}`` on
    4xx). Printing ``resp.text`` first turns a mystery 422 into an
    actionable "you're missing this field" message for the caller.
    """
    if resp.ok:
        return
    print(
        f"HTTP {resp.status_code} from {resp.request.method} {resp.url}\n"
        f"  body: {resp.text[:2000]}",
        file=sys.stderr,
    )
    resp.raise_for_status()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Override the OpenHands system prompt via app conversation API.",
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
        "--prompt",
        default=os.environ.get("SYSTEM_PROMPT", DEFAULT_PROMPT),
        help="Custom system prompt to install (env: SYSTEM_PROMPT).",
    )
    p.add_argument(
        "--message",
        default=os.environ.get("INITIAL_MESSAGE", DEFAULT_MESSAGE),
        help="First user message (env: INITIAL_MESSAGE).",
    )
    p.add_argument(
        "--sandbox-id",
        default=os.environ.get("SANDBOX_ID"),
        help="Reuse an existing RUNNING sandbox instead of provisioning one "
        "(env: SANDBOX_ID).",
    )
    p.add_argument(
        "--keep",
        action="store_true",
        help="Leave the conversation/sandbox running instead of deleting them.",
    )
    p.add_argument(
        "--poll-timeout",
        type=int,
        default=int(os.environ.get("POLL_TIMEOUT", "240")),
        help="Seconds to wait for conversation readiness (env: POLL_TIMEOUT).",
    )
    return p.parse_args()


def start_conversation(
    base_url: str, headers: dict, args: argparse.Namespace
) -> tuple[str, str | None]:
    """POST /api/v1/app-conversations -> resolve the app_conversation_id.

    Returns (app_conversation_id, sandbox_id). ``sandbox_id`` is whichever
    sandbox ends up serving the conversation -- one we passed in, or one the
    App Server provisioned for us.

    The POST is asynchronous: it returns a *start task* whose ``id`` is the
    task id, not the conversation id. Poll
    ``/api/v1/app-conversations/start-tasks?ids=<task_id>`` until it hands
    back an ``app_conversation_id``.
    """
    payload: dict = {
        "system_prompt": args.prompt,
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": args.message}],
        },
        "title": "custom-system-prompt demo",
    }
    if args.sandbox_id:
        payload["sandbox_id"] = args.sandbox_id

    resp = requests.post(
        f"{base_url}/api/v1/app-conversations", headers=headers, json=payload
    )
    _raise_for_status(resp)
    task = resp.json()
    task_id = task["id"]
    conv_id = task.get("app_conversation_id")
    sandbox_id = task.get("sandbox_id")

    deadline = time.monotonic() + args.poll_timeout
    while not conv_id:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Start task {task_id} never produced a conversation")
        time.sleep(3)
        resp = requests.get(
            f"{base_url}/api/v1/app-conversations/start-tasks",
            headers=headers,
            params={"ids": task_id},
        )
        _raise_for_status(resp)
        item = resp.json()[0]
        status = item.get("status")
        print("  start-task status:", status)
        if status == "ERROR":
            raise RuntimeError(f"Start task failed: {item.get('detail', 'unknown')}")
        conv_id = item.get("app_conversation_id")
        sandbox_id = item.get("sandbox_id") or sandbox_id
    return conv_id, sandbox_id


def fetch_system_prompt_event(
    base_url: str, headers: dict, conv_id: str, timeout: int
) -> dict:
    """Fetch the first ``SystemPromptEvent`` for the conversation.

    The event is created before the first LLM turn, so it's usually
    available the moment the conversation is READY -- but retry briefly
    to absorb the small write-read gap.
    """
    deadline = time.monotonic() + timeout
    url = f"{base_url}/api/v1/conversation/{conv_id}/events/search"
    while True:
        resp = requests.get(
            url,
            headers=headers,
            params={"kind__eq": "SystemPromptEvent", "limit": 1},
        )
        _raise_for_status(resp)
        items = resp.json().get("items") or []
        if items:
            return items[0]
        if time.monotonic() > deadline:
            raise TimeoutError(f"No SystemPromptEvent for {conv_id} after {timeout}s")
        time.sleep(2)


def cleanup(base_url: str, headers: dict, conv_id: str, sandbox_id: str | None) -> None:
    requests.delete(f"{base_url}/api/v1/app-conversations/{conv_id}", headers=headers)
    print("  deleted conversation", conv_id)
    if sandbox_id:
        # DELETE /api/v1/sandboxes/{id} also requires sandbox_id as a
        # query parameter; omitting it returns HTTP 422.
        requests.delete(
            f"{base_url}/api/v1/sandboxes/{sandbox_id}",
            headers=headers,
            params={"sandbox_id": sandbox_id},
        )
        print("  deleted sandbox", sandbox_id)


def main() -> None:
    args = parse_args()
    if not args.api_key:
        sys.exit("error: set --api-key or the OH_API_KEY environment variable")
    headers = {"Authorization": f"Bearer {args.api_key}"}

    print("=== start conversation ===")
    print(
        "  custom prompt:", args.prompt[:80] + ("..." if len(args.prompt) > 80 else "")
    )
    conv_id, sandbox_id = start_conversation(args.base_url, headers, args)
    print("conversation:", conv_id)
    if sandbox_id:
        print("sandbox:", sandbox_id)

    print("\n=== verify SystemPromptEvent ===")
    event = fetch_system_prompt_event(
        args.base_url, headers, conv_id, args.poll_timeout
    )
    # SystemPromptEvent shape:
    #   { "kind": "SystemPromptEvent",
    #     "system_prompt":   {"cache_prompt": bool, "type": "text", "text": "..."},
    #     "dynamic_context": {"cache_prompt": bool, "type": "text", "text": "..."},
    #     "tools": [...], ... }
    sp_text = (event.get("system_prompt") or {}).get("text", "")
    dc_text = (event.get("dynamic_context") or {}).get("text", "")
    print(f"  system_prompt.text ({len(sp_text)} chars): {sp_text[:120]}...")
    print(
        f"  dynamic_context.text ({len(dc_text)} chars) — appended automatically; "
        f"contains repo/skills/secrets/datetime blocks"
    )

    ok = args.prompt in sp_text
    verdict = "PASS" if ok else "FAIL"
    found = "found" if ok else "NOT found"
    print(f"\n  {verdict}: custom prompt {found} in SystemPromptEvent")

    if args.keep:
        url = f"{args.base_url}/conversations/{conv_id}"
        print(f"\nLeft running (--keep). Open: {url}")
    else:
        print("\n=== cleanup ===")
        cleanup(args.base_url, headers, conv_id, sandbox_id)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
