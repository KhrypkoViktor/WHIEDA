# Подключение партнёра: PRO-статус на сайте, инструкция, клуб и многострочная оплата — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Партнёр на сайте видит свой статус (баланс WWC$, PRO до даты, CLUB до даты), гость видит витрину с замками PRO; агент имеет один пайплайн подключения партнёра в существующем стандарте; бот принимает оплату клуба, настройки сайта и персональной цены одним платежом на несколько строк, начисляя бонус только со строки PRO.

**Architecture:** Core (FastAPI + PostgreSQL, `backend/platform-api`) отдаёт сайту расширенный `/api/v1/content-access/me`; сайт (Astro, отдельный репозиторий `wwc-best`) рисует карточку статуса и замки в боковом меню без нового входа — используется уже существующая Telegram-сессия. Клуб и настройка сайта хранятся как отдельные продукты рядом с платформой; один платёж владельца разбирается на строки, каждая строка — своя запись в `partner_payment_ledger`, объединённая заголовком платежа. Инструкция дорабатывается в `docs/PARTNER_ONBOARDING_STANDARD_V1.md` сайта, новых документов нет.

**Tech Stack:** Python 3.12 / FastAPI / psycopg 3 / pytest; PostgreSQL 16 (Supabase, одна БД на staging и prod); Astro 4 + vanilla JS + `node --test`; выкладка Core — `backend/deploy/core/release_core.ps1`, сайта — `npm run deploy:staging|prod`.

## Global Constraints

- Названия: платформа = **PRO (сайт)**; клуб = **CLUB**; разовая услуга = **настройка сайта (разово)**; валюта = **WWC$**. Слово «баллы» в интерфейсе не используется. Внутренний код валюты `WUSD` не переименовывать.
- Курс: **1 W$ = 1 WWC$ = 100 ₽**. Цены: PRO 30 WWC$ / 3 мес (6 мес 54, 12 мес 96 — уже в БД); CLUB **40 WWC$/мес → 120 за 3 мес**; настройка сайта **20 WWC$ разово**; персональная цена Ольги Самцовой PRO = 15 WWC$ (постоянно, до явной отмены). Акционная цена клуба 75 существует только внутри пакета 105 и вводится владельцем как строка с пометкой `акция`.
- Бонус рефереру — **только со строки PRO**: 20 % с первой оплаты, 10 % с продления. Клуб, настройка сайта, курсы — 0. Правило уже так работает через `referral_reward_rules` (правило есть только для `platform_subscription`) — новые продукты правил **не получают**.
- Клуб = учёт срока + напоминания за 7/3/1 день партнёру и владельцу. Никаких доступов бот не включает.
- Правило B для ручной работы агента: только штатные команды бота, API и синхронизация; в БД — чтение и согласованные миграции.
- Плашка PRO на сайте = замок: показывается только гостю и вошедшему без подписки; у подписчика плашек нет. **В этой фазе замок информационный:** тайлы работают как раньше (калькулятор открывается, прайс №2 скачивается); клик по самой плашке открывает карточку с объяснением и кнопками. Реальное закрытие функций — отдельное решение владельца.
- Схема БД: любой новый `ON CONFLICT` по частичному индексу обязан повторять его предикат; каждую миграцию прогонять в `tests/postgres_testkit.py` до применения. Миграции применяются к общей БД один раз (staging = prod).
- Выкладка Core только через `release_core.ps1` (gate: unit + PostgreSQL-интеграция + schema check). Сайт: `deploy:staging` → показать владельцу → `deploy:prod`.
- Коммиты небольшие, сообщения объясняют «почему». Тесты пишутся до кода.

---

## Файлы

**Core (`D:/Projects/_worktrees/whieda-release-partner-subscriptions`, ветка `master`):**
- Create `backend/platform-api/app/content_access/account.py` — сбор «карточки аккаунта» для сайта (баланс, PRO, CLUB) по `telegram_user_id`.
- Modify `backend/platform-api/app/content_access/service.py:408-425` — `format_me_payload` получает `account`.
- Modify `backend/platform-api/app/content_access/routes.py:118-156` — `_me`/`me_site` вызывают `load_site_account`.
- Create `backend/platform-api/app/telegram/pro_start.py` — обработчик `/start pro`.
- Modify `backend/platform-api/app/telegram/processor.py:313-318` — маршрут `pro` перед identity-token.
- Create `postgres/sql/platform_partner_products_v7.sql` — `partner_product_access`, `partner_price_overrides`, `partner_payments`, новые планы, расширение intents/ledger/reminder_log.
- Create `backend/platform-api/app/subscriptions/pricing.py` — каталог продуктов, персональные цены, разбор многострочной команды.
- Modify `backend/platform-api/app/subscriptions/service.py` — запись многострочного платежа.
- Modify `backend/platform-api/app/subscriptions/reminders.py` — напоминания клуба.
- Modify `backend/platform-api/app/telegram/billing.py` — многострочная `оплата`, `цена`.
- Modify тексты: `app/telegram/*.py` — `W$` → `WWC$` там, где видит человек; сверка текстов с поведением.
- Tests: `tests/test_site_account.py`, `tests/test_pro_start.py`, `tests/test_pricing.py`, `tests/test_multiline_payment_postgres.py`, `tests/test_club_reminders.py`, `tests/test_bot_copy_audit.py`.

**Сайт (`D:/Projects/_worktrees/wwc-staging-referral-isolation`, новая ветка `feat/pro-status-card` от `master`):**
- Create `src/lib/account/status.js` — чистая функция: payload `/me` → модель карточки (дни, подписи, замки).
- Create `src/components/AccountStatusCard.astro` — разметка карточки + замок-диалог, стили.
- Modify `src/components/SiteMenu.astro` — карточка в шапке панели, `CLUB` на «Академии», `data-pro-lock` на плашках, скрипт подписки на `/me`.
- Modify `src/styles/global.css:5155-5215, 5275-5290` — стили плашек-замков и карточки.
- Modify `docs/PARTNER_ONBOARDING_STANDARD_V1.md` — раздел «Подключение партнёра: пайплайн» и исправления устаревшего.
- Tests: `tests/unit/account-status.test.mjs`.

---

## Фаза A — сайт видит статус (staging сегодня)

### Task A1: Core — карточка аккаунта в `/me`

**Files:**
- Create: `backend/platform-api/app/content_access/account.py`
- Modify: `backend/platform-api/app/content_access/service.py:408-425`
- Modify: `backend/platform-api/app/content_access/routes.py:118-156`
- Test: `backend/platform-api/tests/test_site_account.py`

**Interfaces:**
- Produces: `async def load_site_account(tenant_id: str, telegram_user_id: int, *, at: datetime | None = None) -> dict | None` → `{"actor_id": str, "balance": {"currency": "WWC$", "amount_minor": int}, "pro": {"status": "active|grace|suspended|none", "paid_until": datetime|None, "days_left": int|None, "ref_code": str|None}, "club": {"status": "none", "paid_until": None, "days_left": None}}` (клуб заполняется в C3).
- Produces: `format_me_payload(session, *, partner_subscription=None, account=None)` — добавляет ключ `"account"` в ответ `/me`.

- [ ] **Step 1: Failing test для модели аккаунта**

