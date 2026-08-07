-- Staging seed for journey/onboarding local E2E (tenant whieda only)
begin;

insert into onboarding_programs (tenant_id, program_id, program_key, title, duration_days, is_default)
values (
  'whieda',
  'a0000000-0000-4000-8000-000000000001'::uuid,
  'starter_7d',
  '7-дневный старт',
  7,
  true
)
on conflict (tenant_id, program_key) do nothing;

insert into onboarding_program_days (tenant_id, program_id, day_number, title, body_markdown, cta_text)
values
  ('whieda', 'a0000000-0000-4000-8000-000000000001'::uuid, 1, 'День 1', 'Знакомство с каталогом.', 'Открыть каталог'),
  ('whieda', 'a0000000-0000-4000-8000-000000000001'::uuid, 2, 'День 2', 'Первый продукт.', 'Выбрать продукт')
on conflict (tenant_id, program_id, day_number) do nothing;

commit;
