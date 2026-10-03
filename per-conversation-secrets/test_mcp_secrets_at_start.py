#!/usr/bin/env python3
"""
End-to-end test for per-conversation secrets with MCP plugin - SECRETS AT START.

This script tests the NEW approach from PR #14009:
1. Start MCP server that expects a specific token
2. Start a conversation WITH SECRETS AND PLUGIN in the same request
3. The plugin's MCP config uses ${MCP_SERVER_URL} and ${MCP_SECRET_TOKEN}
4. Both variables are expanded from secrets passed at conversation start
5. Agent calls the MCP tool - server validates the token was passed correctly

This proves that:
- Secrets passed at conversation start (via `secrets` field) work with
  MCP config expansion
- The full flow works: secrets-at-start → plugin → MCP config →
  variable expansion → MCP server

Prerequisites:
    - OH_API_KEY environment variable
    - MCP server running (start with mcp_server.py)

Usage:
    # Terminal 1: Start MCP server
    python mcp_server.py --port 12000 --expected-token "per-conv-secret-xyz-123"

    # Terminal 2: Run test
    export OH_API_KEY="sk-oh-..."
    export OH_API_URL="https://ohpr-14009-30.staging.all-hands.dev/api"
    export MCP_SERVER_URL="https://work-1-xxx.prod-runtime.all-hands.dev"
    python test_mcp_secrets_at_start.py
"""

import os
import sys
import time

import requests


# Configuration
API_KEY = os.environ.get("OH_API_KEY", "")
API_URL = os.environ.get("OH_API_URL", "https://app.all-hands.dev/api")

# MCP Server URL - must be accessible from the sandbox
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "")

# The secret token - must match what the MCP server expects
SECRET_TOKEN = "per-conv-secret-xyz-123"

# Plugin source - from GitHub (uses ${MCP_SERVER_URL} and ${MCP_SECRET_TOKEN})
PLUGIN_SOURCE = "github:OpenHands/enterprise-cookbook"
PLUGIN_PATH = "per-conversation-secrets/test-plugin"

# Timeout constants
SANDBOX_READY_TIMEOUT = 180
SANDBOX_POLL_INTERVAL = 2
CONV_APPEAR_TIMEOUT = 30
AGENT_PROCESS_WAIT = 60


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


def get_sandbox_via_search(headers: dict, sandbox_id: str) -> dict | None:
    """Get sandbox by ID using search endpoint."""
    resp = requests.get(f"{API_URL}/v1/sandboxes/search", headers=headers, timeout=30)
    if resp.status_code == 200:
        for item in resp.json().get("items", []):
            if item.get("id") == sandbox_id:
                return item
    return None


def create_sandbox(headers: dict) -> dict:
    """Create a new sandbox."""
    resp = requests.post(
        f"{API_URL}/v1/sandboxes", headers=headers, json={}, timeout=60
    )
    resp.raise_for_status()
    return resp.json()


def delete_sandbox(headers: dict, sandbox_id: str) -> None:
    """Delete sandbox."""
    try:
        requests.delete(
            f"{API_URL}/v1/sandboxes/{sandbox_id}",
            headers=headers,
            params={"sandbox_id": sandbox_id},
            timeout=30,
        )
    except Exception:
        pass


def get_agent_server_info(sandbox_data: dict) -> tuple[str, str] | None:
    """Extract agent server URL and session key from sandbox data."""
    session_key = sandbox_data.get("session_api_key")
    exposed_urls = sandbox_data.get("exposed_urls", [])

    for url_info in exposed_urls:
        if url_info.get("name") == "AGENT_SERVER":
            return url_info.get("url"), session_key
    return None