```python
# tests/test_site_account.py
"""/me tells the site what the signed-in person owns: balance, PRO, CLUB."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.content_access.account import build_site_account, load_site_account
from app.content_access.service import format_me_payload


NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def test_build_site_account_counts_days_and_balance():
    account = build_site_account(
        actor_id="olga-samtsova",
        bonus_minor=1250,
        pro_paid_until=NOW + timedelta(days=84, hours=3),
        pro_ref_code="olga-samtsova",
        club_paid_until=None,
        at=NOW,
    )
    assert account["balance"] == {"currency": "WWC$", "amount_minor": 1250}
    assert account["pro"]["status"] == "active"
    assert account["pro"]["days_left"] == 84
    assert account["pro"]["ref_code"] == "olga-samtsova"
    assert account["club"] == {"status": "none", "paid_until": None, "days_left": None}


def test_build_site_account_without_subscription_is_none_status():
    account = build_site_account(
        actor_id="telegram:whieda:1", bonus_minor=0, pro_paid_until=None,
        pro_ref_code=None, club_paid_until=None, at=NOW,
    )
    assert account["pro"] == {"status": "none", "paid_until": None, "days_left": None, "ref_code": None}


def test_expired_pro_reports_grace_then_suspended():
    grace = build_site_account(
        actor_id="a", bonus_minor=0, pro_paid_until=NOW - timedelta(days=1),
        pro_ref_code="a", club_paid_until=None, at=NOW,
    )
    assert grace["pro"]["status"] == "grace"
    assert grace["pro"]["days_left"] == 0
    suspended = build_site_account(
        actor_id="a", bonus_minor=0, pro_paid_until=NOW - timedelta(days=40),
        pro_ref_code="a", club_paid_until=None, at=NOW,
    )
    assert suspended["pro"]["status"] == "suspended"


def test_me_payload_carries_account_and_keeps_legacy_fields():
    account = build_site_account(
        actor_id="a", bonus_minor=600, pro_paid_until=NOW + timedelta(days=10),
        pro_ref_code="a", club_paid_until=None, at=NOW,
    )
    payload = format_me_payload({"expires_at": NOW}, partner_subscription={"partner_paid": True, "subscription_status": "active", "paid_until": NOW}, account=account)
    assert payload["partner_paid"] is True
    assert payload["account"]["balance"]["amount_minor"] == 600
    assert payload["account"]["pro"]["paid_until"] == (NOW + timedelta(days=10)).isoformat()


@pytest.mark.anyio
async def test_load_site_account_reads_actor_balance_and_pro(monkeypatch: pytest.MonkeyPatch):
    rows = iter([
        {"actor_id": "olga-samtsova", "bonus_minor": 1250, "pro_paid_until": NOW + timedelta(days=5), "pro_ref_code": "olga-samtsova"},
    ])

    async def fake_fetch_one(conn, sql, params=None):
        assert params == ("whieda", 525317405)
        return next(rows)

    @asynccontextmanager
    async def fake_conn(tenant_id: str):
        yield object()

    monkeypatch.setattr("app.content_access.account.tenant_connection", fake_conn)
    monkeypatch.setattr("app.content_access.account.fetch_one", fake_fetch_one)
    account = await load_site_account("whieda", 525317405, at=NOW)
    assert account["actor_id"] == "olga-samtsova"
    assert account["pro"]["days_left"] == 5


@pytest.mark.anyio
async def test_load_site_account_unknown_person_is_none(monkeypatch: pytest.MonkeyPatch):
    async def fake_fetch_one(conn, sql, params=None):
        return None

    @asynccontextmanager
    async def fake_conn(tenant_id: str):
        yield object()

    monkeypatch.setattr("app.content_access.account.tenant_connection", fake_conn)
    monkeypatch.setattr("app.content_access.account.fetch_one", fake_fetch_one)
    assert await load_site_account("whieda", 1) is None
```

- [ ] **Step 2: Run** `python -m pytest tests/test_site_account.py -q` → FAIL `No module named app.content_access.account`.

- [ ] **Step 3: Реализация `account.py`**

```python
"""What the signed-in person owns, for the site menu: balance, PRO, CLUB.

One query by telegram_user_id. A person without a referral profile still gets
their balance (bonuses accrue to lead_actors, not to a site). Club is filled in
once partner_product_access exists (Phase C); until then it is "none".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db import fetch_one, tenant_connection
from app.subscriptions.service import subscription_state

DISPLAY_CURRENCY = "WWC$"


def _days_left(paid_until: datetime | None, at: datetime) -> int | None:
    if paid_until is None:
        return None
    delta = paid_until.astimezone(timezone.utc) - at
    return max(0, delta.days)


def _product(paid_until: datetime | None, at: datetime, **extra: Any) -> dict[str, Any]:
    if paid_until is None:
        return {"status": "none", "paid_until": None, "days_left": None, **extra}
    return {
        "status": subscription_state(paid_until, at=at),
        "paid_until": paid_until,
        "days_left": _days_left(paid_until, at),
        **extra,
    }


def build_site_account(
    *,
    actor_id: str,
    bonus_minor: int,
    pro_paid_until: datetime | None,
    pro_ref_code: str | None,
    club_paid_until: datetime | None,
    at: datetime,
) -> dict[str, Any]:
    return {
        "actor_id": actor_id,
        "balance": {"currency": DISPLAY_CURRENCY, "amount_minor": int(bonus_minor or 0)},
        "pro": _product(pro_paid_until, at, ref_code=pro_ref_code),
        "club": _product(club_paid_until, at),
    }


async def load_site_account(
    tenant_id: str, telegram_user_id: int, *, at: datetime | None = None
) -> dict[str, Any] | None:
    current = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select la.actor_id,
                   coalesce((
                     select sum(b.amount_minor) from partner_bonus_ledger b
                      where b.tenant_id = la.tenant_id and b.actor_id = la.actor_id
                        and b.currency = 'WUSD'
                   ), 0)::bigint as bonus_minor,
                   ps.paid_until as pro_paid_until,
                   rp.ref_code as pro_ref_code
              from lead_actors la
              left join referral_profiles rp
                on rp.tenant_id = la.tenant_id and rp.owner_id = la.actor_id and rp.enabled = true
              left join partner_subscriptions ps
                on ps.tenant_id = rp.tenant_id and ps.ref_code = rp.ref_code
             where la.tenant_id = %s and la.telegram_user_id = %s and la.active = true
             order by rp.ref_code
             limit 1
            """,
            (tenant_id, int(telegram_user_id)),
        )
    if not row:
        return None
    return build_site_account(
        actor_id=str(row["actor_id"]),
        bonus_minor=int(row["bonus_minor"] or 0),
        pro_paid_until=row.get("pro_paid_until"),
        pro_ref_code=row.get("pro_ref_code"),
        club_paid_until=None,
        at=current,
    )
```

`format_me_payload` в `service.py`:

```python
def format_me_payload(
    session: dict[str, Any],
    *,
    partner_subscription: dict[str, Any] | None = None,
    account: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expires_at = session.get("expires_at")
    subscription = partner_subscription or {}
    payload = {
        "ok": True,
        "authenticated": True,
        "telegram_verified": True,
        "scope": session.get("scope") or "telegram_verified",
        "expires_at": _isoformat(expires_at),
        "partner_paid": bool(subscription.get("partner_paid")),
        "subscription_status": subscription.get("subscription_status") or "no_subscription",
        "paid_until": _isoformat(subscription.get("paid_until")),
        "grace_until": _isoformat(subscription.get("grace_until")),
        "account": None,
    }
    if account:
        payload["account"] = {
            **account,
            "pro": {**account["pro"], "paid_until": _isoformat(account["pro"]["paid_until"])},
            "club": {**account["club"], "paid_until": _isoformat(account["club"]["paid_until"])},
        }
    return payload
```

`routes.py` — в `_me` и `me_site` после `subscription = ...`:

```python
    account = None
    if telegram_user_id is not None:
        account = await load_site_account(tenant.tenant_id, int(telegram_user_id))
    return format_me_payload(session, partner_subscription=subscription, account=account)
```
(импорт `from app.content_access.account import load_site_account`). В `me_site` вернуть `{**format_me_payload(...), "catalog": load_repeat_price_catalog()}` — посмотреть текущую сборку ответа на строках 138-156 и добавить `account` тем же способом.

- [ ] **Step 4: Run** `python -m pytest tests/test_site_account.py tests/test_content_access.py -q` → PASS.
- [ ] **Step 5: Schema map** — `partner_bonus_ledger` уже в `core` списке `app/schema_requirements.py`; `python -m pytest tests/test_schema_requirements.py -q` → PASS.
- [ ] **Step 6: Commit** `feat(content-access): /me carries balance and PRO/CLUB status for the site`.

### Task A2: Core — `/start pro` из карточки на сайте

**Files:**
- Create: `backend/platform-api/app/telegram/pro_start.py`
- Modify: `backend/platform-api/app/telegram/processor.py:313-318`
- Test: `backend/platform-api/tests/test_pro_start.py`

**Interfaces:**
- Produces: `PRO_START_TOKEN = "pro"`, `async def handle_pro_start(tenant, msg, trace_id) -> dict`. Текст и клавиатура зависят от того, есть ли у человека сайт: с сайтом → кнопка `Продлить платформу` (`renew:start`), без → `Создать свой сайт` (`site:create`) — те же callback, что в `app/telegram/referral_bonus.py:139-142`.

- [ ] **Step 1: Failing test**

```python
# tests/test_pro_start.py
from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.processor import process_core_telegram_update


def _update(text: str) -> dict:
    return {"message": {"text": text, "chat": {"id": 300, "type": "private"}, "from": {"id": 300, "username": "guest"}}}


@pytest.mark.asyncio
async def test_start_pro_offers_renewal_to_partner_with_site(whieda_tenant, whieda_bot_binding):
    deliver = AsyncMock()
    with patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)), \
         patch("app.telegram.pro_start.resolve_partner_subscription_by_telegram_user_id", AsyncMock(return_value={"ref_code": "olga-samtsova", "paid_until": None, "partner_paid": False})), \
         patch("app.telegram.pro_start.deliver_text", deliver):
        result = await process_core_telegram_update(whieda_tenant, _update("/start pro"), "t-pro", binding=whieda_bot_binding)
    assert result["route"] == "pro_start"
    text = deliver.await_args.args[1]
    markup = deliver.await_args.kwargs["reply_markup"]
    assert "PRO (сайт)" in text and "30 WWC$" in text and "3 месяца" in text
    assert markup["inline_keyboard"][0][0]["callback_data"] == "renew:start"


@pytest.mark.asyncio
async def test_start_pro_offers_site_to_newcomer(whieda_tenant, whieda_bot_binding):
    deliver = AsyncMock()
    with patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)), \
         patch("app.telegram.pro_start.resolve_partner_subscription_by_telegram_user_id", AsyncMock(return_value=None)), \
         patch("app.telegram.pro_start.deliver_text", deliver):
        result = await process_core_telegram_update(whieda_tenant, _update("/start pro"), "t-pro2", binding=whieda_bot_binding)
    assert result["route"] == "pro_start"
    assert deliver.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "site:create"
```

