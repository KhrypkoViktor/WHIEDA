# WHIEDA Drovosek: Telegram Live Path Recovery Audit V1

## Goal

Establish the exact current production path for a Telegram update and explain why
the live bot can still produce legacy catalogue fallbacks while the local Core
Golden suite passes. This is an evidence-gathering task. Do not repair, deploy,
publish, restart, activate, deactivate, or edit any live service.

## Required context

Read, do not edit:

- `WHIEDA_LIVE_STATUS.md`
- `00_READ_FIRST_WHIEDA_CANON.md`
- `backend/platform-api/docs/TELEGRAM_GOLDEN_HTTP_ACCEPTANCE_LAB_LOCAL_REPORT.md`
- `backend/platform-api/docs/TELEGRAM_GOLDEN_LOCAL_MASTER_PARITY_REPORT.md`
- `n8n/current/WHIEDA_DEPLOY_HOSTS.md`
- `n8n/current/whieda_core_route_ops.py`
- `n8n/current/whieda_assert_production_routing_2026-08-04.py`
- `n8n/current/whieda_telegram_webhook_readonly_status.py`

Use Git Bash for shell work. Existing read-only SSH environment variables and
the configured read-only runtime DSN may be used. Never print credentials,
tokens, cookies, DSNs, or raw request headers in a report.

## Scope

### A. Route truth

Build one evidence table for the real path:

`Telegram webhook -> n8n workflow -> route decision -> Core/legacy/Dify -> Telegram delivery`.

For every hop record:

- host and service name;
- endpoint or workflow id/name;
- active/inactive state;
- configured route mode (`core`, `legacy`, `shadow`, `off`) where applicable;
- deployed image/version/commit when readable;
- evidence command or source file;
- confidence (`confirmed`, `inferred`, `unknown`).

Read only. For n8n, inspect workflow definition and latest executions only.
Do not run a manual Telegram update and do not alter a webhook.

### B. Runtime and data parity

Read-only checks:

1. Check Core health/readiness and OpenAPI.
2. Identify the currently deployed Core image or commit.
3. Read structured-sync last success/failure and row counts for products,
   aliases, cards and resources.
4. Compare the available runtime data version/snapshot timestamp with the
   master snapshot used by local Golden (`20260810T083328Z` or newer).
5. Verify whether the deployed runtime contains the actual key aliases:
   `активатор`, `активатор pro`, `активатор про`, `активаор`, `ативатор`.
6. Check whether there is one active Telegram delivery path or more than one.
   State duplicate-risk evidence separately from confirmed duplication.

### C. Safe direct probes

Do not call Telegram. Direct HTTP probes are allowed only against already
public/internal advisor endpoints and only with an isolated audit session.

Run and record response mode, product SKU/name, latency and a redacted answer
preview for these inputs:

- `хай`
- `че ты можеь?`
- `какие есть товары?`
- `активаор`
- `активатор`
- `активатор pro`
- `сравни активатор и pro`
- `фото pro` after that comparison in the same audit session
- `сколько стоит?` after that comparison in the same audit session

If direct Core and legacy endpoints are both identifiable, probe both and show
the difference. If one endpoint is unavailable, mark it unavailable; do not
invent a result.

### D. Recovery-ready cutover plan

Produce a short, executable but non-executed plan:

1. backup points;
2. exact single route/configuration expected after cutover;
3. deploy input and version to use;
4. a 10-message Telegram canary run;
5. rollback command/path;
6. success criteria: no duplicate responses, service intents work, product
   choice preserves context, cards are photo-first where data exists.

No implementation changes in this task. If a missing artifact prevents an
audit, report that exact artifact instead of creating a substitute.

## Deliverables

Create only:

- `WHIEDA_TELEGRAM_LIVE_PATH_AUDIT_2026-08-14.md`
- `WHIEDA_TELEGRAM_LIVE_PATH_AUDIT_2026-08-14.json`
- `backend/platform-api/docs/WHIEDA_TELEGRAM_LIVE_CUTOVER_CHECKLIST_V1.md`

Reports must be redacted and contain:

- confirmed facts;
- unknowns;
- likely root cause ranked by evidence;
- commands actually run and exit status;
- explicitly state that no production change was made.

## Hard boundaries

- No writes to n8n, Telegram, Postgres/Supabase, Google Sheets, Dify or servers.
- No Docker rebuild/deploy/restart.
- No edits under `backend/platform-api/app/**`.
- No commits.

## Acceptance

The task is accepted only if a separate Core owner can choose a cutover path
without guessing which live component currently answers the user.
