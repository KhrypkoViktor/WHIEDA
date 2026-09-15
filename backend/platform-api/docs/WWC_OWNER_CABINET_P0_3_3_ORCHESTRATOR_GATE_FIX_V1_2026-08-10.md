# WWC Owner Cabinet — P0.3.3: исправление стоп-шлюза staging deploy

**Исполнитель:** Composer.  
**Цель:** исключить запуск staging deploy при любом неуспешном precheck.

## Причина

Текущий `precheck_staging_cabinet_p0_3_2_2026-08-10.py` возвращает:

- `1` — `missing_server_secret`;
- `2` — `missing_site_ssh`;
- `3` — любой иной провал проверки, в том числе текущий `/api/v1/admin/me → 404`.

Но `run_staging_cabinet_p0_3_2_2026-08-10.py --apply` останавливается только на коде `1`. При коде `2` или `3` он может пойти дальше к SQL/API/nginx-действиям. Это недопустимо.

## Единственная правка

В `n8n/current/run_staging_cabinet_p0_3_2_2026-08-10.py` после запуска precheck:

1. При **любом** `result.returncode != 0` немедленно завершать orchestrator с тем же exit-code.
2. Не выполнять ни один шаг из `steps`, smoke и webhook.
3. В выводе показать понятное сообщение: `STOP: precheck failed; apply/webhook not started.` Без печати секретов.

Не менять:

- production;
- nginx-vhost;
- API-контракт;
- SQL/миграции;
- статический UI;
- секреты;
- Telegram webhook.

## Приёмка

Обязательные прогоны:

```text
# precheck возвращает 1 -> ни один deploy-скрипт не вызван
# precheck возвращает 2 -> ни один deploy-скрипт не вызван
# precheck возвращает 3 -> ни один deploy-скрипт не вызван
# precheck возвращает 0 -> orchestrator продолжает прежнюю последовательность
```

Добавить unit-тест или изолированный тест subprocess/mock для всех четырёх сценариев.

## Текущий статус P0.3.2

Не считать выполненным. Сейчас на `admin-staging.wwc.best`:

```text
/cabinet/                → 200
/wwc-cabinet-config.json → enabled=true
/api/v1/admin/me         → 404
```

Целевое состояние перед webhook: `/api/v1/admin/me → 401` без cookie. До этого результата staging-боту webhook не назначать и Виктора на QR-приёмку не звать.

## Отчёт

Ровно четыре пункта:

1. Что изменено.
2. Где видно.
3. Что проверено фактически.
4. Что не проверено/ограничения.