- [ ] **Step 2: Run** `python -m pytest tests/test_pro_start.py -q` → FAIL (нет модуля / маршрут `start_token` падает на `token_not_found`).

- [ ] **Step 3: Реализация**

```python
# app/telegram/pro_start.py
"""`/start pro` — the site's PRO lock sends people here to see the price and act."""

from __future__ import annotations

from typing import Any

from app.subscriptions.service import resolve_partner_subscription_by_telegram_user_id
from app.telegram.delivery import deliver_text
from app.telegram.update_parser import TelegramMessage
from app.tenancy import TenantContext

PRO_START_TOKEN = "pro"

_TEXT = (
    "PRO (сайт) — ваш партнёрский сайт, калькулятор, прайс повторной покупки, "
    "дизайн сайта и академия.\n"
    "Стоимость: 30 WWC$ за 3 месяца (1 WWC$ = 100 ₽).\n\n"
    "{action}"
)


async def handle_pro_start(tenant: TenantContext, msg: TelegramMessage, trace_id: str) -> dict[str, Any]:
    subscription = await resolve_partner_subscription_by_telegram_user_id(tenant.tenant_id, msg.user_id)
    if subscription:
        action = "Нажмите «Продлить платформу» — бот покажет реквизиты, после оплаты пришлите чек сюда."
        button = {"text": "Продлить платформу", "callback_data": "renew:start"}
    else:
        action = "Своего сайта у вас ещё нет. Нажмите «Создать свой сайт» — заявка уйдёт Виктору."
        button = {"text": "Создать свой сайт", "callback_data": "site:create"}
    await deliver_text(msg.chat_id, _TEXT.format(action=action), reply_markup={"inline_keyboard": [[button]]})
    return {"ok": True, "route": "pro_start", "has_site": bool(subscription), "trace_id": trace_id}
```

В `processor.py` перед `handle_start_token`:

```python
    start_token = parse_start_token(msg.text)
    if start_token:
        if parse_referral_start_token(start_token) is not None:
            return await handle_referral_start_token(tenant, msg, start_token, trace_id)
        if start_token.strip().lower() == PRO_START_TOKEN:
            return await handle_pro_start(tenant, msg, trace_id)
        return await handle_start_token(tenant, msg, start_token, trace_id)
```
Проверить сигнатуру `deliver_text` в `app/telegram/delivery.py` (позиционные `chat_id, text`, kwarg `reply_markup`) — если отличается, подстроить тест и вызов.

- [ ] **Step 4: Run** `python -m pytest tests/test_pro_start.py tests/test_telegram_processor.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(telegram): /start pro explains PRO and routes to renewal or site request`.

### Task A3: Сайт — модель карточки статуса (чистая функция)

**Files:**
- Create: `src/lib/account/status.js`
- Test: `tests/unit/account-status.test.mjs`

**Interfaces:**
- Produces: `buildAccountStatus(me, { now = new Date(), locale = 'ru' })` → `{ signedIn: bool, hasPro: bool, hasClub: bool, balance: {text: '12,5 WWC$'} | null, pro: {label:'PRO', status, daysLeft, daysText:'84 дн.', untilText:'до 06.12.2026', urgent: bool}, club: {...same, status 'none' → daysText 'не подключён'} }`. `hasPro` = `pro.status ∈ {active, grace}`. `urgent` = `daysLeft <= 7` при active.

- [ ] **Step 1: Failing test**

```js
// tests/unit/account-status.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildAccountStatus, formatWwc } from '../../src/lib/account/status.js';

const NOW = new Date('2026-09-13T12:00:00Z');

test('guest: nothing signed in, locks on', () => {
  const s = buildAccountStatus({ ok: false }, { now: NOW });
  assert.equal(s.signedIn, false);
  assert.equal(s.hasPro, false);
  assert.equal(s.balance, null);
});

test('active partner: balance, days, dates', () => {
  const me = { ok: true, data: { authenticated: true, account: {
    balance: { currency: 'WWC$', amount_minor: 1250 },
    pro: { status: 'active', paid_until: '2026-12-06T10:00:00+00:00', days_left: 84, ref_code: 'olga-samtsova' },
    club: { status: 'none', paid_until: null, days_left: null },
  } } };
  const s = buildAccountStatus(me, { now: NOW });
  assert.equal(s.signedIn, true);
  assert.equal(s.hasPro, true);
  assert.equal(s.balance.text, '12,5 WWC$');
  assert.equal(s.pro.daysText, '84 дн.');
  assert.equal(s.pro.untilText, 'до 06.12.2026');
  assert.equal(s.pro.urgent, false);
  assert.equal(s.club.status, 'none');
  assert.equal(s.club.daysText, 'не подключён');
});

test('less than a week left is urgent; grace shows 0 days', () => {
  const me = { ok: true, data: { authenticated: true, account: {
    balance: { currency: 'WWC$', amount_minor: 0 },
    pro: { status: 'active', paid_until: '2026-09-18T10:00:00+00:00', days_left: 4, ref_code: 'a' },
    club: { status: 'grace', paid_until: '2026-09-12T10:00:00+00:00', days_left: 0 },
  } } };
  const s = buildAccountStatus(me, { now: NOW });
  assert.equal(s.pro.urgent, true);
  assert.equal(s.club.daysText, 'истёк');
  assert.equal(s.hasClub, true);
});

test('signed in without account (not a partner yet) shows zero balance and locks', () => {
  const s = buildAccountStatus({ ok: true, data: { authenticated: true, account: null } }, { now: NOW });
  assert.equal(s.signedIn, true);
  assert.equal(s.hasPro, false);
  assert.equal(s.balance.text, '0 WWC$');
});

test('formatWwc: whole numbers without decimals, comma decimal, thin space thousands', () => {
  assert.equal(formatWwc(3000), '30 WWC$');
  assert.equal(formatWwc(1250), '12,5 WWC$');
  assert.equal(formatWwc(123456), '1 234,56 WWC$');
});
```

- [ ] **Step 2: Run** `node --test tests/unit/account-status.test.mjs` → FAIL (module not found).

- [ ] **Step 3: Реализация**

```js
// src/lib/account/status.js
// Модель карточки статуса в меню. Чистая функция: вход — ответ /me, выход —
// готовые подписи. Единица показа — WWC$; amount_minor из Core — сотые WWC$.
const ACTIVE = new Set(['active', 'grace']);

export function formatWwc(amountMinor) {
  const value = Number(amountMinor || 0) / 100;
  const text = value.toLocaleString('ru-RU', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  return `${text.replace(/\u00a0/g, ' ')} WWC$`;
}

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  return `${dd}.${mm}.${d.getFullYear()}`;
}

function product(label, raw) {
  const status = raw?.status || 'none';
  const daysLeft = typeof raw?.days_left === 'number' ? raw.days_left : null;
  let daysText = 'не подключён';
  if (status === 'active') daysText = `${daysLeft} дн.`;
  else if (status === 'grace') daysText = 'истёк';
  else if (status === 'suspended') daysText = 'приостановлен';
  return {
    label,
    status,
    daysLeft,
    daysText,
    untilText: raw?.paid_until ? `до ${formatDate(raw.paid_until)}` : '',
    urgent: status === 'active' && daysLeft !== null && daysLeft <= 7,
  };
}

export function buildAccountStatus(me, { now = new Date() } = {}) {
  const data = me && me.ok ? me.data || {} : {};
  const signedIn = Boolean(data.authenticated);
  const account = data.account || null;
  const pro = product('PRO', account?.pro);
  const club = product('CLUB', account?.club);
  return {
    signedIn,
    hasPro: ACTIVE.has(pro.status),
    hasClub: ACTIVE.has(club.status),
    balance: signedIn ? { text: formatWwc(account?.balance?.amount_minor || 0) } : null,
    pro,
    club,
    now,
  };
}
```

- [ ] **Step 4: Run** `node --test tests/unit/account-status.test.mjs` → PASS (проверить `toLocaleString('ru-RU')` в Node: если разделитель тысяч — `\u202f`, заменить и его).
- [ ] **Step 5: Commit** `feat(menu): account status model for the site menu`.

### Task A4: Сайт — карточка статуса, CLUB на «Академии», плашки-замки

