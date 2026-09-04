# Structured Sync Safety P0: Error Audit rollback state fix

## Defect

`apply_deploy()` remembers the main workflow state but not the existing Error Audit workflow state.

On a repeat deploy, an already active Error Audit workflow is saved as inactive. If the deployment fails before it is activated again, rollback restores main but leaves the pre-existing Error Audit workflow unexpectedly inactive.

## Required change

Update only the deploy helper and its focused tests.

1. Before any save, capture `prior_error_state` when an Error Audit workflow exists: id and `active`.
2. On rollback:
   - restore existing Error Audit workflow document/state to its prior `active` value;
   - if the Error Audit workflow was newly created by this run, leave it inactive;
   - report `prior_error_state`, `final_error_state`, and rollback result in JSON.
3. Do not make dry-run use network.
4. Keep main state preservation and error-workflow linking unchanged.

## Required tests

- existing active Error Audit + forced failure before main activation -> main restored active and Error Audit restored active;
- existing inactive Error Audit + forced failure -> stays inactive;
- newly created Error Audit + forced failure -> remains inactive;
- success path still has link verified before activation;
- all existing 19 tests remain green.

## Scope

Local only. No n8n, Postgres, Sheets, Telegram, or Dify deployment. One focused commit after tests.
