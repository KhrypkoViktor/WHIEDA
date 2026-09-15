# WWC Owner Cabinet — P0.1B Completion Report (V1, 2026-08-09)

**Scope:** Telegram ingress for `admin_login_*` + atomic poll session exchange.  
**Out of scope:** production deploy, nginx, UI, n8n cutover, Sheets/markets.  
**Acceptance (owner, 2026-08-09):** принято по коду и локальной проверке. Живой внешний контур (QR → бот → staging webhook → браузер) — отдельный ручной sign-off; до него кабинет нельзя выкладывать как рабочий инструмент. P0.2 UI можно начинать параллельно.

---

## 1. Что изменено

- **`app/telegram/admin_login.py`:** перехват `/start admin_login_*`, server-side `confirm_login_from_telegram`, нейтральные ответы в Telegram, metadata с `update_id`.
- **`app/telegram/routes.py`:** intercept в `_process_telegram_update` **до** legacy n8n forward.
- **`app/telegram/processor.py`:** intercept в `process_core_telegram_update` **до** `handle_start_token`.
- **`app/admin/auth/service.py`:** `poll_login_challenge` — атомарный `UPDATE … SET status='used' … RETURNING` перед insert в `platform_admin_sessions`.
- **Тесты:** `tests/test_admin_cabinet.py` (concurrent poll, used/expired), `tests/test_telegram_processor.py` (admin_login ingress, legacy intercept, normal `/start` без регрессии).
- **Smoke:** `scripts/admin_cabinet_staging_smoke.py` — primary path через `try_handle_admin_login` (simulated Telegram update), запись API base / binding_id / update_id / challenge_id / cookie poll.

---

## 2. Где реально проверено (staging URL/API + Telegram scenario)

| Среда | URL / путь |
|-------|------------|
| Local Docker Postgres | `127.0.0.1:55432` / `whieda_platform_local_core` |
| In-process API | `http://cabinet.test.local` (TestClient + smoke script) |
| Telegram ingress | **Simulated** update via `try_handle_admin_login` (`binding_id=whieda-local-smoke-bot`, `update_id=8809001`) |
| Remote staging VPS / live bot webhook | **Не проверялось** |

---

## 3. Что проверено фактически

- **Pytest full suite:** **463 passed** (owner-verified, 2026-08-09).
- **Admin cabinet tests:** concurrent poll → одна session; used/expired poll без session; admin_login не вызывает `handle_start_token` / legacy forward.
- **Local smoke:** challenge → simulated Telegram ingress confirm → poll authenticated + Set-Cookie → `/v1/admin/me` super_admin → read APIs → logout.
- Confirm secret не попадает в poll body, deep link query (только opaque token), smoke logs.

---

## 4. Что не проверено/ограничения

- **Живой staging Telegram** (`/v1/telegram/{binding_id}/webhook` на VPS с реальным Bot API и QR deep link) — не выполнялся; доказательство ingress = local simulation + unit tests.
- Production nginx / `cabinet.staging.wwc.best` публичный маршрут — без изменений.
- n8n legacy consultant path для обычных `/start` — не менялся; admin_login intercept только в platform-api.

**Вывод:** P0.1B backend **принят владельцем** по коду, pytest (463) и local smoke (20/20, simulated Telegram). **Не закрыто:** один ручной прогон живого контура QR → настоящий Telegram-бот → staging webhook → браузер. P0.2 UI можно начинать; публикация кабинета как рабочего инструмента — только после live Telegram sign-off.
