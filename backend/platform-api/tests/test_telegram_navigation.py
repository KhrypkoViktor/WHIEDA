"""Telegram navigation, catalog browse, and callback routing tests."""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.advisor.sql.text import detect_service_intent
from app.telegram.bindings import binding_context_scope
from app.telegram.catalog_browse import (
    handle_callback_query,
    handle_catalog_products,
    handle_navigation_text,
    handle_newcomer_panel,
    handle_open_calculator,
    render_catalog_page,
)
from app.telegram.delivery import (
    answer_callback_query,
    configure_telegram_command_menu,
    deliver_structured_advisor_response,
    format_telegram_html,
    send_telegram_text,
)
from app.telegram.navigation import (
    LABEL_CALCULATOR,
    LABEL_PRODUCTS,
    MENU_LABELS,
    NEWCOMER_PANEL_TEXT,
    CALCULATOR_WEB_URL,
    advisor_followup_inline_keyboard,
    build_newcomer_callback,
    build_product_action_callback,
    calculator_open_inline_keyboard,
    catalog_list_inline_keyboard,
    clarification_choice_inline_keyboard,
    fallback_direction_inline_keyboard,
    is_newcomer_panel_request,
    main_menu_reply_keyboard,
    newcomer_inline_keyboard,
    parse_callback_data,
    product_action_question,
    resolve_menu_text_intent,
    telegram_menu_commands,
)
from app.telegram.processor import process_core_telegram_update
from app.telegram.sequencer import reset_chat_sequencer_for_tests
from app.telegram.update_parser import is_start_command, parse_start_token, parse_telegram_callback
from app.tenancy import TenantContext


@pytest.fixture(autouse=True)
def _fresh_sequencer():
    reset_chat_sequencer_for_tests()
    yield
    reset_chat_sequencer_for_tests()


@pytest.fixture
def tenant() -> TenantContext:
    return TenantContext(
        tenant_id="whieda",
        status="active",
        display_name="WHIEDA",
        entitlements={"structure_basic": True, "partner_leads": True, "deep_coach": False},
    )


def test_calculator_web_url_opens_existing_calc_mode():
    assert CALCULATOR_WEB_URL == "https://wwc.best/price/?calc=1"
    markup = calculator_open_inline_keyboard()
    button = markup["inline_keyboard"][0][0]
    assert button["text"] == "Открыть калькулятор"
    assert button["url"] == CALCULATOR_WEB_URL
    assert "callback_data" not in button


def test_ambiguous_product_gets_choice_buttons():
    markup = clarification_choice_inline_keyboard(["product_ambiguity_activator"])
    assert markup is not None
    labels = [btn["text"] for row in markup["inline_keyboard"] for btn in row]
    callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert labels == ["Активатор клеток", "Активатор PRO"]
    assert callbacks == ["cat:s:M015-00", "cat:s:EU-N000031-25"]
    assert parse_callback_data("cat:s:M015-00").kind == "catalog_sku"


def test_unrouted_gap_gets_direction_buttons():
    markup = advisor_followup_inline_keyboard({"gap_kind": "unrouted_message"})
    assert markup is not None
    texts = [btn["text"] for row in markup["inline_keyboard"] for btn in row]
    assert "📦 Товары" in texts
    assert "🧭 Подбор" in texts
    assert "Открыть калькулятор" in texts
    url_btn = next(btn for row in markup["inline_keyboard"] for btn in row if "url" in btn)
    assert url_btn["url"] == CALCULATOR_WEB_URL
    fallback = fallback_direction_inline_keyboard()
    assert fallback["inline_keyboard"][0][0]["callback_data"] == "nav:products"


def test_newcomer_panel_text_matches_contract():
    assert NEWCOMER_PANEL_TEXT == (
        "Помогу быстро освоиться. Названия товаров знать не обязательно — выберите, что хотите сделать.\n\n"
        "📦 Посмотреть товары\n"
        "🧭 Подобрать под задачу\n"
        "🛒 Первая покупка\n"
        "🧮 Посчитать корзину\n"
        "🎥 Отзывы и материалы\n"
        "📚 Как пользоваться\n"
        "👤 Мой наставник"
    )


