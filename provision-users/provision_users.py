#!/usr/bin/env python3
"""Provision users into an organization on OpenHands Enterprise (superadmin flow).

This is an **OpenHands Enterprise** recipe. It does not work against OpenHands
Cloud or the open-source build: the endpoints below only exist on Enterprise,
and the user-provisioning endpoint is off unless the operator enabled it
(Helm ``userProvisioning.enabled`` / env ``USER_PROVISIONING_ENABLED``).

The flow, as a superadmin:

    1. POST /api/organizations                       -> create an org
    2. POST /api/organizations/provision-user        -> create a user in that org
       (target org supplied via the ``X-Org-Id`` header)
    3. use the returned api_key as a Bearer token    -> act on that user's behalf

Step 3 is what makes provisioning useful for OEM integrations: the response
hands back an API key bound to the target org, so your own system can drive
OpenHands on behalf of each provisioned user without a human ever touching the
UI.

Auth: every call in steps 1-2 is made *as the superadmin*, with a normal
``Authorization: Bearer <token>`` header — NOT the internal ``X-Service-API-Key``
header (see the "Wrong endpoint" note below). On a fresh install the superadmin
is simply the first user to authenticate. Supply that user's token via
``--api-key`` / ``$OH_API_KEY``.

    export OH_API_KEY=...                         # superadmin bearer token
    export OH_API_BASE=https://openhands.example.com
    python provision_users.py                     # demo: new org, one new user
    python provision_users.py --org-name "Acme Corp" \
        --contact-name "Ada Lovelace" --contact-email ada@example.com \
        --user alice@example.com --user bob@example.com --role member

Idempotency: the provision-user endpoint is idempotent. Re-running it for an
email that already exists is safe — the server skips the create steps, ensures
org membership, and by default returns the *existing* API key. The response
tells you which branch was taken:

    - action="created"       (HTTP 201) brand-new user; ``password`` is set
    - action="added_to_org"  (HTTP 200) existing user added to this org
    - action="reprovisioned" (HTTP 200) existing user, already a member;
                                        only the API key was resolved

On both idempotent paths ``password`` is ``None`` (the existing Keycloak
password is never rotated). Pass ``--reissue-api-key`` to mint a fresh key
(deleting the old one of the same name) instead of returning the existing one.

Wrong endpoint? If you get 401 ``Invalid service API key``, you are calling the
internal ``/api/service/*`` routes (guarded by the operator's
``AUTOMATIONS_SERVICE_KEY`` shared secret via the ``X-Service-API-Key`` header),
not this admin flow. User provisioning uses ``/api/organizations/provision-user``
with an ``Authorization: Bearer`` superadmin token.

Demo mode: with no --org-name, --org-id, or --user, the script creates a
throwaway org and user, verifies the key, and deletes both again. Pass --keep to
leave them in place. Naming any org or user yourself also keeps them.

If a provisioning call fails after the account is partially created, the server
compensates on its own (see the Enterprise docs).

The provision-user response contains a plaintext password and an API key, so
the script masks both unless you pass ``--show-secrets``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

import requests


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--api-key",
        default=os.environ.get("OH_API_KEY"),
        help="Superadmin bearer token (default: $OH_API_KEY).",
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("OH_API_BASE"),
        help="Enterprise base URL, e.g. https://openhands.example.com "
        "(default: $OH_API_BASE).",
    )
    p.add_argument(
        "--org-name",
        help="Create a new org with this name and provision into it "
        "(default: a generated demo name when --org-id is not given).",
    )
    p.add_argument(
        "--org-id",
        help="Provision into an existing org id instead of creating one. "
        "Mutually exclusive with --org-name.",
    )
    p.add_argument(
        "--contact-name",
        default="Cookbook Demo",
        help="Contact name for a new org (default: %(default)s).",
    )
    p.add_argument(
        "--contact-email",
        default="cookbook-demo@example.com",
        help="Contact email for a new org (default: %(default)s).",
    )
    p.add_argument(
        "--user",
        dest="users",
        action="append",
        default=[],
        metavar="EMAIL",
        help="Email of a user to provision. Repeat for multiple users "
        "(default: one generated demo user).",
    )
    p.add_argument(
        "--role",
        default="member",
        choices=["member", "admin", "owner"],
        help="Org role for each provisioned user (default: member).",
    )
    p.add_argument(
        "--reissue-api-key",
        action="store_true",
        help="On re-provision of an existing user, mint a fresh API key "
        "(deleting the old one of the same name) instead of returning the "
        "existing key. Ignored on a first-time create.",
    )
    p.add_argument(
        "--keep",
        action="store_true",
        help="In demo mode, leave the org and user in place instead of deleting them.",
    )
    p.add_argument(
        "--show-secrets",
        action="store_true",
        help="Print the provisioned password and API key in full instead of "
        "masking them.",
    )
    return p.parse_args()


def mask(secret: str | None) -> str | None:
    """Keep only the last four characters of a credential."""
    if not secret:
        return secret
    return f"****{secret[-4:]}"


def create_org(
    base_url: str, headers: dict, name: str, contact_name: str, contact_email: str
) -> dict:
    """POST /api/organizations -> the created org (superadmin only).

    The superadmin is deliberately NOT added as a member of the org it
    creates; the org starts empty and is populated via provision-user.
    """
    resp = requests.post(
        f"{base_url}/api/organizations",
        headers=headers,
        json={
            "name": name,
            "contact_name": contact_name,
            "contact_email": contact_email,
        },
    )
    resp.raise_for_status()
    return resp.json()


def provision_user(
    base_url: str,
    admin_token: str,
    org_id: str,
    email: str,
    role: str,
    reissue_api_key: bool = False,
) -> dict:
    """POST /api/organizations/provision-user -> the new user's credentials.

    The target org is taken from the ``X-Org-Id`` header, not the URL. The
    response includes ``password`` (only ever returned on a true create) and an
    ``api_key`` bound to ``org_id``.

    Idempotent: re-running for an existing email returns the existing key by
    default. Set ``reissue_api_key=True`` to rotate the key instead. The
    response's ``action`` field ("created" / "added_to_org" / "reprovisioned")
    reports which branch the server took; the HTTP status is 201 on a true
    create and 200 on an idempotent re-provision.
    """
    resp = requests.post(
        f"{base_url}/api/organizations/provision-user",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-Org-Id": org_id,
            "Content-Type": "application/json",
        },
        json={"email": email, "role": role, "reissue_api_key": reissue_api_key},
    )
    resp.raise_for_status()
    result = resp.json()
    result["_status_code"] = resp.status_code
    return result


def whoami_as_user(base_url: str, api_key: str, org_id: str) -> dict:
    """Prove the provisioned key works by reading the user's own membership.

    ``GET /api/organizations/{org_id}/me`` echoes the caller's role and
    permissions in that org -- a cheap, read-only way to confirm the key is
    live and correctly scoped before you drive real work with it.
    """
    resp = requests.get(
        f"{base_url}/api/organizations/{org_id}/me",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    resp.raise_for_status()
    return resp.json()


def delete_org(base_url: str, owner_api_key: str, org_id: str) -> None:
    """DELETE /api/organizations/{org_id}, authenticated as an org *owner*.

    A superadmin has no org-scoped powers, so even org deletion needs the key of
    a user holding the ``owner`` role in that org.
    """
    resp = requests.delete(
        f"{base_url}/api/organizations/{org_id}",
        headers={"Authorization": f"Bearer {owner_api_key}", "X-Org-Id": org_id},
    )
    resp.raise_for_status()


def delete_user(base_url: str, admin_token: str, user_id: str, org_id: str) -> None:
    """DELETE /api/admin/users/{user_id} as the superadmin."""
    resp = requests.delete(
        f"{base_url}/api/admin/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"org_id": org_id},
    )
    resp.raise_for_status()


def clean_up(
    base_url: str,
    admin_token: str,
    org_id: str,
    created_org: bool,
    created_users: list[dict],
    suffix: str,
) -> None:
    """Delete only what this run created: its org, then its users."""
    if created_org:
        owner = next((u for u in created_users if u["role"] == "owner"), None)
        if owner is None:
            owner = provision_user(
                base_url, admin_token, org_id, f"cleanup-{suffix}@example.com", "owner"
            )
            created_users.append(owner)
        delete_org(base_url, owner["api_key"], org_id)
        print(f"\ndeleted org {org_id}")
    for user in created_users:
        delete_user(base_url, admin_token, user["user_id"], org_id)
        print(f"deleted user {user['email']}")


def main() -> int:
    args = parse_args()

    if not args.base_url:
        sys.exit("error: set --base-url or $OH_API_BASE")
    if not args.api_key:
        sys.exit("error: set --api-key or $OH_API_KEY")
    if args.org_name and args.org_id:
        sys.exit("error: provide at most one of --org-name or --org-id")

    demo = not (args.org_name or args.org_id or args.users)
    suffix = uuid.uuid4().hex[:8]
    org_name = args.org_name or f"Provision Users Demo {suffix}"
    users = args.users or [f"demo-user-{suffix}@example.com"]

    base_url = args.base_url.rstrip("/")
    admin_headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
    }

    # 1. Resolve the target org: create a fresh one, or reuse an existing id.
    created_org = not args.org_id
    if created_org:
        org = create_org(
            base_url,
            admin_headers,
            org_name,
            args.contact_name,
            args.contact_email,
        )
        org_id = org["id"]
        print(f"created org: {org['name']} ({org_id})")
    else:
        org_id = args.org_id
        print(f"using existing org: {org_id}")

    # 2. Provision each user into the org, then 3. verify their API key works.
    created_users = []
    for email in users:
        provisioned = provision_user(
            base_url, args.api_key, org_id, email, args.role, args.reissue_api_key
        )
        status_code = provisioned.pop("_status_code", None)
        if provisioned.get("action") == "created":
            created_users.append(provisioned)
        action = provisioned.get("action", "?")
        print(f"\nprovisioned {email} as {provisioned['role']}: ", end="")
        print(f"action={action} (HTTP {status_code})")
        shown = dict(provisioned)
        if not args.show_secrets:
            shown["password"] = mask(shown.get("password"))
            shown["api_key"] = mask(shown.get("api_key"))
        print(json.dumps(shown, indent=2))

        me = whoami_as_user(base_url, provisioned["api_key"], org_id)
        print(f"  api_key verified -> role={me['role']} in org {me['org_id']}")

    if demo and not args.keep:
        clean_up(base_url, args.api_key, org_id, created_org, created_users, suffix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
