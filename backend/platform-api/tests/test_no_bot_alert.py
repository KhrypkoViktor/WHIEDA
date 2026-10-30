"""Алерт «записался на Живую встречу, но бота не открыл» (срез 9 бот-воронки): тексты, кнопки, выборка, порядок
действий, идемпотентность, шаг воркера. Без базы: настоящий SQL (окно, отметка в jsonb, карточка, возврат шага
после открытия бота) — в tests/test_no_bot_alert_postgres.py.
"""

from __future__ import annotations

import datetime as dt
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.funnel import no_bot_alert as alert
from app.leads import meeting_leads

UTC = dt.timezone.utc
# 05.11.2026 16:30 UTC = чт 05.11 19:30 МСК
STARTS = dt.datetime(2026, 11, 5, 16, 30, tzinfo=UTC)
NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
LEAD_ID = "11111111-1111-1111-1111-111111111111"
CARD_ID = "22222222-2222-2222-2222-222222222222"
CARD_URL = "https://igor.wwc.best/crm/?contact=" + CARD_ID + "#contact/" + CARD_ID
TEXT = (
    "⚠️ Анна Петрова записался(ась) на Живую встречу чт 05.11 19:30 МСК, но бота не открыл(а). "
    "Телефон: +79991112233. Мессенджер: Telegram. Напишите в WhatsApp или позвоните."
)


def _row(**extra) -> dict:
    return {
        "lead_id": LEAD_ID,
        "name": "Анна Петрова",
        "phone_e164": "+79991112233",
        "messenger_pref": "telegram",
        "page_url": "https://igor.wwc.best/vstrecha/",
        "owner_id": "igor-actor",
        "card_id": CARD_ID,
        "starts_at": STARTS,
        **extra,
    }


# ---- тексты и кнопки -------------------------------------------------------------------------


def test_alert_text_is_the_agreed_sentence():
    text = alert.alert_text(
        name="Анна Петрова", phone_e164="+79991112233", messenger="telegram", starts_at=STARTS, now=NOW
    )
    assert text == TEXT


def test_alert_text_names_the_messenger_and_drops_missing_parts():
    via_max = alert.alert_text(name="Олег", phone_e164="+375291234567", messenger="max", starts_at=None, now=NOW)
    assert via_max == (
        "⚠️ Олег записался(ась) на Живую встречу, но бота не открыл(а). "
        "Телефон: +375291234567. Мессенджер: Max. Напишите в WhatsApp или позвоните."
    )
    bare = alert.alert_text(name="", phone_e164=None, messenger=None, starts_at=None, now=NOW)
    assert bare == "⚠️ Без имени записался(ась) на Живую встречу, но бота не открыл(а). Напишите в WhatsApp или позвоните."


def test_alert_text_year_is_shown_only_when_it_is_not_the_current_one():
    next_year = dt.datetime(2027, 1, 12, 16, 30, tzinfo=UTC)
    text = alert.alert_text(name="Анна", phone_e164="+79991112233", messenger=None, starts_at=next_year, now=NOW)
    assert "вт 12.01.2027 19:30 МСК" in text


def test_alert_text_cannot_carry_html_from_the_name():
    text = alert.alert_text(
        name='<b>Анна</b> & <a href="https://evil.example">клик</a>\n<script>',
        phone_e164="+79991112233",
        messenger="telegram",
        starts_at=None,
        now=NOW,
    )
    assert "<" not in text and ">" not in text  # скобки заменены на ‹ ›; & экранирует отправка
    assert "\n" not in text
    assert "‹b›Анна‹/b›" in text
    from app.telegram.delivery import format_telegram_html

    sent = format_telegram_html(text)
    assert "<b>" not in sent and "<a " not in sent and "<script" not in sent and "&amp;" in sent


def test_whatsapp_url_is_digits_only():
    assert alert.whatsapp_url("+79991112233") == "https://wa.me/79991112233"
    assert alert.whatsapp_url("+375291234567") == "https://wa.me/375291234567"
    for bad in (None, "", "89991112233", "+7 999 111-22-33", "+79991112233 ", "javascript:alert(1)", "+7999"):
        assert alert.whatsapp_url(bad) is None


def test_markup_has_whatsapp_and_card_buttons():
    markup = alert.alert_markup("+79991112233", CARD_URL)
    assert markup == {
        "inline_keyboard": [
            [{"text": "WhatsApp", "url": "https://wa.me/79991112233"}],
            [{"text": "👤 Открыть карточку", "url": CARD_URL}],
        ]
    }
    assert alert.alert_markup("+79991112233", None) == {
        "inline_keyboard": [[{"text": "WhatsApp", "url": "https://wa.me/79991112233"}]]
    }
    assert alert.alert_markup(None, CARD_URL) == {"inline_keyboard": [[{"text": "👤 Открыть карточку", "url": CARD_URL}]]}
    assert alert.alert_markup(None, None) is None


