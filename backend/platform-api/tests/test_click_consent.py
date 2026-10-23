"""Согласие с офертой и политикой по нажатию (владелец, 23.10.2026; аудит «legal»).

* «Заказать сайт»: пока нет нажатия «Принимаю», заявка не создаётся; нажатие пишет
  согласие с источником ``site_order``; открытая анкета и повторный заказ не переспрашиваются;
  сбой записи — заявки нет (fail closed).
* Вход на сайт через бота (``/start content_access_…``): строка «Входя, вы принимаете
  оферту и политику» со ссылками в подтверждении; подтверждённый вход пишет согласие
  с источником ``site_login`` и сбоем записи не блокируется.
* Старое «продолжая общение» (``policy_version`` — просто дата) этим не затронуто.
* Бот витрины автора Академии (ALMIRA, ``binding.showcase_id``) документов WWC не показывает и
  согласия с ними не пишет: у витрины свой оператор и своя политика (``showcase:<id>:<дата>``);
  заказать сайт WWC в боте витрины нельзя вовсе.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.telegram import consent as consent_module
from app.telegram import site_requests as sr
from app.telegram.consent import (
    CLICK_CONSENT_VERSION,
    CONSENT_POLICY_VERSION,
    SITE_ORDER_ACCEPT_CALLBACK,
    SOURCE_SITE_LOGIN,
    SOURCE_SITE_ORDER,
    click_consent_version,
    has_click_consent,
    record_click_consent,
    site_login_consent_line,
    site_order_consent_keyboard,
    site_order_consent_text,
)
from app.telegram.update_parser import TelegramCallbackQuery

OFFER = "https://wwc.best/offer/"
POLICY = "https://wwc.best/privacy-policy/"


# --- texts -------------------------------------------------------------------


def test_order_text_has_phrase_both_links_and_accept_button():
    text = site_order_consent_text("whieda")
    assert "Оформляя заказ, вы принимаете оферту и политику обработки персональных данных" in text
    assert OFFER in text and POLICY in text
    button = site_order_consent_keyboard()["inline_keyboard"][0][0]
    assert button == {"text": "Принимаю", "callback_data": SITE_ORDER_ACCEPT_CALLBACK}
    assert SITE_ORDER_ACCEPT_CALLBACK == "site:accept"


def test_login_line_has_phrase_and_both_links():
    line = site_login_consent_line("whieda")
    assert line.startswith("Входя, вы принимаете оферту")
    assert OFFER in line and POLICY in line


def test_tenants_without_published_documents_get_nothing():
    assert site_order_consent_text("nsp") is None
    assert site_login_consent_line("nsp") is None


def test_source_travels_in_policy_version_and_never_equals_the_old_plain_date():
    assert click_consent_version(SOURCE_SITE_ORDER) == f"site_order:{CLICK_CONSENT_VERSION}"
    assert click_consent_version(SOURCE_SITE_LOGIN) == f"site_login:{CLICK_CONSENT_VERSION}"
    # «Продолжая общение» хранится под голой датой: клик отличим по префиксу источника.
    assert click_consent_version(SOURCE_SITE_ORDER) != CONSENT_POLICY_VERSION
    assert len(CLICK_CONSENT_VERSION) == 10


@pytest.mark.asyncio
async def test_record_click_consent_writes_the_versioned_source():
    with patch.object(consent_module, "record_telegram_consent", AsyncMock(return_value=True)) as record:
        assert await record_click_consent("whieda", telegram_user_id=7, telegram_chat_id=8, source=SOURCE_SITE_ORDER)
    record.assert_awaited_once_with(
        "whieda", telegram_user_id=7, telegram_chat_id=8, policy_version=f"site_order:{CLICK_CONSENT_VERSION}"
    )


@pytest.mark.asyncio
async def test_has_click_consent_asks_for_the_same_version():
    seen: list[dict] = []

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def fake_fetch_one(conn, sql, params):
        seen.append(params)
        return {"consented_at": "2026-10-23"}

    with patch.object(consent_module, "tenant_connection", lambda tenant_id: _Conn()), patch.object(
        consent_module, "fetch_one", fake_fetch_one
    ):
        assert await has_click_consent("whieda", 7, SOURCE_SITE_LOGIN) is True
    assert seen == [{"tenant_id": "whieda", "user_id": 7, "version": f"site_login:{CLICK_CONSENT_VERSION}"}]


# --- «Заказать сайт» ---------------------------------------------------------


def _callback(data: str, *, chat_type: str = "private") -> TelegramCallbackQuery:
    return TelegramCallbackQuery(
        chat_id=100, user_id=200, callback_query_id="cb-1", data=data, chat_type=chat_type,
        raw={"callback_query": {"from": {"id": 200, "username": "someone"}}},
    )


class _Site:
    """Подмена всего, что трогает базу и Telegram в обработчике «Заказать сайт»."""

    def __init__(self, *, open_request=None, consented=False, record=None) -> None:
        self.sent: list[tuple[int, str, dict | None]] = []
        self.begin = AsyncMock(return_value={"status": "awaiting_name", "request_id": "r-1"})
        self.record = record or AsyncMock(return_value=True)
        self.has = AsyncMock(return_value=consented)
        self.open_request = open_request

        async def deliver(chat_id, text, *, reply_markup=None, thread_id=None):
            self.sent.append((chat_id, text, reply_markup))

        self.patches = [
            patch.object(sr, "_actor", AsyncMock(return_value="actor-1")),
            patch.object(sr, "get_open_site_request", AsyncMock(return_value=open_request)),
            patch.object(sr, "has_click_consent", self.has),
            patch.object(sr, "record_click_consent", self.record),
            patch.object(sr, "begin_site_request", self.begin),
            patch.object(sr, "_deliver", deliver),
            patch.object(sr, "answer_callback_query", AsyncMock(return_value={"ok": True})),
            patch.object(sr, "current_bot_binding", lambda: SimpleNamespace(bot_token="t", binding_id="b")),
        ]

    def __enter__(self):
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self.patches:
            item.stop()


@pytest.mark.asyncio
async def test_create_without_click_asks_for_consent_and_creates_no_request(whieda_tenant):
    with _Site() as site:
        result = await sr.try_handle_site_request_callback(whieda_tenant, _callback("site:create"), trace_id="t")
    assert result["status"] == "consent_required"
    site.begin.assert_not_awaited()
    site.record.assert_not_awaited()
    (chat_id, text, markup), = site.sent
    assert chat_id == 100
    assert "Оформляя заказ, вы принимаете оферту и политику обработки персональных данных" in text
    assert OFFER in text and POLICY in text
    assert markup == site_order_consent_keyboard()


@pytest.mark.asyncio
async def test_accept_records_site_order_consent_then_creates_the_request(whieda_tenant):
    with _Site() as site:
        result = await sr.try_handle_site_request_callback(whieda_tenant, _callback("site:accept"), trace_id="t")
    assert result["ok"] is True and result["status"] == "awaiting_name" and result["consent"] == "site_order"
    site.record.assert_awaited_once_with(
        "whieda", telegram_user_id=200, telegram_chat_id=100, source=SOURCE_SITE_ORDER
    )
    site.begin.assert_awaited_once_with("whieda", "actor-1")
    assert "имя и фамилию" in site.sent[-1][1]


@pytest.mark.asyncio
async def test_consent_is_recorded_before_the_request_is_created(whieda_tenant):
    order: list[str] = []
    record = AsyncMock(side_effect=lambda *a, **k: order.append("record") or True)
    with _Site(record=record) as site:
        site.begin.side_effect = lambda *a, **k: order.append("begin") or {"status": "awaiting_name"}
        await sr.try_handle_site_request_callback(whieda_tenant, _callback("site:accept"), trace_id="t")
    assert order == ["record", "begin"]


@pytest.mark.asyncio
async def test_storage_failure_on_accept_creates_no_request(whieda_tenant):
    with _Site(record=AsyncMock(side_effect=RuntimeError("db down"))) as site:
        result = await sr.try_handle_site_request_callback(whieda_tenant, _callback("site:accept"), trace_id="t")
    assert result == {"ok": False, "route": "site_request", "status": "consent_storage_failed", "trace_id": "t"}
    site.begin.assert_not_awaited()
    # Человек видит, что случилось, и кнопка «Принимаю» остаётся под рукой.
    assert "заявка не создана" in site.sent[-1][1]
    assert site.sent[-1][2] == site_order_consent_keyboard()


@pytest.mark.asyncio
async def test_repeat_order_after_a_click_goes_straight_to_the_form(whieda_tenant):
    with _Site(consented=True) as site:
        result = await sr.try_handle_site_request_callback(whieda_tenant, _callback("site:create"), trace_id="t")
    assert result["status"] == "awaiting_name"
    site.has.assert_awaited_once_with("whieda", 200, SOURCE_SITE_ORDER)
    site.begin.assert_awaited_once()
    site.record.assert_not_awaited()
    assert "Оформляя заказ" not in site.sent[-1][1]


@pytest.mark.asyncio
async def test_open_form_is_resumed_without_asking(whieda_tenant):
    # Анкета, начатая до этого правила (или после клика), продолжается как раньше.
    with _Site(open_request={"status": "awaiting_photo", "request_id": "r-0"}) as site:
        result = await sr.try_handle_site_request_callback(whieda_tenant, _callback("site:create"), trace_id="t")
    assert result["status"] == "awaiting_name"
    site.has.assert_not_awaited()
    site.begin.assert_awaited_once()


@pytest.mark.asyncio
async def test_tenant_without_published_documents_is_not_gated():
    from app.tenancy import TenantContext

    nsp = TenantContext(tenant_id="nsp", status="active", display_name="NSP", entitlements={})
    with _Site() as site:
        result = await sr.try_handle_site_request_callback(nsp, _callback("site:create"), trace_id="t")
    assert result["status"] == "awaiting_name"
    site.begin.assert_awaited_once()
    site.has.assert_not_awaited()


@pytest.mark.asyncio
async def test_accept_in_a_group_chat_is_refused(whieda_tenant):
    with _Site() as site:
        result = await sr.try_handle_site_request_callback(
            whieda_tenant, _callback("site:accept", chat_type="supergroup"), trace_id="t"
        )
    assert result["status"] == "private_chat_required"
    site.record.assert_not_awaited()
    site.begin.assert_not_awaited()


# --- вход на сайт через бота -------------------------------------------------


def _login_update(user_id: int = 210, chat_id: int = 110) -> dict:
    return {
        "update_id": 700001,
        "message": {"text": "/start content_access_tokXYZ", "chat": {"id": chat_id}, "from": {"id": user_id}},
    }


@pytest.mark.asyncio
async def test_login_confirmation_carries_the_line_and_records_site_login(whieda_tenant, whieda_bot_binding):
    from app.telegram.processor import process_core_telegram_update

    confirm = AsyncMock(return_value={"ok": True, "status": "approved", "challenge_id": "c-1", "return_to": "/academy/"})
    record = AsyncMock(return_value=True)
    with patch("app.telegram.content_access.confirm_content_from_telegram", confirm), patch(
        "app.telegram.content_access.record_click_consent", record
    ), patch("app.telegram.content_access.deliver_text", AsyncMock()) as deliver:
        result = await process_core_telegram_update(
            whieda_tenant, _login_update(), "t-login", binding=whieda_bot_binding
        )
    assert result["route"] == "content_access" and result["ok"] is True
    record.assert_awaited_once_with("whieda", telegram_user_id=210, telegram_chat_id=110, source=SOURCE_SITE_LOGIN)
    message = deliver.await_args.args[1]
    assert "Вход подтверждён" in message
    assert message.endswith(site_login_consent_line("whieda"))
    assert "Входя, вы принимаете оферту" in message and OFFER in message and POLICY in message
    # Лишних шагов нет: кнопка по-прежнему одна — «Открыть сайт в браузере».
    assert [b["text"] for row in deliver.await_args.args[2]["inline_keyboard"] for b in row] == [
        "Открыть сайт в браузере"
    ]


@pytest.mark.asyncio
async def test_declined_login_records_nothing(whieda_tenant, whieda_bot_binding):
    from fastapi import HTTPException

    from app.telegram.processor import process_core_telegram_update

    confirm = AsyncMock(side_effect=HTTPException(status_code=410, detail={"error": "challenge_expired"}))
    record = AsyncMock(return_value=True)
    with patch("app.telegram.content_access.confirm_content_from_telegram", confirm), patch(
        "app.telegram.content_access.record_click_consent", record
    ), patch("app.telegram.content_access.deliver_text", AsyncMock()) as deliver:
        result = await process_core_telegram_update(
            whieda_tenant, _login_update(), "t-expired", binding=whieda_bot_binding
        )
    assert result["ok"] is False and result["status"] == "challenge_expired"
    record.assert_not_awaited()
    assert "Входя, вы принимаете" not in deliver.await_args.args[1]


@pytest.mark.asyncio
async def test_login_still_succeeds_when_the_consent_cannot_be_stored(whieda_tenant, whieda_bot_binding):
    from app.telegram.processor import process_core_telegram_update

    confirm = AsyncMock(return_value={"ok": True, "status": "approved", "challenge_id": "c-2"})
    record = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("app.telegram.content_access.confirm_content_from_telegram", confirm), patch(
        "app.telegram.content_access.record_click_consent", record
    ), patch("app.telegram.content_access.deliver_text", AsyncMock()) as deliver:
        result = await process_core_telegram_update(
            whieda_tenant, _login_update(), "t-nostore", binding=whieda_bot_binding
        )
    assert result["ok"] is True
    assert "Вход подтверждён" in deliver.await_args.args[1]


# --- бот витрины автора Академии ----------------------------------------------


@pytest.fixture
def almira_binding(whieda_bot_binding):
    return replace(
        whieda_bot_binding, binding_id="almira-academy-bot", bot_username="ALMIRA_I_bot", showcase_id="almira"
    )


def _almira():
    from app.academy.showcases import Showcase

    return Showcase(
        tenant_id="whieda", showcase_id="almira", is_default=False, hosts=("academy.almira.by",),
        owner_actor_id="almira", brand_name="ALMIRA", academy_title="Онлайн-академия ALMIRA",
        links={"privacy_url": "https://almira.by/privacy-policy/"}, telegram_binding_id="almira-academy-bot",
    )


@pytest.mark.asyncio
async def test_showcase_bot_login_has_no_wwc_offer_line_and_no_site_login_consent(whieda_tenant, almira_binding):
    from app.telegram.content_access import _SUCCESS_MESSAGE
    from app.telegram.processor import process_core_telegram_update

    confirm = AsyncMock(return_value={"ok": True, "status": "approved", "challenge_id": "c-a", "return_to": "/academy/"})
    record = AsyncMock(return_value=True)
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=_almira())), patch(
        "app.telegram.content_access.confirm_content_from_telegram", confirm
    ), patch("app.telegram.content_access.record_click_consent", record), patch(
        "app.telegram.content_access.deliver_text", AsyncMock()
    ) as deliver, patch("app.telegram.showcase_bot._first_policy_notice", AsyncMock()) as showcase_policy, patch(
        "app.telegram.showcase_bot._note_bot_open", AsyncMock()
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _login_update(), "t-almira", binding=almira_binding
        )
    assert result["route"] == "content_access" and result["ok"] is True
    message = deliver.await_args.args[1]
    assert message == _SUCCESS_MESSAGE
    assert "оферт" not in message.lower() and OFFER not in message and POLICY not in message
    record.assert_not_awaited()
    # Своё согласие витрины пишется как раньше (политика витрины, не WWC).
    showcase_policy.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_message_and_record_depend_on_the_binding(whieda_bot_binding, almira_binding):
    from app.telegram import content_access as ca

    assert ca._success_message(whieda_bot_binding).endswith(site_login_consent_line("whieda"))
    assert ca._success_message(almira_binding) == ca._SUCCESS_MESSAGE
    with patch.object(ca, "record_click_consent", AsyncMock(return_value=True)) as record:
        msg = SimpleNamespace(user_id=1, chat_id=2)
        await ca._record_login_consent(almira_binding, msg, trace_id="t")
        record.assert_not_awaited()
        await ca._record_login_consent(whieda_bot_binding, msg, trace_id="t")
        record.assert_awaited_once_with("whieda", telegram_user_id=1, telegram_chat_id=2, source=SOURCE_SITE_LOGIN)


@pytest.mark.asyncio
async def test_site_order_is_unreachable_from_the_showcase_bot(whieda_tenant, almira_binding):
    """«Заказать сайт» в боте витрины не существует: callback site:create уходит в «проигнорирован»,
    ни вопроса про оферту WWC, ни заявки."""
    from app.telegram.processor import process_core_telegram_update

    update = {"callback_query": {"id": "cb-s", "data": "site:create", "from": {"id": 300},
                                 "message": {"message_id": 5, "chat": {"id": 300, "type": "private"}}}}
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=_almira())), patch(
        "app.telegram.showcase_bot.answer_callback_query", AsyncMock(return_value={"ok": True})
    ), patch("app.telegram.processor.try_handle_site_request_callback", AsyncMock()) as handler, patch(
        "app.telegram.site_requests.begin_site_request", AsyncMock()
    ) as begin, patch("app.telegram.site_requests._deliver", AsyncMock()) as deliver:
        result = await process_core_telegram_update(whieda_tenant, update, "t-site", binding=almira_binding)
    assert result["route"] == "showcase_ignored_callback"
    handler.assert_not_awaited()
    begin.assert_not_awaited()
    deliver.assert_not_awaited()
