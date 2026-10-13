import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import org_admin
import pytest


ME = {"id": "u-1", "email": "root@example.com", "org_name": "Mine", "org_id": "u-1"}
ORGS = {
    "items": [
        {"id": "u-1", "name": "user_u-1_org", "is_personal": True},
        {"id": "o-1", "name": "Acme", "is_personal": False},
    ],
    "next_page_id": None,
}


class FakeServer:
    """A real HTTP server that records requests and replays canned responses."""

    def __init__(self):
        self.routes: dict[tuple[str, str], tuple[int, object]] = {}
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                outer.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "body": body,
                        "org": self.headers.get("X-Org-Id"),
                        "auth": self.headers.get("Authorization"),
                    }
                )
                status, payload = outer.routes.get(
                    (self.command, self.path.split("?")[0]), (404, {"detail": "nope"})
                )
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = _handle

            def log_message(self, format, *args):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    srv = FakeServer()
    srv.routes[("GET", "/api/v1/users/me")] = (200, ME)
    srv.routes[("GET", "/api/organizations")] = (200, ORGS)
    yield srv
    srv.close()


def run(server, *argv):
    return org_admin.main(["--api-key", "k", "--base-url", server.url, *argv])


def test_status_lists_super_admins_and_marks_caller(server, capsys):
    server.routes[("GET", "/api/admin/super-admins")] = (
        200,
        {"super_admins": [{"user_id": "u-1", "email": "root@example.com"}]},
    )
    assert run(server) == 0
    out = capsys.readouterr().out
    assert "u-1  root@example.com  <- you" in out
    assert "o-1  team" in out
    assert server.requests[0]["auth"] == "Bearer k"


def test_status_explains_how_to_proceed_when_listing_is_forbidden(server, capsys):
    server.routes[("GET", "/api/admin/super-admins")] = (403, {"detail": "no"})
    assert run(server, "status") == 0
    assert "Enable OEM User Creation Flow" in capsys.readouterr().out


def test_mutation_is_a_dry_run_without_yes(server, capsys):
    assert run(server, "super-admins", "grant", "--email", "a@example.com") == 0
    assert "[dry run]" in capsys.readouterr().out
    assert server.requests == []


def test_grant_with_yes_posts_the_email(server):
    server.routes[("POST", "/api/admin/super-admins")] = (
        201,
        {"user_id": "u-2", "email": "a@example.com"},
    )
    argv = ["super-admins", "grant", "--email", "a@example.com", "--yes"]
    assert run(server, *argv) == 0
    assert server.requests[-1]["body"] == {"email": "a@example.com"}


def test_revoke_with_yes_sends_delete(server):
    server.routes[("DELETE", "/api/admin/super-admins/u-2")] = (200, {"user_id": "u-2"})
    assert run(server, "super-admins", "revoke", "u-2", "--yes") == 0
    assert server.requests[-1]["method"] == "DELETE"


def test_create_org_posts_all_fields(server, capsys):
    server.routes[("POST", "/api/organizations")] = (201, {"id": "o-2", "name": "Acme"})
    argv = ["create-org", "--name", "Acme", "--contact-name", "A"]
    argv += ["--contact-email", "a@example.com", "--yes"]
    assert run(server, *argv) == 0
    assert server.requests[-1]["body"] == {
        "name": "Acme",
        "contact_name": "A",
        "contact_email": "a@example.com",
    }
    assert "o-2" in capsys.readouterr().out


def test_invite_prints_link_when_no_email_provider(server, capsys):
    server.routes[("POST", "/api/organizations/o-1/members/invite")] = (
        201,
        {
            "successful": [
                {"email": "x@example.com", "role": "owner", "invite_url": "http://i/1"}
            ],
            "failed": [],
            "email_delivery_configured": False,
        },
    )
    argv = ["invite", "--org-id", "o-1", "--role", "owner", "x@example.com", "--yes"]
    assert run(server, *argv) == 0
    assert "http://i/1" in capsys.readouterr().out
    assert server.requests[-1]["body"] == {"emails": ["x@example.com"], "role": "owner"}


def test_provision_user_keeps_secrets_out_of_stdout(server, tmp_path, capsys):
    server.routes[("POST", "/api/organizations/provision-user")] = (
        201,
        {"email": "n@example.com", "password": "hunter2hunter2", "api_key": "sk-1"},
    )
    creds = tmp_path / "creds.json"
    argv = ["provision-user", "--org-id", "o-1", "--email", "n@example.com"]
    argv += ["--role", "owner", "--credentials-file", str(creds), "--yes"]
    assert run(server, *argv) == 0
    out = capsys.readouterr().out
    assert "hunter2hunter2" not in out and "sk-1" not in out
    assert json.loads(creds.read_text())["api_key"] == "sk-1"
    assert creds.stat().st_mode & 0o777 == 0o600
    assert server.requests[-1]["org"] == "o-1"


def test_dry_run_redacts_supplied_password(server, capsys):
    argv = ["provision-user", "--org-id", "o-1", "--email", "n@example.com"]
    assert run(server, *argv, "--password", "s3cretpassword") == 0
    assert "s3cretpassword" not in capsys.readouterr().out


def test_api_error_is_reported_with_status(server, capsys):
    server.routes[("POST", "/api/organizations")] = (403, {"detail": "denied"})
    argv = ["create-org", "--name", "A", "--contact-name", "A"]
    argv += ["--contact-email", "a@example.com", "--yes"]
    assert run(server, *argv) == 1
    assert "HTTP 403: denied" in capsys.readouterr().err


def test_missing_api_key_is_an_error(monkeypatch):
    monkeypatch.delenv("OH_API_KEY", raising=False)
    assert org_admin.main([]) == 2
