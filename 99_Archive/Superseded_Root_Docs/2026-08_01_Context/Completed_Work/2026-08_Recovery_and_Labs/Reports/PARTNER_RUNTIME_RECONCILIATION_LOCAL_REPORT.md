# Partner Runtime Reconciliation — Local Report

**Date:** 2026-08-10  
**Status:** `local_verified` / `live_not_deployed`  
**Task:** `WHIEDA_DROVOSEK_PARTNER_RUNTIME_RECONCILIATION_TASK_V1_2026-08-10.md`

## Context

Runtime audit (`WHIEDA_RUNTIME_MASTER_AUDIT_2026-08-10.md`) found **6** `Partners_Ref` master rows vs **8** active `lead_actors`. The two extras are platform/system actors (`viktor`, `viktor-test`), not sheet partner rows. This package adds an explicit allowlist and a review-first reconciliation path — no live mutations in this task.

## Deliverables

| Artifact | Purpose |
|---|---|
| `n8n/current/whieda_partner_runtime_allowlist.json` | Explicit `platform_root_allowlist` (separate from business partners) |
| `n8n/current/whieda_partner_runtime_reconciliation_lib.py` | Plan builder, upsert SQL, disable-only deactivation SQL |
| `n8n/current/run_whieda_partner_runtime_reconciliation_2026-08-10.py` | CLI: dry-run default, `--apply` for staging only |
| `qa/partner_runtime/` | Fixtures + runner |
| `backend/platform-api/tests/test_partner_runtime_reconciliation.py` | Unit tests |

## Policy

1. **Master rows** → normal upsert into `lead_actors`, `lead_actor_roles`, `referral_profiles` (same contract as structured sync).
2. **Active runtime actor absent from master and not allowlisted** → propose `active=false` on actor, `enabled=false` on owned profiles. **Never DELETE.**
3. **Allowlisted roots** (`viktor`, `viktor-test`) → never proposed for disable; roles retained outside sheet sync.
4. **Malformed/empty master** → abort before any mutation.
5. **Default dry-run**; `--apply` only on local staging Postgres (docker) or after architect review.

## Allowlist (current)

| actor_id | kind | reason |
|---|---|---|
| `viktor` | platform_owner | Owner of `nnm` platform_root profile; not a `partner_id` row |
| `viktor-test` | test_admin | Isolated Telegram test super-admin (`khrypko_pro`) |

## Sample dry-run (fixture: 6 master + 8 runtime + 1 retired)

Command:

```powershell
python n8n/current/run_whieda_partner_runtime_reconciliation_2026-08-10.py `
  --master-tsv qa/partner_runtime/fixtures/master_six_partners.tsv `
  --runtime-json qa/partner_runtime/fixtures/runtime_with_extras.json
```

Summary:

- **Master actors:** harold, ladnaya, mariam, nnm, olga-samtsova, onlineelena
- **Active runtime (9 in fixture):** above + viktor + viktor-test + retired-partner
- **Proposed deactivations:** `retired-partner` actor + `retired-partner` profile only
- **Protected:** viktor, viktor-test (allowlisted)
- **would_delete:** false

## Verification

```powershell
python qa/partner_runtime/run_partner_runtime_reconciliation.py
```

Result: **8 passed** (pytest) + sample dry-run checks **PASS**.

Structured Sync Safety P0 deploy helper (`prepare_whieda_structured_sync_safety_deploy_2026-08-10.py`) already preserves active main sync on `--apply` — ready for your controlled live release (backup → migration → one sync → re-verify).

## Not in scope (by design)

- No production Postgres, Sheets, n8n publish, Telegram, or website edits
- No automatic disable of live platform/test actors without architect review of dry-run report

## Next steps (architect)

1. Run dry-run against snapshot TSV + read-only runtime export (or staging clone).
2. Confirm allowlist matches operational intent.
3. If deactivations look correct, `--apply` on staging first, then schedule live reconciliation separately from Safety P0 release.
