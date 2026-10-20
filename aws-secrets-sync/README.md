# AWS Secrets Bootstrap

Import explicitly selected AWS Secrets Manager strings into OpenHands Enterprise
organization secrets. Use this as the starting point for a customer-owned sync
job, including an existing Keeper-to-AWS workflow.

> [!WARNING]
> Prototype: create-only. Existing destination values are never updated or deleted.
> Rotation is detected and exits unsuccessfully until a supported atomic value
> replacement API is available. This example is not an operated AWS connector.

## How It Works

The script first verifies that the API key is bound to the configured organization
using `bound_org_id`, lists OpenHands secret names, then describes each allowlisted AWS
secret's `AWSCURRENT` version. Dry run stops there without reading secret values.
With `--apply`, it reads missing destinations' exact current versions, optionally
extracts one top-level JSON string field, and creates organization secrets.

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
- An organization-bound OpenHands API key with owner/admin permission to create
  shared secrets. The current example does not create users, keys, or permissions.
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
to copy the entire string; nested JSON paths and binary secrets are unsupported.
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

## Retry And Recovery

AWS reads use bounded SDK retries. OpenHands metadata reads retry transient
failures. Creates are sent once; there is no idempotency contract allowing safe
write retries after ambiguous outcomes. Error responses are not printed because
servers can echo request values.

Keep the same journal on a persistent local volume and run one writer per
organization. The local lock only coordinates jobs using that same journal on the
same machine. It is not a distributed lease. Separate jobs must not manage the
same destination. Journal identity includes the origin, organization, source ARN,
JSON field, and destination name, so a mapping change cannot inherit a checkpoint.

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

The tested organization `PUT` endpoint updates name/description only. Sending a
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

Offline tests cover ambiguous writes, source failures, rotation refusal, mapping
changes, partial recovery, multiple regions, JSON field types, key scope, malformed
metadata, pagination refusal, redaction, redirects, and transient metadata retries:

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

The OpenHands adapter sends `X-Access-Token` and explicit `X-Org-Id`, verifies
HTTPS, rejects redirects, and refuses incomplete paginated listings. It never
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
