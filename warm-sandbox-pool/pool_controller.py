#!/usr/bin/env python3
"""
Warm Sandbox Pool Controller

Maintains a pool of pre-initialized OpenHands sandboxes with Ruby/Sinatra services
already running. When users start conversations, sandboxes are pulled from the pool
and attached instantly, with automatic refill.

Usage:
    export OH_API_KEY=your_key_here
    python pool_controller.py

Then open http://localhost:5000 in your browser.
"""

import argparse
import json
import logging
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pool_controller")


class SandboxState(str, Enum):
    """States a sandbox progresses through in the pool."""

    STARTING = "STARTING"  # OpenHands provisioning the sandbox
    PREPARING = "PREPARING"  # Running initialization script
    READY = "READY"  # Fully initialized and available
    ALLOCATED = "ALLOCATED"  # Pulled from pool and attached to conversation
    FAILED = "FAILED"  # Initialization failed


@dataclass
class PooledSandbox:
    """Represents a sandbox in the pool with its current state."""

    id: str
    state: SandboxState
    agent_url: str | None = None
    session_api_key: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    ready_at: datetime | None = None
    allocated_at: datetime | None = None
    conversation_id: str | None = None
    error_message: str | None = None
    init_log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state": self.state.value,
            "agent_url": self.agent_url,
            "created_at": self.created_at.isoformat(),
            "ready_at": self.ready_at.isoformat() if self.ready_at else None,
            "allocated_at": self.allocated_at.isoformat()
            if self.allocated_at
            else None,
            "conversation_id": self.conversation_id,
            "error_message": self.error_message,
            "init_log": self.init_log[-10:],  # Last 10 log lines
        }