def test_shared_names_stay_in_step_with_mark_bot_opened():
    # mark_bot_opened читает метку шага SQL-литералом, алерт пишет её своим SQL.
    assert meeting_leads.ALERT_STEP_KEY == "no_bot_alert_step"
    assert "'no_bot_alert_step'" in alert._STEP_MARK_SQL
    assert "'no_bot_alert_at'" in alert._CLAIM_SQL and "'no_bot_alert_at'" in alert._CANDIDATES_SQL
    assert "'no_bot_alert_skip'" in alert._SKIP_SQL and "'no_bot_alert_skip'" in alert._CANDIDATES_SQL


# ---- выборка -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidates_query_takes_the_window_and_skips_marked_and_opened(monkeypatch):
    monkeypatch.setenv("PLATFORM_ORGANIC_OWNER_ID", "organic")
    from app.settings import get_settings

    get_settings.cache_clear()
    fetch = AsyncMock(return_value=[_row()])
    with patch.object(alert, "fetch_all", fetch):
        rows = await alert.find_candidates(object(), "whieda")
    get_settings.cache_clear()
    assert rows == [_row()]
    sql, params = fetch.await_args.args[1], fetch.await_args.args[2]
    assert params == {
        "tenant_id": "whieda",
        "organic_owner_id": "organic",
        "grace": dt.timedelta(minutes=3),
        "lookback": dt.timedelta(hours=24),
        "limit": 50,
    }
    for fragment in (
        "l.lead_kind = 'meeting'",
        "l.bot_opened_at is null",
        "l.created_at <= now() - %(grace)s",
        "l.created_at > now() - %(lookback)s",
        "->> 'no_bot_alert_at' is null",
        "->> 'no_bot_alert_skip' is null",
        "l.deleted_at is null",
        "coalesce(l.assigned_owner_id, %(organic_owner_id)s)",
    ):
        assert fragment in sql


# ---- один алерт -------------------------------------------------------------------------------


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    async def execute(self, sql, params=()):
        self.conn.executed.append((sql, params))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _Conn:
    """Подключение: transaction() — savepoint-пустышка, cursor() записывает команды."""

    def __init__(self):
        self.executed: list[tuple] = []
        self.rolled_back = 0

    @asynccontextmanager
    async def transaction(self):
        try:
            yield self
        except BaseException:
            self.rolled_back += 1
            raise

    def cursor(self):
        return _Cursor(self)


class _World:
    """Подменяет всё, что алерт берёт снаружи, и пишет порядок вызовов."""

    def __init__(self, *, chat=("7001", 7001), card_url=CARD_URL, claim=True, created=True, moved=dt.date(2026, 10, 30)):
        self.calls: list[str] = []
        self.conn = _Conn()
        self.claim = claim

        async def fetch_one(_conn, sql, params=()):
            if sql is alert._CLAIM_SQL:
                self.calls.append("claim")
                return {"lead_id": params[1]} if self.claim else None
            if sql is alert._SKIP_SQL:
                self.calls.append(f"skip:{params[0]}")
                return {"lead_id": params[2]}
            raise AssertionError(sql)

        async def enqueue(conn, **kwargs):
            self.calls.append("enqueue")
            self.enqueued = kwargs
            return {"outbox_id": 1, "status": "scheduled", "created": created}

        async def move(conn, **kwargs):
            self.calls.append("move_step")
            self.moved_args = kwargs
            return moved

        async def event(conn, **kwargs):
            self.calls.append("card_note")
            self.event_args = kwargs
            return True

        self.owner_chat = AsyncMock(return_value=chat)
        self.card_button_url = AsyncMock(return_value=card_url)
        self.move = AsyncMock(side_effect=move)
        self.event = AsyncMock(side_effect=event)
        self.patches = [
            patch.object(alert, "fetch_one", fetch_one),
            patch.object(alert, "enqueue_outbox_event", enqueue),
            patch.object(alert, "owner_chat", self.owner_chat),
            patch.object(alert, "card_button_url", self.card_button_url),
            patch.object(alert, "move_card_step_today", self.move),
            patch.object(alert, "record_card_event", self.event),
        ]

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *_exc):
        for p in reversed(self.patches):
            p.stop()

    async def run(self, row=None) -> str:
        return await alert.alert_one(self.conn, tenant_id="whieda", binding_id="whieda-advisor-bot", row=row or _row(), now=NOW)


