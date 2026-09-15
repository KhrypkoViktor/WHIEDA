# WWC Owner Cabinet — P0.3.1: один staging host, без лишнего API-домена

**Статус:** обязательная правка P0.3 до любого live-apply.  
**Причина:** P0.3 runbook противоречив: UI уже проксирует `/api/` напрямую на staging Core, но чеклист всё ещё требует отдельный `api-staging` DNS/TLS/docker-nginx. Это лишняя поверхность, лишние сертификаты и лишний риск.

## 1. Утверждённая схема

Используется **один** публичный staging host:

```text
admin-staging.wwc.best
```

Единственная DNS-запись для владельца:

```text
admin-staging.wwc.best  A  →  site VPS 173.249.45.83
```

Не создавать и не требовать для P0.3:

- `api-staging.wwc.best`;
- `cabinet-staging.wwc.best`;
- отдельный docker-nginx vhost на n8n/Dify;
- отдельный TLS-сертификат для API host.

Схема запросов:

```text
Browser / Telegram
  → https://admin-staging.wwc.best
  → /api/* в nginx site VPS
  → staging Platform API :8081 на Core VPS
```

Webhook нового staging-бота:

```text
https://admin-staging.wwc.best/api/v1/telegram/{staging_binding_id}/webhook
```

Он не должен указывать на рабочий бот, production host или отдельный `api-staging`.

## 2. Обязательные изменения в конфигурации и runbook

1. `admin-staging.wwc.best.nginx.conf`:
   - оставить только `server_name admin-staging.wwc.best`;
   - сертификат должен быть именно для `admin-staging.wwc.best`, не файл сертификата `wwc.best`, если тот не wildcard;
   - `location /api/` остаётся same-origin proxy и корректно превращает `/api/v1/...` в upstream `/v1/...`;
   - `X-Telegram-Bot-Api-Secret-Token` не удаляется nginx и доходит до Core;
   - lead-detail static fallback остаётся;
   - `X-Robots-Tag` остаётся.
2. Удалить из P0.3 execution list вызов `patch_ai_nginx_api_staging_2026-08-09.py`, требования DNS/TLS `api-staging` и упоминания `cabinet-staging`.
3. Актуализировать smoke и итоговый URL только на `https://admin-staging.wwc.best/cabinet/`.
4. Секреты не передаются в PowerShell-строке, чате, git или runbook. Если deployment script требует пароль БД/SSH, он читает его из защищённого server-side env/secrets file либо через скрытый prompt без печати и без сохранения команды в history.

## 3. Сетевая защита Core VPS

`185.252.232.93:8081` не должен быть публичным API для всего интернета.

До применения site nginx proxy проверить bind/firewall. Разрешить подключение к staging API `:8081` только с site VPS `173.249.45.83` (и localhost Core VPS). Все остальные входящие подключения запретить. Если это невозможно без риска текущим сервисам — остановиться и предложить безопасный private-network/reverse-proxy вариант.

## 4. TLS и deploy-порядок

1. Владелец создаёт одну DNS A-запись `admin-staging.wwc.best`.
2. На site VPS получить сертификат только для `admin-staging.wwc.best`.
3. Поднять staging Platform API, миграции и server-only secrets нового staging-бота.
4. Установить site nginx vhost и выложить static cabinet с `enabled: true` только в staging config.
5. Сначала smoke:
   - `/cabinet/` = 200;
   - config enabled = true;
   - `/api/v1/admin/me` без cookie = **401**, не 404;
   - HTML без advisor/Metrika.
6. Только затем назначить webhook **новому** боту и выполнить QR flow.

## 5. Приёмка

- в runbook нет `api-staging`, `cabinet-staging`, `PGPASSWORD="..."` и неиспользуемого docker-nginx patch;
- один TLS host, same-origin cookie;
- 8081 закрыт от внешнего интернета кроме site VPS;
- smoke получает 401 на `/me`;
- production и рабочий бот не изменены.
