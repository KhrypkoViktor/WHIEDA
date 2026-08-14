# Human Language Rails Backlog — 2026-08-14

Offline backlog for corpus expectations that are not yet fully implemented in Core/Telegram surfaces.
Pending rows live in the main corpus with `acceptance_status` and are mirrored to
`fixtures/pending_assertions.jsonl`.

## surface_gap

| ID | User pattern | Expected rail | Notes |
|---|---|---|---|
| SGF-001 | Greeting (`привет`, `хай`, `здарова`) | `universal_menu` | Current surface uses `structured_business` greeting text without the full GAP universal menu phrase pair. Corpus marks these `pending_surface`. |
| SGF-002 | Capabilities slang (`че ты можеь?`, `а что моешь`) | `universal_menu` | Routed to capabilities/help copy; lacks exact `Я лучше всего помогаю с товарами WHIEDA` wording. |
| SGF-003 | Smalltalk fragments (`ку`, `ок`, `спасибо`) | `universal_menu` | Need stable menu fallback with direction buttons, not conversational dead-end. |
| SGF-004 | `покажи любой товар` | `universal_menu` | Telegram navigation may intercept before advisor free-text; kept as **accepted** expectation with strict `must_contain_all` until surface parity is proven. |

## policy_gap

| ID | User pattern | Expected rail | Notes |
|---|---|---|---|
| PGF-001 | `болят колени` / `болит спина` | `task_selection` | Safe orientation should offer goal/direction selection; must not drift into treatment recommendation. Corpus marks `pending_policy`. |
| PGF-002 | `что для ребёнка` | `task_selection` | Excluded from corpus until pediatric policy is approved; do not mine chat testimonials. |
| PGF-003 | Medical follow-up after product card | `task_selection` or boundary | See `CONV-F23-medical-boundary`; classify separately from product recommendation. |

## Accepted universal menu rule

Accepted rows require **both** markers via `must_contain_all`:

- `Я лучше всего помогаю с товарами WHIEDA`
- `Выберите направление`

## Counts

- `surface_gap`: 4 (3 pending_surface + 1 accepted probe SGF-004)
- `policy_gap`: 3 (PGF-001 represented in corpus as `pending_policy`)

Corpus command:

```bash
python qa/human_language_rails/run_human_language_rails.py --offline
```