def test_newcomer_keyboard_hides_unfinished_actions():
    markup = newcomer_inline_keyboard()
    labels = [btn["text"] for row in markup["inline_keyboard"] for btn in row]
    assert "📦 Посмотреть товары" in labels
    assert "🧭 Подобрать под задачу" in labels
    assert "🛒 Первая покупка" in labels
    assert "🧮 Посчитать корзину" in labels
    assert "📚 Как пользоваться" in labels
    assert "👤 Мой наставник" in labels
    assert "🎥 Отзывы и материалы" not in labels
    callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert all(item.startswith("nc:") for item in callbacks)
    assert parse_callback_data(build_newcomer_callback("products")).kind == "newcomer_products"


def test_newcomer_panel_request_phrases():
    assert is_newcomer_panel_request("с чего начать") is True
    assert is_newcomer_panel_request("С чего начать?") is True
    assert is_newcomer_panel_request("/start") is True
    assert is_newcomer_panel_request("подбери товар") is False
    assert is_start_command("/start") is True
    assert is_start_command("/start elena") is True
    assert parse_start_token("/start") is None
    assert parse_start_token("/start elena") == "elena"


@pytest.mark.asyncio
async def test_newcomer_panel_removes_old_keyboard_before_inline_actions(
    tenant,
    whieda_bot_binding,
):
    with binding_context_scope(whieda_bot_binding):
        with patch("app.telegram.catalog_browse.send_telegram_text", AsyncMock()) as deliver:
            await handle_newcomer_panel(tenant, 42, trace_id="trace-menu")

    assert deliver.await_count == 2
    first, second = deliver.await_args_list
    assert first.kwargs["reply_markup"] == {"remove_keyboard": True}
    assert second.kwargs["reply_markup"]["inline_keyboard"]


def test_legacy_reply_keyboard_is_removed_and_commands_are_tenant_aware():
    assert len(MENU_LABELS) == 6
    assert LABEL_PRODUCTS in MENU_LABELS
    assert main_menu_reply_keyboard() == {"remove_keyboard": True}
    commands = telegram_menu_commands()
    assert {item["command"] for item in commands} == {
        "start",
        "products",
        "calculator",
        "business",
        "company",
        "match",
        "events",
        "cabinet",
        "invite",
        "support",
    }
    nsp_commands = telegram_menu_commands(include_calculator=False)
    assert "calculator" not in {item["command"] for item in nsp_commands}


def test_standard_menu_commands_resolve_without_entering_advisor():
    assert resolve_menu_text_intent("/products") == "nav_products"
    assert resolve_menu_text_intent("/calculator@WHIEDA_bot") == "nav_calculator"
    assert resolve_menu_text_intent("/business") == "nav_business"
    assert resolve_menu_text_intent("/company") == "nav_company"
    assert resolve_menu_text_intent("/match") == "nav_basket"
    assert resolve_menu_text_intent("/events") == "nav_events"
    assert resolve_menu_text_intent("/unknown") is None


def test_catalog_keyboard_pagination_edges():
    products = [{"sku": f"S{i}", "canonical_name": f"Product {i}"} for i in range(3)]
    first = catalog_list_inline_keyboard(page=1, total_pages=2, products=products)
    last = catalog_list_inline_keyboard(page=2, total_pages=2, products=products)
    first_texts = [btn["text"] for row in first["inline_keyboard"] for btn in row]
    last_texts = [btn["text"] for row in last["inline_keyboard"] for btn in row]
    assert "◀ Назад" not in first_texts
    assert "Далее ▶" in first_texts
    assert "Далее ▶" not in last_texts
    assert "◀ Назад" in last_texts


def test_invalid_callback_rejected():
    assert parse_callback_data("cat:s:';drop--") is None
    assert parse_callback_data("x" * 80) is None


@pytest.mark.asyncio
async def test_render_catalog_page_from_repository(tenant):
    sample = [{"sku": "LOCAL-ACT", "canonical_name": "Активатор клеток"}]

    @asynccontextmanager
    async def fake_conn(_tenant_id: str):
        yield object()

    with patch("app.telegram.catalog_browse.tenant_connection", fake_conn):
        with patch("app.telegram.catalog_browse.repo.count_catalog_products", AsyncMock(return_value=1)):
            with patch("app.telegram.catalog_browse.repo.list_catalog_products", AsyncMock(return_value=sample)):
                text, markup = await render_catalog_page(tenant, page=1)
    assert "Активатор клеток" in text
    assert markup is not None
    assert "inline_keyboard" in markup