**Files:**
- Create: `src/components/AccountStatusCard.astro`
- Modify: `src/components/SiteMenu.astro:95-119, 200-208` и скрипт
- Modify: `src/styles/global.css` (после `.quick-tile .menu-pro`, ~5211)
- Test: `tests/smoke/*.test.mjs` — существующий smoke собирает страницу; добавить проверку, что в собранном HTML меню есть `data-account-card` и плашка `CLUB` у академии.

**Interfaces:**
- Consumes: `buildAccountStatus`, `createContentAccessClient().getMe()`.
- DOM-контракт: `[data-account-card]` (корень карточки, `hidden` до ответа), `[data-account-balance]`, `[data-account-pro-days]`, `[data-account-pro-until]`, `[data-account-club-days]`, `[data-account-club-until]`; `[data-pro-lock]` на каждой плашке PRO (калькулятор, академия, прайс №2); `[data-pro-gate]` — карточка-объяснение с `[data-pro-gate-login]` и ссылкой `https://t.me/WHIEDA_Advisor_bot?start=pro`. Атрибут `data-access="guest|member|pro"` на `.site-menu__panel` управляет видимостью через CSS.

- [ ] **Step 1: Разметка карточки**

```astro
---
// src/components/AccountStatusCard.astro
// Карточка статуса в шапке бокового меню. Три плитки: баланс WWC$, PRO, CLUB.
// Показывается только вошедшему; гость видит витрину с замками (см. SiteMenu).
const { locale = 'ru' } = Astro.props;
const copy = locale === 'en'
  ? { balance: 'Balance', pro: 'PRO', club: 'CLUB', gateTitle: 'PRO opens', gateBody: 'your partner site, the calculator, the repeat-order price list, site design and the academy — 30 WWC$ per 3 months.', login: 'Sign in with Telegram', buy: 'Get PRO', close: 'Close' }
  : { balance: 'Баланс', pro: 'PRO', club: 'CLUB', gateTitle: 'PRO открывает', gateBody: 'ваш партнёрский сайт, калькулятор, прайс повторной покупки, дизайн сайта и академию — 30 WWC$ за 3 месяца.', login: 'Войти через Telegram', buy: 'Подключить PRO', close: 'Закрыть' };
---
<section class="account-card" data-account-card hidden aria-label={copy.balance}>
  <div class="account-tile account-tile--balance">
    <span class="account-tile__label">{copy.balance}</span>
    <strong class="account-tile__value" data-account-balance>—</strong>
  </div>
  <div class="account-tile" data-account-tile="pro">
    <span class="account-tile__label">{copy.pro}</span>
    <strong class="account-tile__value" data-account-pro-days>—</strong>
    <small class="account-tile__note" data-account-pro-until></small>
  </div>
  <div class="account-tile" data-account-tile="club">
    <span class="account-tile__label">{copy.club}</span>
    <strong class="account-tile__value" data-account-club-days>—</strong>
    <small class="account-tile__note" data-account-club-until></small>
  </div>
</section>

<div class="pro-gate" data-pro-gate hidden role="dialog" aria-modal="false" aria-label={copy.gateTitle}>
  <p class="pro-gate__title"><em class="menu-pro">PRO</em> {copy.gateTitle}</p>
  <p class="pro-gate__body">{copy.gateBody}</p>
  <div class="pro-gate__actions">
    <button type="button" class="pro-gate__button pro-gate__button--ghost" data-pro-gate-login>{copy.login}</button>
    <a class="pro-gate__button" href="https://t.me/WHIEDA_Advisor_bot?start=pro" target="_blank" rel="noopener" data-pro-gate-buy>{copy.buy}</a>
  </div>
  <button type="button" class="pro-gate__close" data-pro-gate-close aria-label={copy.close}>×</button>
</div>
```

- [ ] **Step 2: Вставить в `SiteMenu.astro`**: `import AccountStatusCard from './AccountStatusCard.astro';` и сразу после `.site-menu__head` — `<AccountStatusCard locale={locale} />`. На плашках PRO (калькулятор, академия, прайс №2) добавить `data-pro-lock` и `role="button" tabindex="0"`; на «Академии» заменить текст `PRO` → `CLUB` и класс `menu-pro menu-pro--club`. Панели дать `data-access="guest"` по умолчанию.

- [ ] **Step 3: Скрипт (новый `<script>` modульный, не inline) в конце `SiteMenu.astro`**

```astro
<script>
  import { createContentAccessClient } from '../lib/content-access/api.js';
  import { buildAccountStatus } from '../lib/account/status.js';

  const root = document.querySelector('[data-site-menu]');
  if (root) {
    const panel = root.querySelector('.site-menu__panel');
    const card = root.querySelector('[data-account-card]');
    const gate = root.querySelector('[data-pro-gate]');
    const set = (selector, text) => { const el = root.querySelector(selector); if (el) el.textContent = text; };
    let statusPromise = null;
    const load = () => (statusPromise ||= createContentAccessClient().getMe().catch(() => ({ ok: false })));
    const render = (status) => {
      panel?.setAttribute('data-access', status.hasPro ? 'pro' : status.signedIn ? 'member' : 'guest');
      if (!card) return;
      card.hidden = !status.signedIn;
      if (!status.signedIn) return;
      set('[data-account-balance]', status.balance.text);
      set('[data-account-pro-days]', status.pro.daysText);
      set('[data-account-pro-until]', status.pro.untilText);
      set('[data-account-club-days]', status.club.daysText);
      set('[data-account-club-until]', status.club.untilText);
      root.querySelector('[data-account-tile="pro"]')?.classList.toggle('is-urgent', status.pro.urgent);
      root.querySelector('[data-account-tile="club"]')?.classList.toggle('is-urgent', status.club.urgent);
      root.querySelector('[data-account-tile="pro"]')?.classList.toggle('is-off', !status.hasPro);
      root.querySelector('[data-account-tile="club"]')?.classList.toggle('is-off', !status.hasClub);
    };
    const refresh = () => load().then((me) => render(buildAccountStatus(me)));
    // Запрос только при открытии меню — гость на главной не ждёт Core зря.
    document.querySelector('[data-site-menu-open]')?.addEventListener('click', refresh, { once: false });
    window.addEventListener('wwc:auth-confirmed', () => { statusPromise = null; refresh(); });

    const openGate = (event) => {
      event.preventDefault(); event.stopPropagation();
      if (gate) gate.hidden = false;
    };
    root.querySelectorAll('[data-pro-lock]').forEach((lock) => {
      lock.addEventListener('click', openGate);
      lock.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') openGate(e); });
    });
    gate?.querySelector('[data-pro-gate-close]')?.addEventListener('click', () => { gate.hidden = true; });
    gate?.querySelector('[data-pro-gate-login]')?.addEventListener('click', () => {
      gate.hidden = true;
      root.querySelector('[data-utility-auth]')?.click();
    });
    load().then((me) => {
      const status = buildAccountStatus(me);
      // Вошедшему кнопка «Войти» в карточке не нужна.
      const login = gate?.querySelector('[data-pro-gate-login]');
      if (login) login.hidden = status.signedIn;
    });
  }
</script>
```

- [ ] **Step 4: CSS в `global.css`** (после `.quick-tile .menu-pro { ... }`)

