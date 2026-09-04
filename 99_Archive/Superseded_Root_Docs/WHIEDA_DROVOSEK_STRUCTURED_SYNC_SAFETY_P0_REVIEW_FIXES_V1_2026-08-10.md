# Structured Sync Safety P0: mandatory review fixes

Do not publish anything. Fix local artifacts and repeat the complete local
Safety P0 verification.

## P0-1: failed audit must update the exact running sync

The separate Error Trigger workflow cannot rely on
`$('Code: Build Structured Sync SQL')`: that node belongs to a different
execution and is not guaranteed to exist in Error Trigger context.

Required implementation:

1. The main workflow writes a `running` row before the atomic apply with the
   original n8n execution id and a generated `sync_run_uuid`.
2. The Error Trigger reads the *original failed execution* id from the actual
   n8n error payload, not its own error-workflow execution id.
3. It updates that exact `running` row using original execution id. It must not
   create a second generic failed row when a matching running row exists.
4. If no running row exists, it may insert one fallback `failed` row, marked
   `unmatched_error_trigger=true` or equivalent in a safe metadata field.
5. Filter the Error Trigger path to only the WHIEDA Structured Sync workflow
   id. A failure in any other workflow must write no WHIEDA sync audit record.
6. Add an integration-style local test with a representative n8n Error Trigger
   payload. Assert: one original `running` row becomes one `failed` row, same
   `sync_run_uuid`, same original execution id, redacted message.

## P0-2: workflow patches must be inert by default

Both JSON patch artifacts currently contain `"active": true`.

Required implementation:

1. Commit/export artifacts with `active: false`.
2. Create a deployment helper or exact deployment runbook that:
   - creates/updates Error Audit workflow first;
   - obtains its real n8n workflow ID;
   - inserts that ID, not its display name, into main workflow `settings.errorWorkflow`;
   - backs up the live main workflow before any write;
   - validates credentials, workflow id and version id;
   - activates both workflows only as an explicit final step.
3. The helper must default to dry-run. `--apply` is required for any write.
4. No automatic cron change, no sync trigger, no restart, and no Sheet write.

## P1: report and tests

1. Add a test proving `active` is false in both artifacts.
2. Add a test proving `settings.errorWorkflow` is an ID placeholder only in
   the artifact and an actual ID only after controlled deployment preparation.
3. Update `STRUCTURED_SYNC_SAFETY_P0_LOCAL_REPORT.md` with the correction and
   explicitly keep status `local_verified / live_not_deployed`.
4. Make one focused commit. Do not mix Master Integrity work into it.