@pytest.mark.asyncio
async def test_nsp_catalog_and_calculator_do_not_expose_whieda_brand_or_url(tenant):
    nsp_tenant = TenantContext(
        tenant_id="nsp-maxim",
        status="active",
        display_name="NSP",
        entitlements={"structure_basic": True},
    )
    sample = [{"sku": "NSP-1", "canonical_name": "NSP Product"}]

    @asynccontextmanager
    async def fake_conn(_tenant_id: str):
        yield object()

    with patch("app.telegram.catalog_browse.tenant_connection", fake_conn):
        with patch(
            "app.telegram.catalog_browse.repo.count_catalog_products",
            AsyncMock(return_value=1),
        ):
            with patch(
                "app.telegram.catalog_browse.repo.list_catalog_products",
                AsyncMock(return_value=sample),
            ):
                text, _markup = await render_catalog_page(nsp_tenant, page=1)

    assert "Каталог NSP" in text
    assert "WHIEDA" not in text

    with patch(
        "app.telegram.catalog_browse._deliver_navigation_text",
        AsyncMock(),
    ) as deliver:
        result = await handle_open_calculator(nsp_tenant, 5, trace_id="nsp-calc")

    assert result["route"] == "calculator_unavailable"
    assert "wwc.best" not in str(deliver.await_args)


@pytest.mark.asyncio
async def test_whieda_calculator_opens_cart_session_url(tenant, whieda_bot_binding):
    public = {"cart_session_id": "opaqueCartId123"}
    with binding_context_scope(whieda_bot_binding):
        with patch(
            "app.telegram.catalog_browse.create_session",
            AsyncMock(return_value=public),
        ) as create:
            with patch(
                "app.telegram.catalog_browse._deliver_navigation_text",
                AsyncMock(),
            ) as deliver:
                result = await handle_open_calculator(tenant, 42, trace_id="calc")
    create.assert_awaited_once_with("whieda", {})
    assert result["route"] == "open_calculator"
    assert result["cart_session_id"] == "opaqueCartId123"
    url = deliver.await_args.kwargs["inline_markup"]["inline_keyboard"][0][0]["url"]
    assert "cart=opaqueCartId123" in url
    assert "sku" not in url.lower()
    assert url.startswith("https://wwc.best/price/?")


@pytest.mark.asyncio
async def test_whieda_calculator_falls_back_without_cart_on_store_error(
    tenant, whieda_bot_binding
):
    with binding_context_scope(whieda_bot_binding):
        with patch(
            "app.telegram.catalog_browse.create_session",
            AsyncMock(side_effect=RuntimeError("store down")),
        ):
            with patch(
                "app.telegram.catalog_browse._deliver_navigation_text",
                AsyncMock(),
            ) as deliver:
                result = await handle_open_calculator(tenant, 7, trace_id="calc-fail")
    assert result["route"] == "open_calculator"
    assert result["cart_session_id"] is None
    url = deliver.await_args.kwargs["inline_markup"]["inline_keyboard"][0][0]["url"]
    assert url == CALCULATOR_WEB_URL
    assert "cart=" not in url


@pytest.mark.asyncio
async def test_product_action_question_maps_to_card():
    assert product_action_question("card", "Активатор клеток") == "карточка Активатор клеток"


@pytest.mark.asyncio
async def test_callback_sku_routes_to_product_actions(tenant):
    update = {
        "update_id": 9001,
        "callback_query": {
            "id": "cb-1",
            "data": "cat:s:LOCAL-ACT",
            "from": {"id": 1},
            "message": {"chat": {"id": 42, "type": "private"}},
        },
    }
    callback = parse_telegram_callback(update)
    assert callback is not None

    with patch("app.telegram.catalog_browse._ack_callback", AsyncMock()) as ack:
        with patch(
            "app.telegram.catalog_browse.repo.resolve_catalog_product_by_sku",
            AsyncMock(return_value={"sku": "LOCAL-ACT", "canonical_name": "Активатор клеток"}),
        ):
            with patch("app.telegram.catalog_browse.tenant_connection", _fake_conn()):
                with patch("app.telegram.catalog_browse._deliver_navigation_text", AsyncMock()) as deliver:
                    result = await handle_callback_query(tenant, callback, "trace-cb")
    ack.assert_awaited_once()
    deliver.assert_awaited()
    assert result["route"] == "catalog_sku"


