# Академия «полка» — STATE исполнителя (25.09.2026)

ТЗ: `PROCESS/wwc-academy-shelf-20260925/TZ_IMPLEMENTER_V1.md` (ветка лида
`wip/consolidation-20260831`, commit 192fdb5). Рамки: ARCHITECTURE_V1, решения 4, 5, 10.

## Где работа

| Repo | Worktree | Ветка | База | Commits |
|---|---|---|---|---|
| Core (корень WHIEDA) | `D:\Projects\_worktrees\whieda-core-academy-shelf` | `core/academy-shelf-v1` | origin/master 99562dc | 2592a54 код+тесты; следующий — правки ревью + этот STATE |
| Сайт wwc-best | `D:\Projects\_worktrees\wwc-academy-shelf` | `site/academy-shelf-v1` | origin/master 939007c | 69c091f |

Ветки не запушены (push по команде лида/владельца). Прод и staging не трогались,
миграция никуда не применялась.

## Что сделано

- `postgres/sql/platform_academy_shelf_v15.sql`: source `'key'`, `academy_access_keys`,
  `academy_shelf` (RLS), check `partner_product_access` + `academy_shelf`,
  `partner_subscription_plans.course_slug`. Без знака доллара, идемпотентно.
- Регистрация в 6 местах; `EXPECTED_APPLY_COUNT` 36 → **38**: вместе с V15 в порядок
  применения добавлен `platform_academy_v1.sql` — V15 меняет её таблицы, а её в
  apply-all не было. `schema_requirements`: + `academy_access_keys`, `academy_shelf`.
- `app/academy/keys.py`: `issue_keys` (автор + активная полка, или владелец/preview-админ;
  ≤100; только опубликованный курс), `redeem_key` (одна транзакция, `for update`;
  у человека уже есть доступ → `already_open`, использование не списывается).
- `app/academy/service.py`: `grant_course_access` (+ `grant_course_access_for_product` в
  savepoint: нет курса/Telegram — warning, платёж проходит), `extend_shelf_in_connection`,
  `course_lock_reason` для `pro` = PRO или строка доступа, `author_contact` в замке.
- Бот: «ключи <slug> <N>» (>20 — txt-файлом через sendDocument), «мои курсы»,
  `/start course_<код>`, текст замка `purchase_required` с контактом автора.
- Оплата: строка `course_*` → `academy_access` по `course_slug`; `academy_shelf` →
  `academy_shelf.paid_until` (от конца, если полка ещё оплачена). Продление: `academy_shelf`
  в каталоге и своя строка оплаты; вставка `partner_product_access` для `course_*` убрана.
- `load_bundle.py`: `--author`, `--status`, `--access`, `<tenant> publish <slug>`.
- Сайт `src/lib/academy/app.js`: `lockText()` — автор вместо поддержки.

## Решения, которые принял исполнитель (проверить лиду)

1. **Строки тарифа `academy_shelf_3m` в V15 нет.** `price_wusd_minor/price_rub_minor`
   NOT NULL и > 0 (`platform_referral_bonuses_v1.sql`), цену выдумывать нельзя. После
   ответа владельца выполнить (подставить цены):
   ```sql
   insert into partner_subscription_plans
     (tenant_id, plan_code, product_code, access_months, price_wusd_minor, price_rub_minor,
      active, title, sort_order)
   values ('whieda', 'academy_shelf_3m', 'academy_shelf', 3, <W$ × 100>, <₽ × 100>,
           false, 'Полка Академии на 3 месяца', 200)
   on conflict (tenant_id, plan_code) do nothing;
   ```
   `active=true` — отдельной командой, когда владелец разрешит продажу.
2. ТЗ считало, что полка «появится сама» — неверно: фильтр каталога продления пропускал
   только сайт, пакет и `course_*`, а строка без своей ветки записалась бы как
   **продление сайта**. Исправлено в `renewal_requests/service.py`.
3. `load_bundle.py` без флагов: новый курс — `draft`, существующий сохраняет статус
   (перезаливка живого «Запуск WWC» его не прячет). Без `--access`: новый курс с
   `--author` — `purchase`; существующий курс автора сохраняет правило (перезаливка не
   открывает его всем PRO); курс платформы берёт `access_rule` из бандла.
4. «мои курсы» у владельца без своих курсов — все курсы тенанта; у не-автора — обычная
   Академия ученика.
5. После ревью: подтверждение продления полки/курса шлёт свой текст (срок полки / «курс
   открыт в Академии») вместо «Сайт… Доступ до» — правка в `app/telegram/renewal_requests.py`
   (зона core, за пределами узкого списка ТЗ). Один человек с двумя ключами одновременно
   тратит один (advisory-lock). «ключи kurs 0» — отказ, а не один ключ.

## Проверки (локально)

- Unit Core: 22 failed / 1615 passed / 4 errors; master до правок: 22 / 1567 / 4 —
  набор падений тот же (golden/fixtures), новых нет.
- Фокус-гейт CI + academy: 236 passed (до правок ревью). `check_zone.py`: OK (zone core).
- Независимое ревью Core-diff: две находки (доступ при перезаливке, текст после оплаты
  полки) и две мелкие — исправлены, тесты добавлены.
- `run_postgres_integration_tests.ps1`: 12 passed (новый `test_academy_shelf_postgres.py`
  проходит весь сценарий ТЗ; `test_renewal_services_postgres.py` обновлён под доступ в Академии).
- Сайт `npm run test:unit`: 276 pass / 1 fail / 5 skip; master: 273 / 1 / 5 — падает
  тот же `motion-reveal-fallback` (нужен собранный `dist`).

## Не проверено

- Staging/боевой бот, реальный `sendDocument` (в тестах замокан), сборка сайта и
  визуальная проверка замка на /academy/.
- Ручная команда владельца «оплата @… курс …» (`app/telegram/billing.py`) по-прежнему шлёт
  общий текст «Сайт… Доступ до: сегодня, осталось 0» — было и до этого среза; путь
  продления исправлен, ручной — отдельной правкой.

## Следующий шаг (лид)

Merge с `core/crm-v1` (конфликт в счётчике 38 vs 37 → 39 и порядке apply-all),
миграция V15 через `wwc_sql.py` на бой, затем `release_core.ps1`; строка тарифа — после цены.
