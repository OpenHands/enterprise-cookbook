#!/usr/bin/env python3
"""Example: load a published pip package tool in OpenHands Cloud.

This example demonstrates loading a custom tool from a **published Python package**
(oh-markdown-tool) via pip install + tool_module_qualnames. The package is installed
into the agent-server's own working directory (the sandbox spec's working_dir, which
is on the agent-server's import path), then the agent-server imports it and registers
the tool.

This is the natural evolution of custom-agent-with-tool: same loading mechanism
(tool_module_qualnames), but the *deploy* step becomes "pip-install-into-working-dir"
instead of "upload source files."

Prerequisites:
  pip install requests
  export OH_API_KEY=your-openhands-cloud-api-key
  export LLM_API_KEY=your-llm-api-key

Optional overrides (sensible defaults are used otherwise):
  export LLM_MODEL=litellm_proxy/claude-sonnet-4-5-20250929
  export LLM_BASE_URL=https://llm-proxy.app.all-hands.dev/

Run:
  python working_example.py            # runs and cleans up the sandbox
  python working_example.py --keep     # leave the sandbox up for inspection
"""

import argparse
import os
import sys
import time

import requests


BASE_URL = os.getenv("OPENHANDS_CLOUD_API_URL", "https://app.all-hands.dev")
WORKING_DIR = "/workspace"

# The published package and its tool.
PACKAGE_SPEC = "oh-markdown-tool[openhands]==0.2.1"
TOOL_NAME = "markdown_document"
TOOL_MODULE = "oh_markdown_tool.tool"

# LLM configuration for the agent-server (LiteLLM proxy by default).
LLM_MODEL = os.getenv("LLM_MODEL", "litellm_proxy/claude-sonnet-4-5-20250929")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://llm-proxy.app.all-hands.dev/")

# A sample markdown doc with messy numbering and no TOC. We'll ask the agent to fix it.
SAMPLE_DOC = """# Project Documentation

## 5. Introduction

This is the introduction section.

### 5.2 Background

Some background information.

## 10. Methods

This is the methods section.

### 10.1 Approach

Our approach.

## 3. Results

The results section.
"""

TASK = (
    "I have a markdown document at `sample.md` with inconsistent section numbering. "
    "Please renumber the sections sequentially starting from 1, and add a table of "
    "contents after the title. Use the markdown_document tool to do this."
)


def log(msg, prefix="[demo]"):
    print(f"{prefix} {msg}")


def check_env():
    """Return (api_key, llm_key) or exit if required variables are missing."""
    api_key = os.getenv("OH_API_KEY")
    llm_key = os.getenv("LLM_API_KEY")
    if not api_key:
        log("ERROR: OH_API_KEY environment variable is required", "[error]")
        sys.exit(1)
    if not llm_key:
        log("ERROR: LLM_API_KEY environment variable is required", "[error]")
        sys.exit(1)
    return api_key, llm_key


def create_sandbox(api_key):
    """Create a sandbox and wait for it to be ready."""
    log("Creating sandbox...")
    headers = {"Authorization": f"Bearer {api_key}"}
    resp = requests.post(
        f"{BASE_URL}/api/v1/sandboxes",
        headers=headers,
        json={"image": "ubuntu:24.04"},
        timeout=30,
    )
    resp.raise_for_status()
    sandbox_id = resp.json()["id"]
    log(f"  sandbox id: {sandbox_id}")

    log("  waiting for sandbox to start...")
    for _ in range(120):
        time.sleep(2)
        status_resp = requests.get(
            f"{BASE_URL}/api/v1/sandboxes",
            headers=headers,
            params={"id": sandbox_id},
            timeout=30,
        )
        status_resp.raise_for_status()
        data = status_resp.json()[0]
        if data["status"] == "RUNNING":
            log("  sandbox running")
            agent_server_url = next(
                u["url"] for u in data["exposed_urls"] if u["name"] == "AGENT_SERVER"
            )
            log(f"  agent-server: {agent_server_url}")
            return {
                "id": sandbox_id,
                "agent_server": agent_server_url,
                "session_key": data["session_api_key"],
                "spec_id": data["sandbox_spec_id"],
            }
    log("Sandbox failed to start in time", "[error]")
    sys.exit(1)