@pytest.mark.asyncio
async def test_callback_invalid_falls_back_to_menu(tenant):
    callback = parse_telegram_callback(
        {
            "callback_query": {
                "id": "cb-2",
                "data": "bad:data",
                "from": {"id": 1},
                "message": {"chat": {"id": 7, "type": "private"}},
            }
        }
    )
    assert callback is not None
    with patch("app.telegram.catalog_browse._ack_callback", AsyncMock()) as ack:
        with patch("app.telegram.catalog_browse._deliver_navigation_text", AsyncMock()) as deliver:
            result = await handle_callback_query(tenant, callback, "trace-bad")
    ack.assert_awaited_once()
    deliver.assert_awaited()
    assert result["route"] == "navigation_fallback"


@pytest.mark.asyncio
async def test_navigation_text_products_opens_catalog(tenant):
    msg = type("Msg", (), {"chat_id": 5, "text": LABEL_PRODUCTS, "user_id": 1, "chat_type": "private", "raw": {}})()
    with patch("app.telegram.catalog_browse.handle_catalog_products", AsyncMock(return_value={"ok": True, "route": "catalog_products"})) as catalog:
        result = await handle_navigation_text(tenant, msg, "trace-nav")
    catalog.assert_awaited_once()
    assert result["route"] == "catalog_products"


def test_free_text_routing_matrix():
    assert resolve_menu_text_intent("📦 Товары") == "nav_products"
    assert resolve_menu_text_intent("какие есть товары") == "nav_products"
    assert resolve_menu_text_intent("какие есть товары?") == "nav_products"
    assert resolve_menu_text_intent("какой товар есть!") == "nav_products"
    assert resolve_menu_text_intent("покажи любой товар") == "nav_products"
    assert detect_service_intent("какие есть товары") is None
    assert detect_service_intent("хай") == "greeting"
    assert detect_service_intent("что можешь") == "capabilities"


@pytest.mark.asyncio
async def test_product_card_action_uses_existing_delivery(tenant, whieda_bot_binding):
    callback = parse_telegram_callback(
        {
            "callback_query": {
                "id": "cb-card",
                "data": build_product_action_callback("card", "LOCAL-ACT"),
                "from": {"id": 1},
                "message": {"chat": {"id": 9, "type": "private"}},
            }
        }
    )
    assert callback is not None
    core = {"answer_text": "card text", "answer_mode": "structured_card", "media": {"photo_url": "https://x/y.jpg"}}

    with patch("app.telegram.catalog_browse._ack_callback", AsyncMock()):
        with patch("app.telegram.catalog_browse.tenant_connection", _fake_conn()):
            with patch(
                "app.telegram.catalog_browse.repo.resolve_catalog_product_by_sku",
                AsyncMock(return_value={"sku": "LOCAL-ACT", "canonical_name": "Активатор клеток"}),
            ):
                with patch("app.telegram.catalog_browse.handle_structured_query", AsyncMock(return_value=core)) as advisor:
                    with patch("app.telegram.catalog_browse.deliver_structured_advisor_response", AsyncMock()) as deliver:
                        with binding_context_scope(whieda_bot_binding):
                            result = await handle_callback_query(tenant, callback, "trace-card")
    advisor.assert_awaited_once()
    deliver.assert_awaited_once()
    assert result["route"] == "product_action"


