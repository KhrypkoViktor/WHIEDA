# Review Fix: Human Language Rails Corpus V1.1

The offline corpus is useful and its source provenance is real, but it is not
accepted yet. Make the following QA-only corrections. Keep the hard scope of
the original task: do not modify `app/**`, `n8n/**`, `postgres/**`, Sheets,
runtime, Telegram, or production.

## Findings to correct

### 1. Separate accepted rails from known surface gaps

The accepted corpus currently contains greeting/capabilities/smalltalk cases
classified as `universal_menu`, but their expected mode and markers still
describe the old greeting/help surface. A weak `must_contain_any: ["WHIEDA",
...]` makes that look green even though it does not verify the new rail.

For every known `surface_gap`:

- move it out of the live-acceptance assertion corpus into an explicit pending
  fixture, or add `acceptance_status: "pending_surface"` and ensure the normal
  runner excludes it from PASS totals;
- keep the user phrase, rail, source and rationale in the backlog/pending
  fixture so it cannot disappear;
- do not change Core wording or modes.

For accepted `universal_menu` assertions, require both durable markers as
`must_contain_all`:

- `Я лучше всего помогаю с товарами WHIEDA`
- `Выберите направление`

`must_contain_any` is allowed only when genuinely multiple product names or
equivalent content labels are acceptable. The offline runner and lint must
support and test `must_contain_all`.

### 2. Fix flow accounting

Current build output says 218 turns across 168 flows, while case data contains
162 distinct `flow_id` values. Make one truth:

- every metadata flow has at least one corpus turn;
- every corpus flow has exactly one metadata row;
- report `flows_total`, `flows_metadata_rows`, and distinct corpus flow ids
  from the same set;
- multi-turn count must be derived from actual corpus turns, not a hand count.

Add a test that fails on orphan metadata and metadata missing for a corpus
flow.

### 3. Make rail acceptance explicit

Add `acceptance_status` to every assertion turn, with only:

- `accepted` — eligible for future HTTP acceptance;
- `pending_surface` — known Telegram/API surface gap;
- `pending_policy` — owner decision required.

The default offline PASS totals must report all three separately and must not
call pending rows accepted. The minimum rail distribution in the original task
applies to `accepted` rows only.

### 4. Preserve honest scope

Do not weaken the prohibited-fragment checks. Do not turn pending medical or
pediatric cases into fake green assertions. No Core code, no production,
no data writes.

## Required proof

```bash
cd /d/Projects/WHIEDA
python qa/human_language_rails/build_corpus.py
python qa/human_language_rails/run_human_language_rails.py --offline
python -m pytest backend/platform-api/tests/test_human_language_rails_corpus.py -q
```

The report must show:

- accepted/pending counts per rail;
- one reconciled flow count;
- exact `must_contain_all` universal-menu rule;
- source/provenance and malformed counts;
- no claim of HTTP, Telegram or production verification.

One focused commit only for this corpus/review fix.
