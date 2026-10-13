#!/usr/bin/env python3
"""Find super admins, mint new ones, and create organizations on OpenHands Enterprise.

Run with no subcommand for a read-only preflight. Every subcommand that changes
anything prints the request it would send and stops, unless you pass --yes.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import requests


DEFAULT_BASE_URL = "https://app.all-hands.dev"
REQUEST_TIMEOUT = 60

SUPER_ADMIN_HINT = """\
Listing super admins is restricted to super admins by default. Either:
  - run this tool with an API key that belongs to a super admin, or
  - on OHE 1.62.0 or later, enable "Enable OEM User Creation Flow" in the
    Replicated admin console (sets USER_PROVISIONING_ENABLED, which lets any
    signed-in user list them), or
  - ask the database (see README.md, "Find the Super Admin Without the API")."""


class ApiError(Exception):
    def __init__(self, method: str, path: str, status: int, detail: str):
        super().__init__(f"{method} {path} -> HTTP {status}: {detail}")
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail


class Client:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        org_id: str | None = None,
    ) -> Any:
        headers = {"X-Org-Id": org_id} if org_id else {}
        resp = self.session.request(
            method,
            f"{self.base_url}{path}",
            json=body,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code >= 400:
            try:
                detail = str(resp.json().get("detail", resp.text))
            except ValueError:
                detail = resp.text
            raise ApiError(method, path, resp.status_code, detail[:300])
        return resp.json() if resp.content else None


def emit(data: Any) -> None:
    print(json.dumps(data, indent=2))


def redact(body: dict[str, Any]) -> dict[str, Any]:
    return {k: ("***" if k == "password" else v) for k, v in body.items()}


def mutate(
    client: Client,
    args: argparse.Namespace,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    org_id: str | None = None,
) -> Any:
    """Send a state-changing request, or just describe it when --yes is absent."""
    if not args.yes:
        print(f"[dry run] {method} {client.base_url}{path}")
        if org_id:
            print(f"[dry run] X-Org-Id: {org_id}")
        if body:
            print(f"[dry run] body: {json.dumps(redact(body))}")
        print("[dry run] Nothing was sent. Re-run with --yes to apply.")
        return None
    return client.request(method, path, body=body, org_id=org_id)


def list_super_admins(client: Client) -> list[dict[str, Any]]:
    return client.request("GET", "/api/admin/super-admins")["super_admins"]


def list_orgs(client: Client) -> list[dict[str, Any]]:
    orgs: list[dict[str, Any]] = []
    page_id = None
    while True:
        path = "/api/organizations?limit=100"
        if page_id:
            path += f"&page_id={page_id}"
        page = client.request("GET", path)
        orgs.extend(page["items"])
        page_id = page.get("next_page_id")
        if not page_id:
            return orgs


def cmd_status(client: Client, _args: argparse.Namespace) -> int:
    me = client.request("GET", "/api/v1/users/me")
    print(f"Signed in as: {me.get('email')} (user id {me.get('id')})")
    print(f"Current org:  {me.get('org_name')} ({me.get('org_id')})")
    print(f"Role there:   {me.get('role')}")

    print("\nOrganizations you belong to:")
    orgs = list_orgs(client)
    for org in orgs:
        kind = "personal" if org["is_personal"] else "team"
        print(f"  {org['id']}  {kind:8}  {org['name']}")
    print(
        "  (Only orgs you are a member of are listed. A super admin is not "
        "automatically a member of the orgs it creates.)"
    )

    print("\nSuper admins:")
    try:
        admins = list_super_admins(client)
    except ApiError as exc:
        if exc.status not in (401, 403):
            raise
        print(f"  Could not list them (HTTP {exc.status}).\n")
        print(SUPER_ADMIN_HINT)
        return 0
    for admin in admins:
        mark = "  <- you" if admin["user_id"] == me.get("id") else ""
        print(f"  {admin['user_id']}  {admin['email']}{mark}")
    if not admins:
        print("  none")
    return 0


def cmd_super_admins(client: Client, args: argparse.Namespace) -> int:
    if args.action == "list":
        emit(list_super_admins(client))
        return 0
    if args.action == "grant":
        body = {"email": args.email} if args.email else {"user_id": args.user_id}
        result = mutate(client, args, "POST", "/api/admin/super-admins", body)
    else:
        path = f"/api/admin/super-admins/{args.user_id}"
        result = mutate(client, args, "DELETE", path)
    if result:
        emit(result)
    return 0


def cmd_create_org(client: Client, args: argparse.Namespace) -> int:
    body = {
        "name": args.name,
        "contact_name": args.contact_name,
        "contact_email": args.contact_email,
    }
    org = mutate(client, args, "POST", "/api/organizations", body)
    if org:
        print(f"Created org {org['name']!r} with id {org['id']}")
        print("It has no members yet. Add an owner with `invite` or `provision-user`.")
    return 0


def cmd_invite(client: Client, args: argparse.Namespace) -> int:
    path = f"/api/organizations/{args.org_id}/members/invite"
    body = {"emails": args.emails, "role": args.role}
    result = mutate(client, args, "POST", path, body)
    if not result:
        return 0
    for inv in result["successful"]:
        print(f"Invited {inv['email']} as {inv['role']}")
        if not result["email_delivery_configured"] and inv.get("invite_url"):
            print(f"  No email provider is configured. Share: {inv['invite_url']}")
    for fail in result["failed"]:
        print(f"Failed: {fail}")
    if result["successful"]:
        print(
            "Existing users are added at their next login; if they were on their "
            "personal org they land in this one."
        )
    return 1 if result["failed"] else 0


def write_private_file(path: Path, data: dict[str, Any]) -> None:
    mode = stat.S_IRUSR | stat.S_IWUSR
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


def cmd_provision_user(client: Client, args: argparse.Namespace) -> int:
    body: dict[str, Any] = {"email": args.email, "role": args.role}
    if args.password:
        body["password"] = args.password
    path = "/api/organizations/provision-user"
    result = mutate(client, args, "POST", path, body, org_id=args.org_id)
    if not result:
        return 0
    out = Path(args.credentials_file or f"provisioned-{args.email}.json")
    write_private_file(out, result)
    print(f"Provisioned {result['email']} ({result.get('action', 'ok')}).")
    print(f"Password and API key written to {out} (mode 0600). Hand off, then delete.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key", default=os.environ.get("OH_API_KEY"))
    parser.add_argument(
        "--base-url", default=os.environ.get("OH_API_BASE", DEFAULT_BASE_URL)
    )
    sub = parser.add_subparsers(dest="command")

    def add_yes(p: argparse.ArgumentParser) -> None:
        p.add_argument("--yes", action="store_true", help="Actually send the request")

    sub.add_parser("status", help="Read-only preflight (default)")

    sa = sub.add_parser("super-admins", help="List, grant, or revoke super admins")
    sa_sub = sa.add_subparsers(dest="action", required=True)
    sa_sub.add_parser("list")
    grant = sa_sub.add_parser("grant")
    who = grant.add_mutually_exclusive_group(required=True)
    who.add_argument("--email")
    who.add_argument("--user-id")
    add_yes(grant)
    revoke = sa_sub.add_parser("revoke")
    revoke.add_argument("user_id")
    add_yes(revoke)

    co = sub.add_parser("create-org", help="Create a team org (super admin only)")
    co.add_argument("--name", required=True)
    co.add_argument("--contact-name", required=True)
    co.add_argument("--contact-email", required=True)
    add_yes(co)

    inv = sub.add_parser("invite", help="Invite people to an org")
    inv.add_argument("--org-id", required=True)
    inv.add_argument("--role", choices=["member", "admin", "owner"], default="member")
    inv.add_argument("emails", nargs="+")
    add_yes(inv)

    pu = sub.add_parser(
        "provision-user", help="Create a user in an org (needs the OEM user flow)"
    )
    pu.add_argument("--org-id", required=True)
    pu.add_argument("--email", required=True)
    pu.add_argument("--role", choices=["member", "admin", "owner"], default="member")
    pu.add_argument("--password", help="Default: the server generates one")
    pu.add_argument("--credentials-file", help="Default: provisioned-<email>.json")
    add_yes(pu)
    return parser


COMMANDS = {
    None: cmd_status,
    "status": cmd_status,
    "super-admins": cmd_super_admins,
    "create-org": cmd_create_org,
    "invite": cmd_invite,
    "provision-user": cmd_provision_user,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.api_key:
        print("error: set OH_API_KEY or pass --api-key", file=sys.stderr)
        return 2
    client = Client(args.base_url, args.api_key)
    try:
        return COMMANDS[args.command](client, args)
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
