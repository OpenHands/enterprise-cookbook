#!/usr/bin/env python3
"""Start and monitor a scoped conversation on a remote macOS Agent Server."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any


class UsageError(Exception):
    """Invalid input before creating a conversation."""


class RemoteError(Exception):
    """The remote request failed or the conversation did not finish."""


def read_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Mac Agent Server HTTPS URL")
    parser.add_argument("--workspace", required=True, help="Approved absolute Mac path")
    parser.add_argument("--prompt", required=True, help="Approved task for the Mac")
    parser.add_argument("--target-key-env", default="MAC_AGENT_SERVER_KEY")
    parser.add_argument(
        "--ca-cert-env", help="PEM or base64-encoded Mac certificate env var"
    )
    parser.add_argument(
        "--tool",
        action="append",
        choices=("terminal", "file_editor", "task_tracker"),
        default=[],
        help="Explicitly permit one Mac tool; repeat for more (default: none)",
    )
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--poll-interval", type=float, default=2)
    parser.add_argument("--timeout", type=float, default=120)
    return parser.parse_args(argv)


def validate(
    args: argparse.Namespace, env: Mapping[str, str]
) -> tuple[str, ssl.SSLContext | None]:
    parts = urllib.parse.urlsplit(args.target)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise UsageError(
            "--target must be an HTTPS origin without credentials or a path"
        )
    try:
        parts.port
    except ValueError as error:
        raise UsageError("--target has an invalid port") from error
    if not os.path.isabs(args.workspace) or not args.prompt.strip():
        raise UsageError("--workspace must be absolute and --prompt must not be empty")
    if args.max_iterations < 1 or args.poll_interval <= 0 or args.timeout <= 0:
        raise UsageError(
            "--max-iterations, --poll-interval, and --timeout must be positive"
        )
    if not env.get(args.target_key_env):
        raise UsageError(f"${args.target_key_env} is not set")
    context = None
    if args.ca_cert_env:
        certificate = env.get(args.ca_cert_env, "")
        if "BEGIN CERTIFICATE" not in certificate:
            try:
                certificate = base64.b64decode(
                    certificate.strip(), validate=True
                ).decode()
            except (binascii.Error, UnicodeDecodeError):
                certificate = ""
        if "BEGIN CERTIFICATE" not in certificate:
            raise UsageError(f"${args.ca_cert_env} must hold a PEM certificate")
        try:
            context = ssl.create_default_context(cadata=certificate)
        except ssl.SSLError as error:
            raise UsageError(
                f"${args.ca_cert_env} is not a valid certificate"
            ) from error
    return args.target.rstrip("/"), context


class Client:
    def __init__(self, target: str, key: str, context: ssl.SSLContext | None):
        self.target = target
        self.key = key
        self.context = context

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        encrypted: bool = False,
    ) -> tuple[int, Any]:
        headers = {"X-Session-API-Key": self.key, "Accept": "application/json"}
        if encrypted:
            headers["X-Expose-Secrets"] = "encrypted"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.target + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request, timeout=30, context=self.context
            ) as response:
                status, data = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, data = error.code, b""
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise RemoteError("Mac TLS certificate was not accepted") from None
            raise RemoteError("Cannot reach the Mac Agent Server") from None
        if status in (401, 403):
            raise RemoteError(
                f"Mac Agent Server rejected the session key (HTTP {status})"
            )
        if status >= 400:
            raise RemoteError(
                f"Mac Agent Server returned HTTP {status} "
                f"for {method} {path.split('?')[0]}"
            )
        try:
            return status, json.loads(data)
        except ValueError:
            raise RemoteError("Mac Agent Server returned invalid JSON") from None


def launch(client: Client, args: argparse.Namespace) -> str:
    _, profiles = client.request("GET", "/api/profiles")
    if not isinstance(profiles, dict) or not profiles.get("active_profile"):
        raise RemoteError("Mac has no active LLM profile; run sync_llm.py --check")
    _, settings = client.request("GET", "/api/settings", encrypted=True)
    agent_settings = (
        settings.get("agent_settings") if isinstance(settings, dict) else None
    )
    llm = agent_settings.get("llm") if isinstance(agent_settings, dict) else None
    if not isinstance(llm, dict) or not str(llm.get("api_key", "")).startswith(
        "gAAAAA"
    ):
        raise RemoteError("Mac settings did not provide an encrypted LLM key")
    agent_settings = {
        key: value
        for key, value in agent_settings.items()
        if key not in ("schema_version", "mcp_config", "agent_context")
    }
    agent_settings["tools"] = [
        {"name": tool, "params": {}} for tool in dict.fromkeys(args.tool)
    ]
    agent_settings["agent_context"] = {
        "load_memory": False,
        "load_public_skills": False,
        "load_user_skills": False,
        "load_project_skills": False,
    }
    status, result = client.request(
        "POST",
        "/api/conversations",
        body={
            "secrets_encrypted": True,
            "agent_settings": agent_settings,
            "workspace": {"kind": "LocalWorkspace", "working_dir": args.workspace},
            "worktree": False,
            "autotitle": False,
            "max_iterations": args.max_iterations,
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": args.prompt}],
                "run": True,
            },
        },
    )
    if status not in (200, 201) or not isinstance(result, dict) or not result.get("id"):
        raise RemoteError("Mac did not return a conversation ID; check before retrying")
    return str(result["id"])


def latest_reply(client: Client, conversation_id: str) -> str | None:
    query = urllib.parse.urlencode(
        {
            "kind": "MessageEvent",
            "source": "agent",
            "sort_order": "TIMESTAMP_DESC",
            "limit": 10,
        }
    )
    _, result = client.request(
        "GET", f"/api/conversations/{conversation_id}/events/search?{query}"
    )
    events = (
        result.get("items", result.get("events", []))
        if isinstance(result, dict)
        else []
    )
    for event in events:
        if event.get("kind") != "MessageEvent" or event.get("source") != "agent":
            continue
        content = event.get("llm_message", {}).get("content", [])
        text = "\n".join(part["text"] for part in content if part.get("type") == "text")
        if text:
            return text
    return None


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    target, context = validate(args, env)
    client = Client(target, env[args.target_key_env], context)
    conversation_id = launch(client, args)
    print(f"Mac conversation: {conversation_id}", flush=True)
    deadline = time.monotonic() + args.timeout
    while True:
        _, result = client.request("GET", f"/api/conversations/{conversation_id}")
        state = result.get("execution_status") if isinstance(result, dict) else None
        if state == "finished":
            reply = latest_reply(client, conversation_id)
            if not reply:
                raise RemoteError(
                    f"Conversation {conversation_id} finished with no agent text reply"
                )
            print(reply)
            return 0
        if state in ("error", "stuck", "stopped", "waiting_for_confirmation", "paused"):
            raise RemoteError(f"Conversation {conversation_id} is {state}")
        if state not in ("idle", "running"):
            raise RemoteError(f"Conversation {conversation_id} has unknown status")
        if time.monotonic() >= deadline:
            print(
                f"Conversation {conversation_id} still {state}; timed out (not stopped)"
            )
            return 3
        time.sleep(min(args.poll_interval, max(0, deadline - time.monotonic())))


def main(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> int:
    try:
        return run(read_args(argv), os.environ if env is None else env)
    except UsageError as error:
        print(f"error: {error}")
        return 2
    except RemoteError as error:
        print(f"FAIL {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
