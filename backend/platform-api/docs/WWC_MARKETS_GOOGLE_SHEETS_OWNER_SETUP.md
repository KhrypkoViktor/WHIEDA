# WWC Markets — Google Sheets: три шага для владельца

Шаблон с реальными ценами каталога:  
`backend/platform-api/data/wwc_markets_sheets_template/WWC_MARKETS_SHEETS_TEMPLATE.xlsx`

---

## Шаг 1. Загрузить шаблон в Google Sheets

1. Откройте [Google Sheets](https://sheets.google.com) → **Создать** → **Импортировать файл**.
2. Загрузите `WWC_MARKETS_SHEETS_TEMPLATE.xlsx` (или пять CSV из той же папки — по одному на вкладку).
3. Убедитесь, что **имена вкладок** точно такие:
   - `markets`
   - `ref_structures`
   - `service_centers`
   - `service_center_coverage`
   - `product_prices`
4. Скопируйте **ID таблицы** из URL:  
   `https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit`
5. Вкладки `ref_structures`, `service_centers`, `service_center_coverage` **оставьте пустыми** (только заголовки), пока не будут реальные ref и центры. Не добавляйте выдуманные контакты.

---

## Шаг 2. Service account в Google Cloud

1. [Google Cloud Console](https://console.cloud.google.com/) → выберите или создайте проект (например `whieda-platform`).
2. **APIs & Services** → **Enable APIs** → включите **Google Sheets API**.
3. **IAM & Admin** → **Service Accounts** → **Create service account**:
   - **Name:** `wwc-markets-sync`
   - **ID:** `wwc-markets-sync` (будет частью email)
   - Роль для SA не обязательна (доступ к таблице — через Share).
4. Откройте созданный SA → **Keys** → **Add key** → **JSON**.
5. Сохраните файл **только на сервере**, вне git, например:  
   `/opt/whieda-platform-staging/secrets/wwc-markets-sync.json`  
   Права: `chmod 600`, владелец — пользователь docker/деплоя.

**Email для шага 3** (подставьте свой project id):

```text
wwc-markets-sync@<GCP_PROJECT_ID>.iam.gserviceaccount.com
```

Пример: `wwc-markets-sync@whieda-platform.iam.gserviceaccount.com`

После загрузки ключа на сервер точный email можно проверить:

```bash
python -c "import json; print(json.load(open('/opt/whieda-platform-staging/secrets/wwc-markets-sync.json'))['client_email'])"
```

---

## Шаг 3. Доступ Viewer к таблице

1. В Google Sheet → **Share** (Настройки доступа).
2. Добавьте email service account (шаг 2) с ролью **Viewer** (достаточно read-only).
3. Не делайте таблицу публичной и не кладите JSON-ключ в репозиторий.

---

## Переменные на сервере (staging)

В `.env` Platform API (см. `backend/deploy/staging/.env.staging.example`):

```env
WWC_MARKETS_SYNC_MODE=google
WWC_MARKETS_SHEET_ID=<SHEET_ID из шага 1>
WWC_MARKETS_GOOGLE_CREDENTIALS_PATH=/opt/whieda-platform-staging/secrets/wwc-markets-sync.json
```

---

## Первая синхронизация (после шагов 1–3)

На хосте **staging** Platform API (не production):

```bash
cd /opt/whieda-platform-staging/src/platform-api
docker compose exec api python scripts/sync_wwc_markets.py --tenant whieda
```

Ожидаемый вывод: `google_service_account: wwc-markets-sync@...` и `{'ok': True, ...}`.

Планировщик (`run_scheduled_jobs.py`) подхватит sync **после** первой успешной ручной загрузки.

---

## Что уже в шаблоне

| Вкладка | Содержимое |
|---------|------------|
| `markets` | RU / BY / global |
| `product_prices` | 35 SKU × RU (RUB) + BY (BYN) из `03_Website/wwc-best/src/data/products.js` |
| `ref_structures` | пусто (заголовки) |
| `service_centers` | пусто |
| `service_center_coverage` | пусто |

Global-цены на сайте берутся из RUB (backend fallback), отдельные строки `global` в `product_prices` не нужны.

Пересборка шаблона после изменения прайса на сайте:

```bash
python backend/platform-api/scripts/generate_wwc_markets_sheets_template.py
```