@pytest.mark.asyncio
async def test_alert_goes_to_the_owner_with_both_buttons_and_marks_the_card():
    with _World() as world:
        assert await world.run() == "queued"
    # отметка ставится до сообщения (в одной транзакции), карточка — после
    assert world.calls == ["claim", "enqueue", "move_step", "card_note"]
    sent = world.enqueued
    assert sent["tenant_id"] == "whieda"
    assert sent["event_type"] == "lead_meeting_no_bot_alert"
    assert sent["idempotency_key"] == f"no_bot_alert:{LEAD_ID}"
    assert sent["due_at"] == NOW
    assert sent["payload"] == {
        "chat_id": "7001",
        "text": TEXT,
        "binding_id": "whieda-advisor-bot",
        "lead_id": LEAD_ID,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "WhatsApp", "url": "https://wa.me/79991112233"}],
                [{"text": "👤 Открыть карточку", "url": CARD_URL}],
            ]
        },
        "site_login_user_id": 7001,
    }
    world.owner_chat.assert_awaited_once_with(world.conn, "whieda", "igor-actor")
    # карточка: шаг «Напомнить о себе» на сегодня (только у новой карточки с шагом «Пригласить») и заметка
    assert world.moved_args == {
        "tenant_id": "whieda", "contact_id": CARD_ID, "status": "new", "from_steps": ("invite", None), "to_step": "ping",
    }
    assert world.event_args == {
        "tenant_id": "whieda",
        "contact_id": CARD_ID,
        "note": "Бот не открыт за 3 минуты. Следующий шаг: написать в WhatsApp или позвонить.",
    }
    # дата шага запомнена в заявке: по ней открытие бота вернёт шаг по умолчанию
    sql, params = world.conn.executed[-1]
    assert sql is alert._STEP_MARK_SQL and params == ("2026-10-30", "whieda", LEAD_ID)


@pytest.mark.asyncio
async def test_lead_without_a_card_gets_whatsapp_button_only():
    with _World(card_url=None) as world:
        assert await world.run(_row(card_id=None)) == "queued"
    payload = world.enqueued["payload"]
    assert payload["reply_markup"] == {"inline_keyboard": [[{"text": "WhatsApp", "url": "https://wa.me/79991112233"}]]}
    assert "site_login_user_id" not in payload  # вход на сайт нужен только кнопке карточки
    assert world.calls == ["claim", "enqueue"]  # карточки нет — шаг и заметку писать некуда
    world.card_button_url.assert_awaited_once_with(world.conn, "whieda", 7001, None)


@pytest.mark.asyncio
async def test_closed_crm_keeps_whatsapp_button_and_still_notes_the_card():
    with _World(card_url=None) as world:  # card_button_url → None: у партнёра закрыта CRM
        assert await world.run() == "queued"
    payload = world.enqueued["payload"]
    assert [row[0]["text"] for row in payload["reply_markup"]["inline_keyboard"]] == ["WhatsApp"]
    assert "site_login_user_id" not in payload
    assert world.calls == ["claim", "enqueue", "move_step", "card_note"]


@pytest.mark.asyncio
async def test_card_step_is_not_marked_when_it_was_not_replaced():
    with _World(moved=None) as world:  # партнёр уже отложил шаг или сменил статус
        assert await world.run() == "queued"
    assert world.calls == ["claim", "enqueue", "move_step", "card_note"]
    assert all(sql is not alert._STEP_MARK_SQL for sql, _ in world.conn.executed)


@pytest.mark.asyncio
async def test_same_key_twice_gives_one_message_and_one_card_note():
    with _World(created=False) as world:  # ключ no_bot_alert:<lead_id> уже в очереди
        assert await world.run() == "duplicate"
    assert world.calls == ["claim", "enqueue"]  # карточку второй раз не пишем


@pytest.mark.asyncio
async def test_bot_opened_or_marked_meanwhile_sends_nothing():
    with _World(claim=False) as world:  # update не нашёл строку: бот открыт или отметка уже стоит
        assert await world.run() == "raced"
    assert world.calls == ["claim"]


@pytest.mark.asyncio
async def test_staging_page_is_skipped_with_a_reason():
    for page in ("https://staging.wwc.best/vstrecha/", "https://admin.wwc.best/x", "https://cabinet.wwc.best/"):
        with _World() as world:
            assert await world.run(_row(page_url=page)) == "skipped:test_page"
        assert world.calls == ["skip:test_page"]
        world.owner_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_without_a_telegram_chat_is_skipped_with_a_reason():
    with _World(chat=None) as world:
        assert await world.run() == "skipped:no_owner_chat"
    assert world.calls == ["skip:no_owner_chat"]


