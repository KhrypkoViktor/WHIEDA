# WHIEDA Drovosek: Human Language Rails Corpus V1

## Goal

Build an offline regression corpus for the Telegram advisor's universal user
experience. The corpus must answer one product question: does a messy human
message land on a useful WHIEDA rail instead of a technical fallback?

This is a QA/data task. Do not modify Core runtime behavior.

## Product rails

Every assertion turn must be classified into exactly one of these rails:

1. `direct_answer` — intent and product are understood; expect the relevant
   structured mode: card, price, media, comparison, basket, business answer.
2. `product_choices` — the name is uncertain or genuinely ambiguous; expect
   two or three understandable product choices, never a raw error.
3. `task_selection` — the task is understood but a product is not; expect a
   compact selection route by goal or direction.
4. `universal_menu` — message is vague, off-topic, slang, greeting after a
   dead end, or cannot be classified; expect the universal WHIEDA menu rail.

The visible wording of the universal rail must contain:

`Я лучше всего помогаю с товарами WHIEDA`

and:

`Выберите направление`

Forbidden visible fragments for every rail:

- `не знаю`
- `нет в базе`
- `не смог обработать`
- `передам на проверку`
- `needs human review`
- stack traces or internal identifiers.

## Hard scope boundary

Allowed changes:

- `qa/human_language_rails/**`
- `backend/platform-api/tests/test_human_language_rails_corpus.py`
- `backend/platform-api/docs/HUMAN_LANGUAGE_RAILS_CORPUS_LOCAL_REPORT.md`

Read-only inputs:

- `WHIEDA_LIVE_STATUS.md`
- `WHIEDA_ADVISOR_EXPERIENCE_CONTRACT_V1_2026-08-13.md`
- `qa/telegram_golden/**`
- `qa/conversation_reliability/**`
- `qa/no_blind_zone/**`
- `qa/telegram_experience/**`
- `qa/telegram_navigation/**`
- `qa/catalog_experience/**`
- approved structured master snapshots under `n8n/live-exports/structured-master/`.

Forbidden:

- `backend/platform-api/app/**`
- `postgres/**`
- `n8n/**`
- master Google Sheets, runtime database, live Telegram, deploy, production,
  network calls, generated runtime reports, `.env` files.

## Corpus requirements

Create `qa/human_language_rails/whieda_human_language_rails_v1.jsonl`.

Minimum: **150 assertion turns**, including at least **35 multi-turn flows**.
No duplicate `(normalized user_text, context_before, expected_rail)` tuples.

Required minimum distribution:

| Rail | Minimum | Examples |
|---|---:|---|
| `direct_answer` | 55 | product typos, prices, cards, photo/video/certificate, comparison, basket, business FAQ |
| `product_choices` | 25 | `активатор`, `паста`, colour-only elixir, `пояс`, uncertain product nickname |
| `task_selection` | 30 | what to choose for home, gift, salon, recovery routine, start budget, selection request |
| `universal_menu` | 40 | greetings, slang, fragments, accidental keyboard input, harmless OOS, vague continuation |

At least 45 examples must be intentionally malformed human text: typos,
missing words, wrong keyboard layout, colloquial phrases, incomplete follow-up,
or one-word messages. Do not invent product facts; only vary the user's wording.

At least 20 flows must test the transition from a vague first turn to a useful
second turn. At least 10 flows must test product context followed by `цена`,
`фото`, `видео`, `сертификат`, `сравни`, or basket mutation.

## Case schema

Use JSONL. Each assertion case must include:

```json
{
  "case_id": "HLR-P0-001",
  "priority": "P0",
  "flow_id": "HLR-FLOW-001",
  "turn_index": 2,
  "turn_role": "assertion",
  "user_text": "че ты можеь?",
  "context_before": [],
  "expected_rail": "universal_menu",
  "expected_mode": "capabilities",
  "must_contain_any": ["WHIEDA", "товар"],
  "must_not_contain": ["не знаю", "нет в базе", "не смог обработать"],
  "expected_context_transition": {"sets": [], "requires": []},
  "source": {"kind": "telegram_experience", "ref": "relative/path:case-id"},
  "rationale": "Why this belongs on this rail."
}
```

Setup turns may omit assertion fields but must have `turn_role: "setup"`.
`source.kind` and `source.ref` are mandatory and must resolve to an existing
local input. A mined phrase without provenance is rejected by lint.

Use `expected_mode` only where a current contract proves it. For a phrase whose
surface has not yet been implemented, mark it `surface_gap` in a separate
backlog; do not fabricate an expectation and do not modify Core.

## Explicit exclusions

- This is not a list of every Russian phrase.
- Do not put raw medical testimonials, blocked RAW material, phone numbers,
  private names, or unpublished claims into the corpus.
- Do not treat a safety boundary as a product recommendation. It belongs on
  `task_selection` or `universal_menu` only when the current UX contract says
  so; otherwise record it as a `policy_gap` backlog item.
- Do not copy long answer texts. Assertions should use short durable markers.

## Deliverables

1. `qa/human_language_rails/` with:
   - corpus JSONL;
   - `flows_v1.jsonl` or flow metadata;
   - `lab/corpus.py` lint/metrics;
   - `run_human_language_rails.py --offline`;
   - `HUMAN_LANGUAGE_RAILS_BACKLOG.md` with `surface_gap` and `policy_gap`;
   - fixtures only when needed.
2. `backend/platform-api/tests/test_human_language_rails_corpus.py` with
   schema, source, duplicate, distribution, forbidden-fragment, and flow tests.
3. `backend/platform-api/docs/HUMAN_LANGUAGE_RAILS_CORPUS_LOCAL_REPORT.md`:
   totals per rail, malformed count, source distribution, flow count, backlog
   counts, exact commands and honest limitations.

## Acceptance

```bash
cd /d/Projects/WHIEDA
python qa/human_language_rails/run_human_language_rails.py --offline
python -m pytest backend/platform-api/tests/test_human_language_rails_corpus.py -q
```

Both must pass. Do not claim live Core, Telegram, or production verification.
Use one focused commit containing only this QA corpus and its report.
