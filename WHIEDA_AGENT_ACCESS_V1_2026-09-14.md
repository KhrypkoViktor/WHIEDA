# Доступы агента: что есть, где лежит, как этим пользоваться

Этот файл отвечает на вопрос «а есть ли у меня доступ к …» — чтобы агент не
задавал его владельцу. **Читать до первого вопроса о доступах.** Значений
секретов здесь нет и не будет: только имена переменных, пути и механизмы.

Правило: агент **не создаёт, не читает и не пересылает** ключи и пароли.
Если доступа нет — говорит владельцу, какой именно переменной или файла не
хватает, и на этом останавливается.

## Сводная таблица

| Что нужно | Как | Состояние 14.09.2026 |
|---|---|---|
| Сайт: деплой staging/prod | `npm run deploy:staging` / `deploy:prod` из живого дерева | **работает**, ключ `~/.ssh/wwc_deploy_ed25519` |
| n8n: workflow, executions | REST через `login_session()` | **работает**, env `WHIEDA_N8N_EMAIL` / `WHIEDA_N8N_PASSWORD` |
| Runtime-база Core (Postgres) | `n8n/current/wwc_sql.py` — через n8n, без SSH | **работает** чтение и запись |
| SSH на Core VPS | `ssh_run()` в n8n-helper, env `WHIEDA_SSH_PASSWORD` | **не работает**: пароль ротирован, переменная устарела |
| Google Sheet `Partner_Subscriptions` | только через n8n Google Sheets-узел | не проверено; строки вставляет владелец |
| GitHub push | `git push` | **заблокирован** для агента классификатором; пушит владелец |
| Публичный API партнёров | `GET https://wwc.best/api/v1/public/ref/<code>` | без авторизации |
| Фото от владельца | картинка из чата Claude → транскрипт сессии (base64), см. п. 8; либо `Downloads\Telegram Desktop\photo_*.jpg` | **работает** оба пути |

## 1. Сайт

Живое рабочее дерево — то, где `git rev-list --count HEAD..master` даёт `0`
(на 14.09 это `D:\Projects\_worktrees\wwc-staging-referral-isolation`, ветка
`master`). Деплой: `scripts/deploy.py`, SSH-ключом `~/.ssh/wwc_deploy_ed25519`
на сервер сайта. Это **другой сервер**, не Core VPS — ключ сайта к Core не
подходит.

## 2. n8n

Помощник: `n8n/current/publish_and_run_whieda_sync_2026-07-13.py`.
`helper.login_session()` возвращает `requests.Session` с cookie n8n,
`helper.BASE_URL` — адрес. Переменные окружения `WHIEDA_N8N_EMAIL`,
`WHIEDA_N8N_PASSWORD` **уже заданы** у агента — проверять через
`[ -n "$WHIEDA_N8N_PASSWORD" ]`, не печатать.

Что этим делается:
- патчи workflow — образец `patch_wwc_website_leads_host_owner_2026-09-14.py`
  (бэкап → `patch_workflow()` → `PATCH /rest/workflows/<id>` →
  `POST /rest/workflows/<id>/activate`);
- чтение executions — `read_lead_execution.py [execution_id]` показывает, кому
  ушёл лид (`assigned_owner_id`, `telegram_chat_id`, `host_ref_code`).

**REST `activate` делает версию активной** (`activeVersionId` == `versionId`,
проверено на бою 14.09.2026). Шаг `ssh_run('n8n publish:workflow …')` в
старых скриптах лишний; если он падает по SSH — это не ошибка патча.

## 3. Runtime-база Core

`n8n/current/wwc_sql.py`:

```bash
python wwc_sql.py "select actor_id, telegram_chat_id from lead_actors where actor_id='natali'"
python wwc_sql.py --file fix_olesya_ref_code_2026-09-14.sql
```

Поднимает временный workflow с Postgres-узлом (credential n8n), вызывает
webhook, отдаёт строки, удаляет workflow. SSH не нужен.

Известная странность: запросы с агрегатами (`count(*)`, `union all` со
строковыми литералами) возвращают `[]`. Пользоваться простыми `select … from
… where …` и считать в Python.

Запись (`UPDATE`/`DELETE`) в боевую базу классификатор прав **блокирует** —
это правильно. Скрипт готовится, запускает владелец или разрешает явно.

Ключевые таблицы: `referral_profiles` (ref_code, owner_id, public_profile
jsonb: page_mode, public_site_url, display_name), `lead_actors` (actor_id =
owner_id, telegram_chat_id), `website_leads` (assigned_owner_id, metadata
jsonb: routing_version, routing_error), `partner_subscriptions` (ref_code —
FK на referral_profiles **без каскада**).

## 4. SSH на Core VPS — устарел

`WHIEDA_SSH_PASSWORD` задана, но пароль на сервере ротирован: `paramiko`
даёт `Authentication failed`. Падает всё, что идёт через `helper.ssh_run()`:
`docker restart n8n`, `apply_sql_via_core`, деплой Core
(`deploy_platform_core_2026-08-02.py`).

Что нужно от владельца: либо обновить переменную, либо положить на Core VPS
публичный ключ и указать путь к приватному. Агент значение не трогает.
Пока — всё, что можно, делается через REST (разделы 2–3).

## 5. Google Sheet `Partner_Subscriptions`

<https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2v1lehw93jwqff7ldbz4/edit?gid=1209283579#gid=1209283579>

У агента нет прямого доступа на запись. Строку готовит агент, вставляет
владелец. Образец временного workflow с Google Sheets-узлом —
`update_partners_sheet_subdomains_2026-08-01.py`, но он тянет `ssh_run` и
без п.4 не проходит.

## 6. GitHub

`git push` агенту заблокирован. Коммиты делать; пуш — владелец, или добавить
правило в `.claude/settings.json`. `deploy:prod` выравнивает локальный
`master`, но `origin/master` без пуша отстаёт.

## 7. Что проверять первым при подключении партнёра

Не «есть ли доступ», а три команды:

```bash
curl -s https://wwc.best/api/v1/public/ref/<ref_code>          # 200 и public_site_url поддомена
python n8n/current/wwc_sql.py "select actor_id, telegram_chat_id from lead_actors where actor_id='<owner_id>'"
python n8n/current/read_lead_execution.py                       # после тестовой заявки с поддомена
```

Все три доступны агенту без единого вопроса владельцу.

## 8. Картинки, вставленные в чат Claude

На диск они не сохраняются, но лежат в транскрипте сессии
`C:\Users\srs\.claude\projects\D--Projects-WHIEDA\<session>.jsonl` как блоки
`{"type":"image","source":{"type":"base64",...}}` внутри сообщений `type: user`.
Достать последнюю:

```python
import json, base64
rows = [json.loads(l) for l in open(PATH, encoding='utf-8')]
imgs = [b for r in rows if r.get('type') == 'user' and isinstance(r.get('message', {}).get('content'), list)
        for b in r['message']['content'] if b.get('type') == 'image']
open('photo.jpg', 'wb').write(base64.b64decode(imgs[-1]['source']['data']))
```

Дальше как с любым фото партнёра: 4:5, 640×800, webp quality 82 →
`public/media/partners/<ref>.webp`, `photoUrl` в `src/data/referrals.js`,
`node scripts/sync-runtime-assets.mjs` (иначе падает юнит-тест на
`public/wwc-api/referral-registry.js`), `npm test`, deploy.
