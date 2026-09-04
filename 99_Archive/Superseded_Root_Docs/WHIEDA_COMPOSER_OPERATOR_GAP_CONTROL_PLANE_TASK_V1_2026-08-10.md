# WHIEDA: Operator Gap Control Plane - Composer Task

## Goal

Build a local-only operator layer around the existing `advisor_gap` events.

The owner must be able to see what the advisor did not close, what repeats, what needs a doctor/business/owner decision, and what can become a candidate for a future knowledge update.

This is not a second knowledge base, not a second queue, and not automatic publishing.

## Scope and hard boundaries

Work only in:

- `backend/platform-api/`
- `postgres/sql/` and `postgres/scripts/` only when a new staging migration is genuinely required;
- `qa/`.

Do not touch:

- production environment;
- n8n workflows, credentials, Telegram webhook or bot settings;
- Google Sheets, Dify, RAG datasets;
- `03_Website/`;
- owner-locked product cards and marketing texts;
- existing runtime `interaction_events` data.

No publish, deploy, SSH, browser UI work, live migrations, or access-map changes.

## Existing foundation - use it, do not duplicate it

- `interaction_events` is the single raw event stream.
- `event_type='advisor_gap'` is the gap event.
- `app/advisor/gap.py` already writes idempotent gap events and exposes `fetch_gap_operator_summary()`.
- A gap includes `gap_kind`, normalized question, hashed session reference, product if detected, repeat count and timestamps.
- The event stream remains immutable. Do not edit or delete event payloads.
- Tenant isolation already exists. Every new entity must have `tenant_id` and enforce it.

## Deliverable A - read-only operator API

Create an admin-only API module and route family under a clear stable path:

`GET /v1/admin/advisor-gaps/summary`

Returns, per tenant:

- total unique gaps;
- total repeats;
- gaps by `gap_kind`;
- top unresolved normalized questions;
- first/last seen range;
- current review counts by status;
- no raw Telegram identifiers, phone numbers or full unredacted session IDs.

`GET /v1/admin/advisor-gaps`

Filters:

- `gap_kind`;
- `status`;
- `priority`;
- `from` / `to` timestamp;
- pagination (`limit`, cursor or offset);
- tenant is derived server-side, never trusted from request JSON.

Each row must contain only:

- stable queue item id;
- gap kind;
- normalized question;
- detected product if any;
- repeat count;
- first and last seen;
- current triage status and priority;
- assigned owner role/name if assigned;
- compact safe evidence reference.

`GET /v1/admin/advisor-gaps/{id}`

Returns the same safe record plus the allowed operator notes/audit history. Never return raw session ID or user contact data.

Authentication/authorization:

- reuse the existing platform admin / tenant permission pattern;
- super-admin sees its tenant data;
- unauthorized → 401/403;
- cross-tenant access must be impossible.

If a real admin auth middleware does not yet exist locally, make the route explicitly feature-gated and test it through the existing local test identity pattern. Do not invent a fake production login system.

## Deliverable B - triage queue, not auto-learning

Create one tenant-scoped queue table, for example `advisor_gap_review_items`.

It is a projection over events, not a replacement for them.

Required fields:

- `id` UUID;
- `tenant_id`;
- `dedup_key` unique within tenant;
- `gap_kind`;
- `question_normalized`;
- `detected_product` nullable;
- `event_count`;
- `first_seen_at`, `last_seen_at`;
- `status`: `new`, `triaged`, `in_review`, `approved_candidate`, `rejected`, `resolved`;
- `priority`: `p0`, `p1`, `p2`, `p3`;
- `owner_role`: `owner`, `medical`, `business`, `admin`, nullable;
- `owner_name` nullable;
- `operator_note` nullable;
- `candidate_type`: `alias`, `clarification_rule`, `resource_link`, `business_faq`, `medical_review`, `safety_review`, `intent_gap`, `none`;
- `created_at`, `updated_at`;
- `resolved_at` nullable.

Rules:

