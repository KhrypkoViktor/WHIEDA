"""Напоминание незаконченным заявкам на сайт: worker раз в сутки в 10:00 МСК (24.10.2026).

Без сети и без базы: фейки на месте БД, Telegram и обращений. SQL выборки и ключ дня
проверяет на настоящей базе test_site_request_nudge_postgres.py."""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.site_requests import nudge

UTC = dt.timezone.utc

EXPECTED_TEXT_ANNA = "\n".join([
    "Анна, здравствуйте! Вы начали оформлять личный сайт WHIEDA, а до конца мы вас пока не увидели. Очень ждём вас в команде!",
    "",
    "Что-то остановило: вопрос, непонятный шаг, раздумья? Напишите сюда, подскажем и поможем дойти до результата.",
    "",
    "Если удобно, расскажу о системе полного запуска онлайн-бизнеса в МЛМ, три части работают вместе:",
    "• Клуб — ядро изменений мышления.",
    "• Курс — навыки соцсетей и ИИ.",
    "• Сайт — премиум-визитка и сервисы, которые ускоряют вашу работу.",
    "",
    "Напишите, что вам откликается, разберём вашу ситуацию.",
])


def test_letter_text_is_the_agreed_one():
    assert nudge.template("Анна") == EXPECTED_TEXT_ANNA
    nameless = nudge.template("")
    assert nameless.startswith("Здравствуйте! Вы начали оформлять личный сайт WHIEDA")
    assert nameless.splitlines()[1:] == EXPECTED_TEXT_ANNA.splitlines()[1:]


def test_first_name_comes_from_the_form_then_from_the_profile_but_never_from_a_username():
    assert nudge.first_name({"partner_name": "Анна Иванова", "display_name": "@anna"}) == "Анна"
    assert nudge.first_name({"partner_name": "  ", "display_name": "Мария К"}) == "Мария"
    assert nudge.first_name({"partner_name": None, "display_name": "@nick"}) == ""
    assert nudge.first_name({"partner_name": None, "display_name": None}) == ""


def test_the_letter_goes_out_at_ten_moscow_time_and_until_eight_in_the_evening():
    # 24.10.2026, Москва = UTC+3.
    assert nudge.due_day(dt.datetime(2026, 10, 24, 6, 59, tzinfo=UTC)) is None  # 09:59 МСК
    assert nudge.due_day(dt.datetime(2026, 10, 24, 7, 0, tzinfo=UTC)) == dt.date(2026, 10, 24)  # 10:00
    # Worker перезапущен днём — первый запуск после 10:00 всё ещё рассылает.
    assert nudge.due_day(dt.datetime(2026, 10, 24, 12, 30, tzinfo=UTC)) == dt.date(2026, 10, 24)  # 15:30
    assert nudge.due_day(dt.datetime(2026, 10, 24, 16, 59, tzinfo=UTC)) == dt.date(2026, 10, 24)  # 19:59
    assert nudge.due_day(dt.datetime(2026, 10, 24, 17, 0, tzinfo=UTC)) is None  # 20:00 — поздно
    # Дата считается по Москве, не по UTC: 00:30 МСК 25.10 — ночь, не «сегодня 24-го».
    assert nudge.due_day(dt.datetime(2026, 10, 24, 21, 30, tzinfo=UTC)) is None
    assert nudge.due_day(dt.datetime(2026, 10, 25, 7, 5, tzinfo=UTC)) == dt.date(2026, 10, 25)


def _settings(monkeypatch, *, environment: str, enabled: bool = True) -> None:
    monkeypatch.setattr(
        nudge, "get_settings",
        lambda: SimpleNamespace(environment=environment, platform_site_request_nudge_enabled=enabled),
    )


class _Day:
    """Ключ дня как в базе: второй insert за тот же день ничего не возвращает."""

    def __init__(self) -> None:
        self.claimed: set[dt.date] = set()

    async def claim(self, day: dt.date) -> bool:
        if day in self.claimed:
            return False
        self.claimed.add(day)
        return True


