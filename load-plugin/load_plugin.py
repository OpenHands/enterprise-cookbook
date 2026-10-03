#!/usr/bin/env python3
"""Create an OpenHands Cloud conversation with a plugin loaded (minimal).

This is the smallest useful recipe for starting a conversation that has a
plugin pre-loaded, using only the V1 App Server REST API:

    POST /api/v1/app-conversations
    {
      "plugins":         [{"source": ..., "ref": ..., "repo_path": ...}],
      "initial_message": {"role": "user",
                          "content": [{"type": "text", "text": ...}]}
    }

The call is asynchronous: it returns a *start task*, not the conversation
itself. We poll ``/api/v1/app-conversations/start-tasks`` until it hands back
an ``app_conversation_id``, then print the conversation URL.

This example is self-contained: the plugin it loads lives right next door in
``dad-joke/`` and is fetched from this repo on GitHub. By default it runs the
plugin's entry command (``/dad-joke:about duck``), which tells a dad joke about
a duck. Override anything via flags or env vars.

    export OH_API_KEY=...                 # Cloud API key (required)
    python load_plugin.py                 # dad-joke + "/dad-joke:about duck"

    # Load the plugin, then drive it with a natural-language prompt. The bundled
    # skill asks for your favorite animal and then tells a joke about it:
    python load_plugin.py --message "Tell me a dad joke"

A plugin can also live in a *private* repo. Put a ``${SECRET}`` placeholder in
the source URL and pass the value with --secret; it is expanded against the
conversation's secrets just before the plugin is fetched (see "Loading a private
plugin" in the README):

    python load_plugin.py \
        --source 'https://x-access-token:${GIT_TOKEN}@github.com/me/plugins.git' \
        --repo-path plugins/my-plugin \
        --message '/my-plugin:start' \
        --secret GIT_TOKEN="$GIT_TOKEN"
"""

import argparse
import os
import sys
import time

import requests


# The plugin is fetched from this repo on GitHub (this very directory's
# dad-joke/). Until your changes are merged to the default branch, point --ref
# at your branch so the fetch can find the plugin.
DEFAULT_SOURCE = "github:OpenHands/enterprise-cookbook"
DEFAULT_REF = "main"
DEFAULT_REPO_PATH = "load-plugin/dad-joke"
DEFAULT_MESSAGE = "/dad-joke:about duck"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Start a Cloud conversation with a plugin loaded.",
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
        "--source",
        default=os.environ.get("PLUGIN_SOURCE", DEFAULT_SOURCE),
        help="Plugin source, e.g. 'github:owner/repo' (env: PLUGIN_SOURCE).",
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
        "--message",
        default=os.environ.get("INITIAL_MESSAGE", DEFAULT_MESSAGE),
        help="First message for the conversation (env: INITIAL_MESSAGE).",
    )
    p.add_argument(
        "--secret",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Per-conversation secret (repeatable). Referenced as ${KEY} in the "
        "plugin source/ref to fetch a private repo.",
    )
    p.add_argument(
        "--poll-timeout",
        type=int,
        default=int(os.environ.get("POLL_TIMEOUT", "240")),
        help="Seconds to wait for the conversation to start.",
    )
    return p.parse_args()


def start_conversation_with_plugin(
    base_url: str,
    headers: dict,
    plugin: dict,
    message: str,
    timeout: int,
    secrets: dict | None = None,
) -> str:
    """POST /api/v1/app-conversations, then poll the start task for its id.

    Omitting ``sandbox_id`` tells the server to provision a fresh sandbox for
    us. The single ``plugins`` field is all it takes to pre-load a plugin.

    ``secrets`` (optional) are per-conversation secrets; a ``${KEY}`` reference
    in the plugin's ``source``/``ref`` is expanded against them before the repo
    is cloned, so a private plugin can be fetched without hard-coding a token.
    """
    payload = {
        "plugins": [plugin],
        "initial_message": {
            "role": "user",
            "content": [{"type": "text", "text": message}],
        },
        "title": "load-plugin demo",
    }
    if secrets:
        payload["secrets"] = secrets
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

    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
    }
    plugin = {
        "source": args.source,
        "ref": args.ref,
        "repo_path": args.repo_path,
    }
    secrets = dict(kv.split("=", 1) for kv in args.secret) if args.secret else None

    print(f"Loading plugin: {plugin['source']}@{plugin['ref']}:{plugin['repo_path']}")
    print(f"Initial message: {args.message!r}")
    if secrets:
        print(f"Secrets provided: {', '.join(secrets)}")
    print("Creating conversation...")

    conv_id = start_conversation_with_plugin(
        args.base_url, headers, plugin, args.message, args.poll_timeout, secrets
    )

    print("\nConversation ready.")
    print(f"  {args.base_url}/conversations/{conv_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
