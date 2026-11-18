"""A local, read-only dashboard for organization conversation usage.

The API key stays in this process. The browser talks only to this server,
which sends GET requests to the OpenHands API.
"""

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests


DEFAULT_BASE_URL = "https://app.all-hands.dev"
COUNTERS = (
    "cost",
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
LIST_FIELDS = (
    "id",
    "title",
    "tags",
    "user_email",
    "execution_status",
    "llm_model",
    "created_at",
    "updated_at",
    "accumulated_cost",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)
CONVERSATION_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")
INDEX_HTML = Path(__file__).with_name("index.html")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


ERROR_MESSAGES = {
    401: "The API key was rejected. Check OH_API_KEY.",
    403: "This API key can't view this organization's conversations. "
    "Use an Owner or Admin key for ORG_ID.",
    404: "Not found. Check ORG_ID and OH_API_BASE.",
}
DETAIL_DISABLED = (
    "Detailed usage is off for this organization. An Owner or Admin can turn "
    "it on with PATCH /api/organizations/{org_id}."
)


class UsageClient:
    """GET-only client for one organization."""

    def __init__(self, base_url: str, api_key: str, org_id: str, timeout=30):
        self.base = f"{base_url.rstrip('/')}/api/organizations/{org_id}"
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"
        self.timeout = timeout

    def _get(self, path: str, params=None) -> dict:
        try:
            response = self.session.get(
                self.base + path, params=params, timeout=self.timeout
            )
        except requests.RequestException:
            raise ApiError(502, "Couldn't reach the OpenHands API.") from None
        if response.ok:
            return response.json()
        message = ERROR_MESSAGES.get(
            response.status_code, f"The API returned {response.status_code}."
        )
        raise ApiError(response.status_code, message)

    def list_conversations(
        self, page=1, per_page=20, search=None, execution_status=None
    ) -> dict:
        params = {"page": page, "per_page": per_page}
        if search:
            params["search"] = search
        if execution_status:
            params["execution_status"] = execution_status
        return self._get("/conversations", params)

    def get_usage(self, conversation_id: str) -> dict:
        try:
            return self._get(f"/conversations/{conversation_id}/usage")
        except ApiError as e:
            if e.status == 403:
                raise ApiError(403, DETAIL_DISABLED) from None
            raise


def matches_tag(tags: dict, tag_filter: str) -> bool:
    """`key` matches any value; `key=value` matches exactly."""
    key, sep, value = tag_filter.partition("=")
    if key.strip() not in tags:
        return False
    return not sep or tags[key.strip()] == value.strip()


def conversation_page(client: UsageClient, query: dict) -> dict:
    page = max(int(query.get("page", 1)), 1)
    per_page = min(max(int(query.get("per_page", 20)), 1), 100)
    data = client.list_conversations(
        page, per_page, query.get("search"), query.get("execution_status")
    )
    items = [{k: item.get(k) for k in LIST_FIELDS} for item in data["items"]]
    tag = (query.get("tag") or "").strip()
    if tag:
        items = [i for i in items if matches_tag(i["tags"] or {}, tag)]
    return {
        "items": items,
        "loaded": len(data["items"]),
        "tag": tag or None,
        "page": data.get("page", page),
        "per_page": data.get("per_page", per_page),
        "total_pages": data.get("total_pages", 0),
        "total_items": data.get("total_items", 0),
        "pagination_accurate": data.get("pagination_accurate", True),
    }


def unattributed(totals: dict, buckets: list[dict]) -> dict:
    """Recorded totals minus the bucket sum, per counter.

    Buckets are disjoint parts of the totals, so they are never added to
    them. A counter is None when the totals or any bucket didn't report it.
    """
    result = {}
    for name in COUNTERS:
        values = [b["usage"].get(name) for b in buckets]
        total = totals.get(name)
        if total is None or None in values:
            result[name] = None
        else:
            result[name] = round(total - sum(values), 10)
    return result


def usage_detail(client: UsageClient, conversation_id: str) -> dict:
    if not CONVERSATION_ID.match(conversation_id):
        raise ApiError(400, "Invalid conversation ID.")
    data = client.get_usage(conversation_id)
    data["unattributed"] = (
        unattributed(data["totals"], data["buckets"]) if data["buckets"] else None
    )
    return data


def make_handler(client: UsageClient):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data):
            self._send(status, json.dumps(data).encode(), "application/json")

        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            parts = url.path.strip("/").split("/")
            try:
                if url.path == "/":
                    self._send(200, INDEX_HTML.read_bytes(), "text/html")
                elif parts == ["api", "conversations"]:
                    self._json(200, conversation_page(client, query))
                elif len(parts) == 4 and parts[:2] == ["api", "conversations"]:
                    if parts[3] != "usage":
                        raise ApiError(404, "Not found.")
                    self._json(200, usage_detail(client, parts[2]))
                else:
                    raise ApiError(404, "Not found.")
            except ApiError as e:
                self._json(e.status, {"error": e.message, "status": e.status})
            except ValueError:
                self._json(400, {"error": "Invalid request.", "status": 400})

        def log_message(self, format, *args):  # noqa: A002
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key", default=os.environ.get("OH_API_KEY"))
    parser.add_argument(
        "--base-url", default=os.environ.get("OH_API_BASE", DEFAULT_BASE_URL)
    )
    parser.add_argument("--org-id", default=os.environ.get("ORG_ID"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8501)))
    args = parser.parse_args()
    if not args.api_key or not args.org_id:
        sys.exit("Set OH_API_KEY and ORG_ID.")

    client = UsageClient(args.base_url, args.api_key, args.org_id)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(client))
    print(f"Dashboard: http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
