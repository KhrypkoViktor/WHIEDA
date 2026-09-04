# Drovosek Task: Partner Runtime Reconciliation

## Goal

Prepare a safe local/staging reconciliation policy for `Partners_Ref` -> `lead_actors`, `lead_actor_roles`, and `referral_profiles`.

The master currently has 6 partner rows; runtime has two additional platform/system actors. Do not assume they are errors and do not change live data.

## Required design

1. Create an explicit `platform_root_allowlist` source/configuration. It is separate from business partner rows.
2. For master partner rows:
   - update current actor/profile/roles normally;
   - preserve history;
   - when an actor disappears from master and is not in the allowlist, propose `active=false` / `enabled=false`, never DELETE.
3. For allowlisted platform roots:
   - retain roles and access even when absent from `Partners_Ref`.
4. Emit a review report before any state change:
   - master actors;
   - active runtime actors;
   - allowlisted roots;
   - proposed deactivations;
   - enabled referral profiles that would be affected.
5. Require explicit `--apply` for any mutation. Default is dry-run.

## Tests

- master actor removal proposes disable, does not delete;
- allowlisted root is never proposed for disable;
- referral profile follows actor disable safely;
- rerun is idempotent;
- empty/malformed master source aborts before any mutation;
- no production, Sheets, n8n, Telegram, or website changes in this task.

## Result

Local/staging script, tests, one report with sample dry-run, and one focused commit. Do not publish it.
