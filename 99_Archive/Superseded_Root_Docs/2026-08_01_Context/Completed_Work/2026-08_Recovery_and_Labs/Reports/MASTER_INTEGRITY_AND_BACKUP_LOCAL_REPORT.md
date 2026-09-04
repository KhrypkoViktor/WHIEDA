# Master Integrity and Backup — Local Report

Date: 2026-08-10  
Task: `WHIEDA_DROVOSEK_MASTER_INTEGRITY_AND_BACKUP_TASK_V1_2026-08-10.md` (commit marker `746280d`)  
Scope: local read-only tools and tests. **No Sheets, Postgres runtime, n8n, cron, or server deploy.**

Baseline input (unchanged): `n8n/live-exports/structured-master/20260810T083328Z/manifest.json`

---

## What was built

### A. Master snapshot contract

- Extended `capture_whieda_structured_master_snapshot.py`
- Manifest now includes `header_hash` per layer
- Validation: 20 layers, file presence, TSV header validity, critical floors
- Optional empty `product_details` (header only) allowed
- `.partial` dirs removed on failure; completed snapshots preserved
- Retention helper: `--retention-root` (dry-run) / `--apply-retention`

### B. Drift classifier

- `compare_whieda_master_snapshots.py` — any two snapshot dirs
- Per layer: row delta, hash/header change, ID add/remove (when ID field known)
- Classes: `expected_content_change`, `review_required`, `blocking`

### C. Runtime parity reader

- `check_whieda_master_runtime_parity.py --snapshot … --db-url …`
- Read-only SQL row counts + stable content hashes (`advisor_structured_*`)
- Statuses: `in_sync`, `stale_runtime`, `runtime_missing_layer`, `master_review_required`, `not_checked`
- DSN/password redaction in errors and reports
- **No live DSN run in this report** — local fake-data tests only

### D. Deployment-ready (not deployed)

- `server_whieda_master_snapshot_cron.template.sh` — 6h schedule template, output under `whieda-master-snapshots`, retention dry-run only

Core library: `n8n/current/whieda_master_integrity_lib.py`

---

## Commands

```powershell
# Validate architect baseline
python n8n/current/capture_whieda_structured_master_snapshot.py --validate-only n8n/live-exports/structured-master/20260810T083328Z

# Compare two snapshots
python n8n/current/compare_whieda_master_snapshots.py SNAP_A SNAP_B

# Retention dry-run
python n8n/current/capture_whieda_structured_master_snapshot.py --retention-root n8n/live-exports/structured-master

# Runtime parity (explicit DSN only — architect controlled)
python n8n/current/check_whieda_master_runtime_parity.py --snapshot PATH --db-url "postgresql://..."

# Full local lab
python qa/master_integrity/run_master_integrity.py
python -m pytest backend/platform-api/tests/test_master_integrity.py -q
```

---

## Test results (2026-08-10)

| Test | Result |
|------|--------|
| Valid 20-layer snapshot | PASS |
| Missing layer → blocking | PASS |
| Invalid/empty TSV → blocking | PASS |
| Empty optional product_details | PASS |
| Critical row collapse → blocking | PASS |
| Header mutation → review_required | PASS |
| Runtime parity fake data + DSN redaction | PASS |
| Retention dry-run (no auto-delete) | PASS |
| Architect baseline `20260810T083328Z` valid | PASS |
| pytest (`test_master_integrity.py`) | 9 passed |

```
MASTER_INTEGRITY: PASS
```

---

## Critical floors (aligned with sync P0)

| Layer | Floor |
|-------|------:|
| products | 20 |
| aliases | 60 |
| resources | 25 |
| product_cards | 10 |

---

## Files changed / added

**Modified**

- `n8n/current/capture_whieda_structured_master_snapshot.py`

**Added**

- `n8n/current/whieda_master_integrity_lib.py`
- `n8n/current/compare_whieda_master_snapshots.py`
- `n8n/current/check_whieda_master_runtime_parity.py`
- `n8n/current/server_whieda_master_snapshot_cron.template.sh`
- `qa/master_integrity/build_fixtures.py`
- `qa/master_integrity/run_master_integrity.py`
- `backend/platform-api/tests/test_master_integrity.py`
- `MASTER_INTEGRITY_AND_BACKUP_LOCAL_REPORT.md`

---

## Architect-controlled live deployment (not done)

1. Install `server_whieda_master_snapshot_cron.template.sh` on VPS after review
2. Create `/var/backups/whieda-master-snapshots` (or agreed path)
3. Run retention dry-run on server snapshots; approve; then optional `--apply-retention`
4. Optional: one read-only runtime parity run with explicit DSN (counts/hashes only in report)
5. Safety P0 workflow patch — separate review before n8n publish

---

## Relation to Safety P0

- Same critical floors as `whieda_structured_sync_code_2026-07-13.js` circuit breaker
- Master integrity validates **Sheet export**; Safety P0 protects **Postgres apply**
- Runtime parity closes the gap: successful n8n run ≠ proven parity

No production, Sheets, or runtime DB were modified during this task.