@pytest.fixture
def step(monkeypatch):
    """Шаг worker с фейками: binding, ключ дня, рассылка. Возвращает (claims, run, binding_lookup)."""
    _settings(monkeypatch, environment="production")
    days = _Day()
    run = AsyncMock(return_value=[{"rid": "a", "sent": True}, {"rid": "b", "sent": False}])
    lookup = AsyncMock(return_value=SimpleNamespace(bot_token="tok"))
    monkeypatch.setattr(nudge, "_claim_day", days.claim)
    monkeypatch.setattr(nudge, "run_nudge", run)
    monkeypatch.setattr(nudge, "resolve_bot_binding_context", lookup)
    return SimpleNamespace(days=days, run=run, lookup=lookup)


MORNING = dt.datetime(2026, 10, 24, 7, 0, tzinfo=UTC)  # 10:00 МСК


@pytest.mark.asyncio
async def test_staging_never_sends(monkeypatch, step):
    # Staging делит базу и бота с боем: ни бот не ищется, ни ключ дня не берётся, ни письма.
    for environment in ("staging", "development", ""):
        _settings(monkeypatch, environment=environment)
        assert await nudge.site_request_nudge_step(MORNING) == 0
    step.lookup.assert_not_awaited()
    step.run.assert_not_awaited()
    assert step.days.claimed == set()


@pytest.mark.asyncio
async def test_switch_off_in_production(monkeypatch, step):
    _settings(monkeypatch, environment="production", enabled=False)
    assert await nudge.site_request_nudge_step(MORNING) == 0
    step.run.assert_not_awaited()
    assert step.days.claimed == set()


@pytest.mark.asyncio
async def test_production_sends_once_a_day_and_again_the_next_day(step):
    assert await nudge.site_request_nudge_step(MORNING) == 1  # из двух писем доставлено одно
    assert step.run.await_count == 1 and step.run.await_args.kwargs["send"] is True
    assert step.run.await_args.kwargs["binding"].bot_token == "tok"
    # Worker проверяет каждые 5 минут и дольше: в тот же день — ничего.
    for minutes in (5, 60, 6 * 60):
        assert await nudge.site_request_nudge_step(MORNING + dt.timedelta(minutes=minutes)) == 0
    assert step.run.await_count == 1
    # Следующий день — снова.
    assert await nudge.site_request_nudge_step(MORNING + dt.timedelta(days=1, minutes=1)) == 1
    assert step.run.await_count == 2
    assert step.days.claimed == {dt.date(2026, 10, 24), dt.date(2026, 10, 25)}


@pytest.mark.asyncio
async def test_worker_started_after_ten_still_sends_that_day_but_not_at_night(step):
    assert await nudge.site_request_nudge_step(dt.datetime(2026, 10, 24, 5, 0, tzinfo=UTC)) == 0  # 08:00 — рано
    assert step.days.claimed == set()
    assert await nudge.site_request_nudge_step(dt.datetime(2026, 10, 24, 11, 40, tzinfo=UTC)) == 1  # 14:40
    assert await nudge.site_request_nudge_step(dt.datetime(2026, 10, 24, 20, 0, tzinfo=UTC)) == 0  # 23:00
    assert step.run.await_count == 1


@pytest.mark.asyncio
async def test_without_the_bot_the_day_is_not_spent(step):
    # Бот не нашёлся (сбой базы) — ключ дня не берём: следующая проверка через 5 минут попробует снова.
    step.lookup.return_value = None
    assert await nudge.site_request_nudge_step(MORNING) == 0
    assert step.days.claimed == set()
    step.lookup.return_value = SimpleNamespace(bot_token="tok")
    assert await nudge.site_request_nudge_step(MORNING + dt.timedelta(minutes=5)) == 1


# --- run_nudge: письмо одному партнёру ----------------------------------------------------------------

BINDING = SimpleNamespace(
    bot_token="tok", binding_id="whieda-advisor-bot", tenant=SimpleNamespace(tenant_id="whieda"),
)


def _row(rid: str, *, name: str = "Анна Иванова", user_id: int | None = 111, username: str | None = "anna") -> dict:
    return {
        "rid": rid, "status": "awaiting_plan", "partner_name": name, "upd": "2026-10-22 08:15:00.123+00",
        "telegram_chat_id": str(user_id) if user_id else "999", "telegram_user_id": user_id,
        "display_name": "Anna", "telegram_username": username,
    }