def get_import_dir(api_key, spec_id):
    """Return the directory the agent-server imports tool modules from.

    The agent-server puts its own working directory on its import path, and the
    sandbox spec's `working_dir` is that directory on app servers 1.53.0 and later.
    """
    resp = requests.get(
        f"{BASE_URL}/api/v1/sandbox-specs",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"id": spec_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()[0]["working_dir"]


def deploy_tool_package(agent_server, session_key, install_dir):
    """Install the published package into the agent-server's working directory."""
    log("Deploying tool package via pip...")
    headers = {"X-Session-API-Key": session_key}

    # Install into the agent-server's working directory so it can import the package.
    # We skip dependencies that are already bundled in the frozen server
    # (openhands-sdk, pydantic, rich) by installing with --no-deps, then installing
    # only the non-bundled deps explicitly. For oh-markdown-tool[openhands], the
    # bundled deps are openhands-sdk, pydantic, rich; the non-bundled are mdformat
    # and pymarkdownlnt.
    install_cmd = (
        f"pip install --target {install_dir} --no-deps {PACKAGE_SPEC} && "
        f"pip install --target {install_dir} mdformat pymarkdownlnt"
    )

    resp = requests.post(
        f"{agent_server}/api/bash/execute_bash_command",
        headers=headers,
        json={"command": install_cmd},
        timeout=120,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("exit_code", 1) != 0:
        log(
            f"pip install failed:\n{result.get('stderr', result.get('stdout', ''))}",
            "[error]",
        )
        sys.exit(1)

    log(f"  installed {PACKAGE_SPEC} into {install_dir}")
    log(f"  tool module {TOOL_MODULE} is now importable")


def create_sample_file(agent_server, session_key):
    """Write the sample markdown doc into the workspace."""
    log("Creating sample.md...")
    headers = {"X-Session-API-Key": session_key}
    resp = requests.post(
        f"{agent_server}/api/file/upload",
        headers=headers,
        params={"path": f"{WORKING_DIR}/sample.md"},
        files={"file": ("sample.md", SAMPLE_DOC.encode("utf-8"))},
        timeout=30,
    )
    resp.raise_for_status()
    log("  sample.md ready")


def create_conversation(agent_server, session_key, llm_key):
    """Create a conversation that loads the tool via tool_module_qualnames."""
    log("Creating conversation with the tool...")
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
                {"name": TOOL_NAME},  # the markdown tool
            ],
        },
        "tool_module_qualnames": {TOOL_NAME: TOOL_MODULE},
        "workspace": {"working_dir": WORKING_DIR},
        "initial_message": {"content": [{"text": TASK}]},
    }

    resp = requests.post(
        f"{agent_server}/api/conversations",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()

    conv_id = resp.json()["id"]
    log(f"  conversation created: {conv_id}")
    return conv_id


def run_and_verify(agent_server, session_key, conv_id):
    """Run the conversation and verify the tool registered, was used,
    and changed the file."""
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
    registered_titles = (
        [t.get("title") for t in system_events[0].get("tools", [])]
        if system_events
        else []
    )
    # Check if our tool is registered (by title, since name field is not populated)
    tool_registered = any(
        TOOL_NAME in (t.get("name") or "")
        or "Markdown Document" in (t.get("title") or "")
        for t in (system_events[0].get("tools", []) if system_events else [])
    )
    used = sorted(
        {
            e.get("tool_name")
            for e in items
            if e.get("kind") == "ActionEvent" and e.get("tool_name")
        }
    )

    # Check if the file was actually modified (proof the tool did something).
    file_resp = requests.post(
        f"{agent_server}/api/bash/execute_bash_command",
        headers=headers,
        json={"command": f"cat {WORKING_DIR}/sample.md"},
        timeout=30,
    )
    file_resp.raise_for_status()
    final_content = file_resp.json().get("stdout") or ""
    has_toc = (
        "Table of Contents" in final_content or "Table Of Contents" in final_content
    )
    has_renumbered = "## 1." in final_content and "## 2." in final_content

    log("")
    log("=== Verification ===")
    log(f"  registered tools: {registered_titles}")
    log(f"  tools used: {used}")

    ok = True
    if tool_registered:
        log("  PASS: tool 'markdown_document' is registered")
    else:
        ok = False
        log("  FAIL: tool 'markdown_document' is NOT registered", "[error]")

    if TOOL_NAME in used:
        log(f"  PASS: tool '{TOOL_NAME}' was invoked by the agent")
    else:
        ok = False
        log(f"  FAIL: tool '{TOOL_NAME}' was not invoked", "[error]")

    if has_toc and has_renumbered:
        log("  PASS: file was modified (TOC added, sections renumbered)")
    else:
        ok = False
        log("  FAIL: file was not properly modified", "[error]")

    return ok


def cleanup(sandbox_info):
    """Delete the sandbox.

    Note: the DELETE endpoint requires sandbox_id as BOTH a path segment and a query
    parameter; omitting the query param returns HTTP 422 and leaks the sandbox.
    """
    api_key = check_env()[0]
    headers = {"Authorization": f"Bearer {api_key}"}
    sid = sandbox_info["id"]
    resp = requests.delete(
        f"{BASE_URL}/api/v1/sandboxes/{sid}",
        headers=headers,
        params={"sandbox_id": sid},
        timeout=30,
    )
    if resp.status_code == 200:
        log("Sandbox deleted.")
    else:
        log(f"Sandbox cleanup warning: status {resp.status_code}", "[warn]")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the sandbox running after the demo (for inspection)",
    )
    parser.add_argument(
        "--install-dir",
        help=(
            "Directory to install the package into. Defaults to the sandbox "
            "spec's working_dir. Use /workspace on app servers older than 1.53.0."
        ),
    )
    args = parser.parse_args()

    api_key, llm_key = check_env()
    sandbox_info = create_sandbox(api_key)

    try:
        install_dir = args.install_dir or get_import_dir(
            api_key, sandbox_info["spec_id"]
        )
        log(f"  import directory: {install_dir}")
        deploy_tool_package(
            sandbox_info["agent_server"], sandbox_info["session_key"], install_dir
        )
        create_sample_file(sandbox_info["agent_server"], sandbox_info["session_key"])
        conv_id = create_conversation(
            sandbox_info["agent_server"], sandbox_info["session_key"], llm_key
        )
        ok = run_and_verify(
            sandbox_info["agent_server"], sandbox_info["session_key"], conv_id
        )

        log(f"View conversation: {BASE_URL}/conversations/{conv_id}")

        if ok:
            log("SUCCESS: the tool was loaded from the pip package and used.")
        else:
            log("FAILURE: verification checks did not pass.", "[error]")
            sys.exit(1)

        if args.keep:
            log("")
            log("=== Keeping resources (--keep) ===")
            log(f"  sandbox id:   {sandbox_info['id']}")
            log(f"  agent-server: {sandbox_info['agent_server']}")
            log("  Delete later with:")
            log(
                f"    curl -X DELETE '{BASE_URL}/api/v1/sandboxes/"
                f"{sandbox_info['id']}?sandbox_id={sandbox_info['id']}' "
                f'-H "Authorization: Bearer $OH_API_KEY"'
            )
        else:
            cleanup(sandbox_info)
    except Exception as e:
        log(f"Error: {e}", "[error]")
        if not args.keep:
            cleanup(sandbox_info)
        raise


if __name__ == "__main__":
    main()
