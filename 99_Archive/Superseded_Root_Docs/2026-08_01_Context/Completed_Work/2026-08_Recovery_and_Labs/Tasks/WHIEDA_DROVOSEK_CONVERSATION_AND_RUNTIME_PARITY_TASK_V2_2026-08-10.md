# WHIEDA Drovosek: Conversation Reliability + Runtime Partner Parity V2

## Result expected

Two local/read-only deliverables. Do not deploy, publish, change n8n, modify
Google Sheets, touch Telegram configuration, or run SQL writes against the
external runtime.

Commit only the files belonging to this task. Do not stage unrelated dirty files.

---

## Block A: Conversation Reliability Lab

Implement the existing task without changing its scope:

`WHIEDA_COMPOSER_CONVERSATION_RELIABILITY_LAB_TASK_V1_2026-08-10.md`

This is not optional and comes first. The local runner must execute the real
Core HTTP contract, not an n8n legacy webhook. Keep all previously approved
No Blind Zone wording and product cards unchanged.

Required final evidence:

- at least 24 flows and 80 turns;
- offline corpus lint and unit tests;
- local Docker E2E result, or a truthful `NOT_RUN` reason;
- no test-only expectation weakening without a written product rationale;
- separate commit only for Block A.

---

## Block B: Partner Runtime Reconciliation V2 - real read-only parity

The existing reconciliation implementation is accepted only as a local model.
It is not live-ready because it can read only `whieda-local-staging-postgres`.
Add a separate **read-only external runtime mode**.

### Scope

Only:

- `n8n/current/whieda_partner_runtime_reconciliation_*.py`;
- `qa/partner_runtime/`;
- focused tests under `backend/platform-api/tests/` if needed;
- one report and documentation for this task.

Never use the production DSN for `INSERT`, `UPDATE`, `DELETE`, DDL, or an
`--apply` operation. This block has no live mutation phase.

### CLI contract

Extend the existing CLI with:

```powershell
python n8n/current/run_whieda_partner_runtime_reconciliation_2026-08-10.py `
  --master-tsv <captured Partners_Ref.tsv> `
  --runtime-dsn-env WHIEDA_RUNTIME_READONLY_DSN `
  --tenant whieda `
  --report-out <local json>
```

Rules:

1. Default remains dry-run.
2. `--runtime-dsn-env` reads a named environment variable; the DSN must never
   appear in stdout, reports, tracebacks, or fixtures.
3. `--runtime-dsn-env` and `--apply` together must fail before opening a DB
   connection.
4. Runtime mode permits a single read-only transaction only:
   `BEGIN READ ONLY`, `SELECT`, `COMMIT`.
5. Reject an empty master, duplicate `partner_id`, missing allowlist, unsupported
   tenant, and missing required runtime tables with an explicit abort reason.
6. Keep staging `--apply` behaviour unchanged and explicitly separate from
   runtime read-only mode.

### Runtime data to read

Read only the minimal current state for the given tenant:

- `lead_actors`: actor id, display name, active, Telegram username;
- `referral_profiles`: ref code, owner id, enabled, display mode;
- active roles only when needed to explain an owner/root exception.

No phones, chat IDs, raw profile JSON, credentials, or Telegram IDs in the
human report.

### Output and classification

Produce an operator-friendly Markdown and machine JSON report with:

- master active partner count;
- active runtime actor count;
- active referral profile count;
- allowlisted platform roots;
- master-only rows needing upsert if a future approved release is made;
- runtime-only actors proposed for disable-only treatment;
- profiles affected by a proposed owner disable;
- invalid or ambiguous rows requiring owner review;
- exact summary: `safe / review_required / abort`.

Do not label two intentional platform roots as errors when present in the
allowlist. For the current captured master, the expected dry-run conclusion is
that only genuinely retired non-allowlisted actors are candidates; platform
roots remain protected.

### Tests

Add tests for:

1. runtime DSN mode uses read-only SQL only;
2. `--apply` with runtime DSN refuses before DB access;
3. DSN and password are redacted from every error/report path;
4. platform roots are retained;
5. retired non-allowlisted actor is proposed as disable-only;
6. duplicate/malformed master aborts;
7. missing runtime relation aborts safely;
8. tenant isolation: another tenant never appears in the plan;
9. output has no chat ID, phone, raw Telegram ID, or raw public-profile JSON.

### Acceptance

```powershell
python -m pytest backend/platform-api/tests -q
python qa/partner_runtime/run_partner_runtime_reconciliation.py
python n8n/current/run_whieda_partner_runtime_reconciliation_2026-08-10.py --help
```

Do not claim the external runtime was verified unless the real DSN mode was
explicitly executed. If it was not available, state `NOT_RUN`.

Create:

- `PARTNER_RUNTIME_RECONCILIATION_V2_LOCAL_REPORT.md`;
- a focused Block B commit.

## Final response format

For each block separately provide: commit hash, files, test count, real E2E or
`NOT_RUN`, defects found/fixed, and anything deliberately not verified.
