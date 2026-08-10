# Partner Runtime Reconciliation V2 — Local Report

**Date:** 2026-08-10  
**Status:** `local_verified` / external runtime `NOT_RUN`

## Block B deliverables

| Artifact | Purpose |
|---|---|
| `n8n/current/whieda_partner_runtime_readonly_runtime.py` | Read-only DSN loader, redaction, operator report |
| `n8n/current/run_whieda_partner_runtime_reconciliation_2026-08-10.py` | Extended CLI (`--runtime-dsn-env`, `--tenant`, `--markdown-out`) |
| `qa/partner_runtime/` | Fixtures + runner (unchanged staging `--apply` path) |

## CLI (runtime read-only)

```powershell
python n8n/current/run_whieda_partner_runtime_reconciliation_2026-08-10.py `
  --master-tsv qa/partner_runtime/fixtures/master_six_partners.tsv `
  --runtime-dsn-env WHIEDA_RUNTIME_READONLY_DSN `
  --tenant whieda `
  --report-out .tmp/partner_parity.json `
  --markdown-out .tmp/partner_parity.md
```

Rules enforced:

- Default dry-run; `--apply` + `--runtime-dsn-env` refused before DB
- Single read-only transaction (`BEGIN READ ONLY` → `SELECT` → `COMMIT`)
- DSN never echoed in stdout/reports (redaction on errors)
- Platform roots (`viktor`, `viktor-test`) protected via allowlist
- No `DELETE`; disable-only proposals for retired non-allowlisted actors

## Sample fixture dry-run

Using `master_six_partners.tsv` + `runtime_with_extras.json`:

- **Summary:** `review_required` (retired-partner candidate only)
- **Protected:** viktor, viktor-test
- **Proposed disable:** retired-partner (+ profile)

## External runtime

`WHIEDA_RUNTIME_READONLY_DSN` was **not set** in this session → external parity **`NOT_RUN`**. Do not claim live verification until the env var is provided and the CLI completes successfully.

## Verification

```powershell
python -m pytest backend/platform-api/tests/test_partner_runtime_reconciliation.py -q
python qa/partner_runtime/run_partner_runtime_reconciliation.py
python n8n/current/run_whieda_partner_runtime_reconciliation_2026-08-10.py --help
```