@pytest.mark.asyncio
async def test_photo_first_not_broken_in_delivery():
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo:
        with patch("app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})) as text:
            result = await deliver_structured_advisor_response(
                1,
                {"answer_text": "body", "media": {"photo_url": "https://img/x.jpg"}},
                bot_token="token",
            )
    photo.assert_awaited_once()
    text.assert_awaited_once()
    assert result["photo_sent"] is True
    assert result["text_sent"] is True
    photo_kwargs = photo.await_args.kwargs
    assert "reply_markup" not in photo_kwargs
    assert "caption" not in (photo.await_args.kwargs or {})


@pytest.mark.asyncio
async def test_send_text_accepts_reply_markup():
    response = MagicMock()
    response.status_code = 200
    response.text = '{"ok": true, "result": {"message_id": 1}}'
    response.json.return_value = {"ok": True, "result": {"message_id": 1}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None):
            self.last_json = json
            return response

    fake = FakeClient()
    with patch("app.telegram.delivery.httpx.AsyncClient", return_value=fake):
        markup = main_menu_reply_keyboard()
        await send_telegram_text(chat_id="1", text="hi", bot_token="tok", reply_markup=markup)
    assert fake.last_json["reply_markup"] == markup


@pytest.mark.asyncio
async def test_configure_compact_telegram_command_menu():
    response = MagicMock()
    response.status_code = 200
    response.text = '{"ok": true, "result": true}'
    response.json.return_value = {"ok": True, "result": True}

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None):
            self.calls.append((url, json))
            return response

    fake = FakeClient()
    commands = telegram_menu_commands()
    with patch("app.telegram.delivery.httpx.AsyncClient", return_value=fake):
        result = await configure_telegram_command_menu(
            bot_token="secret-token",
            commands=commands,
        )

    assert result == {"ok": True, "command_count": len(commands)}
    assert [url.rsplit("/", 1)[-1] for url, _ in fake.calls] == [
        "setMyCommands",
        "setChatMenuButton",
    ]
    assert fake.calls[0][1] == {"commands": commands}
    assert fake.calls[1][1] == {"menu_button": {"type": "commands"}}


def test_telegram_html_keeps_approved_emphasis_and_escapes_other_tags():
    assert format_telegram_html("<b>Важное</b> и <script>x</script>") == (
        "<b>Важное</b> и &lt;script&gt;x&lt;/script&gt;"
    )


@pytest.mark.asyncio
async def test_send_text_uses_html_parse_mode():
    response = MagicMock()
    response.status_code = 200
    response.text = '{"ok": true, "result": {"message_id": 1}}'
    response.json.return_value = {"ok": True, "result": {"message_id": 1}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None):
            self.last_json = json
            return response

    fake = FakeClient()
    with patch("app.telegram.delivery.httpx.AsyncClient", return_value=fake):
        await send_telegram_text(chat_id="1", text="<b>Сравнение</b>", bot_token="tok")
    assert fake.last_json["parse_mode"] == "HTML"
    assert fake.last_json["text"] == "<b>Сравнение</b>"
    assert fake.last_json["reply_markup"] == {"remove_keyboard": True}


@pytest.mark.asyncio
async def test_answer_callback_query_ack():
    response = MagicMock()
    response.status_code = 200
    response.text = '{"ok": true}'
    response.json.return_value = {"ok": True}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json=None):
            return response

    with patch("app.telegram.delivery.httpx.AsyncClient", return_value=FakeClient()):
        result = await answer_callback_query(callback_query_id="abc", bot_token="tok")
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_process_core_routes_callback_before_message(tenant, whieda_bot_binding):
    update = {
        "update_id": 77,
        "callback_query": {
            "id": "cb-core",
            "data": "nav:menu",
            "from": {"id": 1},
            "message": {"chat": {"id": 100, "type": "private"}},
        },
    }
    with patch("app.telegram.processor.try_handle_admin_login", AsyncMock(return_value=None)):
        with patch("app.telegram.processor.handle_callback_query", AsyncMock(return_value={"ok": True, "route": "main_menu"})) as cb:
            result = await process_core_telegram_update(
                tenant,
                update,
                "trace",
                binding=whieda_bot_binding,
            )
    cb.assert_awaited_once()
    assert result["route"] == "main_menu"


@pytest.mark.asyncio
async def test_bare_start_opens_newcomer_panel(tenant, whieda_bot_binding):
    update = {
        "update_id": 12,
        "message": {
            "message_id": 1,
            "text": "/start",
            "chat": {"id": 55, "type": "private"},
            "from": {"id": 9},
        },
    }
    with patch("app.telegram.processor.try_handle_admin_login", AsyncMock(return_value=None)):
        with patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)):
            with patch(
                "app.telegram.processor.handle_newcomer_panel",
                AsyncMock(return_value={"ok": True, "route": "newcomer_panel"}),
            ) as panel:
                result = await process_core_telegram_update(
                    tenant,
                    update,
                    "trace-start",
                    binding=whieda_bot_binding,
                )
    panel.assert_awaited_once()
    assert result["route"] == "newcomer_panel"


