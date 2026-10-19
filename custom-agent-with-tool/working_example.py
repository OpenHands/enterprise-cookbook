#!/usr/bin/env python3
"""Working example: add a custom server-side tool to an OpenHands Cloud sandbox.

This demonstrates how to give an agent a completely custom tool (the Rubber Duck
Debugger) using the agent-server's `tool_module_qualnames` mechanism.

How it works:
1. Create a Cloud sandbox and get its agent-server URL + session key.
2. Deploy the tool as an importable Python package in the agent-server's own
   working directory (the sandbox spec's `working_dir`) using the agent-server
   file upload API. That directory is on the agent-server's import path, so no
   `pip install` is required - and because the files are uploaded (not embedded
   in a shell heredoc) the tool source can contain anything without breaking.
3. Create a conversation that lists the custom tool in `agent.tools` and maps it
   to its module via `tool_module_qualnames`. The agent-server imports the
   module, which registers the tool.
4. Run the conversation and verify the tool was registered and used.

Note: the tool's own dependencies (here, `openhands.sdk` and `pydantic`) must be
importable inside the agent-server. Those two are always available. If your tool
needs extra third-party packages, keep that in mind.
"""

import argparse
import os
import re
import sys
import time

import requests


BASE_URL = os.getenv("OPENHANDS_CLOUD_API_URL", "https://app.all-hands.dev")
WORKING_DIR = "/workspace"
# The custom tool is deployed as a package in the working directory.
PKG_NAME = "bug_registry"
TOOL_MODULE = f"{PKG_NAME}.tool"

# LLM configuration for the agent-server (LiteLLM proxy by default).
LLM_MODEL = os.getenv("LLM_MODEL", "litellm_proxy/claude-sonnet-4-5-20250929")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://llm-proxy.app.all-hands.dev/")

# The task requires the tool: only the bug_registry tool can produce the official
# Case ID (a hash of the report), so a correct Case ID in the answer proves the
# agent actually called the tool.
TASK = (
    "My avg() function raises ZeroDivisionError on a single-element list:\n\n"
    "    def avg(nums): return sum(nums) / (len(nums) - 1)\n\n"
    "Please file this bug with the bug_registry tool, then tell me the official "
    "Case ID and classification it assigns, and finally give me the corrected code."
)

# Matches the Case ID format produced by custom_tool_definition.py (BUG-XXXXXX).
CASE_ID_RE = re.compile(r"BUG-[0-9A-F]{6}")


def log(msg, prefix="[demo]"):
    print(f"{prefix} {msg}")


def _event_text(event):
    """Extract readable text from a MessageEvent or ObservationEvent."""
    if event.get("kind") == "MessageEvent":
        content = event.get("llm_message", {}).get("content")
    else:
        content = event.get("observation", {}).get("content")
    if isinstance(content, str):
        return content
    return "\n".join(
        c["text"] for c in (content or []) if isinstance(c, dict) and c.get("text")
    )


def check_env():
    """Return (api_key, llm_key) or exit if required variables are missing."""
    api_key = os.getenv("OH_API_KEY")
    llm_key = os.getenv("LLM_API_KEY")
    if not api_key:
        log(
            "ERROR: OH_API_KEY not set (export OH_API_KEY=your-cloud-api-key)",
            "[error]",
        )
        sys.exit(1)
    if not llm_key:
        log(
            "ERROR: LLM_API_KEY not set (export LLM_API_KEY=your-llm-api-key)",
            "[error]",
        )
        sys.exit(1)
    return api_key, llm_key


def create_sandbox(api_key):
    """Create a Cloud sandbox and wait for it to be ready."""
    log("Creating sandbox...")
    headers = {"Authorization": f"Bearer {api_key}"}

    resp = requests.post(f"{BASE_URL}/api/v1/sandboxes", headers=headers, timeout=30)
    resp.raise_for_status()
    sandbox_id = resp.json()["id"]
    log(f"  sandbox id: {sandbox_id}")

    log("  waiting for sandbox to start...")
    sandbox = None
    for _ in range(90):
        time.sleep(2)
        results = requests.get(
            f"{BASE_URL}/api/v1/sandboxes",
            headers=headers,
            params={"id": sandbox_id},
            timeout=30,
        ).json()
        sandbox = results[0] if results else None
        if sandbox and sandbox.get("status") == "RUNNING":
            break
    if not sandbox or sandbox.get("status") != "RUNNING":
        raise RuntimeError("Sandbox failed to reach RUNNING status")

    agent_server = next(
        (u["url"] for u in sandbox["exposed_urls"] if u["name"] == "AGENT_SERVER"),
        None,
    )
    if not agent_server:
        raise RuntimeError("No AGENT_SERVER URL found in sandbox")

    log("  sandbox running")
    return {
        "sandbox_id": sandbox_id,
        "agent_server": agent_server,
        "session_key": sandbox["session_api_key"],
        "spec_id": sandbox["sandbox_spec_id"],
        "headers": headers,
    }