```css
/* Плашка PRO = замок. Показана гостю и вошедшему без подписки; у подписчика
   (панель data-access="pro") её нет вовсе: нет плашки — есть доступ. */
.site-menu__panel[data-access="pro"] .menu-pro:not(.menu-pro--tech):not(.menu-pro--club) { display: none; }
.menu-pro[data-pro-lock] { cursor: pointer; padding-left: 18px; }
.menu-pro[data-pro-lock]::before {
  content: ''; position: absolute; left: 6px; top: 50%; width: 7px; height: 8px; transform: translateY(-45%);
  border: 1.4px solid currentColor; border-radius: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px;
  box-shadow: inset 0 -3px 0 0 currentColor;
}
.menu-pro { position: relative; }
.menu-pro--club { background: color-mix(in srgb, var(--jade) 14%, transparent); color: var(--jade); }

/* Карточка статуса: три плитки, как системный виджет — крупное значение,
   тихая подпись, без рамок внутри. */
.account-card { display: grid; grid-template-columns: 1.2fr 1fr 1fr; gap: 8px; padding: 12px 24px 0; }
.account-tile { position: relative; padding: 12px 12px 10px; border-radius: 14px; background: color-mix(in srgb, var(--surface) 70%, var(--paper)); border: 1px solid var(--line); display: grid; gap: 2px; }
.account-tile--balance { background: linear-gradient(135deg, color-mix(in srgb, var(--gold-light) 28%, var(--surface)), var(--surface)); }
.account-tile__label { font: 500 var(--text-eyebrow)/1.2 var(--font-head); letter-spacing: .04em; color: var(--ink-mute); }
.account-tile__value { font: 600 1.125rem/1.15 var(--font-head); color: var(--ink); font-variant-numeric: tabular-nums; }
.account-tile__note { font-size: .75rem; color: var(--ink-mute); }
.account-tile.is-off .account-tile__value { color: var(--ink-mute); font-weight: 500; font-size: .95rem; }
.account-tile.is-urgent { border-color: var(--gold); }
.account-tile.is-urgent .account-tile__value { color: var(--gold-ink); }

/* Карточка-объяснение PRO. Не модалка на весь экран: всплывает внутри панели. */
.pro-gate { position: relative; margin: 12px 24px 0; padding: 16px 44px 16px 16px; border-radius: 16px; background: var(--surface); border: 1px solid var(--gold-light); box-shadow: 0 12px 30px color-mix(in srgb, var(--gold) 14%, transparent); }
.pro-gate__title { margin: 0 0 6px; font: 600 var(--text-meta)/1.3 var(--font-head); color: var(--ink); }
.pro-gate__body { margin: 0 0 12px; font-size: var(--text-meta); line-height: 1.45; color: var(--ink-soft); }
.pro-gate__actions { display: flex; flex-wrap: wrap; gap: 8px; }
.pro-gate__button { display: inline-flex; align-items: center; min-height: 36px; padding: 0 14px; border-radius: 999px; border: 1px solid transparent; background: linear-gradient(135deg, var(--gold-light), var(--gold)); color: #2a2410; font: 600 var(--text-eyebrow)/1 var(--font-head); text-decoration: none; cursor: pointer; }
.pro-gate__button--ghost { background: transparent; border-color: var(--line); color: var(--ink-soft); }
.pro-gate__close { position: absolute; top: 8px; right: 10px; width: 28px; height: 28px; border: 0; border-radius: 50%; background: transparent; color: var(--ink-mute); font-size: 20px; line-height: 1; cursor: pointer; }
@media (max-width: 420px) { .account-card { grid-template-columns: 1fr 1fr; } .account-tile--balance { grid-column: 1 / -1; } }
```

- [ ] **Step 5: Smoke-тест** — открыть существующий `tests/smoke/*.test.mjs`, найти тест, который читает `dist/index.html`, добавить:

```js
test('menu carries the account card and CLUB badge on academy', async () => {
  const html = await readFile(new URL('../../dist/index.html', import.meta.url), 'utf8');
  assert.match(html, /data-account-card/);
  assert.match(html, /menu-pro--club[^>]*>CLUB</);
  assert.match(html, /data-pro-gate/);
});
```

- [ ] **Step 6: Run** `npm run build && npm test` → PASS. Локально `npm run dev` (launch.json `wwc-dev`), открыть меню: гость — плашки с замком, клик по плашке → карточка; после входа через Telegram — карточка со значениями (проверить на staging с реальным аккаунтом владельца).
- [ ] **Step 7: Commit** `feat(menu): account status card, PRO locks with explainer, CLUB badge on academy`.

### Task A5: Выкладка фазы A

- [ ] Core: `pwsh backend/deploy/core/release_core.ps1 -Target staging` → gate зелёный, `curl https://staging.wwc.best/api/v1/content-access/me` из браузера владельца после входа отдаёт `account`.
- [ ] Сайт: `npm run deploy:staging`; владелец смотрит на `staging.wwc.best`: гость / вошедший / подписчик.
- [ ] После «ок» владельца: `release_core.ps1 -Target core`, `npm run deploy:prod`.
- [ ] `STATE.md` в этой папке: что выкачено, ревизии, что показать владельцу.

---

## Фаза B — инструкция и тексты бота

### Task B1: Аудит текстов бота против поведения

**Files:**
- Test: `backend/platform-api/tests/test_bot_copy_audit.py`
- Modify: `app/telegram/*.py` (только строки, которые видит человек)

- [ ] **Step 1: Собрать все строки** — `rg -n "\"[А-ЯЁ][^\"]{8,}\"" app/telegram app/subscriptions app/referral_bonus app/renewal_requests app/site_requests` → таблица в `STATE.md`: файл:строка → текст → когда показывается → что реально происходит → вердикт (ок / поправить).
- [ ] **Step 2: Известные расхождения, которые надо снять сразу:**
  - `W$` → `WWC$` во всех текстах бота (`billing.py:_PAY_USAGE`, `referral_bonus.py` история/баланс, `renewal_requests.py` суммы, `referral_admin.py` usage). Внутренние коды `WUSD`/`W$` в парсере команд оставить принимающими оба варианта.
  - `referral_bonus.py`: «История баллов» → «История WWC$»; любые «баллы» → «WWC$».
  - `processor.py:131-134` ответы `/start ref_`: проверить, что после `attributed` человек получает то, что обещано («Напишите «с чего начать»» — команда существует? `is_newcomer_panel_request`); если фраза не распознаётся — заменить на кнопку.
  - `routes.py` общий отказ «Не удалось обработать сообщение. Попробуйте ещё раз.» — при `TelegramIdentityConflictError` заменить на «Ваш Telegram привязан к двум профилям. Виктор уже получил сигнал и разберётся.» (перехват в `_process_telegram_update_body`, лог уже есть).
  - `renewal_requests.py:83-85` реквизиты — сверить с владельцем, что номер и аккаунт актуальны (вопрос владельцу в отчёте, не менять молча).
- [ ] **Step 3: Тест-страж**

```python
# tests/test_bot_copy_audit.py
"""User-facing bot copy must use the agreed names: WWC$, PRO (сайт), CLUB; never 'баллы'."""
from pathlib import Path
import re

APP = Path(__file__).resolve().parents[1] / "app"
USER_FACING_DIRS = ("telegram", "subscriptions", "referral_bonus", "renewal_requests", "site_requests")
_RU_STRING = re.compile(r'"([^"\\]*[А-Яа-яЁё][^"\\]*)"')


def _strings():
    for d in USER_FACING_DIRS:
        for p in (APP / d).rglob("*.py"):
            for m in _RU_STRING.finditer(p.read_text(encoding="utf-8")):
                yield p.name, m.group(1)


def test_no_points_wording_in_bot_copy():
    offenders = [(f, s) for f, s in _strings() if re.search(r"\bбалл", s, re.I)]
    assert not offenders, offenders


def test_currency_is_wwc_dollar_in_bot_copy():
    offenders = [(f, s) for f, s in _strings() if re.search(r"(?<!WW)(?<!W)W\$", s) or " W$" in s]
    assert not offenders, offenders
```
Запустить → FAIL с полным списком мест → править → PASS.
- [ ] **Step 4: Run** весь фокусный набор (`release_core.ps1` gate-список) → PASS. **Commit** `copy(telegram): WWC$, PRO (сайт), CLUB; conflict message; audited against behaviour`.

### Task B2: Пайплайн «Подключение партнёра» в существующем стандарте

**Files:**
- Modify: `docs/PARTNER_ONBOARDING_STANDARD_V1.md` (репозиторий сайта)

- [ ] **Step 1: Исправить устаревшее** в разделе 2: синхронизация читает `Partner_Subscriptions` с `0eee524` (`n8n/current/run_partners_ref_runtime_sync_2026-08-01.py`), абзац про `Partners_Ref` переписать как историю; в разделе 0 «Что приходит потом»: Core вписывает и chat id, и **числовой user id** при первом сообщении (`actor_link.py`, `9af97fb`).
- [ ] **Step 2: Новый раздел «-1. Подключение партнёра: пайплайн целиком»** перед разделом 0 (нумерация остальных не меняется — так ссылки на «раздел 0» в других доках живут). Содержание — тезисами, каждый шаг: *что запросить у владельца → команда/экран → что должно появиться → как проверить*:
  1. **Данные от владельца** (одним сообщением): имя для сайта; `@username`; фото (уже в `Downloads/Telegram Desktop`); страна (BY/RU/другая — влияет на реквизиты); кто пригласил (`ref_code` или «Виктор напрямую»); что оплачено/будет оплачено: PRO (мес.) / CLUB (мес.) / настройка сайта / акция и сумма получено; желаемый `ref_code`, если не транслит имени.
  2. **Реестр**: строка в `Partner_Subscriptions` — печатает агент, вставляет владелец (как раньше).
  3. **Runtime**: `python n8n/current/run_partners_ref_runtime_sync_2026-08-01.py` (dry-run → apply) → `lead_actors` + `referral_profiles`. Проверка: `GET /api/v1/public/ref/<code>` → 200.
  4. **Сайт**: разделы 0–4 этого документа (страница, поддомен, деплой).
  5. **Telegram**: партнёр нажимает `/start` (или любую кнопку) у `@WHIEDA_Advisor_bot`; Core сам заполняет chat id и user id. Проверка: карточка статуса на сайте после входа показывает его PRO.
  6. **Реферер**: новый человек по ссылке `ref_…` привязывается сам (first-touch). Исторический — владелец: `реферер ref:кого ref:кто-пригласил`. Нет пригласившего = «Виктор напрямую» (без привязки). Проверка: `бонусы ref:кто-пригласил` показывает приглашённого в списке.
  7. **Оплата**: владелец боту (формат из фазы C; до неё — только PRO одной строкой `оплата ref:code 30 WWC$ 3`). Бот показывает разбор → «Подтвердить». Проверка: `статус ref:code` → срок; у реферера появилось начисление (20 %/10 % только с PRO).
  8. **Sheet**: колонки оплаты/срока/реферера в `Partner_Subscriptions` заполняет владелец по подсказке агента (Core → Sheet ещё не автоматизирован — это «долг», см. ниже).
  9. **Самопроверка «Подключено»**: чек-лист из ТЗ §16 как таблица «инвариант → как проверить → команда».
  10. **Список долгов**: `PROCESS/wwc-partner-connection-20260913/DEBTS.md` в репозитории Core — одна строка на партнёра и нехватку (клуб не записан в Core / реферер не подтверждён / Sheet не обновлён). Команда владельца «какие у нас долги» → агент читает этот файл и отвечает. Правило: файл ведёт агент, закрывает строку только с проверкой.