def start_conversation_with_secrets_and_plugin(
    headers: dict,
    sandbox_id: str,
    secrets: dict[str, str],
    plugin_source: str,
    plugin_path: str,
) -> dict:
    """
    Start conversation with BOTH secrets AND plugin in the same request.

    This is the key test for PR #14009 - secrets passed at conversation start
    should be available for MCP config variable expansion.
    """
    payload = {
        "sandbox_id": sandbox_id,
        "initial_message": {
            "role": "user",
            "content": [
                {"type": "text", "text": "Say 'Ready' and wait for instructions."}
            ],
        },
        "secrets": secrets,
        "plugins": [{"source": plugin_source, "repo_path": plugin_path}],
    }

    log("Starting conversation with secrets AND plugin...")
    log(f"  Secrets: {list(secrets.keys())}")
    log(f"  Plugin: {plugin_source} / {plugin_path}")

    resp = requests.post(
        f"{API_URL}/v1/app-conversations",
        headers=headers,
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()

    # Check that secrets were accepted
    request_secrets = result.get("request", {}).get("secrets", {})
    log(f"  Secrets in response: {request_secrets}")

    return result


def get_agent_conversations(agent_url: str, session_key: str) -> list[dict]:
    """List conversations on agent server."""
    headers = {"X-Session-API-Key": session_key}
    resp = requests.get(
        f"{agent_url}/api/conversations/search", headers=headers, timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def send_user_message(
    agent_url: str, session_key: str, conv_id: str, message: str
) -> bool:
    """Send a message to the conversation."""
    headers = {"X-Session-API-Key": session_key, "Content-Type": "application/json"}
    payload = {
        "role": "user",
        "content": [{"type": "text", "text": message}],
        "run": True,
    }
    resp = requests.post(
        f"{agent_url}/api/conversations/{conv_id}/events",
        headers=headers,
        json=payload,
        timeout=60,
    )
    return resp.status_code == 200


def get_conversation_events(
    agent_url: str, session_key: str, conv_id: str
) -> list[dict]:
    """Get events from conversation."""
    headers = {"X-Session-API-Key": session_key}
    resp = requests.get(
        f"{agent_url}/api/conversations/{conv_id}/events/search",
        headers=headers,
        params={"limit": 100},
        timeout=60,
    )
    if resp.status_code == 200:
        return resp.json().get("items", [])
    return []


def check_for_success(events: list[dict]) -> tuple[bool, str]:
    """Check if MCP validation succeeded in the events."""
    for event in events:
        # Check observation content
        obs = event.get("observation", {})
        if isinstance(obs, dict):
            content = obs.get("content", "")
            if isinstance(content, str):
                if "SUCCESS" in content.upper() or "TOKEN VALIDATED" in content.upper():
                    return True, content[:200]
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text", "")
                        if (
                            "SUCCESS" in text.upper()
                            or "TOKEN VALIDATED" in text.upper()
                        ):
                            return True, text[:200]

        # Check action content
        action = event.get("action", {})
        if isinstance(action, dict):
            for key in ["message", "content", "text"]:
                val = action.get(key, "")
                if isinstance(val, str):
                    if "SUCCESS" in val.upper() or "TOKEN VALIDATED" in val.upper():
                        return True, val[:200]

    return False, ""


def main() -> int:
    print("=" * 70)
    print("  MCP SECRETS AT START TEST (PR #14009)")
    print("  Testing: secrets + plugin in AppConversationStartRequest")
    print("=" * 70)
    print()

    # Validate configuration
    if not API_KEY:
        print("ERROR: OH_API_KEY environment variable not set")
        return 1

    if not MCP_SERVER_URL:
        print("ERROR: MCP_SERVER_URL environment variable not set")
        print("  Set it to the URL where mcp_server.py is running")
        print(
            "  Example: export MCP_SERVER_URL='https://work-1-xxx.prod-runtime.all-hands.dev'"
        )
        return 1

    log(f"API URL: {API_URL}")
    log(f"MCP Server: {MCP_SERVER_URL}")
    log(f"Secret Token: {SECRET_TOKEN[:10]}...")

    # Verify MCP server is accessible
    if not verify_mcp_server():
        print()
        print("ERROR: MCP server not accessible")
        print("Please start the MCP server first:")
        print(f'  python mcp_server.py --port 12000 --expected-token "{SECRET_TOKEN}"')
        return 1

    app_headers = {"X-Access-Token": API_KEY, "Content-Type": "application/json"}
    sandbox_id = None

    try:
        # Step 1: Create sandbox
        log("Creating sandbox...")
        sandbox_data = create_sandbox(app_headers)
        sandbox_id = sandbox_data.get("id")
        log(f"  Sandbox ID: {sandbox_id}")

        # Wait for sandbox to be ready
        log(f"Waiting for sandbox to be ready (max {SANDBOX_READY_TIMEOUT}s)...")
        for _ in range(SANDBOX_READY_TIMEOUT // SANDBOX_POLL_INTERVAL):
            sandbox_data = get_sandbox_via_search(app_headers, sandbox_id)
            if sandbox_data and sandbox_data.get("status") == "RUNNING":
                break
            log(
                f"  Status: {sandbox_data.get('status') if sandbox_data else 'unknown'}"
            )
            time.sleep(SANDBOX_POLL_INTERVAL)
        else:
            log("ERROR: Sandbox did not become ready")
            return 1

        # Get agent server info
        agent_info = get_agent_server_info(sandbox_data)
        if not agent_info:
            log("ERROR: Could not get agent server URL")
            return 1
        agent_url, session_key = agent_info
        log(f"Agent Server: {agent_url}")

        # Get baseline conversations
        before_convs = {
            c["id"] for c in get_agent_conversations(agent_url, session_key)
        }

        # Step 2: Start conversation with SECRETS AND PLUGIN
        # Key test: secrets should be available for MCP config expansion
        secrets = {
            "MCP_SERVER_URL": MCP_SERVER_URL,
            "MCP_SECRET_TOKEN": SECRET_TOKEN,
        }

        start_conversation_with_secrets_and_plugin(
            app_headers,
            sandbox_id,
            secrets,
            PLUGIN_SOURCE,
            PLUGIN_PATH,
        )

        # Step 3: Find conversation on agent server
        log(f"Finding conversation on agent server (max {CONV_APPEAR_TIMEOUT}s)...")
        agent_conv_id = None
        for _ in range(CONV_APPEAR_TIMEOUT):
            after_convs = {
                c["id"] for c in get_agent_conversations(agent_url, session_key)
            }
            new_convs = after_convs - before_convs
            if new_convs:
                agent_conv_id = list(new_convs)[0]
                break
            time.sleep(1)

        if not agent_conv_id:
            log("ERROR: Conversation did not appear")
            return 1
        log(f"  Conversation ID: {agent_conv_id}")

        # Step 4: Wait for initial setup and plugin loading
        log("Waiting for plugin to load (30s)...")
        time.sleep(30)

        # Step 5: Ask agent to call the MCP tool
        log("Asking agent to call validate_token MCP tool...")
        message = (
            "Please use the validate_token tool from the token-validator MCP server. "
            'Call it with echo_message="Testing PR 14009 secrets at start". '
            "Report the exact result - whether validation succeeded or failed."
        )

        if not send_user_message(agent_url, session_key, agent_conv_id, message):
            log("ERROR: Failed to send message")
            return 1

        # Step 6: Wait for agent to process
        log(f"Waiting for agent to call MCP tool ({AGENT_PROCESS_WAIT}s)...")
        time.sleep(AGENT_PROCESS_WAIT)

        # Step 7: Check results
        log("Checking events for MCP validation result...")
        events = get_conversation_events(agent_url, session_key, agent_conv_id)
        log(f"  Total events: {len(events)}")

        success, match_text = check_for_success(events)

        print()
        print("=" * 70)
        if success:
            print("  ✅ SUCCESS! MCP secrets-at-start expansion works!")
            print()
            print("  Proven workflow:")
            print("  1. Conversation started with secrets AND plugin in same request")
            print("  2. Secrets: MCP_SERVER_URL and MCP_SECRET_TOKEN")
            print("  3. Plugin loaded with MCP config using ${MCP_SERVER_URL}")
            print("     and ${MCP_SECRET_TOKEN}")
            print(
                "  4. Both variables expanded from secrets passed at conversation start"
            )
            print("  5. MCP server received correct URL and token")
            print("  6. Token validation succeeded!")
            print()
            print(f"  Match found: {match_text}")
            print("=" * 70)
            return 0
        else:
            print("  ❌ FAILED: Could not verify MCP token validation")
            print()
            print("  Possible issues:")
            print(
                "  - Secrets not expanded in MCP config (PR #14009 / SDK #2873 issue)"
            )
            print("  - Plugin didn't load correctly")
            print("  - MCP server connection failed")
            print("  - Agent didn't call the tool")
            print()
            print("  Recent events:")
            for i, event in enumerate(events[-10:]):
                kind = event.get("kind", "?")
                action = event.get("action", {})
                obs = event.get("observation", {})
                print(f"    [{i}] {kind}")
                if action:
                    print(f"        action: {str(action)[:100]}...")
                if obs:
                    print(f"        obs: {str(obs)[:100]}...")
            print("=" * 70)
            return 1

    except requests.RequestException as e:
        log(f"ERROR: API request failed: {e}")
        return 1
    except Exception as e:
        log(f"ERROR: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        if sandbox_id:
            log("Cleaning up sandbox...")
            delete_sandbox(app_headers, sandbox_id)
            log("  Done.")


if __name__ == "__main__":
    sys.exit(main())
