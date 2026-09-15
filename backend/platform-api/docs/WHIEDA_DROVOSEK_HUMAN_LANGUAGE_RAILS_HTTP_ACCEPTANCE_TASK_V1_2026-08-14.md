# WHIEDA Drovosek: Human Language Rails HTTP Acceptance Lab V1

## Objective

Turn the accepted Human Language Rails corpus into a deterministic local HTTP
acceptance lab. This must prove how the current Core behaves against messy
human Telegram-style language; it must not change how Core behaves.

The output is a factual backlog for the Core owner, not a way to make failing
cases green by weakening expectations.

## Inputs

- `qa/human_language_rails/whieda_human_language_rails_v1.jsonl`
- `qa/human_language_rails/flows_v1.jsonl`
- `qa/human_language_rails/fixtures/pending_assertions.jsonl`
- existing Golden target/lab patterns under `qa/telegram_golden/`
- existing local Core Docker lab and master seed support.

Only `acceptance_status: accepted` cases enter HTTP acceptance. Pending rows
must be reported separately as `NOT_RUN_PENDING`; they are never silently
included in pass totals and never treated as failures.

## Scope

Allowed:

- `qa/human_language_rails/**`
- `backend/platform-api/tests/test_human_language_rails_http.py`
- a narrow hook in `backend/platform-api/scripts/run_local_core_lab.py` and its
  local orchestrator only if required to invoke the runner;
- `backend/platform-api/docs/HUMAN_LANGUAGE_RAILS_HTTP_ACCEPTANCE_LOCAL_REPORT.md`.

Forbidden:

- `backend/platform-api/app/**`
- `postgres/**`, `n8n/**`
- production, remote HTTP, live Telegram, Sheets, runtime databases, deploy,
  raw Telegram sends, secrets and `.env` files.

## A. Target and safety

1. Reuse the existing localhost-only Core lab target pattern. Reject non-local
   hostnames and ports outside the Core lab configuration.
2. `--dry-run` must make zero HTTP calls and output exact selected counts.
3. `--check-target` checks health/OpenAPI only.
4. Default target is a gitignored local copy. The committed example contains no
   credential, token or production URL.
5. Timeout: 5 seconds per assertion turn, 300 seconds per run. Always print
   cleanup/report status even after a failure.

## B. HTTP execution

Create `run_human_language_rails.py --live` or an equivalent explicit command.

- Execute all accepted assertion turns, including setup turns required to build
  context in their unique per-flow session.
- Do not send setup turns as assertions. A failed setup makes only the related
  assertion `NOT_RUN_SETUP`, never a false pass.
- Preserve execution order by `flow_id` + `turn_index`.
- Run each flow with a run-scoped session suffix so a previous run cannot leak
  context.
- POST only to the existing local Core advisor API using the same tenant/ref/
  country envelope as Golden acceptance.
- For each assertion check: HTTP status, `answer_mode`, `gap_kind` when set,
  `must_contain_all`, `must_contain_any`, `must_not_contain`, and declared
  context transition when the API exposes it.
- `expected_rail` is an audit dimension, not an invented Core field. Report it
  on every result.

## C. Reporting and failure truth

Create one JSON and one Markdown report per `run_id`, under gitignored reports.
Report must show separately:

- selected / executed / PASS / FAIL / NOT_RUN_SETUP / timeout;
- accepted cases by rail and priority;
- pending surface and pending policy counts, explicitly `NOT_RUN_PENDING`;
- latency p50/p95/max per priority;
- failures grouped by `expected_rail`, `expected_mode`, `gap_kind`, and source;
- raw response storage may be local/gitignored, redacted and capped.

No automatic baseline update. A baseline may be created only with
`--accept-baseline`, only after a fully executed run with zero failures and no
unasserted accepted rows. Failed and dry-run executions must never update it.

## D. Local Core lab integration

Add:

```bash
python backend/platform-api/scripts/run_local_core_lab.py \
  --e2e --human-language-rails --golden-master-seed
```

Requirements:

- existing master seed compiles and applies before Core starts;
- HLR check-target, P0, full accepted corpus, and pending summary run even if
  P0 has failures, so one invocation yields the complete truthful backlog;
- Core container/network are cleaned up in `finally`; persistent local
  Postgres volume is not deleted;
- no change to the behavior of existing lab flags.

## E. Tests

Add mocked tests for at least:

- localhost target rejection;
- dry run makes zero HTTP calls;
- only accepted rows execute;
- pending rows reported but not counted as pass/fail;
- context setup/order and unique sessions;
- `must_contain_all` versus `must_contain_any`;
- failed setup -> `NOT_RUN_SETUP`;
- timeout and all-phases-after-P0-fail;
- baseline guards;
- per-run distinct report paths.

Run:

```bash
cd /d/Projects/WHIEDA
python qa/human_language_rails/run_human_language_rails.py --offline
python qa/human_language_rails/run_human_language_rails.py --dry-run
python -m pytest backend/platform-api/tests/test_human_language_rails_corpus.py \
  backend/platform-api/tests/test_human_language_rails_http.py -q
```

## Acceptance and deliverable

Commit one focused QA/lab change. Report honestly whether Docker E2E was run.
Do not fix Core failures in this task. List every live failure by case id and
rail in `HUMAN_LANGUAGE_RAILS_HTTP_ACCEPTANCE_LOCAL_REPORT.md`.
