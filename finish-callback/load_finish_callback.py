#!/usr/bin/env python3
"""Start a Cloud conversation with the finish-callback plugin loaded.

A thin, purpose-built wrapper around the pattern shown in ``../load-plugin`` —
it starts a conversation with the ``oh-finish-callback`` plugin pre-loaded and,
crucially, passes the callback settings as **conversation secrets** so the Stop
hook picks them up as environment variables:

    OH_CALLBACK_URL      (required)  where the hook POSTs on finish
    OH_CALLBACK_TOKEN    (optional)  sent as the X-Callback-Token header
    OH_CALLBACK_PAYLOAD  (optional)  path (in the sandbox) to a JSON body file

Pair it with ``callback_receiver.py`` running behind a public tunnel:

    # terminal 1
    python callback_receiver.py --port 8000 --token s3cr3t
    # (expose http://localhost:8000 with a tunnel; note the public URL)

    # terminal 2
    export OH_API_KEY=...   # Cloud API key
    python load_finish_callback.py \\
        --callback-url https://your-tunnel.example/oh_finish \\
        --callback-token s3cr3t

When the agent finishes, the receiver prints the POST it got.

The plugin itself is fetched from GitHub (this repo). While iterating on a
branch, pass ``--ref your-branch`` so the fetch finds your copy; it resolves to
``main`` once merged.
"""

import argparse
import os
import sys
import time

import requests


DEFAULT_SOURCE = "github:OpenHands/enterprise-cookbook"
DEFAULT_REF = "main"
DEFAULT_REPO_PATH = "finish-callback/oh-finish-callback"
DEFAULT_MESSAGE = "Say hello and then finish."


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Start a Cloud conversation with the finish-callback plugin.",
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
        "--callback-url",
        default=os.environ.get("OH_CALLBACK_URL"),
        help="Where the Stop hook POSTs on finish (env: OH_CALLBACK_URL).",
    )
    p.add_argument(
        "--callback-token",
        default=os.environ.get("OH_CALLBACK_TOKEN"),
        help="Optional shared secret sent as X-Callback-Token "
        "(env: OH_CALLBACK_TOKEN).",
    )
    p.add_argument(
        "--callback-payload",
        default=os.environ.get("OH_CALLBACK_PAYLOAD"),
        help="Optional path IN THE SANDBOX to a JSON body file "
        "(env: OH_CALLBACK_PAYLOAD).",
    )
    p.add_argument(
        "--message",
        default=os.environ.get("INITIAL_MESSAGE", DEFAULT_MESSAGE),
        help="First message for the conversation (env: INITIAL_MESSAGE).",
    )
    p.add_argument(
        "--source",
        default=os.environ.get("PLUGIN_SOURCE", DEFAULT_SOURCE),
        help="Plugin source (env: PLUGIN_SOURCE).",
    )
    p.add_argument(
        "--ref",
        default=os.environ.get("PLUGIN_REF", DEFAULT_REF),
        help="Git ref/branch/tag of the plugin source (env: PLUGIN_REF).",
    )
    p.add_argument(
        "--repo-path",
        default=os.environ.get("PLUGIN_REPO_PATH", DEFAULT_REPO_PATH),
        help="Sub-directory of the plugin within the source (env: PLUGIN_REPO_PATH).",
    )
    p.add_argument(
        "--poll-timeout",
        type=int,
        default=int(os.environ.get("POLL_TIMEOUT", "240")),
        help="Seconds to wait for the conversation to start.",
    )
    return p.parse_args()


def start_conversation(
    base_url: str,
    headers: dict,
    plugin: dict,
    message: str,
    secrets: dict,
    timeout: int,
) -> str:
    """POST /api/v1/app-conversations, then poll the start task for its id.

    The ``plugins`` field pre-loads the hook; the ``secrets`` field supplies the
    callback settings as env vars the Stop hook reads (OH_CALLBACK_URL, etc.).
    Omitting ``sandbox_id`` tells the server to provision a fresh sandbox.
    """
    payload = {
        "plugins": [plugin],
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": message}],
        },
        "secrets": secrets,
        "title": "finish-callback demo",
    }
    resp = requests.post(
        f"{base_url}/api/v1/app-conversations", headers=headers, json=payload
    )
    resp.raise_for_status()
    task = resp.json()
    task_id = task["id"]
    conv_id = task.get("app_conversation_id")

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
        if item.get("status") == "ERROR":
            raise RuntimeError(f"Start task failed: {item.get('detail', 'unknown')}")
        conv_id = item.get("app_conversation_id")
    return conv_id


def main() -> int:
    args = parse_args()
    if not args.api_key:
        print("Error: set OH_API_KEY (or pass --api-key).", file=sys.stderr)
        return 1
    if not args.callback_url:
        print(
            "Error: set --callback-url (or OH_CALLBACK_URL). Without it the hook "
            "is a no-op and nothing is POSTed.",
            file=sys.stderr,
        )
        return 1

    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
    }
    plugin = {
        "source": args.source,
        "ref": args.ref,
        "repo_path": args.repo_path,
    }
    secrets = {"OH_CALLBACK_URL": args.callback_url}
    if args.callback_token:
        secrets["OH_CALLBACK_TOKEN"] = args.callback_token
    if args.callback_payload:
        secrets["OH_CALLBACK_PAYLOAD"] = args.callback_payload

    print(f"Loading plugin: {plugin['source']}@{plugin['ref']}:{plugin['repo_path']}")
    print(f"Callback URL:   {args.callback_url}")
    print(f"Secrets set:    {', '.join(secrets)}")
    print(f"Initial message: {args.message!r}")
    print("Creating conversation...")

    conv_id = start_conversation(
        args.base_url, headers, plugin, args.message, secrets, args.poll_timeout
    )

    print("\nConversation ready.")
    print(f"  {args.base_url}/conversations/{conv_id}")
    print(
        "\nWatch your callback_receiver.py terminal — a POST should arrive when "
        "the agent finishes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
