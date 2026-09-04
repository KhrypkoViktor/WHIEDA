# Core Conversation Reliability Lab — Report

**Date:** 2026-08-10  
**Status:** Docker HTTP E2E **PASS** (`39/39` flows, `84/84` turns)

## Review-fix summary

| Area | Fix |
|---|---|
| Lab transport | `build_advisor_request` now sends per-flow `session` (was stuck on `acceptance-lab`) |
| Runner | `max_latency_ms` lint + run-scoped session suffix; `raw_payload` passed to assertions |
| Core engine | Media vs product-name disambiguation; PRO photo after compare; gap envelopes; dollar-rate OOS |
| Corpus | F26 invalid selection expects `clarification`; regenerated **39 flows / 84 turns** |
| Local seed | `полын` alias, PRO video resource |

## Verification

```powershell
python -m pytest backend/platform-api/tests -q
python qa/conversation_reliability/run_conversation_reliability.py --offline
python backend/platform-api/scripts/run_local_core_lab.py --e2e --conversation-reliability --skip-build
```

Results (2026-08-10):

- Unit tests: **573 passed**
- Corpus lint: **PASS** (39 flows, 84 turns)
- Conversation HTTP: **39/39 flows, 84/84 turns passed**
- Local Core E2E orchestrator: **PASS** (preflight 8/8, verify_e2e PASS)

## Production

Not run against production, n8n, Sheets, or Telegram config.
