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
``--admin-token`` / ``$OH_ADMIN_TOKEN``.

    export OH_ADMIN_TOKEN=...                     # superadmin bearer token
    export OH_BASE_URL=https://openhands.example.com
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

This script only reads back what the API returns; it does not attempt any
cleanup. If a provisioning call fails after the account is partially created,
the server compensates on its own (see the Enterprise docs).

WARNING: the provision-user response contains a plaintext password and an API
key. Treat stdout as sensitive; do not paste it into logs or tickets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--base-url",
        default=os.environ.get("OH_BASE_URL"),
        help="Enterprise base URL, e.g. https://openhands.example.com "
        "(default: $OH_BASE_URL).",
    )
    p.add_argument(
        "--admin-token",
        default=os.environ.get("OH_ADMIN_TOKEN"),
        help="Superadmin bearer token (default: $OH_ADMIN_TOKEN).",
    )
    p.add_argument(
        "--org-name",
        help="Create a new org with this name and provision into it.",
    )
    p.add_argument(
        "--org-id",
        help="Provision into an existing org id instead of creating one. "
        "Mutually exclusive with --org-name.",
    )
    p.add_argument(
        "--contact-name",
        help="Org contact name (required with --org-name).",
    )
    p.add_argument(
        "--contact-email",
        help="Org contact email (required with --org-name).",
    )
    p.add_argument(
        "--user",
        dest="users",
        action="append",
        default=[],
        metavar="EMAIL",
        help="Email of a user to provision. Repeat for multiple users.",
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
    return p.parse_args()


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


def main() -> int:
    args = parse_args()

    if not args.base_url:
        sys.exit("error: set --base-url or $OH_BASE_URL")
    if not args.admin_token:
        sys.exit("error: set --admin-token or $OH_ADMIN_TOKEN")
    if bool(args.org_name) == bool(args.org_id):
        sys.exit("error: provide exactly one of --org-name or --org-id")
    if args.org_name and not (args.contact_name and args.contact_email):
        sys.exit("error: --org-name requires --contact-name and --contact-email")
    if not args.users:
        sys.exit("error: provide at least one --user EMAIL")

    base_url = args.base_url.rstrip("/")
    admin_headers = {
        "Authorization": f"Bearer {args.admin_token}",
        "Content-Type": "application/json",
    }

    # 1. Resolve the target org: create a fresh one, or reuse an existing id.
    if args.org_name:
        org = create_org(
            base_url,
            admin_headers,
            args.org_name,
            args.contact_name,
            args.contact_email,
        )
        org_id = org["id"]
        print(f"created org: {org['name']} ({org_id})")
    else:
        org_id = args.org_id
        print(f"using existing org: {org_id}")

    # 2. Provision each user into the org, then 3. verify their API key works.
    for email in args.users:
        provisioned = provision_user(
            base_url, args.admin_token, org_id, email, args.role, args.reissue_api_key
        )
        status_code = provisioned.pop("_status_code", None)
        action = provisioned.get("action", "?")
        print(f"\nprovisioned {email} as {provisioned['role']}: ", end="")
        print(f"action={action} (HTTP {status_code})")
        # The password/api_key are sensitive; printed here only for the demo.
        print(json.dumps(provisioned, indent=2))

        me = whoami_as_user(base_url, provisioned["api_key"], org_id)
        print(f"  api_key verified -> role={me['role']} in org {me['org_id']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
