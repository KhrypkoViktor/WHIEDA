"""«Мои заявки» в боте Max (17.10.2026): человек пишет слово — бот отвечает списком заявок с сайта.

Бот сам ничего не присылает (правила Max, п. 1.5): только ответ на сообщение. База — подделка:
проверяем решения модуля (кого узнали, что показали, что не отдали советнику), не SQL —
SQL списка проверяет tests/test_max_my_leads_postgres.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.crm.bot import LOCK_TEXT
from app.crm.rules import CrmViewer
from app.max import my_leads, processor, webapp
from app.max.update_parser import MaxEvent, parse_max_update
from app.settings import get_settings
from app.tenancy import TenantContext

WITH_CRM = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                         entitlements={"structure_basic": True, "crm": True})
WITHOUT_CRM = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                            entitlements={"structure_basic": True})
WITH_WISHES = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                            entitlements={"crm": True, "site_support": True})
PAID = CrmViewer(telegram_user_id=700, is_preview_admin=False, partner_paid=True, ref_code="igor")
UNPAID = CrmViewer(telegram_user_id=700, is_preview_admin=False, partner_paid=False, ref_code="igor")

NOT_LINKED = ("Пока не вижу ваш аккаунт. Войдите на сайт через Max один раз: wwc.best → «Войти» → "
              "«Не получается через Telegram?» → «Войти через Max». После этого заявки будут здесь.")


def _event(text: str, user_id: int = 5001, kind: str = "message") -> MaxEvent:
    return MaxEvent(kind=kind, user_id=user_id, chat_id=777, text=text, username=None, display_name="Айгуль", raw={})


def _lead(day: str, name: str, phone: str = "", note_contact: str = "", status: str = "Новый контакт") -> dict:
    return {"name": name, "phone": phone, "note_contact": note_contact, "status_title": status,
            "requested_on": date.fromisoformat(f"2026-{day}")}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.delenv("PLATFORM_DISABLED_FEATURES", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def bot(monkeypatch):
    """Всё, что обработчик трогает снаружи: отправка в Max, советник, привязка Max → Telegram, CRM."""
    channels: dict[str, int | None] = {}  # Max user_id → telegram_user_id (lead_actors)

    async def fetch_one(_conn, query: str, params: tuple = ()):
        assert "lead_actor_channels" in query and "lead_actors" in query, "the same lookup as the Max web login"
        assert params[0] == "whieda" and "max" in query
        value = channels.get(params[1])
        return {"telegram_user_id": value} if value is not None else None

    @asynccontextmanager
    async def connection(_tenant):
        yield object()

    sent = AsyncMock(return_value={"ok": True})
    advisor = AsyncMock(return_value={"answer_text": "Ответ советника", "answer_mode": "structured_business"})
    viewer = AsyncMock(return_value=PAID)
    leads = AsyncMock(return_value={"items": [], "more": False})
    ensure = AsyncMock()
    monkeypatch.setattr(processor, "send_max_text", sent)
    monkeypatch.setattr(processor, "handle_structured_query", advisor)
    monkeypatch.setattr(processor, "ensure_channel_actor", ensure)
    monkeypatch.setattr(processor, "should_deliver_telegram_response", lambda _r: True)
    monkeypatch.setattr(webapp, "fetch_one", fetch_one)
    monkeypatch.setattr(my_leads, "tenant_connection", connection)
    monkeypatch.setattr(my_leads, "load_viewer", viewer)
    monkeypatch.setattr(my_leads, "recent_site_leads", leads)
    return type("Bot", (), {"sent": sent, "advisor": advisor, "channels": channels, "viewer": viewer, "leads": leads})


def _said(bot) -> str:
    bot.sent.assert_awaited_once()
    return bot.sent.await_args.kwargs["text"]


@pytest.mark.parametrize("text", [
    "Мои заявки", "мои заявки", "МОИ ЗАЯВКИ", "Мои заявки!", "  мои  заявки?  ", "«Мои заявки»", "мои заявки.",
    "Заявки", "заявки", "/leads", "/Leads", "leads", "/заявки",
])
def test_the_words_that_open_the_list(text):
    assert my_leads.is_my_leads_text(text)


@pytest.mark.parametrize("text", [
    "", "заявки на что?", "мои заявки за месяц", "оставить заявку", "заявка", "как оформить заявку",
    "стельки с анионами", "пожелание", "/start", "лидс", "leads please",
])
def test_other_text_is_not_mine(text):
    assert not my_leads.is_my_leads_text(text)


@pytest.mark.asyncio
async def test_person_not_linked_gets_the_login_hint_and_the_advisor_stays_silent(bot):
    out = await processor.handle_max_message(WITH_CRM, _event("Мои заявки"), "t1")
    assert _said(bot) == NOT_LINKED
    assert (out["route"], out["status"]) == ("max_my_leads", "not_linked")
    bot.advisor.assert_not_awaited()
    bot.leads.assert_not_awaited()
    bot.viewer.assert_not_awaited()


@pytest.mark.asyncio
async def test_linked_partner_without_requests_is_told_so(bot):
    bot.channels["5001"] = 700
    out = await processor.handle_max_message(WITH_CRM, _event("заявки"), "t1")
    assert _said(bot) == "Заявок пока нет. Все заявки с сайта попадают в WWC CRM: wwc.best/crm/"
    assert out["status"] == "empty"
    bot.advisor.assert_not_awaited()


@pytest.mark.asyncio
async def test_partner_who_never_opened_the_diary_is_pointed_to_it(bot):
    bot.channels["5001"] = 700
    bot.leads.return_value = None  # no platform_accounts row: nothing is created by reading
    out = await processor.handle_max_message(WITH_CRM, _event("/leads"), "t1")
    text = _said(bot)
    assert text.startswith("Заявок пока нет. Откройте WWC CRM один раз") and text.endswith("wwc.best/crm/")
    assert out["status"] == "no_diary"


@pytest.mark.asyncio
async def test_three_requests_are_listed_newest_first_in_one_line_each(bot):
    bot.channels["5001"] = 700
    bot.leads.return_value = {"items": [
        _lead("10-17", "Ольга Иванова", phone="+79991112233"),
        _lead("10-16", "Марат", note_contact="@marat_tg", status="Приглашён"),
        _lead("10-02", "Без связи", status="Думает"),
    ], "more": False}
    out = await processor.handle_max_message(WITH_CRM, _event("Мои заявки"), "t1")
    assert _said(bot) == (
        "Заявки с сайта:\n"
        "17.10 · Ольга Иванова · +79991112233 · Новый контакт\n"
        "16.10 · Марат · @marat_tg · Приглашён\n"
        "02.10 · Без связи · Думает\n"
        "\n"
        "Все заявки и заметки: wwc.best/crm/"
    )
    assert out["status"] == "ok"
    bot.advisor.assert_not_awaited()
    # Ровно тот человек, чей Max пишет, и ровно десять.
    bot.leads.assert_awaited_once_with("whieda", 700, limit=10)


@pytest.mark.asyncio
async def test_more_than_ten_says_these_are_the_latest(bot):
    bot.channels["5001"] = 700
    bot.leads.return_value = {"items": [_lead("10-17", f"Человек {n}") for n in range(10)], "more": True}
    await processor.handle_max_message(WITH_CRM, _event("мои заявки"), "t1")
    text = _said(bot)
    assert text.startswith("Последние 10 заявок с сайта:\n") and text.endswith("Все заявки и заметки: wwc.best/crm/")
    assert text.count("\n17.10 · ") == 10


@pytest.mark.asyncio
async def test_a_site_form_cannot_stretch_or_break_the_answer(bot):
    bot.channels["5001"] = 700
    bot.leads.return_value = {"items": [
        _lead("10-17", "Очень\nдлинное   имя " + "я" * 200, phone="+7 999 " + "1" * 100),
        _lead("10-16", "   "),
    ], "more": False}
    await processor.handle_max_message(WITH_CRM, _event("мои заявки"), "t1")
    lines = _said(bot).splitlines()
    first, second = lines[1], lines[2]
    assert first.startswith("17.10 · Очень длинное имя яяя") and first.endswith("· Новый контакт")
    assert "…" in first and len(first) < 150
    assert second == "16.10 · Без имени · Новый контакт"


@pytest.mark.asyncio
async def test_without_pro_the_same_text_as_in_telegram(bot):
    bot.channels["5001"] = 700
    bot.viewer.return_value = UNPAID
    out = await processor.handle_max_message(WITH_CRM, _event("мои заявки"), "t1")
    assert _said(bot) == LOCK_TEXT["pro_required"]
    assert out["status"] == "pro_required"
    bot.leads.assert_not_awaited()
    bot.advisor.assert_not_awaited()


@pytest.mark.asyncio
async def test_outside_the_pilot_the_same_text_as_in_telegram(bot, monkeypatch):
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "77")
    get_settings.cache_clear()
    bot.channels["5001"] = 700
    await processor.handle_max_message(WITH_CRM, _event("мои заявки"), "t1")
    assert _said(bot) == LOCK_TEXT["crm_pilot_only"]
    bot.leads.assert_not_awaited()


@pytest.mark.asyncio
async def test_tenant_without_crm_or_with_the_feature_off_gets_the_closed_text(bot, monkeypatch):
    bot.channels["5001"] = 700
    out = await processor.handle_max_message(WITHOUT_CRM, _event("мои заявки"), "t1")
    assert _said(bot) == LOCK_TEXT["crm_pilot_only"] and out["status"] == "crm_unavailable"
    bot.sent.reset_mock()
    monkeypatch.setenv("PLATFORM_DISABLED_FEATURES", "crm")
    get_settings.cache_clear()
    await processor.handle_max_message(WITH_CRM, _event("мои заявки"), "t1")
    assert _said(bot) == LOCK_TEXT["crm_pilot_only"]
    bot.viewer.assert_not_awaited()
    bot.leads.assert_not_awaited()
    bot.advisor.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_database_failure_is_an_answer_not_silence_and_not_a_question_for_the_advisor(bot, caplog):
    bot.channels["5001"] = 700
    bot.leads.side_effect = RuntimeError('duplicate key (name)=(Ольга Иванова) (phone)=(+79991112233)')
    with caplog.at_level("WARNING"):
        out = await processor.handle_max_message(WITH_CRM, _event("мои заявки"), "t1")
    assert _said(bot).startswith("Не получилось загрузить заявки.") and out["status"] == "error"
    bot.advisor.assert_not_awaited()
    assert "Ольга" not in caplog.text and "+7999" not in caplog.text, "a psycopg message carries the failing row"


def test_the_greeting_tells_what_to_write():
    hint = "Напишите «Мои заявки» — покажу заявки с сайта."
    assert processor._greeting("invalid", None).splitlines()[-1] == hint
    assert processor._greeting("attributed", None).splitlines()[-1] == hint
    assert processor.WELCOME_PLAIN.splitlines()[-1] == hint


@pytest.mark.asyncio
async def test_a_group_never_gets_the_list(bot):
    """Группа Max — только мост потока; «Мои заявки» там не отвечает ни советник, ни список."""
    bot.channels["5001"] = 700
    update = {"update_type": "message_created", "message": {
        "sender": {"user_id": 5001, "first_name": "Айгуль"}, "recipient": {"chat_id": 5, "chat_type": "chat"},
        "body": {"mid": "m1", "text": "Мои заявки"}}}
    event = parse_max_update(update)
    assert event is not None and event.kind == "group_message"
    out = await processor.process_max_event(WITH_CRM, event, "t1")
    assert out["route"] == "max_group"
    bot.sent.assert_not_awaited()
    bot.leads.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_word_goes_through_the_whole_webhook_path(bot):
    bot.channels["5001"] = 700
    event = parse_max_update({"update_type": "message_created", "message": {
        "sender": {"user_id": 5001, "first_name": "Айгуль"}, "recipient": {"chat_id": 777, "chat_type": "dialog"},
        "body": {"mid": "m1", "text": "Мои заявки"}}})
    out = await processor.process_max_event(WITH_CRM, event, "t1")
    assert out["route"] == "max_my_leads"
    assert bot.sent.await_args.kwargs["chat_id"] == 777


@pytest.mark.asyncio
async def test_an_ordinary_question_still_goes_to_the_advisor(bot):
    bot.channels["5001"] = 700
    out = await processor.handle_max_message(WITH_CRM, _event("стельки с анионами — размеры?"), "t1")
    assert out["route"] == "max_message"
    bot.advisor.assert_awaited_once()
    assert _said(bot) == "Ответ советника"
    bot.leads.assert_not_awaited()
    bot.viewer.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_wish_flow_works_as_before(bot):
    from app.telegram.feedback import WISH_PROMPT

    start = AsyncMock()
    with patch("app.feedback.service.start_wish", start), \
         patch("app.feedback.service.waiting_wish", AsyncMock(return_value=None)):
        out = await processor.handle_max_message(WITH_WISHES, _event("Пожелание"), "t1")
    assert out["route"] == "max_wish" and out["status"] == "awaiting"
    start.assert_awaited_once()
    assert _said(bot) == WISH_PROMPT
    bot.advisor.assert_not_awaited()
    bot.leads.assert_not_awaited()
    bot.viewer.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_wish_text_itself_still_reaches_the_owner(bot):
    """Человек нажал «Пожелание» и пишет само пожелание — это не команда списка и не вопрос советнику."""
    submit = AsyncMock(return_value={"feedback_no": 17})
    with patch("app.feedback.service.waiting_wish", AsyncMock(return_value={"feedback_id": "f1"})), \
         patch("app.feedback.service.submit_wish", submit), \
         patch("app.telegram.bindings.resolve_bot_binding_context", AsyncMock(return_value=None)):
        out = await processor.handle_max_message(WITH_WISHES, _event("Хочу видеть цены в евро"), "t1")
    assert (out["route"], out["status"], out["no"]) == ("max_wish", "new", 17)
    submit.assert_awaited_once()
    bot.advisor.assert_not_awaited()
    bot.leads.assert_not_awaited()
