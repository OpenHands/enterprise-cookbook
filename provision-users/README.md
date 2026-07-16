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
(generated if you did not supply one), and an `api_key` **bound to the target
org**. Step 3 is the payoff: your system stores that key and uses it to drive
OpenHands (create conversations, etc.) for that user.

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
# -> { "email": "...", "password": "...", "api_key": "sk-...",
#      "user_id": "...", "org_id": "...", "role": "member" }
```

`role` is one of `member` (default), `admin`, or `owner`. You can also pass an
optional `password` (must satisfy the realm policy) and `api_key_name`.

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
```

The script creates/reuses the org, provisions each user, and then calls
`GET /api/organizations/{org_id}/me` **with the freshly minted key** to prove
it is live and scoped to the right org.

## Enabling the feature

The provision-user endpoint is registered only when the operator turns it on:

```yaml
# Helm values
userProvisioning:
  enabled: true      # sets USER_PROVISIONING_ENABLED=true in the deployment
```

When disabled, `POST /api/organizations/provision-user` returns `404`. Org
creation and superadmin management are always available.

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
  endpoint) and
  [#14937](https://github.com/OpenHands/OpenHands/pull/14937) (super roles).
