# Core Conversation Reliability Lab — Report

**Date:** 2026-08-10  
**Status:** offline `PASS` / Docker HTTP E2E `NOT_RUN` (Core not up in this session)

## Deliverables

| Artifact | Purpose |
|---|---|
| `backend/platform-api/docs/ADVISOR_CONVERSATION_STATE_CONTRACT_V1.md` | Session context contract |
| `qa/conversation_reliability/whieda_conversation_flows_v1.jsonl` | **39 flows / 83 turns** (≥24 / ≥80 required) |
| `qa/conversation_reliability/run_conversation_reliability.py` | Offline lint + optional `--live` HTTP |
| `qa/conversation_reliability/lab/` | Corpus lint, assertions, runner, target guard |
| `backend/platform-api/tests/test_conversation_reliability.py` | Lab infrastructure tests |
| `run_local_core_lab.py --e2e --conversation-reliability` | Lab gate hook |

## Corpus coverage (named flows)

Includes all required patterns: card→price/photo/video/cert/подробнее, typo & ambiguous activator, pasta/belt/color elixirs, comparison follow-ups, product switch, cart known/unknown, promo, no-photo/no-cert, bare price/video, medical boundary, external topic, session isolation.

## Verification (this session)

```powershell
python qa/conversation_reliability/run_conversation_reliability.py --offline
```

- Corpus lint: **PASS** (39 flows, 83 turns)
- Unit tests: **PASS**
- HTTP E2E: **NOT_RUN** — requires `run_local_core_lab.py --e2e --conversation-reliability` with Docker Core on `127.0.0.1:8080`

## Production

Not run against production, n8n, Sheets, or Telegram config. Core HTTP contract only (`/v1/advisor/query`).

## Known gaps

Live HTTP may surface P1 flow failures (starter basket, color elixir shorthand) already tracked in parity corpus as `core_bug`; conversation expectations align with parity/local seed, not weakened without rationale.