- [ ] **Step 3: Раздел «Цены и валюта»** — таблица из Global Constraints слово в слово; «W$ — валюта WHIEDA, WWC$ — наша; 1:1; 1 WWC$ = 100 ₽»; «бонус только с PRO».
- [ ] **Step 4:** Ссылки: на `WWC Platform - независимая проверка hotfix 6aaa96c 2026-09-13.md` не ссылаться (Obsidian вне репо) — вместо этого одно предложение «почему так» там, где правило неочевидно.
- [ ] **Step 5: Commit** в репо сайта: `docs(onboarding): full partner connection pipeline, prices in WWC$, sync reads Partner_Subscriptions`. Создать `DEBTS.md` с текущими известными долгами: `natali`, `olesya-vselennaya` — подписки; `olga-samtsova` — персональная цена до оплаты; 10 партнёров без `telegram_user_id` (закроется само при первом сообщении — отметить как «ждём»).

---

## Фаза C — клуб, настройка сайта, персональная цена, один платёж на несколько строк

### Task C1: Миграция `platform_partner_products_v7.sql`

**Files:**
- Create: `postgres/sql/platform_partner_products_v7.sql`
- Modify: `postgres/scripts/apply_staging_platform_all.ps1` (добавить файл в список), `tests/postgres_testkit.py` (`MIGRATIONS` + `platform_partner_renewal_requests_v5.sql`, `platform_partner_subscription_reminders_v6.sql`, новый v7), `app/schema_requirements.py` (`core` += `partner_product_access`, `partner_price_overrides`, `partner_payments`)
- Test: `tests/test_partner_products_schema.py` (статический контракт как `test_referral_bonus_schema.py`) + интеграция в C2.

- [ ] **Step 1: Тест-контракт**

```python
# tests/test_partner_products_schema.py
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
SQL = (ROOT / "postgres" / "sql" / "platform_partner_products_v7.sql").read_text(encoding="utf-8").lower()

def test_products_migration_is_additive_and_isolated():
    for table in ("partner_product_access", "partner_price_overrides", "partner_payments"):
        assert f"create table if not exists {table}" in SQL
        assert f"alter table {table} enable row level security" in SQL
        assert f"{table}_tenant_isolation" in SQL
    assert "drop table" not in SQL

def test_products_migration_seeds_club_and_site_setup_prices():
    assert "('whieda', 'club_3m', 'club_subscription', 3, 12000, 1200000)" in SQL
    assert "('whieda', 'site_setup', 'site_setup', 0, 2000, 200000)" in SQL

def test_ledger_allows_one_line_per_product_per_owner_message():
    assert "partner_payment_ledger_tenant_id_source_telegram_chat_id_telegram_message_id_key" in SQL
    assert "unique (tenant_id, source, telegram_chat_id, telegram_message_id, product_code)" in SQL

def test_price_override_uniqueness_is_partial_and_documented():
    assert "where revoked_at is null" in SQL
```

- [ ] **Step 2: Миграция**

```sql
-- platform_partner_products_v7.sql
-- Клуб и настройка сайта как отдельные продукты; персональные цены; один платёж
-- на несколько строк. Платформа остаётся в partner_subscriptions.
begin;

-- 1. Планы новых продуктов. access_months=0 — разовая услуга.
alter table partner_subscription_plans drop constraint if exists partner_subscription_plans_access_months_check;
alter table partner_subscription_plans add constraint partner_subscription_plans_access_months_check
  check (access_months in (0, 3, 6, 12));
insert into partner_subscription_plans (tenant_id, plan_code, product_code, access_months, price_wusd_minor, price_rub_minor)
values
  ('whieda', 'club_3m', 'club_subscription', 3, 12000, 1200000),
  ('whieda', 'site_setup', 'site_setup', 0, 2000, 200000)
on conflict (tenant_id, plan_code) do nothing;

-- 2. Срок доступа к продуктам, у которых он есть (сейчас — клуб).
create table if not exists partner_product_access (
  tenant_id text not null references tenants (tenant_id),
  ref_code text not null,
  product_code text not null check (product_code in ('club_subscription')),
  paid_until timestamptz,
  updated_at timestamptz not null default now(),
  primary key (tenant_id, ref_code, product_code),
  foreign key (tenant_id, ref_code) references partner_subscriptions (tenant_id, ref_code)
);
alter table partner_product_access enable row level security;
drop policy if exists partner_product_access_tenant_isolation on partner_product_access;
create policy partner_product_access_tenant_isolation on partner_product_access
  using (tenant_id = platform_current_tenant_id()) with check (tenant_id = platform_current_tenant_id());

-- 3. Персональная цена: действует до отзыва. Одна активная на партнёра и продукт.
create table if not exists partner_price_overrides (
  override_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  ref_code text not null,
  product_code text not null,
  price_wusd_minor bigint not null check (price_wusd_minor >= 0),
  reason text not null,
  approved_by_telegram_user_id bigint not null,
  created_at timestamptz not null default now(),
  revoked_at timestamptz
);
-- ВНИМАНИЕ: частичный индекс. Любой ON CONFLICT по нему обязан повторять
-- предикат `where revoked_at is null` (урок 2026-09-12).
create unique index if not exists uq_partner_price_overrides_active
  on partner_price_overrides (tenant_id, ref_code, product_code) where revoked_at is null;
alter table partner_price_overrides enable row level security;
drop policy if exists partner_price_overrides_tenant_isolation on partner_price_overrides;
create policy partner_price_overrides_tenant_isolation on partner_price_overrides
  using (tenant_id = platform_current_tenant_id()) with check (tenant_id = platform_current_tenant_id());

-- 4. Заголовок платежа: сколько реально получено одним переводом.
create table if not exists partner_payments (
  received_payment_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  ref_code text not null,
  received_amount_minor bigint not null check (received_amount_minor >= 0),
  currency text not null check (currency in ('RUB', 'WUSD')),
  source text not null default 'telegram_manual',
  telegram_chat_id bigint not null,
  telegram_message_id bigint not null,
  telegram_user_id bigint not null,
  note text,
  created_at timestamptz not null default now(),
  unique (tenant_id, source, telegram_chat_id, telegram_message_id)
);
alter table partner_payments enable row level security;
drop policy if exists partner_payments_tenant_isolation on partner_payments;
create policy partner_payments_tenant_isolation on partner_payments
  using (tenant_id = platform_current_tenant_id()) with check (tenant_id = platform_current_tenant_id());

-- 5. Строки платежа: одна запись ledger на продукт внутри одного сообщения.
alter table partner_payment_ledger
  add column if not exists received_payment_id uuid references partner_payments (received_payment_id),
  add column if not exists promo_note text,
  add column if not exists list_price_minor bigint;
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_tenant_id_source_telegram_chat_id_telegram_message_id_key;
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_amount_minor_check;
alter table partner_payment_ledger add constraint partner_payment_ledger_amount_minor_check check (amount_minor >= 0);
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_access_months_check;
alter table partner_payment_ledger add constraint partner_payment_ledger_access_months_check check (access_months in (0, 3, 6, 12));
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_period_check;
alter table partner_payment_ledger drop constraint if exists partner_payment_ledger_check;
alter table partner_payment_ledger add constraint partner_payment_ledger_period_check check (period_end >= period_start);
alter table partner_payment_ledger add constraint partner_payment_ledger_one_line_per_product
  unique (tenant_id, source, telegram_chat_id, telegram_message_id, product_code);

-- 6. Разобранные строки многострочной команды хранятся в intent до подтверждения.
alter table partner_payment_intents add column if not exists lines jsonb;

-- 7. Напоминания клуба используют тот же журнал.
alter table partner_subscription_reminder_log drop constraint if exists partner_subscription_reminder_log_event_type_check;
alter table partner_subscription_reminder_log add constraint partner_subscription_reminder_log_event_type_check
  check (event_type in ('due_7d', 'grace_start', 'grace_last', 'club_due_7d', 'club_due_3d', 'club_due_1d'));

commit;
```
Перед написанием: `\d partner_payment_ledger` на production (через `docker exec core-api-1 python -c ...` read-only) — сверить реальные имена constraint'ов (`period_end > period_start` может называться `partner_payment_ledger_check`); миграция должна называть их точно; `drop constraint if exists` для каждого кандидата.

