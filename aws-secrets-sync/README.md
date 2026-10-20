# AWS Secrets Bootstrap

Import explicitly selected AWS Secrets Manager strings into OpenHands Enterprise
organization secrets or one authenticated user's personal secrets. Use this as the
starting point for a customer-owned import job, including an existing
Keeper-to-AWS workflow.

> [!WARNING]
> Prototype: bootstrap only. Known existing destinations are refused; no deletion.
> The personal API is an upsert, so personal mode requires exclusive write access
> during the run to prevent a concurrent same-name write from being overwritten.
> Source-version changes fail instead of replacing values. Production rotation
> requires qualified replacement, concurrency, retry, and runtime refresh behavior.
> This example is not an operated AWS connector.

## How It Works

The script verifies the API key's organization binding using `bound_org_id`. Personal
mode also requires its user ID to match `expected_user_id`. It lists destination
names, then describes each allowlisted AWS
secret's `AWSCURRENT` version. Dry run stops there without reading secret values.
With `--apply`, it reads missing destinations' exact current versions, optionally
extracts one top-level JSON string field, and creates the selected scope's secrets. The AWS reader and journal are shared;
`OpenHands` and `PersonalOpenHands` contain the destination-specific API behavior.
`destination_for()` is the adapter selection point for customer modifications.

A private SQLite journal records the AWS version only after OpenHands acknowledges
creation. Before sending a create, it records a pending operation. Timeouts,
conflicts, and rejected requests leave that operation pending for operator review.
A later run refuses to guess whether the value was installed. No values or tokens
are intentionally logged or written to state; values exist in process memory and
are sent over HTTPS. Disable HTTP/SDK wire logging in the host environment.

## Prerequisites

- Python 3.10+, Linux or macOS, and `pip install -r requirements.txt`.
- An OpenHands Enterprise deployment with organization-secret endpoints. Initial
  end-to-end qualification used Enterprise 1.68.0 with runtime 1.50.1. The
  instance included candidate changes; version numbers alone do not prove
  compatibility. Qualify your installation against the contracts below.
- An organization-bound OpenHands API key. Organization mode requires owner/admin
  permission to create shared secrets. Personal mode uses the intended user's own
  key and requires their independently verified user UUID. This script never
  provisions users, creates keys, or changes permissions.
- AWS credentials from the standard Boto3 credential chain. Prefer a workload role
  in deployment; `--profile` selects a local profile during development.
- Read access to only the mapped ARNs: `secretsmanager:DescribeSecret` and
  `secretsmanager:GetSecretValue`, plus `kms:Decrypt` for applicable customer keys.
  No AWS write or `ListSecrets` permissions are needed by the sync job.

## Source Scope And IAM

The mapping file is the complete allowlist. Every entry is attempted on each run;
only entries needing creation read values. Multiple fields from the same JSON
secret need separate mappings and distinct destination names. Include every
intended secret or field explicitly. The job does not discover an AWS account,
expand a prefix, or copy unrelated credentials. Review the mapping with the vault
owner before granting access. Secrets shared at organization scope are available
to that organization's permitted consumers, rather than a single conversation.
Personal scope targets a single user in the configured organization context.
AWS names/tags do not automatically identify an OpenHands user: maintain that
mapping explicitly, and scope the AWS read role to that user's allowlisted ARNs.

Start from [`iam-policy.example.json`](./iam-policy.example.json), replacing its
resource list with the exact full ARNs in your mapping. It grants no AWS writes
or discovery. When using a customer-managed KMS key, grant `kms:Decrypt` on that
specific key in a separate statement and ensure its key policy allows the role.
Use a separate provisioning role for synthetic fixture creation and cleanup;
the import job does not need those permissions. JSON-field selection is a client
filter: AWS permission grants access to the entire source JSON secret.

## Run It

Run these commands from this example's directory. Copy
[`mapping.example.json`](./mapping.example.json) to `mapping.local.json` and replace
the origin, organization UUID, full ARN, and destination name. Omit `json_field`
to copy the entire string; nested JSON paths and binary secrets are unsupported. `scope` defaults to `organization` for
compatibility with the original mapping format.
Keep credentials out of the mapping. Supply `OH_API_KEY` through your environment
or deployment's secret injection mechanism.

```bash
python -m pip install -r requirements.txt
cp mapping.example.json mapping.local.json
python sync_secrets.py --config mapping.local.json
```

For a missing destination, the default dry run prints:

```text
mapping 1: would create (value not read)
```

Create missing destinations explicitly:

```bash
python sync_secrets.py --config mapping.local.json --apply
```

```text
mapping 1: create acknowledged
```

A subsequent run with the same AWS version and a present destination prints:

