# No Blind Zone — operator sample (fixtures only)

Read-only grouped unresolved questions for owner review. **No production data** — synthetic local fixtures.

| gap_kind | question (normalized) | count | last_seen (fixture) |
|---|---|---:|---|
| unknown_followup | цена | 3 | 2026-08-09T20:15:00Z |
| unknown_product | расскажи про xyzunknown123 | 2 | 2026-08-09T20:14:12Z |
| ambiguous_product | паста | 2 | 2026-08-09T20:13:44Z |
| ambiguous_product | активатор | 1 | 2026-08-09T20:12:30Z |
| missing_resource | фото товар без фото | 1 | 2026-08-09T20:11:05Z |
| missing_resource | видео товар без сертификата | 1 | 2026-08-09T20:10:51Z |
| unsupported_topic | сравни фейк1 и фейк2 | 1 | 2026-08-09T20:09:22Z |
| medical_or_safety_boundary | как лечить диабет | 1 | 2026-08-09T20:08:10Z |
| unsupported_topic | расскажи про международную логистику whieda | 1 | 2026-08-09T20:07:33Z |
| unknown_followup | видео | 1 | 2026-08-09T20:06:18Z |

## Notes

- `session_ref` in stored events is a 16-char SHA-256 prefix — no Telegram chat id or phone.
- Repeated identical gaps in one session increment `repeat_count` within a 30-minute window; a later recurrence becomes a new queue event.
- Query for local lab: `fetch_gap_operator_summary(tenant_id='whieda')` in `app.advisor.gap`.