- [ ] **Step 3: Прогнать на локальном кластере** (`scripts/run_postgres_integration_tests.ps1`), добавив v5/v6/v7 в `MIGRATIONS` testkit'а; тесты C2 создаются с этой миграцией. → PASS дважды (идемпотентность через `twice=True`).
- [ ] **Step 4: Commit** `schema: club and site-setup products, personal prices, multi-line payments (v7)`.

### Task C2: Каталог, персональные цены, разбор многострочной команды

**Files:**
- Create: `app/subscriptions/pricing.py`
- Test: `tests/test_pricing.py`

**Interfaces:**
- Produces:
  - `PRODUCTS = {"platform_subscription": Product(code, label="PRO (сайт)", words=("pro","платформа","сайт"), recurring=True), "club_subscription": Product(label="CLUB", words=("клуб","club"), recurring=True), "site_setup": Product(label="настройка сайта", words=("настройка","настройка сайта","setup"), recurring=False)}`
  - `@dataclass PaymentLine(product_code: str, amount_minor: int, currency: str, access_months: int, promo: bool, note: str)`
  - `@dataclass ParsedPayment(identifier: str, lines: list[PaymentLine], received_minor: int | None, currency: str)`
  - `parse_payment_command(text: str) -> ParsedPayment` — бросает `SubscriptionError(usage)` при неоднозначности. Однострочный старый формат `оплата ref:x 30 WWC$ 3` → одна строка PRO.
  - `async def effective_price_minor(conn, tenant_id, ref_code, product_code, access_months, currency) -> tuple[int, str | None]` — (цена, причина override или None). Курс RUB: `price_rub_minor` из плана; для override в RUB — `price_wusd_minor * 100`.
  - `def validate_lines(lines, prices: dict[str, int], received_minor) -> list[str]` — список проблем: строка без `акция` с суммой ≠ цене; `получено` ≠ сумме строк.

- [ ] **Step 1: Failing tests**

```python
# tests/test_pricing.py
import pytest
from app.subscriptions.pricing import PaymentLine, parse_payment_command, validate_lines
from app.subscriptions.service import SubscriptionError

def test_legacy_one_liner_is_a_single_pro_line():
    parsed = parse_payment_command("оплата ref:olga-samtsova 30 WWC$ 3")
    assert parsed.identifier == "ref:olga-samtsova"
    assert parsed.lines == [PaymentLine("platform_subscription", 3000, "WUSD", 3, False, "")]
    assert parsed.received_minor is None

def test_multiline_bundle_with_promo_and_received():
    parsed = parse_payment_command(
        "Оплата @olga_samtsova\nPRO 15 WWC$ 3\nклуб 75 WWC$ 3 акция\nнастройка сайта 0 WWC$ акция\nполучено 90 WWC$"
    )
    assert parsed.identifier == "@olga_samtsova"
    assert [l.product_code for l in parsed.lines] == ["platform_subscription", "club_subscription", "site_setup"]
    assert parsed.lines[1].promo is True and parsed.lines[1].amount_minor == 7500
    assert parsed.lines[2].access_months == 0
    assert parsed.received_minor == 9000 and parsed.currency == "WUSD"

def test_w_dollar_and_rub_are_accepted():
    assert parse_payment_command("оплата ref:a 30 W$ 3").lines[0].currency == "WUSD"
    assert parse_payment_command("оплата ref:a 3000 RUB 3").lines[0].amount_minor == 300000

def test_mixed_currencies_or_unknown_product_are_rejected():
    with pytest.raises(SubscriptionError):
        parse_payment_command("оплата ref:a\nPRO 30 WWC$ 3\nклуб 12000 RUB 3")
    with pytest.raises(SubscriptionError):
        parse_payment_command("оплата ref:a\nкурс 10 WWC$")

def test_validate_flags_wrong_price_without_promo_and_wrong_total():
    lines = [PaymentLine("platform_subscription", 3000, "WUSD", 3, False, ""), PaymentLine("club_subscription", 7500, "WUSD", 3, False, "")]
    problems = validate_lines(lines, {"platform_subscription": 3000, "club_subscription": 12000}, received_minor=10500)
    assert any("CLUB" in p and "120" in p for p in problems)
    ok = validate_lines([lines[0], PaymentLine("club_subscription", 7500, "WUSD", 3, True, "акция")], {"platform_subscription": 3000, "club_subscription": 12000}, received_minor=10500)
    assert ok == []
    assert validate_lines(lines, {"platform_subscription": 3000, "club_subscription": 12000}, received_minor=9999)
```

- [ ] **Step 2: Run** → FAIL (module missing).
- [ ] **Step 3: Реализация** — `pricing.py` с регулярками: заголовок `^(оплата|/pay)\s+(ref:[\w-]+|@\w+)(?:\s+(\d+(?:[.,]\d{1,2})?)\s+(WWC\$|W\$|WUSD|RUB)(?:\s+(3|6|12))?)?$`; строка `^(?P<product>pro|платформа|сайт|клуб|club|настройка(?: сайта)?|setup)\s+(?P<amount>\d+(?:[.,]\d{1,2})?)\s+(?P<cur>WWC\$|W\$|WUSD|RUB)(?:\s+(?P<months>3|6|12))?(?:\s+(?P<note>.+))?$`; `получено\s+(\d+(?:[.,]\d{1,2})?)\s+(WWC\$|W\$|WUSD|RUB)`. `promo = "акци" in note.lower()`. Все суммы → minor (×100). Разные валюты → `SubscriptionError`. `effective_price_minor` читает `partner_price_overrides` (where revoked_at is null) и `partner_subscription_plans`.
- [ ] **Step 4: Run** → PASS. **Commit** `feat(billing): product catalogue, personal prices and multi-line payment parser`.

### Task C3: Запись многострочного платежа + клуб в `/me`

**Files:**
- Modify: `app/subscriptions/service.py` (новая `record_payment_lines_in_connection`, `_record_manual_payment_in_connection` делегирует ей с одной строкой)
- Modify: `app/content_access/account.py` (клуб из `partner_product_access`)
- Test: `tests/test_multiline_payment_postgres.py` (на testkit)

**Interfaces:**
- Produces: `async def record_payment_lines_in_connection(conn, *, tenant_id, ref_code, lines: list[PaymentLine], received_minor: int, currency: str, telegram_chat_id, telegram_message_id, telegram_user_id) -> dict` → `{"received_payment_id", "lines": [ledger rows], "pro_paid_until", "club_paid_until", "referral_bonus", "idempotent"}`. Идемпотентность — по `(tenant, source, chat, message)` в `partner_payments`: повтор с тем же составом → `idempotent=True`, с другим → `PaymentIdempotencyConflictError`.
- PRO-строка продлевает `partner_subscriptions.paid_until` как сейчас; CLUB-строка продлевает `partner_product_access(club_subscription).paid_until` тем же правилом (от текущего срока, если active/grace, иначе от now); `site_setup` — ledger с `access_months=0, period_start=period_end=now`.
- Бонус: `award_referral_bonus_for_payment` вызывается **только** для строки `platform_subscription` (у других продуктов правил нет — двойная защита).

- [ ] **Step 1: Интеграционный тест** (по образцу `test_referral_start_postgres.py`): партнёр `proof-olga` с профилем, инвайтер `proof-elena` с attribution `proof-olga → proof-elena`; override PRO=1500 для `proof-olga`; записать строки `[PRO 1500 ×3, CLUB 7500 ×3 promo, site_setup 0 promo]`, received 9000 → проверить: 3 строки ledger с одним `received_payment_id`; `partner_subscriptions.paid_until` ≈ +3 мес; `partner_product_access` клуб ≈ +3 мес; `partner_bonus_ledger` у `proof-elena` ровно одна запись на **300** (20 % от 1500), не от 9000; повтор того же сообщения → `idempotent=True` и ничего не удвоилось; повтор с другим составом → `PaymentIdempotencyConflictError`; `load_site_account` для `proof-olga` показывает `club.status == "active"`.
- [ ] **Step 2: Run** → FAIL. **Step 3:** реализация. **Step 4:** Run → PASS (+ `test_partner_subscriptions_postgres.py` не сломан). **Step 5: Commit** `feat(billing): one received payment, many product lines; club access; bonus only from PRO line`.

### Task C4: Бот — многострочная `оплата`, команда `цена`, `статус` с клубом

