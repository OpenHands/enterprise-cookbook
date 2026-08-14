# Provision users into an organization (OpenHands Enterprise)

**Question:** *Can a backend system create organizations and users in OpenHands
without a human clicking through the sign-up UI, and get an API key to act on
each user's behalf?*

**Short answer:** **Yes — on OpenHands Enterprise, as a superadmin.** This is
the building block for **OEM integrations**, where your own user-management
system owns the user/org lifecycle and mirrors it into OpenHands.

> This example is **Enterprise-only**. The endpoints below do not exist on
> OpenHands Cloud (SaaS) or the open-source build. The provisioning endpoint is
> also **off by default** — an operator must enable it (see
> [Enabling the feature](#enabling-the-feature)).

## The flow

Everything is done **as the superadmin**:

```text
1. POST /api/organizations                    -> create an org (superadmin only)
2. POST /api/organizations/provision-user     -> create a user in that org
   (X-Org-Id: <org-id>)                           (bypasses email/TOS/OAuth)
3. use response.api_key as a Bearer token     -> act on the user's behalf
```

The provision-user response returns the new user's `email`, a `password`
(generated if you did not supply one, and **only on a true create**), and an
`api_key` **bound to the target org**. Step 3 is the payoff: your system stores
that key and uses it to drive OpenHands (create conversations, etc.) for that
user.

### Idempotency (safe to re-run)

`provision-user` is **idempotent**. Re-running it for an email that already
exists does *not* fail or duplicate the account — the server pre-checks Keycloak
and its own user store, skips the create steps, ensures org membership, and
resolves an API key. The response's `action` field (and the HTTP status) tells
you which branch was taken:

| `action`         | HTTP | Meaning                                                        | `password` |
|------------------|------|----------------------------------------------------------------|------------|
| `created`        | 201  | Brand-new user created and added to the org                    | set        |
| `added_to_org`   | 200  | User already existed; added to this org                        | `null`     |
| `reprovisioned`  | 200  | User already existed and was already a member; key resolved    | `null`     |

On both idempotent paths the existing Keycloak **password is never rotated**, so
`password` is `null`. By default the endpoint also returns the user's
**existing** API key (matching `api_key_name`) rather than minting a new one.
Pass `reissue_api_key: true` (or `--reissue-api-key` on the script) to delete the
old key and mint a fresh one instead — use this for the "user lost their key"
recovery path.

## Who is the superadmin?

The **superadmin** is the instance-level admin — the only role that can create
organizations and provision users at the instance level. On a fresh install,
the superadmin is simply the **first user to authenticate**. Supply that user's
bearer token as `$OH_ADMIN_TOKEN`.

A superadmin can list its peers with `GET /api/admin/super-admins`, but that
endpoint itself requires superadmin, so it can't be used by an unprivileged user
to *discover* who the superadmin is. There is no self-service "am I the
superadmin?" endpoint today.

## The two calls that matter

Create the org (the superadmin is **not** added as a member):

```python
requests.post(
    f"{base_url}/api/organizations",
    headers={"Authorization": f"Bearer {admin_token}"},
    json={
        "name": "Acme Corp",
        "contact_name": "Ada Lovelace",
        "contact_email": "ada@example.com",
    },
)
# -> { "id": "<org-id>", "name": "Acme Corp", ... }
```

Provision a user into it (target org travels in the **`X-Org-Id` header**, not
the URL):

```python
requests.post(
    f"{base_url}/api/organizations/provision-user",
    headers={
        "Authorization": f"Bearer {admin_token}",
        "X-Org-Id": org_id,
    },
    json={"email": "new-user@example.com", "role": "member"},
)
# 201 Created ->
#   { "email": "...", "password": "...", "api_key": "sk-...",
#     "user_id": "...", "org_id": "...", "role": "member",
#     "created": true, "action": "created" }
```

`role` is one of `member` (default), `admin`, or `owner`. You can also pass an
optional `password` (must satisfy the realm policy), `api_key_name`, and
`reissue_api_key`.

Re-running the same call is safe (see [Idempotency](#idempotency-safe-to-re-run)
above): an existing user returns HTTP 200 with `"password": null`, `"created":
false`, and `action` of `added_to_org` or `reprovisioned`. Add
`"reissue_api_key": true` to rotate the key instead of returning the existing
one.

## Running

```bash
pip install requests

export OH_BASE_URL=https://openhands.example.com
export OH_ADMIN_TOKEN=...          # superadmin bearer token

# Create a new org and provision two members into it
python provision_users.py \
    --org-name "Acme Corp" \
    --contact-name "Ada Lovelace" --contact-email ada@example.com \
    --user alice@example.com --user bob@example.com \
    --role member

# Or provision into an existing org
python provision_users.py \
    --org-id 0f9c3b2a-1d4e-4c8a-9b6f-2e7d5a1c3b4d \
    --user carol@example.com --role admin

# Recover a user who lost their key: re-run with --reissue-api-key to rotate it
python provision_users.py \
    --org-id 0f9c3b2a-1d4e-4c8a-9b6f-2e7d5a1c3b4d \
    --user carol@example.com --reissue-api-key
```

The script creates/reuses the org, provisions each user, and then calls
`GET /api/organizations/{org_id}/me` **with the freshly minted key** to prove
it is live and scoped to the right org. It prints the `action` and HTTP status
for each user so you can see whether it was a create or an idempotent re-provision.

## Enabling the feature

The provision-user endpoint is registered only when the operator turns it on:

```yaml
# Helm values
userProvisioning:
  enabled: true      # sets USER_PROVISIONING_ENABLED=true in the deployment
```

When disabled, `POST /api/organizations/provision-user` returns `404`. Org
creation and superadmin management are always available.

## Troubleshooting

**`401 Invalid service API key`** — You are calling the wrong endpoint. That
error comes from the internal **service-to-service** routes under
`/api/service/*` (e.g. `POST /api/service/users/{user_id}/orgs/{org_id}/api-keys`),
which authenticate with the `X-Service-API-Key` header checked against the
operator-provisioned `AUTOMATIONS_SERVICE_KEY` shared secret — a key meant for
the internal automations service, not for admins. User provisioning is a
different flow: call `POST /api/organizations/provision-user` with an
`Authorization: Bearer <superadmin-token>` header (and `X-Org-Id`). It never uses
`X-Service-API-Key`. Note the near-identical header names: `X-Session-API-Key`
(per-sandbox agent-server auth, used in other examples here) and
`X-Service-API-Key` (internal service auth) are both distinct from the plain
`Authorization: Bearer` token this endpoint expects.

**`404 Not Found`** on `provision-user` — the feature is disabled; see
[Enabling the feature](#enabling-the-feature).

**`403 Forbidden`** — the caller lacks the `PROVISION_USER` permission (org
`owner`/`admin` or an instance super role), or you targeted a personal
workspace (provisioning into a personal workspace is rejected).

## Security

- **The provision-user response is a secret.** The `password` is returned **only
  here** (the endpoint bypasses the email-based set-password flow) and the
  `api_key` grants access as the new user. Do not log the response; hand the
  credential to the user out-of-band; always use TLS. This example prints the
  full response only to make the shape obvious — redact it in real use.
- **Guard the superadmin token.** Anyone holding it can create orgs, mint users,
  and grant/revoke superadmin. Treat it like a root credential.
- A superadmin does **not** inherit org-scoped powers (secrets, billing, LLM
  settings, org deletion) in the orgs it creates.

## Reference

- Enterprise docs: **User Provisioning API** (docs.all-hands.dev → Enterprise)
- Underlying PRs: OpenHands/OpenHands
  [#14864](https://github.com/OpenHands/OpenHands/pull/14864) (provisioning
  endpoint),
  [#14937](https://github.com/OpenHands/OpenHands/pull/14937) (super roles), and
  [#117](https://github.com/OpenHands/enterprise/pull/117) (idempotent
  re-provisioning: `reissue_api_key`, the `action` field, and 200-vs-201 status).
