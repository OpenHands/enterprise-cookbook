#!/usr/bin/env python3
"""Call-for-call reference: Daytona SDK calls -> OpenHands HTTP requests.

This is a *reference*, not a demo you run end to end. Each function shows the
Daytona SDK call you have today (in the docstring) next to the OpenHands request
that replaces it. The OpenHands side uses only ``requests`` so you can drop these
into any codebase.

Run with ``--live`` plus ``OH_API_KEY`` to exercise the create/exec/delete path
against real infrastructure; otherwise importing this module is side-effect free.

    export OH_API_KEY=...
    pip install requests
    python daytona_side_by_side.py --live
"""

import argparse
import os
import sys
import time

import requests


CLOUD = os.environ.get("OH_API_BASE", "https://app.all-hands.dev")


def create_sandbox(cloud_headers: dict) -> dict:
    """Daytona:  sandbox = daytona.create()

    OpenHands: POST /api/v1/sandboxes  (then poll to RUNNING).
    """
    sb = requests.post(f"{CLOUD}/api/v1/sandboxes", headers=cloud_headers).json()
    sid = sb["id"]
    for _ in range(60):
        if sb["status"] == "RUNNING":
            break
        time.sleep(3)
        sb = requests.get(
            f"{CLOUD}/api/v1/sandboxes", headers=cloud_headers, params={"id": sid}
        ).json()[0]
    return sb


def agent_session(sandbox: dict) -> tuple[str, dict]:
    """Daytona:  (the Toolbox API is addressed by sandbox id)

    OpenHands: read the AGENT_SERVER url + the per-sandbox session_api_key.
    """
    agent_url = next(
        u["url"] for u in sandbox["exposed_urls"] if u["name"] == "AGENT_SERVER"
    )
    session = {"X-Session-API-Key": sandbox["session_api_key"]}
    return agent_url, session


def exec_command(
    agent_url: str, session: dict, cmd: str, cwd: str = "/workspace"
) -> dict:
    """Daytona:  sandbox.process.exec(cmd, cwd=cwd, timeout=60)

    OpenHands: POST {agent}/api/bash/execute_bash_command
               -> {stdout, stderr, exit_code}
    """
    return requests.post(
        f"{agent_url}/api/bash/execute_bash_command",
        headers=session,
        json={"command": cmd, "cwd": cwd, "timeout": 60},
    ).json()


def upload_file(agent_url: str, session: dict, path: str, data: bytes) -> None:
    """Daytona:  sandbox.fs.upload_file(data, path)

    OpenHands: POST {agent}/api/file/upload?path=<path>  (multipart form-data).
    """
    requests.post(
        f"{agent_url}/api/file/upload",
        headers=session,
        params={"path": path},
        files={"file": (os.path.basename(path), data)},
    )


def download_file(agent_url: str, session: dict, path: str) -> bytes:
    """Daytona:  sandbox.fs.download_file(path)

    OpenHands: GET {agent}/api/file/download?path=<path>.
    """
    return requests.get(
        f"{agent_url}/api/file/download", headers=session, params={"path": path}
    ).content


def git_changes(
    agent_url: str, session: dict, path: str = "/workspace/project"
) -> dict:
    """Daytona:  sandbox.git.status(path)  (roughly)

    OpenHands: GET {agent}/api/git/changes?path=<path>.
    """
    return requests.get(
        f"{agent_url}/api/git/changes", headers=session, params={"path": path}
    ).json()


def delete_sandbox(cloud_headers: dict, sandbox_id: str) -> None:
    """Daytona:  daytona.delete(sandbox)

    OpenHands: DELETE /api/v1/sandboxes/{id}?sandbox_id=<id>
               (the id goes in BOTH the path and the query).
    """
    requests.delete(
        f"{CLOUD}/api/v1/sandboxes/{sandbox_id}",
        headers=cloud_headers,
        params={"sandbox_id": sandbox_id},
    )


def _live_demo() -> None:
    api_key = os.environ.get("OH_API_KEY")
    if not api_key:
        sys.exit("error: set OH_API_KEY to run --live")
    cloud_headers = {"X-Session-API-Key": api_key}

    sb = create_sandbox(cloud_headers)
    print("sandbox:", sb["id"], sb["status"])
    agent_url, session = agent_session(sb)

    print(exec_command(agent_url, session, "echo hello && ls -la /workspace"))
    upload_file(agent_url, session, "/workspace/input.txt", b"data")
    print("downloaded:", download_file(agent_url, session, "/workspace/input.txt"))

    delete_sandbox(cloud_headers, sb["id"])
    print("deleted:", sb["id"])


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--live",
        action="store_true",
        help="Actually create/exec/delete a sandbox (needs OH_API_KEY).",
    )
    if p.parse_args().live:
        _live_demo()
    else:
        print(__doc__)
        print("Run with --live and OH_API_KEY to exercise the flow.")