class _Sending:
    """Фейки вместо базы, обращений и Telegram; записывают, что и кому ушло."""

    def __init__(self, monkeypatch, rows: list[dict], *, created: bool = True, send_ok: bool = True) -> None:
        self.notice_inserts: list[tuple] = []
        self.messages: list[dict] = []
        self.relayed: list[dict] = []
        self.tickets_for: list[int] = []
        self.fail_for: set[int] = set()
        self.created = created

        @asynccontextmanager
        async def connection(tenant_id):
            cursor = SimpleNamespace(execute=AsyncMock(side_effect=lambda sql, params: self.notice_inserts.append((sql, params))))

            @asynccontextmanager
            async def cur():
                yield cursor

            yield SimpleNamespace(cursor=cur)

        async def open_ticket(tenant_id, **kwargs):
            uid = kwargs["user_telegram_user_id"]
            if uid in self.fail_for:
                raise RuntimeError("db down")
            self.tickets_for.append(uid)
            self.last_open = kwargs
            return {"created": self.created, "ticket_id": f"t{uid}", "forum_chat_id": -100500, "forum_thread_id": 7}

        async def relay(tenant_id, **kwargs):
            self.relayed.append(kwargs)
            return {}

        async def send(**kwargs):
            self.messages.append(kwargs)
            return {"ok": send_ok, "message_id": 5000 + len(self.messages)} if kwargs.get("message_thread_id") is None else {"ok": True}

        monkeypatch.setattr(nudge, "find_candidates", AsyncMock(return_value=rows))
        monkeypatch.setattr(nudge, "tenant_connection", connection)
        monkeypatch.setattr(nudge, "open_or_reuse_ticket", open_ticket)
        monkeypatch.setattr(nudge, "record_relayed_message", relay)
        monkeypatch.setattr(nudge, "send_telegram_text", send)
        sup = nudge.support
        monkeypatch.setattr(sup, "_admin_for_kind", lambda kind: 42)
        monkeypatch.setattr(sup, "_site_card", AsyncMock(return_value={"card": True}))
        monkeypatch.setattr(sup, "_open_forum_topic", AsyncMock(side_effect=lambda tenant, ticket, site=None: ticket))
        monkeypatch.setattr(sup, "_site_header_lines", lambda ticket, site: ["#S-1", "заявка"])
        monkeypatch.setattr(sup, "_send_site_header", AsyncMock(return_value={"message_id": 31}))
        monkeypatch.setattr(sup, "_in_forum", lambda ticket: True)
        monkeypatch.setattr(sup, "ticket_label", lambda ticket: "#S-1042")


@pytest.mark.asyncio
async def test_list_without_send_writes_and_sends_nothing(monkeypatch):
    fakes = _Sending(monkeypatch, [_row("r1"), _row("r2", name="", user_id=222, username=None)])
    report = await nudge.run_nudge(send=False)
    assert [(i["rid"], i["to"], i["stalled_since"]) for i in report] == [
        ("r1", "Анна", "2026-10-22 08:15"), ("r2", "Anna", "2026-10-22 08:15"),
    ]
    assert fakes.tickets_for == [] and fakes.messages == [] and fakes.notice_inserts == []


@pytest.mark.asyncio
async def test_letter_goes_through_the_support_ticket_and_is_remembered_per_request(monkeypatch):
    fakes = _Sending(monkeypatch, [_row("r1")])
    [item] = await nudge.run_nudge(send=True, binding=BINDING)
    assert item["sent"] is True and item["ticket"] == "#S-1042" and item["to"] == "Анна"
    # Обращение открыто на партнёра по сайту, ответ придёт в тему владельца.
    assert fakes.last_open["user_telegram_user_id"] == 111 and fakes.last_open["admin_telegram_user_id"] == 42
    assert fakes.last_open["channel_code"] == nudge.support.CHANNEL_SITE
    assert fakes.last_open["user_display"] == "Анна (@anna)"
    # Партнёру — текст из шаблона без изменений, под номером обращения.
    to_partner = fakes.messages[0]
    assert to_partner["chat_id"] == "111" and to_partner["bot_token"] == "tok"
    assert to_partner["text"] == "Ответ команды WWC по обращению #S-1042:\n" + EXPECTED_TEXT_ANNA
    # В тему владельца — копия, а в журнал обращения — шапка и письмо.
    assert fakes.messages[1]["chat_id"] == "-100500" and fakes.messages[1]["message_thread_id"] == 7
    assert [r["direction"] for r in fakes.relayed] == ["system", "admin_to_user"]
    assert fakes.relayed[1]["text"] == EXPECTED_TEXT_ANNA
    # Защита 1: ключ заявки в service_notice_log.
    [(sql, params)] = fakes.notice_inserts
    assert "service_notice_log" in sql and params == ("site_nudge:r1",)


