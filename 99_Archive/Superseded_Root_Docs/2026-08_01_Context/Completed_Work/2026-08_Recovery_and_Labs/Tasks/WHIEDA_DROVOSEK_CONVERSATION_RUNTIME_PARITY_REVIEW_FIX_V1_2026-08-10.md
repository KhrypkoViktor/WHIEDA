# Drovosek review fix: Conversation E2E and Partner Runtime Parity

## Status before this fix

Do not claim either block fully accepted yet.

Architect ran the real local Docker command:

```powershell
python backend/platform-api/scripts/run_local_core_lab.py --e2e --conversation-reliability --skip-build
```

Infrastructure passed: Core ready, HTTP contract smoke passed, acceptance smoke
8/8, verify_e2e passed, cleanup passed.

Conversation result after fixing the runner contract locally:

```text
39 flows / 84 turns
28 flows passed
71 turns passed
13 real failures remain
```

The initial `0/83` result was a lab defect: every turn lacked
`max_latency_ms`, required by the parity assertion engine. Do not reintroduce
that defect. The locally corrected generator now writes latency limits and the
runner passes the actual HTTP payload to assertion checks.

External Partner parity was also run read-only against the real runtime:

```text
master active partners: 6
runtime active actors: 8
active profiles: 6
allowlisted roots: viktor, viktor-test
disable candidates: 0
summary: safe
```

No runtime mutation was made.

---

## Part 1: Conversation runner hardening

Keep the current local fixes or implement their equivalent:

1. Every generated turn must include `max_latency_ms`:
   - P0: 2000 ms;
   - P1: 4000 ms.
2. Pass the original parsed HTTP response to the parity assertion layer as
   `raw_payload`, so its context assertions inspect the real envelope.
3. Corpus lint must reject a turn without `max_latency_ms`.
4. Add a regression test proving a generated valid flow cannot reach
   `UNASSERTED` merely because a required assertion field is absent.
5. Regenerate the JSONL corpus. Current expected count is **39 flows / 84 turns**.

---

## Part 2: fix or consciously correct the 13 E2E failures

Use actual local Core responses. Do not weaken a product assertion simply to
turn the report green. If an expectation contradicts the documented conversation
state contract, correct the expectation and add a `rationale`.

| Flow / turn | Current result | Required decision |
|---|---|---|
| F08 / 2 | `полын` missing | Fix safe selection of the wormwood toothpaste or use a product-correct approved selection phrase. |
| F12 / 2 | `Драгоцен` missing | Fix blue elixir resolution/content recognition. |
| F14 / 2 | right comparison product `PRO` lost | Fix comparison context; photo after comparison must target right product. |
| F19 / 1-2 | no-photo text and `missing_resource` absent | Fix No Blind Zone missing-resource envelope and its typed `gap_kind`. |
| F20 / 1-2 | no-certificate text absent | Fix the honest no-certificate resource response; no invented document. |
| F21 | bare price has no `unknown_followup` | Expose existing No Blind Zone typed gap in Core response envelope. |
| F22 | bare video has no `unknown_followup` | Same: typed gap in response envelope. |
| F24 / 2 | external topic becomes `unknown_product` | After a known product, unrelated external topic must not reuse it; return `unsupported_topic`. |
| F25 session B | bare follow-up has no `unknown_followup` | Fix/enforce session isolation and typed gap. |
| F26 / 2 | expected `knowledge_gap`, actual `clarification` | The state contract says invalid selection keeps clarification pending. Correct corpus to `clarification`, with rationale; do not force a knowledge gap. |
| F37 / 2 | PRO video returns clarification | Fix explicit `PRO` product resolution before video follow-up. |

For every Core fix add a focused regression test. Preserve approved cards,
catalog values, Sheets and production settings.

Required acceptance:

```powershell
python -m pytest backend/platform-api/tests -q
python qa/conversation_reliability/run_conversation_reliability.py --offline
python backend/platform-api/scripts/run_local_core_lab.py --e2e --conversation-reliability --skip-build
```

The last command must show all 39 flows / 84 turns passing. If any remain,
report them explicitly; do not call the block complete.

---

## Part 3: Partner Runtime Parity report correction

The real read-only run is safe but its output is misleading:

```text
master_only_needing_upsert = all 6 master actors
```

This is wrong. It must contain **only** master actor IDs absent from the runtime
actor set. Add explicit, disjoint buckets:

- `in_sync_master_actors`;
- `master_only_needing_upsert`;
- `runtime_only_disable_candidates`;
- `allowlisted_platform_roots`.

For the current live snapshot expected result is:

```text
in_sync_master_actors: 6
master_only_needing_upsert: 0
runtime_only_disable_candidates: 0
allowlisted_platform_roots: 2
summary: safe
```

Add a fixture/test where one master actor is absent and one runtime actor is
retired. Confirm the buckets are disjoint and exhaustive for active actors.

Keep external mode strictly `BEGIN READ ONLY` + SELECTs. `--apply` combined
with `--runtime-dsn-env` must still abort before opening the connection.

## Delivery

Create separate commits for Conversation and Partner corrections. Final response
must include actual E2E output, exact flow/turn count, and the real read-only
parity bucket counts.
