"""Бот 30.09.2026 (владелец: «ходим по кругу, проверь всё ещё раз»).

* Чек и фото анкеты идут в заявку, даже если у партнёра открыто обращение:
  чек Татьяны ушёл в #S-11 как обычное вложение, кнопок «Подтвердить» не было.
* Брошенная анкета или продление (не двигались 3 дня) не перехватывают
  обычные сообщения: у партнёров с 12–23.09 бот отвечал на всё «напишите
  адрес» / «оплатите» и глотал фото. Ждущее вложение по-прежнему принимается.
* Ответ владельца (Reply) на сообщение бота в теме «Заявки на сайты» уходит
  партнёру через его обращение; раньше он молча пропадал.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.telegram import renewal_requests as rr
from app.telegram import site_requests as sr
from app.telegram import support
from app.telegram.bindings import binding_context_scope
from app.telegram.update_parser import TelegramMessage
from tests.test_telegram_support_site import OWNER, SITE_FORUM, _site_ticket, two_admins  # noqa: F401

PARTNER = 906076712
ORDERS_THREAD = 5
NOW = datetime.now(timezone.utc)


def _private(*, text: str = "", file_id: str | None = None, user: int = PARTNER) -> TelegramMessage:
    return TelegramMessage(
        chat_id=user, user_id=user, message_id=8062, text=text, chat_type="private",
        file_id=file_id, raw={}, username="T4477",
    )


def _orders_reply(text: str, *, reply: dict | None, user: int = OWNER) -> TelegramMessage:
    message = {"message_id": 900, "text": text, "message_thread_id": ORDERS_THREAD, "is_topic_message": True}
    if reply is not None:
        message["reply_to_message"] = reply
    return TelegramMessage(
        chat_id=SITE_FORUM, user_id=user, message_id=900, text=text, chat_type="supergroup",
        file_id=None, raw={"message": message}, thread_id=ORDERS_THREAD, is_forum=True,
    )


def _bot_step(text: str) -> dict:
    return {"message_id": 777, "from": {"id": 1, "is_bot": True}, "text": text}


# ----------------------------------------------------------------------------
# Чек и фото анкеты — в заявку, даже при открытом обращении
# ----------------------------------------------------------------------------

def _support_patches(*, site_status: str | None, renewal_status: str | None = None):
    site = {"status": site_status} if site_status else None
    renewal = {"status": renewal_status} if renewal_status else None
    return (
        patch("app.telegram.support.ensure_telegram_actor", AsyncMock(return_value=f"telegram:whieda:{PARTNER}")),
        patch("app.telegram.support.get_open_site_request", AsyncMock(return_value=site)),
        patch("app.telegram.support.get_open_renewal_request", AsyncMock(return_value=renewal)),
        patch("app.telegram.support.try_relay_user_message", AsyncMock(return_value={"route": "support_relay"})),
    )


@pytest.mark.parametrize(
    ("site_status", "renewal_status"),
    [("awaiting_payment", None), ("awaiting_photo", None), (None, "awaiting_payment")],
)
def test_receipt_or_photo_goes_to_the_request_not_to_the_open_ticket(whieda_tenant, two_admins, site_status, renewal_status):
    a, b, c, relay = _support_patches(site_status=site_status, renewal_status=renewal_status)
    with a, b, c, relay as relayed:
        result = asyncio.run(support.try_handle_support_message(whieda_tenant, _private(file_id="F1"), trace_id="t"))
    assert result is None  # дальше processor отдаёт вложение renewal / site_request
    relayed.assert_not_awaited()


@pytest.mark.parametrize("site_status", [None, "awaiting_text", "pending_confirmation"])
def test_other_attachments_still_go_to_the_open_ticket(whieda_tenant, two_admins, site_status):
    a, b, c, relay = _support_patches(site_status=site_status)
    with a, b, c, relay as relayed:
        result = asyncio.run(support.try_handle_support_message(whieda_tenant, _private(file_id="F1"), trace_id="t"))
    assert result == {"route": "support_relay"}
    relayed.assert_awaited_once()


# ----------------------------------------------------------------------------
# Брошенная анкета / продление не перехватывают сообщения
# ----------------------------------------------------------------------------

def _site_request(status: str, *, age: timedelta) -> dict:
    return {"request_id": "r1", "status": status, "updated_at": NOW - age, "requested_subdomain": "tatiana", "country_code": "RU"}


def _run_site(tenant, msg: TelegramMessage, request: dict, monkeypatch) -> tuple[dict | None, AsyncMock, AsyncMock]:
    monkeypatch.setattr(sr, "_actor", AsyncMock(return_value="a1"))
    monkeypatch.setattr(sr, "get_open_site_request", AsyncMock(return_value=request))
    subdomain = AsyncMock(return_value={**request, "status": "awaiting_photo"})
    photo = AsyncMock(return_value={**request, "status": "awaiting_text"})
    monkeypatch.setattr(sr, "set_site_request_subdomain", subdomain)
    monkeypatch.setattr(sr, "set_site_request_photo", photo)
    monkeypatch.setattr(sr, "_notify_owner_step", AsyncMock())
    monkeypatch.setattr(sr, "_deliver", AsyncMock())
    return asyncio.run(sr.try_handle_site_request_message(tenant, msg, trace_id="t")), subdomain, photo


def test_stale_questionnaire_lets_ordinary_text_through(whieda_tenant, monkeypatch):
    result, subdomain, _ = _run_site(whieda_tenant, _private(text="Здравствуйте, вопрос по сайту"), _site_request("awaiting_subdomain", age=timedelta(days=7)), monkeypatch)
    assert result is None
    subdomain.assert_not_awaited()


def test_stale_questionnaire_still_takes_the_photo_it_waits_for(whieda_tenant, monkeypatch):
    result, _, photo = _run_site(whieda_tenant, _private(file_id="F1"), _site_request("awaiting_photo", age=timedelta(days=7)), monkeypatch)
    assert result and result["route"] == "site_request"
    photo.assert_awaited_once()


def test_fresh_questionnaire_keeps_taking_text(whieda_tenant, monkeypatch):
    result, subdomain, _ = _run_site(whieda_tenant, _private(text="tatiana"), _site_request("awaiting_subdomain", age=timedelta(hours=2)), monkeypatch)
    assert result and result["route"] == "site_request"
    subdomain.assert_awaited_once()


def test_stale_renewal_does_not_answer_every_message_with_pay(whieda_tenant, monkeypatch):
    monkeypatch.setattr(rr, "_actor", AsyncMock(return_value="a1"))
    monkeypatch.setattr(rr, "get_open_renewal_request", AsyncMock(return_value={"status": "awaiting_payment", "updated_at": NOW - timedelta(days=10)}))
    prompt = AsyncMock()
    monkeypatch.setattr(rr, "_prompt", prompt)
    assert asyncio.run(rr.try_handle_renewal_message(whieda_tenant, _private(text="ежедневник"), trace_id="t")) is None
    prompt.assert_not_awaited()


def test_request_is_stale_after_three_days():
    from app.site_requests.service import request_is_stale

    assert request_is_stale({"updated_at": NOW - timedelta(days=4)}, now=NOW)
    assert not request_is_stale({"updated_at": NOW - timedelta(days=2)}, now=NOW)
    assert not request_is_stale({}, now=NOW)


# ----------------------------------------------------------------------------
# Ответ владельца в «Заявках на сайты» — партнёру
# ----------------------------------------------------------------------------

def _forum(tenant_id: str, *, binding_id: str, kind: str = "services"):
    if kind == "site":
        return {"chat_id": SITE_FORUM, "kind": "site", "reports_thread_id": ORDERS_THREAD}
    return None


def _run_orders(msg: TelegramMessage, whieda_bot_binding, *, ticket: dict | None = None):
    ticket = ticket or _site_ticket(user_telegram_user_id=PARTNER, user_chat_id=PARTNER, ticket_no=11, created=False,
                                    forum_chat_id=SITE_FORUM, forum_thread_id=71, user_display="@T4477")
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    open_ticket = AsyncMock(return_value=ticket)
    with binding_context_scope(whieda_bot_binding), patch("app.telegram.support.get_forum", AsyncMock(side_effect=_forum)), patch(
        "app.telegram.support.find_ticket_by_forum_thread", AsyncMock(return_value=None)
    ), patch("app.telegram.support.try_handle_service_command", AsyncMock(return_value=None)), patch(
        "app.telegram.support.open_or_reuse_ticket", open_ticket
    ), patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.set_message_reaction", AsyncMock(return_value={"ok": True})
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False})):
        result = asyncio.run(support.try_handle_support_forum_message(whieda_bot_binding.tenant, msg, trace_id="t"))
    return result, send, open_ticket


def test_owner_reply_in_orders_topic_reaches_the_partner_through_the_ticket(whieda_bot_binding, two_admins):
    step = _bot_step(f"Заявка на сайт — @T4477 · id {PARTNER}: контакты получено.\nАдрес: tatiana.wwc.best")
    result, send, open_ticket = _run_orders(_orders_reply("Татьяна, ждём оплату. Хотите в клуб?", reply=step), whieda_bot_binding)
    assert result["route"] == "site_orders_reply"
    assert open_ticket.await_args.kwargs["user_telegram_user_id"] == PARTNER
    assert open_ticket.await_args.kwargs["user_display"] == "@T4477"
    by_chat = {c.kwargs["chat_id"]: c.kwargs for c in send.await_args_list}
    assert by_chat[str(PARTNER)]["text"] == "Ответ команды WWC по обращению #S-11:\nТатьяна, ждём оплату. Хотите в клуб?"
    topics = [c.kwargs.get("message_thread_id") for c in send.await_args_list if c.kwargs["chat_id"] == str(SITE_FORUM)]
    assert 71 in topics and ORDERS_THREAD in topics  # копия в теме обращения + отметка в «Заявках»


def test_reply_without_partner_id_says_it_went_nowhere(whieda_bot_binding, two_admins):
    result, send, open_ticket = _run_orders(_orders_reply("ок", reply=_bot_step("Новая заявка на сайт.")), whieda_bot_binding)
    assert result["status"] == "no_partner"
    open_ticket.assert_not_awaited()
    assert "никуда не ушёл" in send.await_args.kwargs["text"]


def test_plain_note_in_orders_topic_is_not_relayed(whieda_bot_binding, two_admins):
    topic_root = {"message_id": ORDERS_THREAD, "from": {"id": 1, "is_bot": True}, "text": ""}
    result, send, open_ticket = _run_orders(_orders_reply("заметка себе", reply=topic_root), whieda_bot_binding)
    assert result is None
    send.assert_not_awaited()
    open_ticket.assert_not_awaited()


def test_step_alert_carries_the_partner_id():
    assert sr.partner_tag(_private()) == f"@T4477 · id {PARTNER}"
    no_name = TelegramMessage(chat_id=5, user_id=5, message_id=1, text="", chat_type="private", file_id=None, raw={})
    assert sr.partner_tag(no_name) == "id 5"
