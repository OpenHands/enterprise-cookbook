"""Offline tests for sync_llm.py against local HTTP servers.

Three small servers stand in for Enterprise, the LiteLLM proxy and the Mac Agent
Server. The script talks to them over real HTTP, so every code path runs. No
credentials or network access are needed.
"""

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "macos-agent-server-llm-setup"
    / "scripts"
    / "sync_llm.py"
)
spec = importlib.util.spec_from_file_location("sync_llm", SCRIPT)
assert spec is not None and spec.loader is not None
sync_llm = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sync_llm
spec.loader.exec_module(sync_llm)

API_KEY = "sk-oh-TESTAPIKEY0001"
SESSION = "SESSIONKEY-0123456789abcdef-0123456789abcdef"
LLM_KEY = "sk-litellm-BYORKEY0002"
GITHUB = "ghp_TESTSECRETVALUE0003"
MODELS = ["claude-sonnet-4-5", "gpt-5"]


class Fake:
    """A local HTTP server whose routes are a function of (method, path, ctx)."""

    def __init__(self, route):
        self.requests = []
        self.state = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                headers = {k.lower(): v for k, v in self.headers.items()}
                fake.requests.append((self.command, self.path, headers, body))
                status, payload = route(fake, self.command, self.path, headers, body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = handle_any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def paths(self, method=None):
        return [p for m, p, _, _ in self.requests if method in (None, m)]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def enterprise_route(fake, method, path, headers, body):
    if headers.get("authorization") != f"Bearer {API_KEY}":
        return 401, {"detail": "bad key"}
    permitted = fake.state.get("permitted", True)
    if path == "/api/keys/llm/byor/permitted":
        return 200, {"permitted": permitted}
    if path == "/api/keys/llm/byor":
        if not permitted:
            return 402, {"detail": "not enabled"}
        return 200, {"key": fake.state.get("key", LLM_KEY)}
    return 404, {}


def proxy_route(fake, method, path, headers, body):
    if headers.get("authorization") != f"Bearer {fake.state.get('key', LLM_KEY)}":
        return 401, {"detail": "bad key"}
    return 200, {"object": "list", "data": [{"id": m} for m in MODELS]}


def mac_route(fake, method, path, headers, body):
    if headers.get("x-session-api-key") != SESSION:
        return 401, {"detail": "unauthorized"}
    if path == "/api/profiles":
        return 200, {"profiles": [], "active_profile": None}
    if path.endswith("/validate"):
        if fake.state.get("invalid"):
            return 200, {
                "valid": False,
                "error": {"type": "LLMAuthenticationError", "message": "bad key"},
            }
        return 200, {"valid": True}
    if path.endswith("/activate"):
        fake.state["active"] = path.split("/")[3]
        return 200, {"id": "x", "message": "ok"}
    if path == "/api/settings/secrets" and method == "PUT":
        fake.state.setdefault("secrets", {})[body["name"]] = body["value"]
        return 200, {"name": body["name"]}
    if path.startswith("/api/profiles/") and method == "POST":
        fake.state.setdefault("profiles", {})[path.split("/")[3]] = body["llm"]
        return 201, {"name": path.split("/")[3], "message": "saved"}
    if path.startswith("/api/profiles/") and method == "GET":
        profile = fake.state.get("profiles", {}).get(path.split("/")[3])
        if profile is None:
            return 404, {"detail": "not found"}
        return 200, {"name": "n", "config": profile, "api_key_set": True}
    return 404, {}


@pytest.fixture
def world():
    sync_llm._sensitive.clear()
    enterprise, proxy, mac = Fake(enterprise_route), Fake(proxy_route), Fake(mac_route)
    env = {
        "OPENHANDS_API_KEY": API_KEY,
        "MAC_AGENT_SERVER_KEY": SESSION,
        "GITHUB_TOKEN": GITHUB,
        "OH_WEBHOOKS_0_BASE_URL": f"{enterprise.url}/api/v1/webhooks",
    }
    args = [
        "--target",
        mac.url,
        "--llm-base-url",
        proxy.url,
        "--allow-insecure-http",
    ]
    yield enterprise, proxy, mac, env, args
    for fake in (enterprise, proxy, mac):
        fake.close()


def run(args, env):
    return sync_llm.main(args, env)


def test_sync_saves_profile_and_secrets_without_printing_them(world, capsys):
    enterprise, proxy, mac, env, args = world

    code = run(
        [*args, "--model", "openhands/claude-sonnet-4-5", "--secret", "GITHUB_TOKEN"],
        env,
    )

    out = capsys.readouterr().out
    assert code == 0
    assert mac.state["profiles"]["enterprise"] == {
        "model": "litellm_proxy/claude-sonnet-4-5",
        "base_url": proxy.url,
        "api_key": LLM_KEY,
    }
    assert mac.state["active"] == "enterprise"
    assert mac.state["secrets"] == {"GITHUB_TOKEN": GITHUB}
    for value in (API_KEY, SESSION, LLM_KEY, GITHUB):
        assert value not in out


def test_validation_runs_before_anything_is_saved(world, capsys):
    _, _, mac, env, args = world
    mac.state["invalid"] = True

    code = run([*args, "--model", "gpt-5", "--secret", "GITHUB_TOKEN"], env)

    assert code == 1
    assert "could not use this LLM" in capsys.readouterr().out
    assert "profiles" not in mac.state and "secrets" not in mac.state


def test_export_turned_off_stops_with_admin_instructions(world, capsys):
    enterprise, _, mac, env, args = world
    enterprise.state["permitted"] = False

    code = run([*args, "--model", "gpt-5"], env)

    out = capsys.readouterr().out
    assert code == 1
    assert "Enable LLM Key Export (BYOR)" in out
    assert "/api/keys/llm/byor" not in enterprise.paths()
    assert mac.requests == []


def test_dry_run_changes_nothing_and_fetches_no_key(world, capsys):
    enterprise, _, mac, env, args = world

    code = run(
        [*args, "--dry-run", "--model", "gpt-5", "--secret", "GITHUB_TOKEN"], env
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "nothing was changed" in out and "GITHUB_TOKEN" in out
    assert enterprise.paths() == ["/api/keys/llm/byor/permitted"]
    assert mac.paths() == ["/api/profiles"]


def test_missing_secret_is_reported_before_any_request(world, capsys):
    enterprise, _, mac, env, args = world

    code = run(
        [
            *args,
            "--model",
            "gpt-5",
            "--secret",
            "NOPE",
            "--secret",
            "OPENHANDS_API_KEY",
        ],
        env,
    )

    out = capsys.readouterr().out
    assert code == 2
    assert "NOPE: not set" in out and "OPENHANDS_API_KEY: never copied" in out
    assert enterprise.requests == [] and mac.requests == []


def test_plain_http_is_refused_without_the_flag(world, capsys):
    enterprise, _, mac, env, args = world
    args.remove("--allow-insecure-http")

    code = run([*args, "--model", "gpt-5"], env)

    assert code == 2
    assert "plain HTTP" in capsys.readouterr().out
    assert enterprise.requests == [] and mac.requests == []


def test_list_models_prints_models_and_never_the_key(world, capsys):
    _, _, mac, env, args = world

    code = run([*args, "--list-models"], env)

    out = capsys.readouterr().out
    assert code == 0
    assert all(model in out for model in MODELS)
    assert LLM_KEY not in out
    assert mac.requests == []


def test_wrong_mac_session_key_gets_a_clear_error(world, capsys):
    _, _, _, env, args = world
    env["MAC_AGENT_SERVER_KEY"] = "wrong-key-wrong-key-wrong-key-wrong-key"

    code = run([*args, "--model", "gpt-5"], env)

    assert code == 1
    assert "rejected the session key" in capsys.readouterr().out


def test_check_passes_when_the_mac_holds_the_current_key(world, capsys):
    _, _, _, env, args = world
    assert run([*args, "--model", "gpt-5"], env) == 0
    capsys.readouterr()

    code = run([*args, "--check"], env)

    assert code == 0
    assert "holds the current LLM key" in capsys.readouterr().out


def test_check_asks_for_a_resync_after_the_key_changes(world, capsys):
    enterprise, proxy, _, env, args = world
    assert run([*args, "--model", "gpt-5"], env) == 0
    capsys.readouterr()
    enterprise.state["key"] = proxy.state["key"] = "sk-litellm-NEWKEY0004"

    code = run([*args, "--check"], env)

    out = capsys.readouterr().out
    assert code == 1
    assert "older LLM key" in out
    assert LLM_KEY not in out and "NEWKEY0004" not in out


def test_check_without_a_profile_says_to_sync_first(world, capsys):
    _, _, _, env, args = world

    assert run([*args, "--check"], env) == 1
    assert "Run the sync first" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("enterprise_url", "expected"),
    [
        ("https://app.example.com", "https://llm-proxy.example.com"),
        (
            "https://app.beta.staging.example.dev",
            "https://llm-proxy.beta.staging.example.dev",
        ),
    ],
)
def test_proxy_url_is_derived_from_the_app_hostname(enterprise_url, expected):
    args = sync_llm.read_args(["--target", "https://mac"])
    assert sync_llm.proxy_url(args, enterprise_url) == expected


def test_proxy_url_needs_a_flag_for_a_custom_app_hostname():
    args = sync_llm.read_args(["--target", "https://mac"])
    with pytest.raises(sync_llm.UsageError, match="--llm-base-url"):
        sync_llm.proxy_url(args, "https://openhands.example.com")