def get_import_dir(sandbox_info):
    """Return the directory the agent-server imports tool modules from.

    The agent-server puts its own working directory on its import path, and the
    sandbox spec's `working_dir` is that directory on app servers 1.53.0 and later.
    """
    resp = requests.get(
        f"{BASE_URL}/api/v1/sandbox-specs",
        headers=sandbox_info["headers"],
        params={"id": sandbox_info["spec_id"]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()[0]["working_dir"]


def deploy_custom_tool(agent_server, session_key, import_dir):
    """Upload the tool as an importable package in the agent-server's cwd.

    Files are uploaded via the agent-server file API, so the tool source is never
    interpolated into a shell command - no delimiter/escaping pitfalls.
    """
    log("Deploying custom tool package...")
    headers = {"X-Session-API-Key": session_key}
    pkg_dir = f"{import_dir}/{PKG_NAME}"

    with open("custom_tool_definition.py", "rb") as f:
        tool_code = f.read()

    files = {
        f"{pkg_dir}/__init__.py": b"# Rubber Duck Debugger tool package\n",
        f"{pkg_dir}/tool.py": tool_code,
    }
    for dest, data in files.items():
        resp = requests.post(
            f"{agent_server}/api/file/upload",
            headers=headers,
            params={"path": dest},
            files={"file": (os.path.basename(dest), data, "application/octet-stream")},
            timeout=30,
        )
        resp.raise_for_status()
        log(f"  uploaded {dest}")

    log(f"  tool package ready at {pkg_dir}")


def create_conversation_with_tool(agent_server, session_key, llm_key):
    """Create a conversation that loads the custom tool via tool_module_qualnames."""
    log("Creating conversation with the custom tool...")
    headers = {"X-Session-API-Key": session_key}

    payload = {
        "agent": {
            "llm": {
                "model": LLM_MODEL,
                "api_key": llm_key,
                "base_url": LLM_BASE_URL,
            },
            "tools": [
                {"name": "terminal"},
                {"name": "file_editor"},
                {"name": PKG_NAME},  # the custom tool
            ],
        },
        # Built-in tools are resolved automatically; only the custom tool needs a
        # module mapping so the agent-server knows what to import.
        "tool_module_qualnames": {PKG_NAME: TOOL_MODULE},
        "workspace": {"working_dir": WORKING_DIR},
        "initial_message": {"content": [{"text": TASK}]},
    }

    resp = requests.post(
        f"{agent_server}/api/conversations", headers=headers, json=payload, timeout=30
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Failed to create conversation: {resp.status_code} {resp.text}"
        )

    conv_id = resp.json()["id"]
    log(f"  conversation created: {conv_id}")
    return conv_id


def run_and_verify(agent_server, session_key, conv_id):
    """Run the conversation and verify the custom tool registered and was used."""
    log("Running conversation...")
    headers = {"X-Session-API-Key": session_key}

    requests.post(
        f"{agent_server}/api/conversations/{conv_id}/run", headers=headers, timeout=30
    )

    status = "unknown"
    for _ in range(150):
        time.sleep(2)
        status = (
            requests.get(
                f"{agent_server}/api/conversations/{conv_id}",
                headers=headers,
                timeout=30,
            )
            .json()
            .get("execution_status", "unknown")
        )
        if status in ("finished", "error"):
            break
    log(f"  execution_status: {status}")

    items = (
        requests.get(
            f"{agent_server}/api/conversations/{conv_id}/events/search",
            headers=headers,
            params={"limit": 100},
            timeout=30,
        )
        .json()
        .get("items", [])
    )

    system_events = [e for e in items if e.get("kind") == "SystemPromptEvent"]
    registered = (
        [t.get("title") for t in system_events[0].get("tools", [])]
        if system_events
        else []
    )
    used = sorted(
        {
            e.get("tool_name")
            for e in items
            if e.get("kind") == "ActionEvent" and e.get("tool_name")
        }
    )

    # The Case ID the tool actually issued (ground truth from its observation).
    tool_case_id = None
    for e in items:
        if e.get("kind") == "ObservationEvent" and e.get("tool_name") == PKG_NAME:
            match = CASE_ID_RE.search(_event_text(e))
            if match:
                tool_case_id = match.group(0)
                break

    # The agent's final answer to the user.
    final_answer = ""
    for e in items:
        if e.get("kind") == "MessageEvent":
            msg = e.get("llm_message", {})
            if msg.get("role") == "assistant":
                final_answer = _event_text(e)

    log("")
    log("=== Verification ===")
    log(f"  registered tools: {registered}")
    log(f"  tools used: {used}")
    log(f"  Case ID issued by tool: {tool_case_id}")

    ok = True
    if PKG_NAME in registered:
        log(f"  PASS: custom tool '{PKG_NAME}' is registered")
    else:
        ok = False
        log(f"  FAIL: custom tool '{PKG_NAME}' is NOT registered", "[error]")

    if PKG_NAME in used:
        log(f"  PASS: custom tool '{PKG_NAME}' was used by the agent")
    else:
        ok = False
        log(f"  FAIL: custom tool '{PKG_NAME}' was not used", "[error]")

    # The strong check: the hash-derived Case ID can only come from the tool, so its
    # presence in the final answer proves the tool's output shaped the response.
    if tool_case_id and tool_case_id in final_answer:
        log(f"  PASS: agent reported the tool's Case ID ({tool_case_id}) in its answer")
    else:
        ok = False
        log(
            f"  FAIL: tool Case ID ({tool_case_id}) not found in the agent's answer",
            "[error]",
        )

    return ok


def cleanup(sandbox_info):
    """Delete the sandbox.

    DELETE /api/v1/sandboxes/{id} requires sandbox_id as BOTH a path segment and a
    query parameter; omitting the query parameter returns HTTP 422 and leaks the
    sandbox.
    """
    log("Cleaning up...")
    sandbox_id = sandbox_info["sandbox_id"]
    resp = requests.delete(
        f"{BASE_URL}/api/v1/sandboxes/{sandbox_id}",
        headers=sandbox_info["headers"],
        params={"sandbox_id": sandbox_id},
        timeout=30,
    )
    if resp.ok:
        log("  sandbox deleted")
    else:
        log(
            f"  WARNING: failed to delete sandbox: {resp.status_code} {resp.text}",
            "[warn]",
        )


def main():
    parser = argparse.ArgumentParser(
        description="Add a custom server-side tool to an OpenHands Cloud agent."
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the sandbox and conversation after finishing (for inspection).",
    )
    parser.add_argument(
        "--import-dir",
        help=(
            "Directory to deploy the tool into. Defaults to the sandbox spec's "
            "working_dir. Use /workspace on app servers older than 1.53.0."
        ),
    )
    args = parser.parse_args()

    api_key, llm_key = check_env()

    sandbox_info = None
    try:
        sandbox_info = create_sandbox(api_key)
        log(f"  agent-server: {sandbox_info['agent_server']}")
        import_dir = args.import_dir or get_import_dir(sandbox_info)
        log(f"  import directory: {import_dir}")

        deploy_custom_tool(
            sandbox_info["agent_server"], sandbox_info["session_key"], import_dir
        )
        conv_id = create_conversation_with_tool(
            sandbox_info["agent_server"], sandbox_info["session_key"], llm_key
        )
        passed = run_and_verify(
            sandbox_info["agent_server"], sandbox_info["session_key"], conv_id
        )

        log(f"View conversation: {BASE_URL}/conversations/{conv_id}")
        if passed:
            log("SUCCESS: the custom tool was loaded and used in OpenHands Cloud.")
            return 0
        log("FAILURE: see the FAIL messages above.", "[error]")
        return 1

    except Exception as e:
        log(f"ERROR: {e}", "[error]")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        if sandbox_info and not args.keep:
            cleanup(sandbox_info)
        elif sandbox_info:
            log("\n=== Keeping resources (--keep) ===")
            log(f"  sandbox id:   {sandbox_info['sandbox_id']}")
            log(f"  agent-server: {sandbox_info['agent_server']}")
            log("  Delete later with:")
            log(
                f"    curl -X DELETE '{BASE_URL}/api/v1/sandboxes/"
                f"{sandbox_info['sandbox_id']}?sandbox_id={sandbox_info['sandbox_id']}'"
                ' -H "Authorization: Bearer $OH_API_KEY"'
            )


if __name__ == "__main__":
    sys.exit(main())