@pytest.mark.asyncio
async def test_organic_owner_gets_the_same_message():
    # Заявка без партнёра принадлежит organic-владельцу (PLATFORM_ORGANIC_OWNER_ID): сообщение то же, ему же.
    with _World(chat=("5001", 5001), card_url=None) as world:
        assert await world.run(_row(owner_id="viktor", card_id=None, page_url="https://wwc.best/vstrecha/")) == "queued"
    world.owner_chat.assert_awaited_once_with(world.conn, "whieda", "viktor")
    payload = world.enqueued["payload"]
    assert payload["chat_id"] == "5001" and payload["text"] == TEXT
    assert payload["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://wa.me/79991112233"


@pytest.mark.asyncio
async def test_card_failure_keeps_the_message_and_logs_no_person_data(caplog):
    class Refused(Exception):
        sqlstate = "23514"

        def __str__(self):
            return "Failing row contains (Анна Петрова, +79991112233)"

    with _World() as world:
        world.move.side_effect = Refused()
        with caplog.at_level(logging.WARNING):
            assert await world.run() == "queued"  # сообщение поставлено, сбой карточки — только в лог
    assert world.conn.rolled_back == 1  # откатилась карточка (свой savepoint), а не сообщение
    record = next(r for r in caplog.records if r.message == "no_bot_alert_card_failed")
    assert (record.error_class, record.sqlstate) == ("Refused", "23514")
    assert "Анна" not in caplog.text and "+7999" not in caplog.text


# ---- проход по заявкам -------------------------------------------------------------------------


def _tenant_connection(conn):
    @asynccontextmanager
    async def connection(_tenant_id):
        yield conn

    return connection


@pytest.mark.asyncio
async def test_pass_counts_queued_and_survives_a_failing_lead(caplog):
    class Boom(Exception):
        sqlstate = "57014"

        def __str__(self):
            return "Анна Петрова +79991112233"

    rows = [_row(lead_id="a"), _row(lead_id="b"), _row(lead_id="c", page_url="https://staging.wwc.best/"), _row(lead_id="d")]
    outcomes = ["queued", Boom(), "skipped:test_page", "duplicate"]

    async def one(conn, *, tenant_id, binding_id, row, now=None):
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    conn = _Conn()
    with (
        patch.object(alert, "tenant_connection", _tenant_connection(conn)),
        patch.object(alert, "find_candidates", AsyncMock(return_value=rows)),
        patch.object(alert, "alert_one", one),
        caplog.at_level(logging.INFO),
    ):
        assert await alert.enqueue_no_bot_alerts("whieda", "whieda-advisor-bot") == 1
    assert conn.rolled_back == 1  # savepoint сбойной заявки откатился, остальные пошли дальше
    failed = next(r for r in caplog.records if r.message == "no_bot_alert_failed")
    assert (failed.error_class, failed.sqlstate) == ("Boom", "57014")
    summary = next(r for r in caplog.records if r.message == "no_bot_alert")
    assert (summary.queued, summary.skipped) == (1, 1)
    assert "Анна" not in caplog.text and "+7999" not in caplog.text


@pytest.mark.asyncio
async def test_failing_selection_is_logged_and_the_step_stays_alive(caplog):
    class Boom(Exception):
        sqlstate = "42P01"

    with (
        patch.object(alert, "tenant_connection", _tenant_connection(_Conn())),
        patch.object(alert, "find_candidates", AsyncMock(side_effect=Boom())),
        caplog.at_level(logging.WARNING),
    ):
        assert await alert.enqueue_no_bot_alerts("whieda", "b") == 0
    record = next(r for r in caplog.records if r.message == "no_bot_alert_plan_failed")
    assert (record.error_class, record.sqlstate) == ("Boom", "42P01")


def _binding(tenant_id="whieda", binding_id="whieda-advisor-bot"):
    return SimpleNamespace(binding_id=binding_id, tenant=SimpleNamespace(tenant_id=tenant_id))


@pytest.mark.asyncio
async def test_step_works_only_for_the_whieda_binding():
    plan = AsyncMock(return_value=2)
    with patch.object(alert, "enqueue_no_bot_alerts", plan):
        assert await alert.no_bot_alert_step({"whieda": _binding()}, now=NOW) == 2
        plan.assert_awaited_once_with("whieda", "whieda-advisor-bot", now=NOW)
        plan.reset_mock()
        # процесс без бота whieda (стенд: PLATFORM_SCHEDULED_NOTIFY_BINDINGS пуст) или с чужим тенантом — молчит
        assert await alert.no_bot_alert_step({}) == 0
        assert await alert.no_bot_alert_step({"other": _binding("other", "other-bot")}) == 0
        plan.assert_not_awaited()


# ---- шаг в воркере -----------------------------------------------------------------------------


@pytest.fixture
def _clean_worker_env(monkeypatch):
    from app.settings import get_settings

    for name in ("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", "PLATFORM_ACADEMY_NOTIFY_BINDING"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_worker_runs_the_alert_only_when_planned_and_with_bindings(_clean_worker_env):
    from app.jobs import worker
    from app.settings import get_settings

    _clean_worker_env.setenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", "whieda-advisor-bot")
    get_settings.cache_clear()
    bindings = {"whieda": _binding()}
    step = AsyncMock(return_value=3)
    send = AsyncMock(return_value=1)
    with (
        patch.object(worker, "scheduled_notify_bindings", AsyncMock(return_value=bindings)),
        patch.object(worker, "process_due_notifications", send),
        patch("app.funnel.no_bot_alert.no_bot_alert_step", step),
    ):
        assert await worker.scheduled_notifications_step(plan_crm=False, plan_no_bot=True) == {
            "no_bot_alerts": 3, "due_sent": 1,
        }
        step.assert_awaited_once_with(bindings)
        step.reset_mock()
        assert await worker.scheduled_notifications_step(plan_crm=False) == {"due_sent": 1}  # не по расписанию
        step.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_sends_due_messages_even_if_the_alert_step_fails(_clean_worker_env, caplog):
    from app.jobs import worker
    from app.settings import get_settings

    _clean_worker_env.setenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", "whieda-advisor-bot")
    get_settings.cache_clear()
    send = AsyncMock(return_value=1)
    with (
        patch.object(worker, "scheduled_notify_bindings", AsyncMock(return_value={"whieda": _binding()})),
        patch.object(worker, "process_due_notifications", send),
        patch("app.funnel.no_bot_alert.no_bot_alert_step", AsyncMock(side_effect=RuntimeError("db down"))),
        caplog.at_level(logging.ERROR, logger="app.jobs.worker"),
    ):
        assert await worker.scheduled_notifications_step(plan_crm=False, plan_no_bot=True) == {"due_sent": 1}
    assert any(r.message == "no_bot_alert_step_failed" for r in caplog.records)


@pytest.mark.asyncio
async def test_process_without_scheduled_bindings_never_plans_the_alert(_clean_worker_env):
    from app.jobs import worker

    step = AsyncMock()
    with (
        patch("app.funnel.no_bot_alert.no_bot_alert_step", step),
        patch.object(worker, "scheduled_notify_bindings", AsyncMock(return_value={})) as resolve,
    ):
        assert await worker.scheduled_notifications_step(plan_crm=True, plan_no_bot=True) == {}
    step.assert_not_awaited()
    resolve.assert_not_awaited()


# ---- возврат шага при открытии бота --------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_the_bot_restores_the_step_the_alert_set():
    conn = _Conn()
    restore = AsyncMock(return_value=True)
    with patch.object(meeting_leads, "restore_card_step", restore):
        await meeting_leads._restore_alert_step(conn, "whieda", LEAD_ID, CARD_ID, "2026-10-30")
    restore.assert_awaited_once_with(
        conn, tenant_id="whieda", contact_id=CARD_ID, status="new", from_step="ping", from_next_at=dt.date(2026, 10, 30)
    )
    # метка алерта снимается из заявки, чтобы возврат шага не повторялся
    sql, params = conn.executed[-1]
    assert "- %s" in sql and params == ("no_bot_alert_step", "whieda", LEAD_ID)


@pytest.mark.asyncio
async def test_restore_failure_never_breaks_marking_the_bot_opened(caplog):
    class Refused(Exception):
        sqlstate = "40001"

    conn = _Conn()
    with (
        patch.object(meeting_leads, "restore_card_step", AsyncMock(side_effect=Refused())),
        caplog.at_level(logging.WARNING),
    ):
        await meeting_leads._restore_alert_step(conn, "whieda", LEAD_ID, CARD_ID, "2026-10-30")
        await meeting_leads._restore_alert_step(conn, "whieda", LEAD_ID, CARD_ID, "не дата")  # испорченная метка
    assert conn.rolled_back == 2
    assert [r.message for r in caplog.records].count("meeting_lead_alert_step_restore_failed") == 2