@pytest.mark.asyncio
async def test_reused_ticket_gets_no_second_header(monkeypatch):
    fakes = _Sending(monkeypatch, [_row("r1")], created=False)
    await nudge.run_nudge(send=True, binding=BINDING)
    assert [r["direction"] for r in fakes.relayed] == ["admin_to_user"]
    nudge.support._send_site_header.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_delivery_is_not_remembered_so_the_request_returns_tomorrow(monkeypatch):
    fakes = _Sending(monkeypatch, [_row("r1")], send_ok=False)
    [item] = await nudge.run_nudge(send=True, binding=BINDING)
    assert item["sent"] is False
    assert fakes.notice_inserts == []
    assert [r["direction"] for r in fakes.relayed] == ["system"]  # письма в журнале нет


@pytest.mark.asyncio
async def test_one_broken_partner_does_not_stop_the_rest(monkeypatch):
    fakes = _Sending(monkeypatch, [_row("r1", user_id=111), _row("r2", user_id=222), _row("r3", user_id=333)])
    fakes.fail_for = {222}
    report = await nudge.run_nudge(send=True, binding=BINDING)
    assert [(i["rid"], i["sent"]) for i in report] == [("r1", True), ("r2", False), ("r3", True)]
    assert report[1]["error"] == "RuntimeError"
    assert [params for _, params in fakes.notice_inserts] == [("site_nudge:r1",), ("site_nudge:r3",)]


@pytest.mark.asyncio
async def test_only_limits_the_run_to_one_request(monkeypatch):
    fakes = _Sending(monkeypatch, [_row("r1", user_id=111), _row("r2", user_id=222)])
    report = await nudge.run_nudge(send=True, binding=BINDING, only="r2")
    assert [i["rid"] for i in report] == ["r2"] and fakes.tickets_for == [222]


@pytest.mark.asyncio
async def test_partner_without_user_id_falls_back_to_the_chat_id(monkeypatch):
    fakes = _Sending(monkeypatch, [_row("r1", user_id=None)])
    await nudge.run_nudge(send=True, binding=BINDING)
    assert fakes.tickets_for == [999] and fakes.messages[0]["chat_id"] == "999"


@pytest.mark.asyncio
async def test_sending_without_a_bot_stops_before_touching_anyone(monkeypatch):
    fakes = _Sending(monkeypatch, [_row("r1")])
    monkeypatch.setattr(nudge, "resolve_bot_binding_context", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="bot_binding_unavailable"):
        await nudge.run_nudge(send=True)
    assert fakes.tickets_for == [] and fakes.messages == []


def test_the_selection_keeps_the_agreed_statuses_and_all_three_guards():
    """Условия выборки не должны тихо измениться: статусы awaiting_*, три защиты от дублей."""
    sql = " ".join(nudge.CANDIDATES_SQL.split())
    assert "r.status like %s" in sql
    assert "r.updated_at < now() - make_interval(hours => %s)" in sql
    assert "r.updated_at > now() - make_interval(days => %s)" in sql
    assert "n.notice_key = 'site_nudge:' || r.request_id::text" in sql
    assert "n2.created_at > now() - interval '30 days'" in sql
    assert "m.direction = 'admin_to_user' and m.created_at > now() - interval '24 hours'" in sql
    # В базе ничего не меняем «на всякий случай»: SET и RESET за транзакционным пулером запрещены.
    assert not any(word in sql.lower().split() for word in ("set", "reset"))
