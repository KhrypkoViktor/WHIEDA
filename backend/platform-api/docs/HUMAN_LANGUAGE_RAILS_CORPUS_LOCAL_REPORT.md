# Human Language Rails Corpus — Local Report

Mode: offline QA corpus only. No Core, Telegram, Postgres or Sheets changes.

## Totals

- Assertion turns (all): **223**
- Accepted assertions: **183**
- Pending surface: 38
- Pending policy: 2
- Setup turns: 35
- Flows (reconciled): **202** metadata rows=202
- Multi-turn flows: 35
- Malformed accepted assertions: 72
- Vague→useful flows: 8
- Context media/basket follow-ups: 11

## Accepted by rail (minimums apply here only)

- `direct_answer`: 55 accepted (minimum 55)
- `product_choices`: 33 accepted (minimum 25)
- `task_selection`: 46 accepted (minimum 30)
- `universal_menu`: 49 accepted (minimum 40)

## All assertions by rail (includes pending)

- `direct_answer`: 55 total
- `product_choices`: 33 total
- `task_selection`: 48 total
- `universal_menu`: 87 total

## Universal menu rule (accepted only)

Accepted `universal_menu` rows require `must_contain_all`:
- `Я лучше всего помогаю с товарами WHIEDA`
- `Выберите направление`

## Source kinds (accepted)

- `advisor_experience_contract`: 19
- `catalog_experience`: 4
- `conversation_reliability`: 39
- `human_language_rails_backlog`: 1
- `no_blind_zone`: 74
- `telegram_experience`: 46

## Validation

PASS


## Reproduce

```bash
python qa/human_language_rails/build_corpus.py
python qa/human_language_rails/run_human_language_rails.py --offline
python -m pytest backend/platform-api/tests/test_human_language_rails_corpus.py -q
```

## Limitations

- Corpus validates rail expectations offline; it does not execute Core or Telegram.
- `pending_surface` rows track greeting/capabilities/smalltalk gaps; see `fixtures/pending_assertions.jsonl`.
- `pending_policy` rows require owner decision; they are excluded from accepted minimums.