**Files:**
- Modify: `app/telegram/billing.py`
- Test: `tests/test_telegram_billing_multiline.py`

- [ ] **Step 1: Тесты** (моки как в `tests/test_partner_subscriptions.py`): (a) многострочная команда → preview-текст содержит все строки с подписями `PRO (сайт)`, `CLUB`, `настройка сайта`, пометку «акция», `Получено: 90 WWC$`, «Сумма сходится» и кнопки `Подтвердить/Исправить/Отмена`; (b) сумма не сходится → бот **ничего не создаёт**, отвечает «Сумма не сходится: строки 105 WWC$, получено 90 WWC$»; (c) строка без `акция` с ценой ≠ тарифу → «CLUB: 75 WWC$ вместо 120 WWC$. Добавьте слово «акция» или исправьте сумму»; (d) `цена ref:olga-samtsova PRO 15 WWC$ постоянная скидка 50% за участие в проекте` → preview → confirm → override создан; `цена ref:olga-samtsova PRO снять` → revoked; (e) `статус ref:x` показывает PRO до … и CLUB до … / не подключён.
- [ ] **Step 2–4:** реализация через существующий intent (`create_payment_intent` получает `lines` jsonb; `confirm_payment_intent` вызывает `record_payment_lines_in_connection`). Кнопка «Исправить» = отмена intent + подсказка формата. Тексты — из Global Constraints.
- [ ] **Step 5: Commit** `feat(telegram): multi-line owner payment with preview, personal price command, status with CLUB`.

### Task C5: Напоминания клуба

**Files:**
- Modify: `app/subscriptions/reminders.py`, `scripts/send_due_partner_reminders.py` (если фильтрует event_type)
- Test: `tests/test_club_reminders.py`

- [ ] Тест: `list_due_reminders` возвращает `club_due_7d/3d/1d` для `partner_product_access` с `paid_until` через 7/3/1 день, не возвращает уже отправленные; `build_due_reminder_text` для клуба: «CLUB заканчивается через 3 дня (до 16.09.2026). Продлить — 120 WWC$ за 3 месяца, напишите Виктору.»; владельцу — вторая копия «CLUB <ref_code> истекает …». Реализация: `union all` в SQL `list_due_reminders`, отправка владельцу через `platform_billing_owner_telegram_id` как в `renewal_requests.py`.
- [ ] Commit `feat(reminders): club expiry reminders 7/3/1 days to partner and owner`.

### Task C6: Выкладка фазы C

- [ ] Применить `platform_partner_products_v7.sql` к общей БД **до** выкладки кода (иначе schema gate откажет — это ожидаемо: `check_schema_compatibility.py` покажет три новых таблицы). Порядок: локальный кластер (testkit) → общая БД → `release_core.ps1 -Target staging` → владелец проводит тестовую оплату на staging-боте **на тестового партнёра** (`viktor-test`), не на реального → `release_core.ps1 -Target core`.
- [ ] Провести реальные долги: персональная цена Ольги (`цена ref:olga-samtsova PRO 15 WWC$ …`), затем её пакет 90 по команде владельца; `DEBTS.md` обновить.
- [ ] Обновить раздел 7 пайплайна в стандарте: формат многострочной оплаты становится основным.

---

## Самопроверка плана

- Покрытие: названия/валюта (A3, B1, B2), карточка и замки (A4), гость видит витрину (A4 `data-access`), `/start pro` (A2), клуб = учёт+напоминания (C1, C3, C5), настройка сайта 20 (C1 seed), Ольга 15 (C2, C4, C6), пакет 105/90 через `акция` (C2, C4), бонус только PRO (C3 + существующее правило), инструкция без новых доков (B2), аудит текстов (B1), правило B (B2), список долгов (B2, C6), staging сразу (A5).
- Не покрыто намеренно: реальное закрытие калькулятора/прайса №2 для гостей (решение владельца), Core → Sheet экспорт, поддержка через бота (ТЗ §19), отдельная staging-БД.
- Согласованность имён: `load_site_account`/`build_site_account` (A1 ↔ A4 через `/me.account`), `PaymentLine`/`parse_payment_command`/`validate_lines` (C2 ↔ C3 ↔ C4), `record_payment_lines_in_connection` (C3 ↔ C4), `partner_product_access` (C1 ↔ C3 ↔ C5 ↔ A1-club).

---

# Фаза D: Gemini — продажи, доли, взаиморасчёт с Кариной (план v2, 15.09.2026; не начата)

Условия (владелец, 15.09 вечер): цена клиенту **3 990 ₽** за оба тарифа (6 и 18 мес), механика одна. **Платит Карине всегда Виктор**, назад от неё ничего не приходит. Если продал Виктор сам — платит Карине **2 990** (себе 1 000). Если продал партнёр — платит Карине **2 490** (себе 1 000, партнёру **5 WWC$** = 500 ₽). Партнёрам деньги не платим — только WWC$.

## Конструкция
1. **Продажа привязана к заявке #S-N** — она уже есть (тема в группе Карины). Ничего нового человек не делает.
2. **Кто продавец** решается в момент «Оплачено»: бот предлагает реферера клиента (из реферальной привязки), Карина/Виктор подтверждают или выбирают «продал Виктор». Привязка ≠ продажа: Самцову привёл не Виктор, а продал Виктор → 2 990, без доли партнёра.
3. **Цифры — в тарифе**, одна строка на тариф: цена, Карине при прямой, Карине при партнёрской, партнёру WWC$. Меняются командой `тариф …`.
4. **Хвостов нет**: каждая продажа рождает одну обязанность «перевести Карине X ₽ сегодня» с кнопкой «Переведено». В 21:00 МСК бот напоминает о незакрытых.
5. **Партнёру — 5 WWC$** через существующий бонусный ledger (`entry_type = service_commission`), пуш «+5 WWC$ за Gemini клиента #S-N». Видно в кабинете, в теме «Бонусы» и во вкладке `Referral_Bonuses`.
6. **Два служебных места в группе Карины**: тема «Бонусы» (лента начислений) и тема «Отчёты» (команды `отчёт`, `отчёт партнёр <ref>`, `хвосты`). Без имён клиентов.
7. **Активация**: «Активировано до …» → клиенту «лицензия активна до …», напоминание за 7 дней клиенту и в тему; заявка закрывается.

## Финальная шлифовка (владелец, 15.09 поздно вечером)
- **Депозит у Карины вместо переводов за каждую продажу.** Виктор переводит крупно (например 20 000 ₽) — кнопка «Перевёл» с суммой; Карина нажимает «Получила» (подтверждение только на пополнения, их мало). Каждая продажа списывает с депозита 2 990 или 2 490. Баланс виден в теме «Отчёты» и в `отчёт`; когда остаток меньше одной лицензии — бот предупреждает обоих. Хвостов нет по построению; «переведено день в день» не нужно.
- **Продал партнёр → в записи и в ленте указывается исходная заявка #S-N клиента.**
- **В ленте «Бонусы» и в отчётах партнёр обозначается e-mail-логином**, не ref_code и не Telegram (по нику человека находят, по почте — нет). E-mail партнёра хранится в Core (`lead_actors.email`, миграция v10; заполняется из Google-формы и из реестра владельцем). Пока почты нет — «партнёр без почты» + короткий id.

## Данные (миграция v10, additive)
- `service_tariffs(tenant_id, offer_code, retail_minor, wholesale_direct_minor, wholesale_partner_minor, partner_share_wusd_minor, updated_by, updated_at)`.
- `service_sales(sale_id, tenant_id, ticket_id, offer_code, client_actor_id, seller ('owner'|'partner'), partner_ref, retail_minor, owed_admin_minor, paid_currency (RUB|WUSD), paid_at, activated_until, status (paid|activated|closed), created_by)`.
- `service_admin_deposit(entry_id, tenant_id, admin_telegram_user_id, kind ('topup'|'sale'), amount_minor (+/−), sale_id, sent_by, confirmed_by, confirmed_at, created_at)` — баланс = сумма; пополнение считается после «Получила».
- `lead_actors.email text` — для безымянного обозначения партнёра.
- Бонус партнёру: `partner_bonus_ledger`, idempotency по `sale_id`; строка в `Referral_Bonuses` (Операция «Gemini», payment_id = sale_id).

## Порядок
D1 тариф + «Оплачено» (продавец, расчёт, обязанность Карине) + бонус партнёру + пуш — 1 день. D2 депозит: «Перевёл N», «Получила», списание при продаже, предупреждение о низком остатке; «Активировано» + напоминание за 7 дней — 0,5 дня. D3 темы «Бонусы»/«Отчёты», `отчёт`, зеркало в таблицу — 0,5 дня. Выкат staging → проверка в группе → prod тем же SHA.

## Первая живая запись
Самцова, 6 мес, продал Виктор: клиент заплатил Виктору 40 W$ (переплата 35 уже возвращена на баланс), Карине — 2 990 ₽, доли партнёру нет.
