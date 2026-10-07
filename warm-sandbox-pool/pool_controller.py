#!/usr/bin/env python3
"""
Warm Sandbox Pool Controller

Maintains a pool of pre-initialized OpenHands sandboxes with Ruby/Sinatra services
already running. When users start conversations, sandboxes are pulled from the pool
and attached without a boot or setup wait, with automatic refill.

Usage:
    export OH_API_KEY=your_key_here
    python pool_controller.py

Then open http://localhost:12000 in your browser. The controller prints a random
access code when it starts; paste it into the web page to sign in. Nothing is served
without it.

On exit (Ctrl-C / SIGTERM) every sandbox that is still in the pool is deleted.
Sandboxes already handed to a conversation are left alone.
"""

import argparse
import hmac
import json
import logging
import os
import queue
import secrets
import signal
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template, request, session


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
    attach_seconds: float | None = None
    error_message: str | None = None
    init_log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        warmup = (
            (self.ready_at - self.created_at).total_seconds() if self.ready_at else None
        )
        idle = (
            (self.allocated_at - self.ready_at).total_seconds()
            if self.allocated_at and self.ready_at
            else None
        )
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
            "warmup_seconds": warmup,
            "idle_seconds": idle,
            "attach_seconds": self.attach_seconds,
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
        init_timeout: int = 300,
        max_failures: int = 3,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.pool_size = pool_size
        self.threshold = threshold
        self.sandbox_spec_id = sandbox_spec_id
        self.init_timeout = init_timeout
        self.max_failures = max_failures

        self.headers = {"X-Session-API-Key": api_key}
        self.ready_queue: queue.Queue[PooledSandbox] = queue.Queue()
        self.all_sandboxes: dict[str, PooledSandbox] = {}
        self.lock = threading.RLock()
        self.running = True
        self.consecutive_failures = 0
        self.claim_misses = 0
        self.events: deque[dict] = deque(maxlen=200)
        self.event_seq = 0

        prep_dir = Path(__file__).parent / "sandbox_prep"
        self.init_script = self._load_file(prep_dir / "init_ruby_service.sh")
        self.service_source = self._load_file(prep_dir / "quote_service.rb")

        logger.info(
            f"Pool controller initialized: size={pool_size}, threshold={threshold}"
        )

    @staticmethod
    def _load_file(path: Path) -> str:
        if not path.exists():
            raise FileNotFoundError(f"Sandbox prep file not found: {path}")
        return path.read_text()

    def _record(self, kind: str, message: str, sandbox_id: str | None = None) -> None:
        """Append to the activity feed shown in the web UI."""
        with self.lock:
            self.event_seq += 1
            self.events.append(
                {
                    "seq": self.event_seq,
                    "timestamp": datetime.now().isoformat(),
                    "kind": kind,
                    "sandbox_id": sandbox_id,
                    "message": message,
                }
            )

    @property
    def halted(self) -> bool:
        """True once provisioning has failed too many times in a row."""
        return self.consecutive_failures >= self.max_failures

    def start(self) -> None:
        """Start the pool management background thread."""
        thread = threading.Thread(target=self._pool_manager_loop, daemon=True)
        thread.start()
        logger.info("Pool manager thread started")

    def _pool_manager_loop(self) -> None:
        """Background thread that maintains the pool at the target size."""
        # Initial fill
        logger.info(f"Initial pool fill to {self.pool_size} sandboxes")
        self._record(
            "refill", f"Filling the pool: starting {self.pool_size} sandbox(es)"
        )
        for _ in range(self.pool_size):
            self._provision_sandbox()

        # Maintenance loop
        while self.running:
            time.sleep(5)
            if self.halted:
                continue
            ready_count = self.ready_queue.qsize()
            if ready_count < self.threshold:
                needed = self.pool_size - self._total_initializing_count() - ready_count
                if needed > 0:
                    logger.info(
                        f"Pool below threshold ({ready_count} < {self.threshold}), "
                        f"provisioning {needed} sandbox(es)"
                    )
                    self._record(
                        "refill",
                        f"{ready_count} ready is below the threshold of "
                        f"{self.threshold}: starting {needed} sandbox(es) to get "
                        f"back to {self.pool_size}",
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
            self._record("created", "Requested a new sandbox", sandbox_id)

            # 2. Poll until RUNNING
            sandbox = self._wait_until_running(sandbox, timeout=180)

            # 3. Run initialization script
            sandbox.state = SandboxState.PREPARING
            logger.info(f"Sandbox {sandbox_id}: PREPARING (running init script)")
            self._record("preparing", "Running the init script", sandbox_id)
            self._run_init_script(sandbox)

            # 4. Mark as ready and add to queue
            with self.lock:
                accepted = self.running
                if accepted:
                    sandbox.state = SandboxState.READY
                    sandbox.ready_at = datetime.now()
                    self.ready_queue.put(sandbox)
                    self.consecutive_failures = 0
            if not accepted:
                self._delete_sandbox(sandbox_id)
                return
            warmup = (sandbox.ready_at - sandbox.created_at).total_seconds()
            logger.info(f"Sandbox {sandbox_id}: READY (took {warmup:.0f}s)")
            self._record(
                "ready",
                f"Warm and waiting in the pool (took {warmup:.0f}s)",
                sandbox_id,
            )

        except Exception as e:
            logger.error(f"Failed to provision sandbox: {e}", exc_info=True)
            with self.lock:
                self.consecutive_failures += 1
            self._record(
                "failed",
                f"Provisioning failed: {str(e).splitlines()[0][:150]}",
                sandbox.id if sandbox else None,
            )
            if sandbox:
                sandbox.state = SandboxState.FAILED
                sandbox.error_message = str(e)
                self._delete_sandbox(sandbox.id)
            if self.halted:
                logger.error(
                    f"{self.consecutive_failures} provisioning failures in a row: "
                    "refilling is halted. Fix the cause and restart the controller."
                )
                self._record(
                    "halted",
                    f"{self.consecutive_failures} failures in a row, refilling stopped",
                )

    def _delete_sandbox(self, sandbox_id: str) -> None:
        """Best-effort delete so failed or unused sandboxes don't leak."""
        try:
            resp = requests.delete(
                f"{self.base_url}/api/v1/sandboxes/{sandbox_id}",
                headers=self.headers,
                params={"sandbox_id": sandbox_id},
                timeout=30,
            )
            resp.raise_for_status()
            logger.info(f"Sandbox {sandbox_id}: deleted")
            self._record("deleted", "Sandbox deleted", sandbox_id)
        except Exception as e:
            logger.warning(f"Could not delete sandbox {sandbox_id}: {e}")

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

        sandbox.init_log.append("Uploading init script and service...")
        for name, content in (
            ("init.sh", self.init_script),
            ("quote_service.rb", self.service_source),
        ):
            upload_resp = requests.post(
                f"{sandbox.agent_url}/api/file/upload",
                headers=session_headers,
                params={"path": f"/tmp/{name}"},
                files={"file": (name, content, "text/plain")},
                timeout=30,
            )
            upload_resp.raise_for_status()

        sandbox.init_log.append("Executing init script...")
        exec_resp = requests.post(
            f"{sandbox.agent_url}/api/bash/execute_bash_command",
            headers=session_headers,
            json={
                "command": f"SANDBOX_ID={sandbox.id} bash /tmp/init.sh",
                "timeout": self.init_timeout,
            },
            timeout=self.init_timeout + 20,
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
            self._record(
                "allocated", "Pulled from the pool for a new conversation", sandbox.id
            )
            return sandbox
        except queue.Empty:
            with self.lock:
                self.claim_misses += 1
            self._record(
                "miss", "A conversation was requested but no sandbox was ready"
            )
            return None

    def abandon_claim(self, sandbox: PooledSandbox, error: Exception) -> None:
        """Attaching failed after the sandbox left the pool: don't strand it."""
        sandbox.state = SandboxState.FAILED
        sandbox.error_message = str(error)
        self._record("failed", f"Could not attach a conversation: {error}", sandbox.id)
        self._delete_sandbox(sandbox.id)

    def get_pool_status(self) -> dict:
        """Get current pool status for the UI."""
        with self.lock:
            sandboxes_list = sorted(
                [sb.to_dict() for sb in self.all_sandboxes.values()],
                key=lambda x: x["created_at"],
            )
            events = list(self.events)[-50:]
            claim_misses = self.claim_misses

        def average(values: list[float]) -> float | None:
            return sum(values) / len(values) if values else None

        attach_times = [
            sb["attach_seconds"] for sb in sandboxes_list if sb["attach_seconds"]
        ]
        for sb in sandboxes_list:
            if sb["conversation_id"]:
                sb["conversation_url"] = (
                    f"{self.base_url}/conversations/{sb['conversation_id']}"
                )

        return {
            "pool_size": self.pool_size,
            "threshold": self.threshold,
            "ready_count": self.ready_queue.qsize(),
            "halted": self.halted,
            "stats": {
                "claims": len(attach_times),
                "claim_misses": claim_misses,
                "avg_warmup_seconds": average(
                    [
                        sb["warmup_seconds"]
                        for sb in sandboxes_list
                        if sb["warmup_seconds"] is not None
                    ]
                ),
                "avg_attach_seconds": average(attach_times),
            },
            "events": events,
            "sandboxes": sandboxes_list,
            "timestamp": datetime.now().isoformat(),
        }

    def attach_conversation(self, sandbox: PooledSandbox, message: str) -> str:
        """Attach a new conversation to the given sandbox."""
        started = time.monotonic()
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
        sandbox.attach_seconds = time.monotonic() - started
        idle = (
            (sandbox.allocated_at - sandbox.ready_at).total_seconds()
            if sandbox.allocated_at and sandbox.ready_at
            else 0
        )
        logger.info(f"Conversation {conv_id} attached to sandbox {sandbox.id}")
        self._record(
            "claimed",
            f"Conversation {conv_id[:8]} attached in {sandbox.attach_seconds:.1f}s "
            f"(sandbox had been warm for {idle:.0f}s)",
            sandbox.id,
        )
        return conv_id

    def shutdown(self) -> None:
        """Stop refilling and delete every sandbox not handed to a conversation."""
        with self.lock:
            self.running = False
            leftovers = [
                sb.id
                for sb in self.all_sandboxes.values()
                if sb.state not in (SandboxState.ALLOCATED, SandboxState.FAILED)
            ]
        logger.info(
            f"Pool controller shutting down, deleting {len(leftovers)} sandbox(es)"
        )
        self._record(
            "shutdown", f"Shutting down, deleting {len(leftovers)} sandbox(es)"
        )
        for sandbox_id in leftovers:
            self._delete_sandbox(sandbox_id)


# Flask Application
app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

pool_controller: PoolController | None = None

# No 0/O or 1/I so the code survives being read out or retyped.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_access_code() -> str:
    chars = [secrets.choice(CODE_ALPHABET) for _ in range(10)]
    return "".join(chars[:5]) + "-" + "".join(chars[5:])


ACCESS_CODE = generate_access_code()


@app.before_request
def require_access_code():
    if request.endpoint in ("index", "login", "static"):
        return None
    if not session.get("authenticated"):
        return jsonify({"error": "Access code required"}), 401
    return None


@app.route("/")
def index():
    """Serve the main UI, or the sign-in page until the access code is entered."""
    if session.get("authenticated"):
        return render_template("index.html")
    return render_template("login.html")


@app.route("/api/login", methods=["POST"])
def login():
    supplied = str((request.get_json(silent=True) or {}).get("code", ""))
    supplied = supplied.strip().upper()
    if not hmac.compare_digest(supplied.encode(), ACCESS_CODE.encode()):
        return jsonify({"error": "That access code is not right"}), 403
    session["authenticated"] = True
    return jsonify({"ok": True})


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
        pool_controller.abandon_claim(sandbox, e)
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
        default=int(os.environ.get("PORT", "12000")),
        help="Web server port (env: PORT, default: 12000, one of the ports an "
        "OpenHands sandbox publishes as a work URL)",
    )
    p.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        help="Web server bind address (env: HOST, default: 127.0.0.1). "
        "Use 0.0.0.0 to reach the UI through an OpenHands sandbox work URL. "
        "The UI is gated by the access code printed at startup.",
    )
    p.add_argument(
        "--init-timeout",
        type=int,
        default=int(os.environ.get("INIT_TIMEOUT", "300")),
        help="Seconds the init script may run (env: INIT_TIMEOUT, default: 300)",
    )
    p.add_argument(
        "--max-failures",
        type=int,
        default=int(os.environ.get("MAX_FAILURES", "3")),
        help="Stop refilling after this many consecutive provisioning failures "
        "(env: MAX_FAILURES, default: 3)",
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
        init_timeout=args.init_timeout,
        max_failures=args.max_failures,
    )
    pool_controller.start()

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    logger.info(f"Starting web server on {args.host}:{args.port}")
    logger.info(f"Open http://localhost:{args.port} in your browser")
    logger.info(f"ACCESS CODE: {ACCESS_CODE}  (paste it into the web page to sign in)")
    try:
        app.run(host=args.host, port=args.port, debug=False, threaded=True)
    finally:
        pool_controller.shutdown()


if __name__ == "__main__":
    main()