@pytest.mark.asyncio
async def test_s_chego_nachat_opens_newcomer_panel(tenant, whieda_bot_binding):
    update = {
        "update_id": 13,
        "message": {
            "message_id": 2,
            "text": "с чего начать",
            "chat": {"id": 55, "type": "private"},
            "from": {"id": 9},
        },
    }
    with patch("app.telegram.processor.try_handle_admin_login", AsyncMock(return_value=None)):
        with patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)):
            with patch(
                "app.telegram.processor.handle_newcomer_panel",
                AsyncMock(return_value={"ok": True, "route": "newcomer_panel"}),
            ) as panel:
                result = await process_core_telegram_update(
                    tenant,
                    update,
                    "trace-begin",
                    binding=whieda_bot_binding,
                )
    panel.assert_awaited_once()
    assert result["route"] == "newcomer_panel"


@pytest.mark.asyncio
async def test_telegram_greeting_opens_newcomer_panel(tenant, whieda_bot_binding):
    update = {
        "update_id": 14,
        "message": {
            "message_id": 3,
            "text": "хай",
            "chat": {"id": 55, "type": "private"},
            "from": {"id": 9},
        },
    }
    with patch("app.telegram.processor.try_handle_admin_login", AsyncMock(return_value=None)):
        with patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)):
            with patch(
                "app.telegram.processor.handle_newcomer_panel",
                AsyncMock(return_value={"ok": True, "route": "newcomer_panel"}),
            ) as panel:
                with patch("app.telegram.processor.handle_advisor_query", AsyncMock()) as advisor:
                    result = await process_core_telegram_update(
                        tenant,
                        update,
                        "trace-hai",
                        binding=whieda_bot_binding,
                    )
    panel.assert_awaited_once()
    advisor.assert_not_awaited()
    assert result["route"] == "newcomer_panel"


@pytest.mark.asyncio
async def test_menu_calculator_opens_web_calculator(tenant):
    msg = type("Msg", (), {"chat_id": 5, "text": "🧮 Калькулятор", "user_id": 1, "chat_type": "private", "raw": {}})()
    with patch(
        "app.telegram.catalog_browse.handle_open_calculator",
        AsyncMock(return_value={"ok": True, "route": "open_calculator"}),
    ) as calc:
        result = await handle_navigation_text(tenant, msg, "trace-calc")
    calc.assert_awaited_once()
    assert result["route"] == "open_calculator"


@pytest.mark.asyncio
async def test_newcomer_calc_callback_opens_web_calculator(tenant):
    callback = parse_telegram_callback(
        {
            "callback_query": {
                "id": "cb-calc",
                "data": build_newcomer_callback("calc"),
                "from": {"id": 1},
                "message": {"chat": {"id": 3, "type": "private"}},
            }
        }
    )
    assert callback is not None
    with patch("app.telegram.catalog_browse._ack_callback", AsyncMock()):
        with patch(
            "app.telegram.catalog_browse.handle_open_calculator",
            AsyncMock(return_value={"ok": True, "route": "open_calculator"}),
        ) as calc:
            result = await handle_callback_query(tenant, callback, "trace-nc-calc")
    calc.assert_awaited_once()
    assert result["route"] == "open_calculator"


@pytest.mark.asyncio
async def test_newcomer_products_callback_opens_catalog(tenant):
    callback = parse_telegram_callback(
        {
            "callback_query": {
                "id": "cb-nc",
                "data": build_newcomer_callback("products"),
                "from": {"id": 1},
                "message": {"chat": {"id": 3, "type": "private"}},
            }
        }
    )
    assert callback is not None
    with patch("app.telegram.catalog_browse._ack_callback", AsyncMock()):
        with patch(
            "app.telegram.catalog_browse.handle_catalog_products",
            AsyncMock(return_value={"ok": True, "route": "catalog_products"}),
        ) as catalog:
            result = await handle_callback_query(tenant, callback, "trace-nc")
    catalog.assert_awaited_once()
    assert result["route"] == "catalog_products"


def _fake_conn():
    @asynccontextmanager
    async def _inner(_tenant_id: str):
        yield object()

    return _inner


@pytest.mark.asyncio
async def test_catalog_page_count_math():
    total = 14
    page_size = 8
    total_pages = math.ceil(total / page_size)
    assert total_pages == 2
