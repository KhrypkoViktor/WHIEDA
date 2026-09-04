# Review fix for Telegram Advisor A+B, then Blocks C+D

Read and continue:

`WHIEDA_DROVOSEK_RESTORE_TELEGRAM_ADVISOR_EXPERIENCE_TASK_V1_2026-08-11.md`

Do not wait for another confirmation. Fix the review items below, then execute
Blocks C and D from the parent task in the same working run.

## A+B review result

Focused independent tests passed:

```text
34 passed: product-card renderer + sequencer + Telegram processor
```

Block B is accepted in its explicit one-Core-container scope. Its documentation
correctly states that a durable multi-replica queue is a future upgrade.

Block A has two corrections before acceptance.

### A review fix 1: preserve line breaks before bullet splitting

`telegram_card.py::_split_bullets()` currently calls `_clean_field()` first.
That helper collapses all whitespace, including `\n`, to a normal space. The
following newline split is therefore unreachable for real multiline Sheet cells.

Fix it so that:

1. raw text is stripped of literal Markdown/HTML safely without collapsing
   line breaks;
2. newline-separated values produce one `•` item per nonempty line;
3. semicolon-separated compact lists still produce bullets;
4. prose remains one paragraph.

Add a regression test with a real-style multiline card field and assert three
separate bullet lines in the final Telegram text.

### A review fix 2: honest card footer

Current footer promises a certificate for every product. Change it to:

```text
Могу подсказать цену/PV, фото, видео, сертификат или сравнение с другим товаром — если эти материалы есть в базе.
```

Keep photo-first. Do not change source master text.

Create a focused review-fix commit before C+D.

## Block C: useful first-turn and goal routing

Implement Block C exactly as written in the parent task. Two non-negotiable
details from the owner test:

1. `пивка хочешь?` must be tested independently after `что можешь?` in the
   same chat and must never receive the capability menu.
2. discomfort wording is a safe routing problem, not a request to invent
   medical claims. It needs a helpful next question and typed gap/boundary,
   never an “unknown catalogue product” reply.

For cart mutation, store only the minimum active cart state per session needed
for add/remove/recalculate. Do not make it a global cart or modify prices.

## Block D: realistic acceptance proof

Implement Block D exactly as written in the parent task. The local fixture must
be compiled from the immutable snapshot, and all four E2E categories must be
reported separately:

- presentation;
- ordering/idempotency;
- structured routes;
- safe intentional gaps and remaining data gaps.

## Required proof

```powershell
python -m pytest backend/platform-api/tests -q
python qa/telegram_experience/compile_snapshot_fixtures.py
python qa/telegram_experience/run_telegram_experience.py --offline
python backend/platform-api/scripts/run_local_core_lab.py --e2e --telegram-experience
```

The final report must give real flow/turn counts, explicit failures if any,
and separate commits for review fix, C, and D. No production deployment,
Sheets, n8n, Dify or runtime database changes.