```text
mapping 1: checkpoint unchanged (not value-verified)
```

Exit code `1` means at least one mapping failed. Successful mappings remain
committed; this is not an all-or-nothing batch. Output identifies mapping numbers,
not values, ARNs, or API keys. `--state` chooses the journal file (default
`.sync-state/journal.sqlite`); its directory must have mode `700`.

## Personal Imports

Use [`mapping.personal.example.json`](./mapping.personal.example.json) as the
starting point. Set `scope` to `personal`, `expected_user_id` to the intended
OpenHands user UUID, and `org_id` to the organization context in which they will
use these secrets. Supply **that user's own organization-bound API key** through
`OH_API_KEY`. Before any AWS reads, the script verifies both IDs against
`GET /api/keys/current`. It does not accept an email as an identity or impersonate
another user with an administrator key.

```bash
cp mapping.personal.example.json alice.local.json
python sync_secrets.py --config alice.local.json --state .sync-state/alice/journal.sqlite
python sync_secrets.py --config alice.local.json --state .sync-state/alice/journal.sqlite --apply
```

For ten people, maintain ten reviewed mapping files and run ten separate processes,
each with that user's credential injected and a separate persistent journal.
Configure three mappings per file if each person needs three values. The same
secret name can be used by different users; the journal includes the destination
user ID so their checkpoints cannot be exchanged. Store credentials through your
job runner's secret injection, never in the mapping files or command arguments.
Use a private directory and ignore your local mapping filenames in Git.

The personal adapter lists every page of `/api/v1/secrets/search`, checks scope
metadata, and refuses mapped names that collide with listed shared secrets.
Invalid/repeated cursors or more than 100 pages fail before AWS value reads.
Organization listings still fail on pagination because that endpoint exposes no
pagination input in the qualified release.

> [!WARNING]
> The personal `POST /api/v1/secrets` endpoint can overwrite an existing value.
> The script checks metadata first and refuses known existing destinations, but
> the server has no qualified conditional-create contract. A competing writer
> can create a value between the check and the POST. Use operator-invoked runs
> with exclusive control of the selected personal names. The journal lock covers
> this job's local workers; it cannot lock the UI or other clients.

Personal rotation is deliberately refused by this bootstrap, even though the
personal endpoint permits upsert. A production rotation adapter needs separately
qualified concurrency/retry behavior and runtime refresh semantics. This example
never automatically converts personal values into shared values.

## Retry And Recovery

AWS reads use bounded SDK retries. OpenHands metadata reads retry transient
failures. Creates are sent once; there is no idempotency contract allowing safe
write retries after ambiguous outcomes. Error responses are not printed because
servers can echo request values.

Keep the same journal on a persistent local volume and run one writer per
destination user/scope. The local lock only coordinates jobs using that same journal on the
same machine. It is not a distributed lease. Separate jobs must not manage the
same destination. Journal identity includes the origin, organization, source ARN,
JSON field, destination name, and (for personal mode) user ID and scope, so a mapping change cannot inherit a checkpoint.

A pending write blocks that mapping on future runs, including dry runs. Have an
operator inspect the destination metadata and request/audit evidence. A listed
name cannot prove the value or writer. This prototype has no automatic adoption,
reset, or pending-state repair command. Do not erase state to bypass uncertainty;
resolve ownership and recovery before resuming. A lost journal also causes
existing destinations to be refused.

Unchanged checkpoints are acknowledgments, not ongoing reconciliation: this script
cannot detect someone replacing the destination value out of band. Restrict other
writers and qualify a future ownership/version contract before production use.

## Rotation And Production Boundaries

For shared secrets, the tested organization `PUT` endpoint updates name/description only. Sending a
value can succeed without replacing it. This example never uses that endpoint or
delete/recreate. A changed AWS version or unknown existing destination fails before
reading the value and leaves OpenHands unchanged.

Add a separately tested atomic-replacement adapter when OpenHands provides the
supported contract. It needs retry/idempotency, concurrency, and unknown-outcome
semantics before recurring synchronization is enabled. Do not infer support just
from an endpoint accepting a `value` field.

Store updates also do not establish running-agent refresh. Qualification on
Enterprise 1.71.0 / runtime 1.50.1 found running conversations retained static
values after rotation and deletion. Immutable organization binding, runtime
rotation/revocation behavior, automation delivery, scoped sync authorization, and
audit/redaction coverage remain production qualification requirements.

For customer deployment, agree on allowed users and consumers, polling cadence,
rotation SLA, alerting and on-call ownership, and the journal's backup/recovery
process. Your organization owns the upstream vault synchronization and job operation;
OpenHands owns the API and runtime contract. Start with operator-invoked runs.
This example installs no scheduler and never propagates source deletion, removed
mappings, or failed AWS reads as destination deletion. Define an explicit removal
and credential-revocation process before production.

