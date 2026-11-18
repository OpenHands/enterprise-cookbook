"""SYNTHETIC fixture of the organization conversation APIs, for tests and demos.

All data is made up. Payload shapes follow OrgConversationPage and
OrgConversationUsageResponse in OpenHands Enterprise server 1.94.x.

    python tests/fixture_api.py --port 8600
    GET /__fixture/detail?enabled=1   turn detailed usage on (fixture only)
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


ORG_ID = "00000000-0000-4000-8000-0000000000aa"
API_KEY = "synthetic-test-key"
FULL, PARTIAL, TOTALS_ONLY = (f"00000000-0000-4000-8000-00000000000{i}" for i in "123")


def _counters(cost, prompt, completion, cache_read, cache_write, reasoning):
    return {
        "cost": cost,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "reasoning_tokens": reasoning,
    }


def _conversation(i: int) -> dict:
    special = {1: FULL, 2: PARTIAL, 3: TOTALS_ONLY}
    return {
        "id": special.get(i, f"00000000-0000-4000-8000-{i:012d}"),
        "title": f"Synthetic run {i}",
        "llm_model": "example-model",
        "agent_kind": "openhands",
        "user_id": f"user-{i % 3}",
        "user_email": f"user{i % 3}@example.com",
        "created_at": f"2026-01-{i:02d}T09:00:00Z",
        "updated_at": f"2026-01-{i:02d}T10:00:00Z",
        "sandbox_id": None,
        "sandbox_status": None,
        "runtime_url": None,
        "execution_status": "finished" if i % 4 else "error",
        "selected_repository": None,
        "selected_branch": None,
        "git_provider": None,
        "trigger": None,
        "pr_number": [],
        "pr_merged": None,
        "tags": {
            "team": "alpha" if i % 2 else "beta",
            "env": "demo",
            "archiveworkspacepath": f"/workspace/project/synthetic{i}",
        },
        "accumulated_cost": 1.25 if i == 1 else round(0.1 * i, 2),
        "prompt_tokens": 1000 * i,
        "completion_tokens": 100 * i,
        "total_tokens": 1100 * i,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


CONVERSATIONS = [_conversation(i) for i in range(1, 26)]

USAGE = {
    FULL: {
        "conversation_id": FULL,
        "totals": _counters(1.25, 9000, 900, 4000, 500, 300),
        "completeness": "full",
        "incomplete_reasons": [],
        "latest_snapshot_at": "2026-01-01T10:00:00Z",
        "buckets": [
            {
                "usage_id": "agent",
                "kind": "main_agent",
                "task_id": None,
                "model": "example-model",
                "usage": _counters(0.75, 6000, 600, 3000, 500, 200),
            },
            {
                "usage_id": "condenser",
                "kind": "condenser",
                "task_id": None,
                "model": "example-small-model",
                "usage": _counters(0.1, 1000, 100, 0, 0, 0),
            },
            {
                "usage_id": "task-1",
                "kind": "task",
                "task_id": "task-1",
                "model": "example-model",
                "usage": _counters(0.4, 2000, 200, 1000, 0, 100),
            },
        ],
        "task_calls": [
            {
                "task_id": "task-1",
                "agent_type": "explorer",
                "resumed": False,
                "status": "completed",
                "started_at": "2026-01-01T09:10:00Z",
                "ended_at": "2026-01-01T09:12:30Z",
                "duration_seconds": 150.0,
            }
        ],
    },
    PARTIAL: {
        "conversation_id": PARTIAL,
        "totals": _counters(0.2, 2000, 200, 0, 0, None),
        "completeness": "partial",
        "incomplete_reasons": ["task_usage_missing", "unknown_counters"],
        "latest_snapshot_at": None,
        "buckets": [
            {
                "usage_id": "agent",
                "kind": "main_agent",
                "task_id": None,
                "model": None,
                "usage": _counters(0.15, 1500, 150, 0, None, None),
            }
        ],
        "task_calls": [
            {
                "task_id": "task-9",
                "agent_type": None,
                "resumed": None,
                "status": "unknown",
                "started_at": None,
                "ended_at": None,
                "duration_seconds": None,
            }
        ],
    },
    TOTALS_ONLY: {
        "conversation_id": TOTALS_ONLY,
        "totals": _counters(0.3, 3000, 300, 0, 0, 0),
        "completeness": "totals_only",
        "incomplete_reasons": ["no_stats_snapshot"],
        "latest_snapshot_at": None,
        "buckets": [],
        "task_calls": [],
    },
}


class FixtureApi:
    """A real HTTP server serving synthetic data and recording requests."""

    def __init__(self, port=0):
        self.detail_enabled = False
        self.raw_response: tuple[int, dict, bytes] | None = None
        self.requests: list[dict] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _handler(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data)

            def _record(self):
                fixture.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                    }
                )

            def do_GET(self):
                self._record()
                url = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                if url.path == "/__fixture/detail":
                    fixture.detail_enabled = query.get("enabled") == "1"
                    return self._reply(200, {"enabled": fixture.detail_enabled})
                if fixture.raw_response:
                    status, headers, body = fixture.raw_response
                    self.send_response(status)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.end_headers()
                    return self.wfile.write(body)
                if self.headers.get("Authorization") != f"Bearer {API_KEY}":
                    return self._reply(401, {"detail": "Unauthorized"})
                base = f"/api/organizations/{ORG_ID}/conversations"
                if url.path == base:
                    return self._reply(200, self._page(query))
                if url.path.startswith(base + "/") and url.path.endswith("/usage"):
                    if not fixture.detail_enabled:
                        return self._reply(403, {"detail": "not enabled"})
                    usage = USAGE.get(url.path[len(base) + 1 : -len("/usage")])
                    if usage is None:
                        return self._reply(404, {"detail": "Conversation not found"})
                    return self._reply(200, usage)
                self._reply(404, {"detail": "Not Found"})

            def do_PATCH(self):
                self._record()
                self._reply(405, {"detail": "fixture is read-only"})

            do_POST = do_PUT = do_DELETE = do_PATCH

            def _page(self, query):
                items = CONVERSATIONS
                search = query.get("search", "").lower()
                if search:
                    items = [
                        c
                        for c in items
                        if search in c["title"].lower() or search in c["user_email"]
                    ]
                if "execution_status" in query:
                    status = query["execution_status"]
                    items = [c for c in items if c["execution_status"] == status]
                page, per_page = int(query["page"]), int(query["per_page"])
                return {
                    "items": items[(page - 1) * per_page : page * per_page],
                    "total_items": len(items),
                    "page": page,
                    "per_page": per_page,
                    "total_pages": -(-len(items) // per_page),
                    "pagination_accurate": True,
                }

            def log_message(self, format, *args):  # noqa: A002
                pass

        return Handler

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8600)
    api = FixtureApi(parser.parse_args().port)
    print(f"SYNTHETIC fixture API: {api.url} org={ORG_ID} key={API_KEY}")
    api.server.serve_forever()