- events never disappear;
- refresh is idempotent by tenant + gap kind + normalized question + detected product;
- refresh updates counts and timestamps but does not overwrite an operator's status, priority, owner or note;
- there is no automatic promotion into Product_Aliases, FAQ, RAG, Sheets or runtime;
- `medical_or_safety_boundary` defaults to `p0` and owner role `medical`;
- `missing_resource` defaults to `p1` and owner role `admin`;
- `ambiguous_product` / `unknown_product` default to `p1` and owner role `business`;
- `unknown_followup` defaults to `p2` and owner role `owner`;
- `unsupported_topic` defaults to `p3` and owner role `owner`.

Create a local-only refresh command:

```powershell
python backend/platform-api/scripts/refresh_advisor_gap_review_queue.py --tenant whieda --dry-run
python backend/platform-api/scripts/refresh_advisor_gap_review_queue.py --tenant whieda
```

Requirements:

- `--dry-run` writes nothing;
- normal mode uses a transaction;
- prints inserted / updated / unchanged counts;
- refuses a production-looking host or database by default;
- no hardcoded Supabase password, hostname or tenant;
- a tenant must be explicit;
- safe to run repeatedly.

## Deliverable C - controlled operator actions

Add explicit admin-only actions. Use PATCH, validation and an audit row/table. No delete endpoints.

`PATCH /v1/admin/advisor-gaps/{id}` supports only:

- status;
- priority;
- owner role/name;
- operator note;
- candidate type.

Validation:

- enum values only;
- no arbitrary JSON merge;
- note limit 2,000 characters;
- `resolved` sets `resolved_at`;
- reopening clears `resolved_at`;
- every mutation creates an audit entry with actor, old values, new values and timestamp;
- cross-tenant mutation returns 404 or 403 without leaking whether the item exists.

## Deliverable D - exports for humans

Add a read-only export command and API format:

```powershell
python backend/platform-api/scripts/export_advisor_gap_review.py --tenant whieda --format md
python backend/platform-api/scripts/export_advisor_gap_review.py --tenant whieda --format csv
```

The Markdown export must be readable by a 50+ volunteer, no technical IDs in the main table:

| Priority | Topic / question | Repeats | Last seen | Who decides | What is needed |

Group into:

1. Medical review;
2. Business / wording review;
3. Missing materials;
4. Owner decisions;
5. Closed items (separate, collapsed summary).

The export is a review package only. It does not send Telegram messages or alter content.

## Deliverable E - tests and local proof

Create a staging SQL migration with a new versioned filename. Do not modify old migrations.

Required tests:

1. First refresh creates expected queue items from fixture `advisor_gap` events.
2. Same refresh again: zero duplicates; count/timestamps remain correct.
3. A new event increments the existing item but keeps operator fields.
4. Default routing and priorities for all six `gap_kind` values.
5. Medical/safety is never automatically marked approved.
6. Cross-tenant reads and writes are denied under a non-bypass API role.
7. Unauthorized admin access is denied.
8. PATCH accepts allowed fields only and creates audit history.
9. Markdown/CSV export contains no raw session reference or contact data.
10. `--dry-run` causes no write.
11. Transaction failure rolls back all changes for that refresh.

Extend local Docker lab with an optional flag:

```powershell
python backend/platform-api/scripts/run_local_core_lab.py --e2e --gap-operator
```

It must:

- apply the new migration only to local Docker staging;
- seed synthetic gap events;
- run refresh twice;
- prove RLS under a non-superuser API role;
- prove export redaction;
- stop the Core container in `finally`;
- never access production.

## Required reports

Create:

- `GAP_OPERATOR_CONTROL_PLANE_LOCAL_REPORT.md`;
- `qa/gap_operator/` with fixtures and verification runner;
- API contract document under `backend/platform-api/docs/`.

Report must say exactly:

- what was actually run locally;
- what was not run;
- no claim of production readiness;
- test command and numeric result;
- migration file name;
- list of endpoints;
- proof that raw user IDs are not returned.

## Quality bar and commit

Before final answer:

```powershell
python -m pytest backend/platform-api/tests -q
python backend/platform-api/scripts/run_local_core_lab.py --e2e --gap-operator
```

Commit only files belonging to this task. Do not stage unrelated dirty files.

Final response must contain:

- commit hashes;
- pytest result;
- local Docker E2E result;
- migration file;
- endpoints;
- explicitly list anything not actually verified.
