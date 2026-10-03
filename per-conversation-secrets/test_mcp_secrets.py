#!/usr/bin/env python3
"""
End-to-end test for per-conversation secrets with MCP plugin and variable expansion.

This script demonstrates the FULL workflow:
1. Use an MCP server (already running) that expects a specific token
2. Start a sandbox and inject the secret
3. Start a conversation WITH A PLUGIN that has MCP config using ${MCP_SECRET_TOKEN}
4. The plugin's MCP config expands the variable using the injected secret
5. Agent calls the MCP tool - server validates the token was passed via config expansion

This proves that:
- Secrets can be injected per-conversation via REST API
- MCP config variable expansion (${VAR}) works with injected secrets
- The full plugin → MCP config → secret expansion → MCP server flow works

Prerequisites:
    - OH_API_KEY environment variable
    - MCP server running at MCP_SERVER_URL (or start one with mcp_server.py)

Usage:
    # Terminal 1: Start MCP server
    python mcp_server.py --port 12000 --expected-token "per-conv-secret-xyz-123"

    # Terminal 2: Run test
    export OH_API_KEY="sk-oh-..."
    export MCP_SERVER_URL="https://work-1-xxx.prod-runtime.all-hands.dev"
    python test_mcp_secrets.py
"""

import json
import os
import sys
import time
from typing import Any

import requests


# Configuration
API_KEY = os.environ.get("OH_API_KEY", "")
APP_URL = "https://app.all-hands.dev/api"

# MCP Server URL - should be set to wherever the MCP server is running
# For testing from another OpenHands conversation, use that conversation's work-1 URL
MCP_SERVER_URL = os.environ.get(
    "MCP_SERVER_URL", "https://work-1-xalhxaimdbsjruoa.prod-runtime.all-hands.dev"
)

# The secret we'll inject - must match what the MCP server expects
SECRET_NAME = "MCP_SECRET_TOKEN"
SECRET_VALUE = "per-conv-secret-xyz-123"

# Plugin source - from GitHub
PLUGIN_SOURCE = "github:OpenHands/enterprise-cookbook"
PLUGIN_PATH = "per-conversation-secrets/test-plugin"


