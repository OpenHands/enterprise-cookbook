# Operator Recovery

Use this only after a mapping stops for reconciliation. Routine weekly runs use
the commands in the [quickstart](./README.md#run-it).

## Resolve the Remote Outcome First

1. Stop the scheduled job and all other writers for the selected destination.
2. Identify the exact origin, user/scope, organization, mapping number, and journal
   from the failed run. Pending/conflicting-deleted errors print `checkpoint=...`:
   that hash identifies one journal row and contains no plaintext credential.
3. Use request/audit evidence to establish that the failed POST or DELETE has
   finished or cannot execute later. Absence from a listing, an HTTP timeout, or
   waiting a few minutes is **not** sufficient proof. If the outcome cannot be
   settled, keep the row blocked and involve the service operator.
4. Check the exact destination through the supported OpenHands UI/API. If it is
   present, establish ownership before removing it. To restart from a known empty
   destination, explicitly delete only that owned entry, accept the availability
   gap, and verify its absence. Never delete an unfamiliar existing entry merely
   to make the job pass. Running conversations can still retain the old value.

A 401/403 may explain a rejected request, but it does not automatically clear the
checkpoint. Correct the key/role and complete these checks before resetting it.

## Reset Only the Reconciled Checkpoint

After all requests are settled and the destination is verified absent, use the
standard SQLite CLI against the **same** journal. Replace both the path and the
64-character checkpoint hash below. Keep the private backup for incident review.

```bash
umask 077
sqlite3 /absolute/path/to/journal.sqlite
```

At the SQLite prompt:

```sql
.backup '/absolute/private/path/journal-before-recovery.sqlite'
SELECT version, status FROM writes WHERE mapping = 'CHECKPOINT_HASH';
BEGIN IMMEDIATE;
DELETE FROM writes
WHERE mapping = 'CHECKPOINT_HASH'
  AND status IN ('pending', 'delete_pending', 'deleted');
SELECT changes();
COMMIT;
.quit
```

Confirm the SELECT identified the intended blocked row and `changes()` returned
`1`. This removes only its checkpoint; it does not adopt a listed name or claim a
value was installed. Rerun the normal sync command with `--apply` to read the latest
source version and recreate the now-missing entry. Verify success before resuming
the scheduler. Do not reset the whole journal or mark a row `applied` based only
on destination-name metadata.

For an unknown existing destination or edited ARN/field mapping, prefer a new,
unused destination name. Alternatively, explicitly reconcile/remove the owned
old destination as above, then rerun; an absent destination can be created without
adopting its old value. Retained checkpoints for removed mappings are harmless.
