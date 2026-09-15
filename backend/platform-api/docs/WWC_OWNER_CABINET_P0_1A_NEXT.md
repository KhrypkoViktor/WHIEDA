# WWC Owner Cabinet P0.1A — next staging slice

**Follows:** P0.1 auth + read-only admin API (accepted contract freeze target).

## Goals

1. Persist `visitor_session_id` on website lead ingest (top-level column or guaranteed metadata key) without changing public lead routing semantics.
2. Read-only admin timeline: `GET /v1/admin/interaction-events?session_id=` backed by existing `interaction_events` table.
3. Staging-only site flag (`journeyApi`) to populate sessions/events for cabinet QA — **no production deploy in P0.1A**.

## Non-goals

- No new writers to `website_events`.
- No Metrika import.
- No write admin APIs beyond auth.

## Acceptance

- Lead detail shows linked `visitor_session_id` when present.
- Admin timeline returns ordered events with allowlisted payload fields.
- Pytest covers cross-tenant denial + payload sanitization.
- Staging smoke: lead with session → admin detail → events timeline.