def log(msg: str) -> None:
    """Print with timestamp."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def verify_mcp_server() -> bool:
    """Verify the MCP server is running and accessible."""
    log(f"Verifying MCP server at {MCP_SERVER_URL}...")
    try:
        resp = requests.get(f"{MCP_SERVER_URL}/health", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            log(f"  Server OK: {data}")
            return True
        log(f"  Server returned {resp.status_code}")
    except Exception as e:
        log(f"  Failed to reach server: {e}")
    return False


def start_sandbox() -> dict[str, Any]:
    """Start a new sandbox and wait for it to be ready."""
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    log("Starting sandbox...")
    resp = requests.post(f"{APP_URL}/v1/sandboxes", headers=headers, timeout=60)
    resp.raise_for_status()
    sandbox = resp.json()
    sandbox_id = sandbox["id"]
    log(f"  Sandbox ID: {sandbox_id}")

    # Wait for running
    log("Waiting for sandbox to be ready...")
    for i in range(120):
        resp = requests.get(
            f"{APP_URL}/v1/sandboxes",
            headers=headers,
            params={"id": sandbox_id},
            timeout=30,
        )
        sandboxes = resp.json()
        if sandboxes and sandboxes[0]["status"] == "RUNNING":
            sandbox = sandboxes[0]
            session_key = sandbox["session_api_key"]
            agent_url = None
            for url_info in sandbox.get("exposed_urls", []):
                if url_info["name"] == "AGENT_SERVER":
                    agent_url = url_info["url"]
                    break
            if agent_url:
                log(f"  Agent Server: {agent_url}")
                return {
                    "sandbox_id": sandbox_id,
                    "session_api_key": session_key,
                    "agent_server_url": agent_url,
                    "headers": headers,
                }
        time.sleep(2)

    raise TimeoutError("Sandbox did not become ready in time")


def inject_secret(sandbox_info: dict[str, Any], conv_id: str) -> bool:
    """Inject the secret into the conversation."""
    agent_url = sandbox_info["agent_server_url"]
    agent_headers = {"X-Session-API-Key": sandbox_info["session_api_key"]}

    log(f"Injecting secret: {SECRET_NAME}={SECRET_VALUE}")
    resp = requests.post(
        f"{agent_url}/api/conversations/{conv_id}/secrets",
        headers=agent_headers,
        json={"secrets": {SECRET_NAME: SECRET_VALUE}},
        timeout=30,
    )

    if resp.status_code == 200:
        result = resp.json()
        log(f"  Result: {result}")
        return result.get("success", False)
    else:
        log(f"  ERROR: {resp.status_code} - {resp.text}")
        return False


def start_conversation_with_plugin(sandbox_info: dict[str, Any]) -> str:
    """Start a conversation with the test plugin loaded."""
    agent_url = sandbox_info["agent_server_url"]
    agent_headers = {"X-Session-API-Key": sandbox_info["session_api_key"]}

    # Get baseline conversations
    resp = requests.get(
        f"{agent_url}/api/conversations/search", headers=agent_headers, timeout=30
    )
    before_ids = set(c["id"] for c in resp.json().get("items", []))

    log("Starting conversation with plugin...")
    log(f"  Plugin: {PLUGIN_SOURCE} / {PLUGIN_PATH}")

    # Start conversation via app-server WITH PLUGIN
    resp = requests.post(
        f"{APP_URL}/v1/app-conversations",
        headers=sandbox_info["headers"],
        json={
            "sandbox_id": sandbox_info["sandbox_id"],
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": "Say 'Ready' and nothing else."}],
            },
            "plugins": [{"source": PLUGIN_SOURCE, "repo_path": PLUGIN_PATH}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    log(f"  App-conversation response: {resp.status_code}")

    # Find new conversation on agent-server
    for i in range(30):
        resp = requests.get(
            f"{agent_url}/api/conversations/search", headers=agent_headers, timeout=30
        )
        after_ids = set(c["id"] for c in resp.json().get("items", []))
        new_ids = after_ids - before_ids
        if new_ids:
            conv_id = list(new_ids)[0]
            log(f"  Conversation ID: {conv_id}")
            return conv_id
        time.sleep(1)

    raise TimeoutError("Conversation did not appear on agent server")


def send_message(sandbox_info: dict[str, Any], conv_id: str, message: str) -> bool:
    """Send a message to the conversation."""
    agent_url = sandbox_info["agent_server_url"]
    agent_headers = {"X-Session-API-Key": sandbox_info["session_api_key"]}

    log("Sending message...")
    resp = requests.post(
        f"{agent_url}/api/conversations/{conv_id}/events",
        headers=agent_headers,
        json={
            "role": "user",
            "content": [{"type": "text", "text": message}],
            "run": True,
        },
        timeout=60,
    )

    if resp.status_code != 200:
        log(f"  ERROR: {resp.status_code} - {resp.text[:200]}")
    return resp.status_code == 200


def get_events(sandbox_info: dict[str, Any], conv_id: str) -> list[dict]:
    """Get all events from the conversation."""
    agent_url = sandbox_info["agent_server_url"]
    agent_headers = {"X-Session-API-Key": sandbox_info["session_api_key"]}

    resp = requests.get(
        f"{agent_url}/api/conversations/{conv_id}/events/search",
        headers=agent_headers,
        params={"limit": 100},
        timeout=60,
    )

    if resp.status_code == 200:
        return resp.json().get("items", [])
    return []


def check_in_output(events: list[dict], substring: str) -> bool:
    """Check if substring appears in any event output."""
    for event in events:
        # Check observation content
        obs = event.get("observation", {})
        if isinstance(obs, dict):
            content = obs.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text", "")
                        if substring.lower() in text.lower():
                            return True
            elif isinstance(content, str) and substring.lower() in content.lower():
                return True

        # Check action/response content
        action = event.get("action", {})
        if isinstance(action, dict):
            for key in ["content", "text", "message"]:
                val = action.get(key, "")
                if isinstance(val, str) and substring.lower() in val.lower():
                    return True

        # Check llm_message content
        llm_msg = event.get("llm_message", {})
        if isinstance(llm_msg, dict):
            content = llm_msg.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text", "")
                        if substring.lower() in text.lower():
                            return True

    return False


def cleanup(sandbox_info: dict[str, Any] = None) -> None:
    """Clean up resources."""
    log("Cleaning up...")

    if sandbox_info:
        log("  Deleting sandbox...")
        try:
            requests.delete(
                f"{APP_URL}/v1/sandboxes/{sandbox_info['sandbox_id']}",
                headers=sandbox_info["headers"],
                timeout=30,
            )
        except Exception as e:
            log(f"  Warning: Sandbox cleanup failed: {e}")

    log("  Done.")


def main() -> int:
    print("=" * 70)
    print("  PER-CONVERSATION SECRETS + MCP PLUGIN EXPANSION TEST")
    print("=" * 70)
    print()
    print(f"  MCP Server: {MCP_SERVER_URL}")
    print(f"  Secret: {SECRET_NAME}={SECRET_VALUE}")
    print(f"  Plugin: {PLUGIN_SOURCE} / {PLUGIN_PATH}")
    print()

    if not API_KEY:
        print("ERROR: OH_API_KEY environment variable not set")
        return 1

    # Verify MCP server is running
    if not verify_mcp_server():
        print("ERROR: MCP server not accessible")
        print("Please start the MCP server first:")
        print(f'  python mcp_server.py --port 12000 --expected-token "{SECRET_VALUE}"')
        return 1

    sandbox_info = None

    try:
        # Step 1: Start sandbox
        sandbox_info = start_sandbox()

        # Step 2: Start conversation WITH PLUGIN
        # The plugin has MCP config with ${MCP_SECRET_TOKEN}
        conv_id = start_conversation_with_plugin(sandbox_info)

        # Step 3: Inject the secret BEFORE first message triggers plugin load
        # This is critical - the secret must be in place before MCP config expands
        if not inject_secret(sandbox_info, conv_id):
            log("ERROR: Failed to inject secret")
            return 1

        # Step 4: Wait for initial message and plugin loading
        log("Waiting for plugin to load and initial message...")
        time.sleep(20)

        # Step 5: Ask agent to call the MCP tool
        message = (
            "Please use the validate_token tool from the token-validator "
            'MCP server. Call it with echo_message="Testing secret injection". '
            "Report whether the validation succeeded or failed."
        )

        if not send_message(sandbox_info, conv_id, message):
            log("ERROR: Failed to send message")
            return 1

        # Step 6: Wait for agent to process
        log("Waiting for agent to call MCP tool...")
        time.sleep(45)

        # Step 7: Check results
        events = get_events(sandbox_info, conv_id)
        log(f"Retrieved {len(events)} events")

        # Look for success indicators
        found_success = check_in_output(events, "SUCCESS") or check_in_output(
            events, "Token validated"
        )
        found_test_msg = check_in_output(events, "Testing per-conversation")

        print()
        print("=" * 70)
        if found_success and found_test_msg:
            print("  ✅ FULL SUCCESS!")
            print()
            print("  Proven workflow:")
            print("  1. Secret injected via REST API")
            print("  2. Plugin loaded with MCP config containing ${MCP_SECRET_TOKEN}")
            print("  3. Variable expanded using injected secret")
            print("  4. MCP server received correct token in Authorization header")
            print("  5. Tool call succeeded with our test message")
            print("=" * 70)
            return 0
        elif found_success:
            print("  ⚠️  PARTIAL SUCCESS")
            print("  MCP validation succeeded but test message not found")
            print("=" * 70)
            return 0
        else:
            print("  ❌ FAILED")
            print("  Could not verify MCP token validation")
            print()
            print("  Possible issues:")
            print("  - Plugin didn't load correctly")
            print("  - MCP config variable expansion didn't work")
            print("  - Secret wasn't available at plugin load time")
            print("  - MCP server connection failed")
            print("=" * 70)

            # Dump events for debugging
            log("\nEvents for debugging:")
            for i, event in enumerate(events):
                kind = event.get("kind", "unknown")
                log(f"  [{i}] {kind}")
                if "observation" in event or "action" in event:
                    log(f"      {json.dumps(event, default=str)[:200]}")

            return 1

    except Exception as e:
        log(f"ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        cleanup(sandbox_info)


if __name__ == "__main__":
    sys.exit(main())
