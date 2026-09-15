# Advisor Gap Operator API (local/staging)

Read-only and triage endpoints for the operator control plane over `advisor_gap` events.

**Base path:** `/v1/admin/advisor-gaps`  
**Auth:** platform admin session cookie (`wwc_admin_session`) — same as Owner Cabinet.  
**Tenant:** resolved server-side via host + optional `?tenant_id=` for `super_admin`. Never accept tenant in PATCH body.

## Endpoints

| Method | Path | Roles | Description |
|--------|------|-------|-------------|
| GET | `/v1/admin/advisor-gaps/summary` | viewer+ | Aggregates: unique gaps, repeats, by kind/status, top unresolved |
| GET | `/v1/admin/advisor-gaps` | viewer+ | Paginated queue list with filters |
| GET | `/v1/admin/advisor-gaps/{id}` | viewer+ | Safe item + mutation audit history |
| GET | `/v1/admin/advisor-gaps/export?format=md\|csv` | viewer+ | Human-readable export (no session/contact fields) |
| PATCH | `/v1/admin/advisor-gaps/{id}` | admin+ | Update triage fields only |

## PATCH body (allowed fields only)

```json
{
  "status": "triaged",
  "priority": "p1",
  "owner_role": "business",
  "owner_name": "Иван",
  "operator_note": "Нужен алиас для пасты",
  "candidate_type": "alias"
}
```

Enums: see `app/admin/gap_review/constants.py`.

## Redaction

Responses and exports never include:

- raw Telegram chat id or phone;
- full session id (only internal queue id UUID);
- `session_ref` from event payload.

`evidence_ref` is limited to a short trace prefix, e.g. `trace:abc123…`.

## Local commands

```powershell
python backend/platform-api/scripts/refresh_advisor_gap_review_queue.py --tenant whieda --dry-run
python backend/platform-api/scripts/refresh_advisor_gap_review_queue.py --tenant whieda
python backend/platform-api/scripts/export_advisor_gap_review.py --tenant whieda --format md
python qa/gap_operator/run_gap_operator.py
python backend/platform-api/scripts/run_local_core_lab.py --e2e --gap-operator
```

## Schema

Migration: `postgres/sql/platform_advisor_gap_review_v1.sql`

- `advisor_gap_review_items` — tenant-scoped triage projection (RLS)
- `advisor_gap_review_mutations` — append-only operator change log (RLS)

Raw events remain in `interaction_events` (`event_type='advisor_gap'`) and are never modified.
