-- Synthetic advisor_gap events for local gap operator E2E (whieda tenant only).

insert into interaction_events (
  event_id, tenant_id, session_id, event_type, idempotency_key, payload, created_at
) values
(
  gen_random_uuid(), 'whieda', null, 'advisor_gap',
  'advisor_gap:fixture:medical:1',
  '{"channel":"advisor","session_ref":"abc123deadbeef","question_normalized":"как лечить диабет","gap_kind":"medical_or_safety_boundary","detected_product":null,"trace_id":"trace-med-1","answer_mode":"clarification","repeat_count":2,"first_seen_at":"2026-08-10T08:00:00+00:00","last_seen_at":"2026-08-10T09:00:00+00:00"}'::jsonb,
  '2026-08-10T08:00:00+00:00'
),
(
  gen_random_uuid(), 'whieda', null, 'advisor_gap',
  'advisor_gap:fixture:unknown:1',
  '{"channel":"advisor","session_ref":"feedface123456","question_normalized":"расскажи про xyzunknown123","gap_kind":"unknown_product","detected_product":null,"trace_id":"trace-unk-1","answer_mode":"knowledge_gap","repeat_count":1,"first_seen_at":"2026-08-10T07:00:00+00:00","last_seen_at":"2026-08-10T07:00:00+00:00"}'::jsonb,
  '2026-08-10T07:00:00+00:00'
),
(
  gen_random_uuid(), 'whieda', null, 'advisor_gap',
  'advisor_gap:fixture:missing:1',
  '{"channel":"advisor","session_ref":"cafebabecafebab","question_normalized":"видео товар без фото","gap_kind":"missing_resource","detected_product":"Товар без фото (тест)","trace_id":"trace-miss-1","answer_mode":"clarification","repeat_count":1,"first_seen_at":"2026-08-10T06:00:00+00:00","last_seen_at":"2026-08-10T06:00:00+00:00"}'::jsonb,
  '2026-08-10T06:00:00+00:00'
),
(
  gen_random_uuid(), 'test-acme', null, 'advisor_gap',
  'advisor_gap:fixture:acme:1',
  '{"channel":"advisor","session_ref":"acmeonly123456","question_normalized":"цена","gap_kind":"unknown_followup","detected_product":null,"trace_id":"trace-acme-1","answer_mode":"clarification","repeat_count":1,"first_seen_at":"2026-08-10T05:00:00+00:00","last_seen_at":"2026-08-10T05:00:00+00:00"}'::jsonb,
  '2026-08-10T05:00:00+00:00'
)
on conflict (tenant_id, idempotency_key) do nothing;
