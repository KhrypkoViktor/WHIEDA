# WWC Markets — staging deploy (Platform API + nginx)

**Не production.** Production cutover — только по отдельной команде владельца.

## Что это

| Компонент | Staging | Production (не трогаем) |
|-----------|---------|-------------------------|
| Host API | `whieda-n8n` `:8081` | `:8080` `/opt/whieda-platform-core` |
| nginx upstream | `/whieda-platform-staging/` | `/whieda-platform/` |
| Site markets alias | `staging.wwc.best` (опционально) | `wwc.best` |
| Frontend flag | `market_centers_v1` **OFF** | OFF до owner |

## Локальная проверка (без SSH)

```powershell
docker compose -f postgres\docker-compose.local-staging.yml up -d
python postgres\scripts\ensure_local_core_database.py
python backend\platform-api\scripts\staging_markets_http_proof.py
```

## Staging deploy на whieda-n8n (dry-run по умолчанию)

```powershell
# Просмотр плана без изменений на сервере
python n8n\current\deploy_platform_core_staging_2026-08-09.py --dry-run

# Реальный deploy (только после owner OK)
python n8n\current\deploy_platform_core_staging_2026-08-09.py
```

Deploy кладёт bundle в `/opt/whieda-platform-staging`, API слушает **8081**.

**Google Sheets markets sync (staging):** service account JSON на хосте:

```text
/opt/whieda-platform-staging/secrets/wwc-markets-sync.json
```

`docker-compose.yml` монтирует этот файл read-only в `api` и `worker`. Image собирается с optional deps `[sheets]` (`google-api-python-client`, `google-auth`, `openpyxl`). После `docker compose build api && docker compose up -d api` sync не требует ручного `pip install` или `docker cp`.

См. `backend/platform-api/docs/WWC_MARKETS_GOOGLE_SHEETS_OWNER_SETUP.md`.

## nginx — sysarchn8n (upstream staging)

Файл: `backend/deploy/staging/sysarchn8n.nginx-staging-upstream.conf`

Применение (dry-run):

```powershell
python n8n\current\patch_ai_nginx_markets_staging_2026-08-09.py --dry-run
```

## nginx — markets read-only alias

Файл: `backend/deploy/staging/wwc.best.nginx-markets-staging.conf`

Проксирует только read-only markets endpoints на staging upstream.  
**Не** трогает `/api/lead` и существующие core routes.

Применение на site nginx (dry-run):

```powershell
python n8n\current\patch_site_nginx_markets_staging_2026-08-09.py --dry-run
```

## Smoke после staging deploy

```bash
curl -s "https://sysarchn8n.duckdns.org/whieda-platform-staging/health/live"
curl -s -H "Host: wwc.best" \
  "https://sysarchn8n.duckdns.org/whieda-platform-staging/api/catalog-prices?market_id=ru&sku=M015-00"
```

## Google sync на staging

См. `docs/WWC_MARKETS_GOOGLE_SHEETS_OWNER_SETUP.md`.
