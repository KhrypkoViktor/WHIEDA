# Core Gate E: Tenant Release Package Firewall V1

Дата: 2026-08-21  
Владелец: Core lead  
Исполнитель: Core developer  
Статус: local/staging implementation; без shared staging, production и publish.

## Зачем

NSP и следующий tenant должны загружаться не ручным копированием SQL и не
seed из чужого проекта, а через единый release package. В runtime проходят
только явно approved товары и связанные с ними данные. Candidate, review,
blank или чужой tenant не может стать ответом советника.

## Старт и scope

Новый clean worktree от `42887f9`, ветка `core-tenant-release-package`.

Разрешено:

- generic tenant import/release tooling в `backend/platform-api/scripts/**`;
- additive SQL/staging tables и staging proof;
- `qa/tenant_release_package/**` и изолированные fixtures;
- необходимые repository/read checks и тесты;
- focused report/manifest.

Не трогать: n8n, Sheets, live NSP/WHIEDA rows, setWebhook, production,
shared staging, сайт, cart, admin cabinet. Не merge в грязный
`feat/platform-scale-core`. Никаких `if tenant == nsp-maxim` в Core.

## Формат release package

Определить versioned manifest и schema. Пакет содержит только tenant data:

- tenant id + display configuration;
- products, aliases, prices/PV, cards, media/PDF/video/certificates, FAQ;
- business-review decisions;
- hashes файлов, package id/version, source references;
- явный `release_status`.

NSP workbench будет готовить один такой package, но формат не должен зависеть
от имён файлов NSP. Для тестов сделать synthetic `tenant-alpha` и
`tenant-beta`, а не копию реального каталога.

## Publication firewall

1. `--validate` только читает package и строит отчёт: сеть и БД не нужны.
2. `--stage` допускается только к local verify DB и создаёт отдельные staging
   rows/run, никогда не трогает runtime tables.
3. `--build-release-candidate` создаёт candidate только из `approved` rows.
4. `--publish` в Gate E отсутствует либо всегда отказывает.
5. Для published candidate обязательны: tenant, SKU, canonical product,
   approved card, источник, цена или честный `price_missing`, и media state.
6. `review_required`, `blocked`, `candidate`, отсутствующий source/hash,
   duplicate SKU/alias ambiguity или foreign tenant -> reject/review, не
   runtime.
7. Идемпотентность: тот же package повторно не плодит записи; изменённый hash
   становится новой версией с linkage, старый кандидат superseded.
8. Любой `tenant_id` берётся из manifest и сравнивается со всеми строками.
   Строка другого tenant aborts whole package.

## Проверки

- valid `tenant-alpha` package: validate -> stage -> release candidate;
- тот же package повторно: reused, 0 дублей;
- одна изменённая approved карточка: одна новая версия;
- mixed tenant row: abort без partial rows;
- review/blocked product не попадает в candidate;
- price/media/card gaps честно отмечены, не заменяются нулём/WHIEDA fallback;
- alias collision и hash mismatch требуют review;
- parallel attempts не создают два current candidate;
- local staging apply x2 и RLS; advisor tenant isolation Gate D не ломается;
- весь B1-C-D-E suite зелёный.

## Результат

Один focused commit/push и в `D:\Projects\_peer-sync\nsp-whieda\from-core`:

- `CORE_GATE_E_TENANT_RELEASE_PACKAGE_MANIFEST_V1.json`;
- `CORE_GATE_E_TENANT_RELEASE_PACKAGE_LOCAL_REPORT.md`;
- schema/example package и команды фактических тестов.

После Gate E NSP developer адаптирует свой 19-SKU review package к этому
формату. Затем lead выполняет первый real shared staging gate с правильным
staging DSN и только approved canary subset.