class PoolController:
    """Manages a pool of warm OpenHands sandboxes."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        pool_size: int = 3,
        threshold: int = 2,
        sandbox_spec_id: str | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.pool_size = pool_size
        self.threshold = threshold
        self.sandbox_spec_id = sandbox_spec_id

        self.headers = {"X-Session-API-Key": api_key}
        self.ready_queue: queue.Queue[PooledSandbox] = queue.Queue()
        self.all_sandboxes: dict[str, PooledSandbox] = {}
        self.lock = threading.RLock()
        self.running = True

        # Load initialization script
        self.init_script = self._load_init_script()

        logger.info(
            f"Pool controller initialized: size={pool_size}, threshold={threshold}"
        )

    def _load_init_script(self) -> str:
        """Load the sandbox initialization script."""
        script_path = Path(__file__).parent / "sandbox_prep" / "init_ruby_service.sh"
        if not script_path.exists():
            raise FileNotFoundError(f"Init script not found: {script_path}")
        return script_path.read_text()

    def start(self) -> None:
        """Start the pool management background thread."""
        thread = threading.Thread(target=self._pool_manager_loop, daemon=True)
        thread.start()
        logger.info("Pool manager thread started")

    def _pool_manager_loop(self) -> None:
        """Background thread that maintains the pool at the target size."""
        # Initial fill
        logger.info(f"Initial pool fill to {self.pool_size} sandboxes")
        for _ in range(self.pool_size):
            self._provision_sandbox()

        # Maintenance loop
        while self.running:
            time.sleep(5)
            ready_count = self.ready_queue.qsize()
            if ready_count < self.threshold:
                needed = self.pool_size - self._total_initializing_count() - ready_count
                if needed > 0:
                    logger.info(
                        f"Pool below threshold ({ready_count} < {self.threshold}), "
                        f"provisioning {needed} sandbox(es)"
                    )
                    for _ in range(needed):
                        self._provision_sandbox()

    def _total_initializing_count(self) -> int:
        """Count sandboxes currently initializing (STARTING or PREPARING)."""
        with self.lock:
            return sum(
                1
                for sb in self.all_sandboxes.values()
                if sb.state in (SandboxState.STARTING, SandboxState.PREPARING)
            )

    def _provision_sandbox(self) -> None:
        """Start provisioning a new sandbox (runs in background thread)."""
        thread = threading.Thread(target=self._provision_and_init_sandbox, daemon=True)
        thread.start()

    def _provision_and_init_sandbox(self) -> None:
        """Provision, initialize, and add a sandbox to the ready queue."""
        sandbox = None
        try:
            # 1. Create sandbox via Cloud API
            logger.info("Creating new sandbox...")
            params = (
                {"sandbox_spec_id": self.sandbox_spec_id}
                if self.sandbox_spec_id
                else None
            )
            resp = requests.post(
                f"{self.base_url}/api/v1/sandboxes",
                headers=self.headers,
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            sb_data = resp.json()
            sandbox_id = sb_data["id"]

            sandbox = PooledSandbox(id=sandbox_id, state=SandboxState.STARTING)
            with self.lock:
                self.all_sandboxes[sandbox_id] = sandbox
            logger.info(f"Sandbox {sandbox_id}: STARTING")

            # 2. Poll until RUNNING
            sandbox = self._wait_until_running(sandbox, timeout=180)

            # 3. Run initialization script
            sandbox.state = SandboxState.PREPARING
            logger.info(f"Sandbox {sandbox_id}: PREPARING (running init script)")
            self._run_init_script(sandbox)

            # 4. Mark as ready and add to queue
            sandbox.state = SandboxState.READY
            sandbox.ready_at = datetime.now()
            self.ready_queue.put(sandbox)
            logger.info(
                f"Sandbox {sandbox_id}: READY "
                f"(took {(sandbox.ready_at - sandbox.created_at).seconds}s)"
            )

        except Exception as e:
            logger.error(f"Failed to provision sandbox: {e}", exc_info=True)
            if sandbox:
                sandbox.state = SandboxState.FAILED
                sandbox.error_message = str(e)

    def _wait_until_running(
        self, sandbox: PooledSandbox, timeout: int = 180
    ) -> PooledSandbox:
        """Poll sandbox status until it reaches RUNNING state."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = requests.get(
                f"{self.base_url}/api/v1/sandboxes",
                headers=self.headers,
                params={"id": sandbox.id},
                timeout=10,
            )
            resp.raise_for_status()
            results = resp.json()
            if not results or results[0] is None:
                raise ValueError(f"Sandbox {sandbox.id} not found")

            sb_data = results[0]
            status = sb_data["status"]
            sandbox.init_log.append(f"Status: {status}")

            if status == "RUNNING":
                # Extract agent-server URL and session key
                sandbox.agent_url = self._extract_agent_url(sb_data)
                sandbox.session_api_key = sb_data["session_api_key"]
                return sandbox

            time.sleep(3)

        raise TimeoutError(
            f"Sandbox {sandbox.id} did not reach RUNNING within {timeout}s"
        )

    def _extract_agent_url(self, sandbox_data: dict) -> str:
        """Extract AGENT_SERVER URL from sandbox exposed_urls."""
        url = next(
            (
                u["url"]
                for u in sandbox_data.get("exposed_urls", [])
                if u["name"] == "AGENT_SERVER"
            ),
            None,
        )
        if not url:
            raise ValueError(
                f"AGENT_SERVER URL not found in sandbox {sandbox_data['id']}"
            )
        return url

    def _run_init_script(self, sandbox: PooledSandbox) -> None:
        """Execute the initialization script in the sandbox."""
        if not sandbox.agent_url or not sandbox.session_api_key:
            raise ValueError(f"Sandbox {sandbox.id} missing agent URL or session key")

        session_headers = {"X-Session-API-Key": sandbox.session_api_key}

        # Upload the init script
        sandbox.init_log.append("Uploading init script...")
        upload_resp = requests.post(
            f"{sandbox.agent_url}/api/upload-files",
            headers=session_headers,
            files={"file": ("init.sh", self.init_script, "text/plain")},
            data={"destination": "/tmp/init.sh"},
            timeout=30,
        )
        upload_resp.raise_for_status()

        # Execute the script
        sandbox.init_log.append("Executing init script...")
        exec_resp = requests.post(
            f"{sandbox.agent_url}/api/bash/execute_bash_command",
            headers=session_headers,
            json={
                "command": (
                    "chmod +x /tmp/init.sh && "
                    f"SANDBOX_ID={sandbox.id} bash /tmp/init.sh"
                ),
                "timeout": 300,
            },
            timeout=320,
        )
        exec_resp.raise_for_status()
        result = exec_resp.json()

        # Log output
        stdout = (result.get("stdout") or "").strip()
        stderr = (result.get("stderr") or "").strip()
        if stdout:
            sandbox.init_log.extend(stdout.split("\n")[-20:])  # Last 20 lines
        if stderr:
            sandbox.init_log.append(f"[stderr]: {stderr}")

        exit_code = result.get("exit_code", -1)
        if exit_code != 0:
            raise RuntimeError(
                f"Init script failed with exit code {exit_code}: {stderr or stdout}"
            )

        sandbox.init_log.append("✅ Initialization complete")

    def get_ready_sandbox(self) -> PooledSandbox | None:
        """Get a ready sandbox from the pool (non-blocking)."""
        try:
            sandbox = self.ready_queue.get_nowait()
            sandbox.state = SandboxState.ALLOCATED
            sandbox.allocated_at = datetime.now()
            return sandbox
        except queue.Empty:
            return None

    def get_pool_status(self) -> dict:
        """Get current pool status for the UI."""
        with self.lock:
            sandboxes_list = sorted(
                [sb.to_dict() for sb in self.all_sandboxes.values()],
                key=lambda x: x["created_at"],
            )

        return {
            "pool_size": self.pool_size,
            "threshold": self.threshold,
            "ready_count": self.ready_queue.qsize(),
            "sandboxes": sandboxes_list,
            "timestamp": datetime.now().isoformat(),
        }

    def attach_conversation(self, sandbox: PooledSandbox, message: str) -> str:
        """Attach a new conversation to the given sandbox."""
        payload = {
            "sandbox_id": sandbox.id,
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": message}],
            },
            "title": f"Warm Pool Demo - {datetime.now().strftime('%H:%M:%S')}",
        }

        resp = requests.post(
            f"{self.base_url}/api/v1/app-conversations",
            headers=self.headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        task = resp.json()

        # Poll start task until we get conversation ID
        task_id = task["id"]
        conv_id = task.get("app_conversation_id")
        timeout = 60
        deadline = time.monotonic() + timeout

        while not conv_id and time.monotonic() < deadline:
            time.sleep(2)
            resp = requests.get(
                f"{self.base_url}/api/v1/app-conversations/start-tasks",
                headers=self.headers,
                params={"ids": task_id},
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json()
            item = items[0] if isinstance(items, list) else items
            conv_id = item.get("app_conversation_id")

        if not conv_id:
            raise TimeoutError(f"Start task {task_id} did not produce conversation ID")

        sandbox.conversation_id = conv_id
        logger.info(f"Conversation {conv_id} attached to sandbox {sandbox.id}")
        return conv_id

    def shutdown(self) -> None:
        """Graceful shutdown."""
        self.running = False
        logger.info("Pool controller shutting down")


# Flask Application
app = Flask(__name__)
CORS(app)

pool_controller: PoolController | None = None


@app.route("/")
def index():
    """Serve the main UI."""
    return render_template("index.html")


@app.route("/api/pool/status")
def pool_status():
    """Get current pool status."""
    if not pool_controller:
        return jsonify({"error": "Pool controller not initialized"}), 500
    return jsonify(pool_controller.get_pool_status())


@app.route("/api/pool/events")
def pool_events():
    """Server-Sent Events stream for real-time pool updates."""

    def generate():
        while True:
            if pool_controller:
                status = pool_controller.get_pool_status()
                yield f"data: {json.dumps(status)}\n\n"
            time.sleep(2)

    return app.response_class(generate(), mimetype="text/event-stream")


@app.route("/api/conversation/start", methods=["POST"])
def start_conversation():
    """Start a new conversation with a sandbox from the pool."""
    if not pool_controller:
        return jsonify({"error": "Pool controller not initialized"}), 500

    data = request.json or {}
    message = data.get("message", "Hello! I'm ready to help.")

    # Get a ready sandbox
    sandbox = pool_controller.get_ready_sandbox()
    if not sandbox:
        return (
            jsonify(
                {
                    "error": "No ready sandboxes available",
                    "ready_count": pool_controller.ready_queue.qsize(),
                }
            ),
            503,
        )

    # Attach conversation
    try:
        conv_id = pool_controller.attach_conversation(sandbox, message)
        conv_url = f"{pool_controller.base_url}/conversations/{conv_id}"
        return jsonify(
            {
                "conversation_id": conv_id,
                "conversation_url": conv_url,
                "sandbox_id": sandbox.id,
                "message": "Conversation started with pre-warmed sandbox!",
            }
        )
    except Exception as e:
        logger.error(f"Failed to attach conversation: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def parse_args():
    p = argparse.ArgumentParser(description="Warm Sandbox Pool Controller")
    p.add_argument(
        "--api-key",
        default=os.environ.get("OH_API_KEY"),
        help="OpenHands Cloud API key (env: OH_API_KEY)",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("OH_API_BASE", "https://app.all-hands.dev"),
        help="OpenHands base URL (env: OH_API_BASE)",
    )
    p.add_argument(
        "--pool-size",
        type=int,
        default=int(os.environ.get("POOL_SIZE", "3")),
        help="Target pool size (env: POOL_SIZE, default: 3)",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=int(os.environ.get("POOL_THRESHOLD", "2")),
        help="Refill when pool drops below this (env: POOL_THRESHOLD, default: 2)",
    )
    p.add_argument(
        "--sandbox-spec-id",
        default=os.environ.get("SANDBOX_SPEC_ID"),
        help="Optional sandbox spec ID (env: SANDBOX_SPEC_ID)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "5000")),
        help="Web server port (env: PORT, default: 5000)",
    )
    return p.parse_args()


def main():
    args = parse_args()

    if not args.api_key:
        sys.exit(
            "Error: OH_API_KEY is required. Set via --api-key or environment variable."
        )

    # Initialize pool controller
    global pool_controller
    pool_controller = PoolController(
        api_key=args.api_key,
        base_url=args.base_url,
        pool_size=args.pool_size,
        threshold=args.threshold,
        sandbox_spec_id=args.sandbox_spec_id,
    )
    pool_controller.start()

    # Start Flask app
    logger.info(f"Starting web server on port {args.port}")
    logger.info(f"Open http://localhost:{args.port} in your browser")
    app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
