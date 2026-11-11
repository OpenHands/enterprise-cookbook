#!/usr/bin/env python3
"""Copy LLM access and chosen secrets to a remote Agent Server.

Run it inside an OpenHands Enterprise conversation. It fetches the user's LLM key
for external use (BYOR) from Enterprise, checks it against the LiteLLM proxy, then
saves an LLM profile and the named secrets on the remote Agent Server (for
example the Mac from this recipe). Secret values come from the sandbox
environment and are never printed.

    sync_llm.py --target https://mac.example.net:18443 --list-models
    sync_llm.py --target https://mac.example.net:18443 --model claude-sonnet-4-5 \\
        --secret GITHUB_TOKEN
    sync_llm.py --target https://mac.example.net:18443 --check

With a self-signed certificate on the Agent Server, pass --ca-cert-env NAME, where
$NAME holds the certificate, as PEM or as one line of base64 (the form that
oh-agent-server.sh ca prints). The script then trusts only that certificate for the
Agent Server.

Python 3.10 or later, standard library only.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any


WEBHOOKS_SUFFIX = "/api/v1/webhooks"
WEBHOOKS_ENV = "OH_WEBHOOKS_0_BASE_URL"
DEFAULT_API_KEY_ENV = "OPENHANDS_API_KEY"
DEFAULT_TARGET_KEY_ENV = "MAC_AGENT_SERVER_KEY"
DEFAULT_CA_CERT_ENV = "MAC_AGENT_SERVER_CA"
DEFAULT_PROFILE = "enterprise"
PROXY_PREFIX = "litellm_proxy/"
OLD_PREFIXES = ("openhands/", PROXY_PREFIX)
SECRET_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TIMEOUT_SECONDS = 30

NOT_PERMITTED = (
    "LLM key export (BYOR) is turned off on this Enterprise deployment. Ask your "
    'OpenHands administrator to turn on "Enable LLM Key Export (BYOR)" in the '
    "Admin Console, then run this again."
)


class UsageError(Exception):
    """Bad arguments or environment. Nothing was contacted or changed."""


class SyncError(Exception):
    """A step failed."""


_sensitive: set[str] = set()


def remember(*values: str | None) -> None:
    _sensitive.update(v for v in values if v)


def scrub(text: str) -> str:
    for value in sorted(_sensitive, key=len, reverse=True):
        text = text.replace(value, "***")
    return text


def say(message: str) -> None:
    print(scrub(message), flush=True)


def call(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    body: Any = None,
    context: ssl.SSLContext | None = None,
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, method=method, headers=request_headers
    )
    try:
        with urllib.request.urlopen(
            request, timeout=TIMEOUT_SECONDS, context=context
        ) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (urllib.error.URLError, OSError) as error:
        host = urllib.parse.urlsplit(url).netloc
        reason = getattr(error, "reason", error)
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise SyncError(
                f"{host} presented a certificate that was not accepted "
                f"({scrub(str(reason.verify_message))}). Check that the CA "
                "certificate secret is the current one (run 'oh-agent-server.sh ca' "
                "on the Mac) and that the target host name is one the certificate "
                "was issued for."
            ) from None
        raise SyncError(f"cannot reach {host}: {scrub(str(reason))}") from None
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode(errors="replace")


def detail(payload: Any) -> str:
    if isinstance(payload, dict):
        payload = payload.get("detail", payload)
    return scrub(str(payload))[:300]


def require_https(url: str, label: str, allow_http: bool) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise UsageError(f"{label} must be an http(s) URL, got {url!r}")
    if parts.scheme == "http" and not allow_http:
        raise UsageError(
            f"{label} uses plain HTTP, which would send keys and secrets "
            "unencrypted. Use HTTPS, or pass --allow-insecure-http to accept the risk."
        )
    return url.rstrip("/")


def enterprise_url(args: argparse.Namespace, env: Mapping[str, str]) -> str:
    url = args.enterprise_url
    if not url:
        hook = env.get(WEBHOOKS_ENV, "")
        if not hook.endswith(WEBHOOKS_SUFFIX):
            raise UsageError(
                f"cannot work out the Enterprise URL: ${WEBHOOKS_ENV} is not set "
                "here. Pass --enterprise-url."
            )
        url = hook[: -len(WEBHOOKS_SUFFIX)]
    return require_https(url, "Enterprise URL", args.allow_insecure_http)


def proxy_url(args: argparse.Namespace, enterprise: str) -> str:
    if args.llm_base_url:
        return require_https(
            args.llm_base_url, "LLM proxy URL", args.allow_insecure_http
        )
    parts = urllib.parse.urlsplit(enterprise)
    host = parts.hostname or ""
    if host == "app.all-hands.dev":
        return f"{parts.scheme}://llm-proxy.app.all-hands.dev"
    if not host.startswith("app."):
        raise UsageError(
            f"cannot work out the LLM proxy host from {host!r}. Pass --llm-base-url "
            "(the LLM Proxy Hostname from the Admin Console)."
        )
    return f"{parts.scheme}://llm-proxy.{host[len('app.') :]}"


def route_name(model: str) -> str:
    for prefix in OLD_PREFIXES:
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model


class Enterprise:
    def __init__(
        self, url: str, api_key: str, api_key_env: str, org_id: str | None
    ) -> None:
        self.url = url
        self.api_key_env = api_key_env
        self.headers = {"Authorization": f"Bearer {api_key}"}
        if org_id:
            self.headers["X-Org-Id"] = org_id

    def _get(self, path: str) -> tuple[int, Any]:
        status, payload = call("GET", self.url + path, headers=self.headers)
        if status == 401:
            raise SyncError(f"Enterprise rejected ${self.api_key_env} (HTTP 401)")
        return status, payload

    def export_permitted(self) -> bool:
        status, payload = self._get("/api/keys/llm/byor/permitted")
        if status != 200 or not isinstance(payload, dict):
            raise SyncError(f"export check failed (HTTP {status}): {detail(payload)}")
        return bool(payload.get("permitted"))

    def llm_key(self) -> str:
        status, payload = self._get("/api/keys/llm/byor")
        if status == 402:
            raise SyncError(NOT_PERMITTED)
        if status == 403:
            raise SyncError(
                "LLM key export needs the bundled LiteLLM proxy, which is turned "
                f"off on this deployment: {detail(payload)}"
            )
        key = payload.get("key") if isinstance(payload, dict) else None
        if status != 200 or not key:
            raise SyncError(f"could not get the LLM key (HTTP {status})")
        remember(key)
        return key


def list_models(proxy: str, key: str) -> list[str]:
    status, payload = call(
        "GET", proxy + "/v1/models", headers={"Authorization": f"Bearer {key}"}
    )
    if status != 200 or not isinstance(payload, dict):
        raise SyncError(
            f"LLM proxy rejected the key (HTTP {status}): {detail(payload)}"
        )
    return sorted(str(m["id"]) for m in payload.get("data", []) if "id" in m)


class AgentServer:
    def __init__(
        self, url: str, session_key: str, context: ssl.SSLContext | None
    ) -> None:
        self.url = url
        self.headers = {"X-Session-API-Key": session_key}
        self.context = context

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> tuple[int, Any]:
        headers = {**self.headers, **(extra_headers or {})}
        status, payload = call(
            method, self.url + path, headers=headers, body=body, context=self.context
        )
        if status in (401, 403):
            raise SyncError(
                f"the Agent Server rejected the session key (HTTP {status}). Check "
                "that the saved secret holds this server's current key."
            )
        return status, payload

    def expect(self, status: int, payload: Any, step: str) -> Any:
        if status not in (200, 201):
            raise SyncError(f"{step} failed (HTTP {status}): {detail(payload)}")
        return payload

    def validate(self, name: str, llm: dict[str, Any]) -> None:
        status, payload = self.call(
            "POST", f"/api/profiles/{name}/validate", {"llm": llm}
        )
        result = self.expect(status, payload, "LLM check on the Mac")
        if not result.get("valid"):
            error = result.get("error") or {}
            raise SyncError(
                "the Mac could not use this LLM: "
                f"{error.get('type', 'error')}: {scrub(str(error.get('message', '')))}"
            )


def read_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy LLM access and chosen secrets to a remote Agent Server."
    )
    parser.add_argument(
        "--target", help="Agent Server URL, for example https://mac:18100"
    )
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--target-key-env", default=DEFAULT_TARGET_KEY_ENV)
    parser.add_argument(
        "--ca-cert-env",
        metavar="NAME",
        help="env var holding the Agent Server's certificate (PEM) to trust; "
        f"for example {DEFAULT_CA_CERT_ENV}",
    )
    parser.add_argument("--model", help="proxy model route to use (see --list-models)")
    parser.add_argument("--profile-name", default=DEFAULT_PROFILE)
    parser.add_argument("--secret", action="append", default=[], metavar="NAME")
    parser.add_argument("--enterprise-url")
    parser.add_argument("--org-id")
    parser.add_argument("--llm-base-url")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list-models", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-insecure-http", action="store_true")
    return parser.parse_args(argv)


def decode_certificate(value: str) -> str:
    """Accept the PEM text, or the one-line base64 form that Secrets can hold."""
    if "BEGIN CERTIFICATE" in value:
        return value
    try:
        return base64.b64decode(value.strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return ""


def trust_context(
    args: argparse.Namespace, env: Mapping[str, str], target: str
) -> ssl.SSLContext | None:
    if not args.ca_cert_env:
        return None
    if not target.startswith("https://"):
        raise UsageError("--ca-cert-env only applies to an https:// target")
    pem = decode_certificate(env.get(args.ca_cert_env, ""))
    if "BEGIN CERTIFICATE" not in pem:
        raise UsageError(
            f"${args.ca_cert_env} is not set, or does not hold a PEM certificate"
        )
    try:
        return ssl.create_default_context(cadata=pem)
    except ssl.SSLError as error:
        raise UsageError(f"${args.ca_cert_env} is not a valid certificate: {error}")


def read_inputs(args: argparse.Namespace, env: Mapping[str, str]) -> dict[str, Any]:
    if not env.get(args.api_key_env):
        raise UsageError(f"${args.api_key_env} is not set in this environment")
    if not PROFILE_NAME.match(args.profile_name):
        raise UsageError(f"invalid --profile-name {args.profile_name!r}")
    inputs: dict[str, Any] = {"api_key": env[args.api_key_env]}
    remember(inputs["api_key"])

    if not args.list_models:
        if not args.target:
            raise UsageError("--target is required")
        if not env.get(args.target_key_env):
            raise UsageError(f"${args.target_key_env} is not set in this environment")
        inputs["session_key"] = env[args.target_key_env]
        remember(inputs["session_key"])
        inputs["target"] = require_https(
            args.target, "Agent Server URL", args.allow_insecure_http
        )
        inputs["context"] = trust_context(args, env, inputs["target"])
    if not (args.list_models or args.check or args.dry_run) and not args.model:
        raise UsageError("--model is required (run with --list-models to see them)")

    secrets: dict[str, str] = {}
    problems = []
    for name in dict.fromkeys(args.secret):
        if not SECRET_NAME.match(name):
            problems.append(f"{name}: not a valid secret name")
        elif name in (DEFAULT_API_KEY_ENV, args.api_key_env, args.target_key_env):
            problems.append(f"{name}: never copied")
        elif not env.get(name):
            problems.append(f"{name}: not set in this environment")
        else:
            secrets[name] = env[name]
            remember(env[name])
    if problems:
        raise UsageError("cannot copy secrets:\n  " + "\n  ".join(problems))
    inputs["secrets"] = secrets
    return inputs


def run(args: argparse.Namespace, env: Mapping[str, str]) -> int:
    inputs = read_inputs(args, env)
    enterprise = Enterprise(
        enterprise_url(args, env), inputs["api_key"], args.api_key_env, args.org_id
    )
    proxy = proxy_url(args, enterprise.url)

    if not enterprise.export_permitted():
        raise SyncError(NOT_PERMITTED)
    say("ok   LLM key export is allowed")

    if args.list_models:
        models = list_models(proxy, enterprise.llm_key())
        say(f"ok   {len(models)} models at {proxy} (the list may be partial):")
        for model in models:
            say(f"     {model}")
        return 0

    mac = AgentServer(inputs["target"], inputs["session_key"], inputs["context"])
    status, payload = mac.call("GET", "/api/profiles")
    mac.expect(status, payload, "reaching the Mac")
    say(f"ok   Mac Agent Server reachable at {mac.url}")

    name = args.profile_name
    if args.check:
        return check(enterprise, mac, name)

    secrets = inputs["secrets"]
    if args.dry_run:
        say(
            f"plan LLM profile {name!r}: {PROXY_PREFIX}{args.model or '<model>'} "
            f"at {proxy}"
        )
        say(f"plan secrets: {', '.join(secrets) or 'none'}")
        say("dry run: nothing was changed")
        return 0

    key = enterprise.llm_key()
    route = route_name(args.model)
    models = list_models(proxy, key)
    say(f"ok   the key works at {proxy}")
    if route not in models:
        say(
            f"warn {route!r} is not in the proxy's list (it may be partial); "
            "the Mac check decides"
        )

    llm = {"model": PROXY_PREFIX + route, "base_url": proxy, "api_key": key}
    mac.validate(name, llm)
    say("ok   the Mac completed a test request through the proxy")
    for secret_name, value in secrets.items():
        status, payload = mac.call(
            "PUT",
            "/api/settings/secrets",
            {
                "name": secret_name,
                "value": value,
                "description": "Copied from Enterprise",
            },
        )
        mac.expect(status, payload, f"saving secret {secret_name}")
    say(f"ok   saved {len(secrets)} secret(s) on the Mac")
    status, payload = mac.call(
        "POST", f"/api/profiles/{name}", {"llm": llm, "include_secrets": True}
    )
    mac.expect(status, payload, "saving the LLM profile")
    status, payload = mac.call("POST", f"/api/profiles/{name}/activate")
    mac.expect(status, payload, "activating the LLM profile")
    say(f"ok   LLM profile {name!r} saved and active on the Mac")
    return 0


def check(enterprise: Enterprise, mac: AgentServer, name: str) -> int:
    key = enterprise.llm_key()
    status, payload = mac.call(
        "GET", f"/api/profiles/{name}", extra_headers={"X-Expose-Secrets": "plaintext"}
    )
    if status == 404:
        raise SyncError(f"the Mac has no profile {name!r}. Run the sync first.")
    config = mac.expect(status, payload, "reading the Mac profile")["config"]
    stored = config.get("api_key")
    remember(stored)
    if stored != key:
        raise SyncError(
            "the Mac holds an older LLM key (it was refreshed or replaced). "
            "Run the sync again."
        )
    say("ok   the Mac holds the current LLM key")
    mac.validate(name, config)
    say("ok   the Mac completed a test request through the proxy")
    return 0


def main(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> int:
    try:
        return run(read_args(argv), os.environ if env is None else env)
    except UsageError as error:
        say(f"error: {error}")
        return 2
    except SyncError as error:
        say(f"FAIL {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
