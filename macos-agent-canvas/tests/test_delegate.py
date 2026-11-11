"""Exercise delegated conversations against a real loopback TLS server."""

import base64
import contextlib
import importlib.util
import io
import json
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "skills/macos-agent-server-operations/scripts/delegate.py"
)
spec = importlib.util.spec_from_file_location("delegate", SCRIPT)
assert spec is not None and spec.loader is not None
delegate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = delegate
spec.loader.exec_module(delegate)


def make_cert(directory):
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-days", "1", "-subj", "/CN=localhost",
            "-addext", "subjectAltName=DNS:localhost",
            "-keyout", str(key), "-out", str(cert),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return cert, key


class Server:
    def __init__(self, cert_dir):
        self.requests = []
        self.statuses = ["idle", "running", "finished"]
        self.active_profile = "enterprise"
        self.settings_key = "gAAAAA-encrypted-test-key"
        self.create_status = 201
        self.reply_text = "Why can't you trust atoms? They make up everything."
        self.created = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                self.handle_request()

            def do_POST(self):
                self.handle_request()

            def handle_request(self):
                length = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(length) if length else b""
                body = json.loads(data) if data else None
                headers = {key.lower(): value for key, value in self.headers.items()}
                server.requests.append((self.command, self.path, headers, body))
                if headers.get("x-session-api-key") != "session-key-keep-private":
                    code, response = 401, {"detail": "unauthorized"}
                elif self.path == "/api/profiles":
                    code, response = 200, {"active_profile": server.active_profile}
                elif self.path == "/api/settings":
                    code, response = 200, {
                        "agent_settings": {
                            "agent_kind": "openhands",
                            "schema_version": 1,
                            "mcp_config": {"secret": "do-not-forward"},
                            "agent_context": {"secrets": {"OTHER": "do-not-forward"}},
                            "llm": {
                                "model": "openhands/gpt-6.1-sol",
                                "api_key": server.settings_key,
                            },
                        }
                    }
                elif self.path == "/api/conversations" and self.command == "POST":
                    server.created += 1
                    code, response = server.create_status, {"id": "conversation-123"}
                elif self.path == "/api/conversations/conversation-123":
                    code, response = 200, {
                        "execution_status": server.statuses.pop(0)
                        if len(server.statuses) > 1
                        else server.statuses[0]
                    }
                elif self.path.startswith(
                    "/api/conversations/conversation-123/events/search?"
                ):
                    code, response = 200, {
                        "items": [
                            {
                                "kind": "MessageEvent",
                                "source": "agent",
                                "llm_message": {
                                    "content": [{
                                        "type": "text",
                                        "text": server.reply_text,
                                    }],
                                    "responses_reasoning_item": {
                                        "encrypted_content": "not-for-logs"
                                    },
                                },
                            }
                        ]
                    }
                else:
                    code, response = 404, {"detail": "not found"}
                payload = json.dumps(response).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_dir / "cert.pem", cert_dir / "key.pem")
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
        self.url = f"https://localhost:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()


class DelegateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        cert, _ = make_cert(self.directory / "tls")
        self.server = Server(cert.parent)
        self.env = {
            "MAC_AGENT_SERVER_KEY": "session-key-keep-private",
            "MAC_AGENT_SERVER_CA": base64.b64encode(cert.read_bytes()).decode(),
        }
        self.args = [
            "--target", self.server.url,
            "--workspace", "/tmp",
            "--prompt", "Tell me a science dad joke.",
            "--ca-cert-env", "MAC_AGENT_SERVER_CA",
            "--poll-interval", "0.01",
            "--timeout", "0.1",
        ]
        self.output = io.StringIO()

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()

    def run_cli(self, args=None):
        with contextlib.redirect_stdout(self.output):
            return delegate.main(args if args is not None else self.args, self.env)

    def create_request(self):
        return next(
            req for req in self.server.requests if req[1] == "/api/conversations"
        )

    def test_creates_once_and_reads_only_agent_text(self):
        self.assertEqual(self.run_cli(), 0)
        out = self.output.getvalue()
        self.assertIn("Why can't you trust atoms?", out)
        self.assertIn("conversation-123", out)
        self.assertNotIn("session-key-keep-private", out)
        self.assertNotIn("not-for-logs", out)
        self.assertEqual(self.server.created, 1)
        payload = self.create_request()[3]
        self.assertTrue(payload["secrets_encrypted"])
        self.assertEqual(
            payload["agent_settings"]["llm"]["api_key"], self.server.settings_key
        )
        self.assertEqual(payload["agent_settings"]["tools"], [])
        self.assertNotIn("mcp_config", payload["agent_settings"])
        self.assertNotIn("schema_version", payload["agent_settings"])
        self.assertNotIn("secrets", payload["agent_settings"]["agent_context"])
        self.assertFalse(payload["agent_settings"]["agent_context"]["load_memory"])
        self.assertEqual(payload["workspace"]["working_dir"], "/tmp")
        self.assertTrue(payload["initial_message"]["run"])
        self.assertEqual(payload["max_iterations"], 10)
        self.assertFalse(any(req[1].endswith("/run") for req in self.server.requests))
        settings = next(
            req for req in self.server.requests if req[1] == "/api/settings"
        )
        self.assertEqual(settings[2]["x-expose-secrets"], "encrypted")

    def test_finished_without_agent_text_is_not_success(self):
        self.server.reply_text = ""
        self.assertEqual(self.run_cli(), 1)
        self.assertIn("conversation-123", self.output.getvalue())
        self.assertIn("no agent text", self.output.getvalue())

    def test_tools_are_only_added_when_requested(self):
        self.assertEqual(self.run_cli([*self.args, "--tool", "terminal"]), 0)
        self.assertEqual(
            self.create_request()[3]["agent_settings"]["tools"],
            [{"name": "terminal", "params": {}}],
        )

    def test_refuses_http_and_relative_workspace_before_contact(self):
        self.assertEqual(self.run_cli([
            "--target", "http://localhost:18100", "--workspace", "/tmp",
            "--prompt", "hi",
        ]), 2)
        self.assertEqual(self.run_cli([
            "--target", self.server.url, "--workspace", "relative", "--prompt", "hi",
        ]), 2)
        self.assertEqual(self.server.requests, [])
        self.assertIn("HTTPS", self.output.getvalue())

    def test_no_active_profile_does_not_create(self):
        self.server.active_profile = None
        self.assertEqual(self.run_cli(), 1)
        self.assertEqual(self.server.created, 0)
        self.assertIn("active LLM profile", self.output.getvalue())

    def test_masked_key_does_not_create(self):
        self.server.settings_key = "**********"
        self.assertEqual(self.run_cli(), 1)
        self.assertEqual(self.server.created, 0)
        self.assertIn("encrypted", self.output.getvalue())

    def test_rejects_wrong_certificate_before_sending_key(self):
        other, _ = make_cert(self.directory / "other")
        self.env["MAC_AGENT_SERVER_CA"] = other.read_text()
        self.assertEqual(self.run_cli(), 1)
        self.assertEqual(self.server.requests, [])
        self.assertIn("certificate", self.output.getvalue())

    def test_create_failure_is_not_retried(self):
        self.server.create_status = 429
        self.assertEqual(self.run_cli(), 1)
        self.assertEqual(self.server.created, 1)
        self.assertIn("HTTP 429", self.output.getvalue())

    def test_error_stuck_and_confirmation_keep_id(self):
        for state in ("error", "stuck", "waiting_for_confirmation"):
            with self.subTest(state=state):
                self.server.statuses = [state]
                self.output = io.StringIO()
                self.assertEqual(self.run_cli(), 1)
                self.assertIn("conversation-123", self.output.getvalue())
                self.assertIn(state, self.output.getvalue())

    def test_timeout_keeps_id_without_retry(self):
        self.server.statuses = ["idle"]
        self.assertEqual(self.run_cli(), 3)
        self.assertEqual(self.server.created, 1)
        self.assertIn("conversation-123", self.output.getvalue())

    def test_untrusted_certificate_is_refused_without_ca(self):
        options = self.args[:self.args.index("--ca-cert-env")]
        options += self.args[self.args.index("--poll-interval"):]
        self.assertEqual(self.run_cli(options), 1)
        self.assertEqual(self.server.requests, [])
        self.assertIn("certificate", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