## Validation

On October 20, 2026, live qualification used three disposable AWS Secrets Manager
secrets, four explicit mappings, and a fresh disposable organization on an
Enterprise 1.68.0 candidate installation with runtime 1.50.1:

| Check | Observed Result |
| --- | --- |
| Default dry run | All four entries planned; destination remained empty |
| Initial import | Whole string and two JSON fields created; fourth mapping rejected its missing field |
| Partial recovery | Corrected source field imported; prior successes skipped |
| Repeated run | All four checkpoints skipped; destination contained every intended name |
| Agent consumption | Actual terminal observations matched all four SHA-256 digests; only booleans printed |
| Source rotation | Import refused replacement and preserved destination metadata |
| Mapping removal / scheduled AWS deletion | Destination entries preserved; no automatic deletion |
| Manual store deletion | Existing conversation retained the value; a new conversation lacked it |

No values were passed as conversation overrides: the runtime proof exercised the
organization-secret delivery path. Comparing synthetic hashes establishes value
equality for those fixtures, not comprehensive audit-log redaction or all
permission boundaries. Conversation and sandbox fixtures were removed, the empty
test organization deleted, and test keys made unusable. AWS fixtures were marked
for deletion using a seven-day recovery window; this is not a completed purge.

Reproduce this on a disposable organization and a dedicated synthetic allowlist
before importing real credentials. Use unpredictable fake strings, keep their
expected digests locally, launch a new conversation without `secrets` overrides,
and have its terminal hash the named environment variables and report only
comparison booleans. Check the terminal observation, rather than relying on the
agent's prose. Never use hashes of real low-entropy secrets as public evidence.

Personal-mode qualification used three values each for two disposable users in the
same organization: a whole string and two JSON fields per user. Both users used
the same two destination names with different values, plus one unique name each.
Initial imports, repeats, scope metadata, and wrong-user refusal passed. Actual
agent terminal observations matched each user's three expected digests and
confirmed the other user's unique secret was absent. No values were supplied as
conversation overrides, and the shared organization store remained empty.
An additional three-value test passed under an existing verified principal.

The fake users were created separately through the optional Enterprise provisioning
feature; real users do not need that feature enabled to run this script. The test
secrets, runtime fixtures, and disposable organization were removed, and AWS
fixtures scheduled for seven-day deletion. Fake IdP accounts were deleted and
verified absent. The older lab lacked its documented application-account deletion
API, so complete application-account metadata purge was not qualified. Provisioning
and account lifecycle are outside this import script.

Offline tests cover ambiguous writes, source failures, rotation refusal, mapping
changes, partial recovery, multiple regions, JSON field types, key scope, malformed
metadata, pagination handling, personal-scope payloads, wrong-user refusal,
user/scope journal separation, redaction, redirects, and transient metadata retries:

```bash
python -m pytest aws-secrets-sync/test_sync_secrets.py
```

Run that command from the repository root after installing the example requirements
and the repository's development dependencies.

## APIs Used

| API | Purpose |
| --- | --- |
| AWS `DescribeSecret` | Locate the single `AWSCURRENT` version without reading values |
| AWS `GetSecretValue` | Read that exact version when creating a missing destination |
| OpenHands `GET /api/keys/current` | Verify effective and bound organization match the config |
| OpenHands `GET /api/organizations/{org_id}/secrets` | List destination names without values |
| OpenHands `POST /api/organizations/{org_id}/secrets` | Create a missing organization secret |
| OpenHands `GET /api/v1/secrets/search` | Paginate personal and shared destination metadata |
| OpenHands `POST /api/v1/secrets` | Write a personal secret as the authenticated user |

The OpenHands adapter sends `X-Access-Token` and explicit `X-Org-Id`, verifies
HTTPS, rejects redirects, paginates personal listings, and refuses incomplete
organization listings. It never
changes the user's current organization. Deployments that omit `bound_org_id`
are refused; do not substitute effective `org_id` as proof of binding. The
qualified organization update schema accepts name/description only, and its list
schema exposes no pagination input. A nonempty `next_page_id` therefore fails
before any source values are read.

Each mapping is an independent version snapshot. A source can rotate after its
value is read, and fields mapped separately can observe different versions during
concurrent rotation. There is no batch transaction or continuous freshness claim.

## Related

<!-- docs:cards -->

- [AWS GetSecretValue](https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html) - Version selection and secret-value permissions
- [AWS DescribeSecret](https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_DescribeSecret.html) - Metadata and version stages
- [Per-Conversation Secrets](../per-conversation-secrets/) - A separate delivery scope

<!-- /docs:cards -->
