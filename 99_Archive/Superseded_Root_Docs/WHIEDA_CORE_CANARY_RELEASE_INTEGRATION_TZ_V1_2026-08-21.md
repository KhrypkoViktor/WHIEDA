# Core Gate F: Canary Release Integration V1

Дата: 2026-08-21  
Исполнитель: Core developer  
Статус: локальная интеграция; без shared staging, production, n8n и webhook.

## Цель

Собрать B1, B2, C, D, E и R1 в одну чистую release-ветку, которую lead сможет
применить в отдельный shared staging без переноса грязного
`feat/platform-scale-core`.

## Старт

Создать новый clean worktree от текущего `origin/master` и ветку
`core-tenant-canary-release`.

Перенести в порядке зависимостей только commits:

1. `050f946` Core binding;
2. `5aee716` staging apply;
3. `d053ee2` shared-staging harness;
4. `44aa215` durable inbox;
5. `42887f9` tenant advisor plane;
6. `6d83fa6` release package firewall;
7. `4744737` advisor regression repair.

Если commit не накладывается, исправлять только конфликт в пределах его
исходного scope. Не брать ничего из грязного worktree, cart/site/n8n или
несвязанных веток.

## Acceptance

- лог branch должен содержать все семь функциональных commit или их точные
  конфликт-resolved эквиваленты с таблицей соответствия;
- `python postgres/scripts/run_local_staging_proof.py` -> PASS, 16 migrations
  x2;
- full B1-E+R1 suite -> 186+ passed, 0 deselect;
- local Core Docker стартует на этой release ветке и проходит binding / durable
  inbox / tenant isolation / release-package smoke;
- `--plan` release harness перечисляет 16 файлов; `--apply` по-прежнему
  отказывает;
- NSP data и binding не создаются самим Core apply.

## Результат

Один integration commit только если нужен для конфликтов; иначе не переписывать
историю. Push `origin/core-tenant-canary-release`.

В `D:\Projects\_peer-sync\nsp-whieda\from-core` положить:

- `CORE_GATE_F_CANARY_RELEASE_MANIFEST_V1.json`;
- `CORE_GATE_F_CANARY_RELEASE_LOCAL_REPORT.md`;
- таблицу source commit -> итоговый commit;
- точные команды и фактические результаты.

Не трогать shared staging, production, Supabase, n8n, `setWebhook`, токены или
NSP package. Следующий шаг после F выполняет lead только на отдельной staging
DB.
