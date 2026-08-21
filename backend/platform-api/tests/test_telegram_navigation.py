"""Telegram navigation and catalog browse — tenant-scoped, no cart/newcomer."""

from __future__ import annotations

import math
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.advisor.sql.text import detect_service_intent
from app.telegram.bindings import BotBindingContext, binding_context_scope
from app.telegram.catalog_browse import (
    handle_callback_query,
    handle_catalog_products,
    handle_main_menu,
    handle_navigation_text,
    render_catalog_page,
)
from app.telegram.delivery import (
    answer_callback_query,
    deliver_structured_advisor_response,
    format_telegram_html,
    send_telegram_text,
)
from app.telegram.navigation import (
    LABEL_CALCULATOR,
    LABEL_PRODUCTS,
    MENU_LABELS,
    MENU_INTENT_BY_LABEL,
    build_product_action_callback,
    catalog_list_inline_keyboard,
    main_menu_reply_keyboard,
    parse_callback_data,
    product_action_question,
    product_actions_inline_keyboard,
    resolve_menu_text_intent,
)
from app.telegram.processor import process_core_telegram_update
from app.telegram.sequencer import reset_chat_sequencer_for_tests
from app.telegram.update_parser import parse_telegram_callback
from app.tenancy import TenantContext


TELEGRAM_DIR = Path(__file__).resolve().parents[1] / "app" / "telegram"


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


@pytest.fixture
def nsp_tenant() -> TenantContext:
    return TenantContext(
        tenant_id="nsp-maxim",
        status="active",
        display_name="NSP",
        entitlements={"structure_basic": True},
    )


@pytest.fixture
def whieda_bot_binding(tenant) -> BotBindingContext:
    return BotBindingContext(
        binding_id="whieda-test-binding",
        tenant=tenant,
        bot_token_ref="env:TEST_WHIEDA_BOT_TOKEN",
        webhook_secret_ref="env:TEST_WHIEDA_WEBHOOK_SECRET",
        bot_username="WHIEDA_Advisor_bot",
        status="active",
        processing_mode="core",
        bot_token="whieda-test-token",
        webhook_secret="whieda-test-secret",
    )


@pytest.fixture
def nsp_bot_binding(nsp_tenant) -> BotBindingContext:
    return BotBindingContext(
        binding_id="nsp-binding",
        tenant=nsp_tenant,
        bot_token_ref="env:NSP_BOT_TOKEN",
        webhook_secret_ref="env:NSP_WEBHOOK_SECRET",
        bot_username="NSP_Leader_bot",
        status="active",
        processing_mode="core",
        bot_token="nsp-token",
        webhook_secret="nsp-secret",
    )


def _fake_conn():
    @asynccontextmanager
    async def _inner(_tenant_id: str):
        yield object()

    return _inner


def _nav_message(text: str, *, chat_id: int = 5):
    return type(
        "Msg",
        (),
        {
            "chat_id": chat_id,
            "text": text,
            "user_id": 1,
            "chat_type": "private",
            "raw": {},
        },
    )()


def test_menu_has_six_stable_labels():
    assert len(MENU_LABELS) == 6
    assert LABEL_PRODUCTS in MENU_LABELS
    keyboard = main_menu_reply_keyboard()
    assert len(keyboard["keyboard"]) == 6
    labels = [row[0]["text"] for row in keyboard["keyboard"]]
    assert LABEL_CALCULATOR in labels


def test_nsp_menu_omits_calculator():
    nsp_keyboard = main_menu_reply_keyboard(include_calculator=False)
    nsp_labels = [row[0]["text"] for row in nsp_keyboard["keyboard"]]
    assert LABEL_CALCULATOR not in nsp_labels
    assert LABEL_PRODUCTS in nsp_labels
    assert len(nsp_labels) == 5


@pytest.mark.parametrize("label,intent", list(MENU_INTENT_BY_LABEL.items()))
def test_menu_label_resolves_to_intent(label: str, intent: str):
    assert resolve_menu_text_intent(label) == intent


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
    assert parse_callback_data("nav:products").kind == "nav_products"
    assert parse_callback_data("nav:menu").kind == "nav_menu"
    assert parse_callback_data("cat:p:2").page == 2


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
    assert "Каталог WHIEDA" in text
    assert markup is not None
    assert "inline_keyboard" in markup


@pytest.mark.asyncio
async def test_nsp_catalog_heading_is_not_whieda(nsp_tenant):
    sample = [{"sku": "NSP-1", "canonical_name": "NSP Product"}]

    with patch("app.telegram.catalog_browse.tenant_connection", _fake_conn()):
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


@pytest.mark.asyncio
async def test_nsp_empty_catalog_keyboard_hides_calculator(nsp_tenant):
    with patch("app.telegram.catalog_browse.tenant_connection", _fake_conn()):
        with patch(
            "app.telegram.catalog_browse.repo.count_catalog_products",
            AsyncMock(return_value=0),
        ):
            text, markup = await render_catalog_page(nsp_tenant, page=1)
    assert "пуст" in text.lower()
    labels = [row[0]["text"] for row in markup["keyboard"]]
    assert LABEL_CALCULATOR not in labels


@pytest.mark.asyncio
async def test_nsp_calculator_route_is_hidden(nsp_tenant):
    msg = _nav_message(LABEL_CALCULATOR)
    with patch("app.telegram.catalog_browse._deliver_navigation_text", AsyncMock()) as deliver:
        result = await handle_navigation_text(nsp_tenant, msg, "nsp-calc")
    assert result["route"] == "calculator_hidden"
    deliver.assert_awaited()


@pytest.mark.asyncio
async def test_whieda_calculator_uses_advisor_not_web_url(tenant):
    msg = _nav_message(LABEL_CALCULATOR)
    with patch(
        "app.telegram.catalog_browse._run_advisor_question",
        AsyncMock(return_value={"answer_mode": "structured_faq"}),
    ) as advisor:
        result = await handle_navigation_text(tenant, msg, "trace-calc")
    advisor.assert_awaited_once()
    assert result["route"] == "navigation_text"
    assert result["intent"] == "nav_calculator"
    assert advisor.await_args.args[2] == "калькулятор"


@pytest.mark.parametrize(
    "action,prefix",
    [
        ("card", "карточка"),
        ("price", "цена"),
        ("photo", "фото"),
        ("video", "видео"),
        ("cert", "сертификат"),
    ],
)
def test_product_action_question_maps(action: str, prefix: str):
    assert product_action_question(action, "Активатор клеток") == f"{prefix} Активатор клеток"


def test_compare_action_has_no_advisor_question():
    assert product_action_question("compare", "Активатор клеток") is None


@pytest.mark.asyncio
async def test_callback_sku_routes_to_product_actions(tenant):
    callback = parse_telegram_callback(
        {
            "callback_query": {
                "id": "cb-1",
                "data": "cat:s:LOCAL-ACT",
                "from": {"id": 1},
                "message": {"chat": {"id": 42, "type": "private"}},
            }
        }
    )
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
    msg = _nav_message(LABEL_PRODUCTS)
    with patch(
        "app.telegram.catalog_browse.handle_catalog_products",
        AsyncMock(return_value={"ok": True, "route": "catalog_products"}),
    ) as catalog:
        result = await handle_navigation_text(tenant, msg, "trace-nav")
    catalog.assert_awaited_once()
    assert result["route"] == "catalog_products"


@pytest.mark.parametrize(
    "phrase",
    [
        "какие есть товары",
        "какие есть товары?",
        "какой товар есть!",
        "покажи любой товар",
        "каталог",
        "список товаров",
    ],
)
def test_catalog_list_phrases_resolve_to_products(phrase: str):
    assert resolve_menu_text_intent(phrase) == "nav_products"


def test_free_text_routing_matrix():
    assert resolve_menu_text_intent("📦 Товары") == "nav_products"
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
    core = {
        "answer_text": "card text",
        "answer_mode": "structured_card",
        "media": {"photo_url": "https://x/y.jpg"},
    }

    with patch("app.telegram.catalog_browse._ack_callback", AsyncMock()):
        with patch("app.telegram.catalog_browse.tenant_connection", _fake_conn()):
            with patch(
                "app.telegram.catalog_browse.repo.resolve_catalog_product_by_sku",
                AsyncMock(return_value={"sku": "LOCAL-ACT", "canonical_name": "Активатор клеток"}),
            ):
                with patch("app.telegram.catalog_browse.handle_structured_query", AsyncMock(return_value=core)) as advisor:
                    with patch(
                        "app.telegram.catalog_browse.deliver_structured_advisor_response",
                        AsyncMock(),
                    ) as deliver:
                        with binding_context_scope(whieda_bot_binding):
                            result = await handle_callback_query(tenant, callback, "trace-card")
    advisor.assert_awaited_once()
    deliver.assert_awaited_once()
    assert result["route"] == "product_action"
    assert deliver.await_args.kwargs["bot_token"] == "whieda-test-token"


@pytest.mark.asyncio
async def test_photo_first_not_broken_in_delivery(monkeypatch):
    monkeypatch.setattr(
        "app.telegram.tenant_media.get_settings",
        lambda: type("S", (), {"platform_tenant_media_base_url": "https://media.test.example/media"})(),
    )
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo:
        with patch("app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})) as text:
            result = await deliver_structured_advisor_response(
                1,
                {
                    "answer_text": "body",
                    "product": {"sku": "LOCAL-ACT"},
                    "media": {"filename": "main.webp", "sku": "LOCAL-ACT"},
                },
                bot_token="token",
                tenant_id="whieda",
            )
    photo.assert_awaited_once()
    text.assert_awaited_once()
    assert result["photo_sent"] is True
    assert result["text_sent"] is True
    assert "reply_markup" not in photo.await_args.kwargs
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
        with patch(
            "app.telegram.processor.handle_callback_query",
            AsyncMock(return_value={"ok": True, "route": "main_menu"}),
        ) as cb:
            result = await process_core_telegram_update(
                tenant,
                update,
                "trace",
                binding=whieda_bot_binding,
            )
    cb.assert_awaited_once()
    assert result["route"] == "main_menu"


@pytest.mark.asyncio
async def test_whieda_main_menu_keeps_branding(tenant):
    with patch("app.telegram.catalog_browse._deliver_navigation_text", AsyncMock()) as deliver:
        result = await handle_main_menu(tenant, 1, trace_id="menu")
    assert result["route"] == "main_menu"
    text = deliver.await_args.args[1]
    assert "WHIEDA" in text
    assert "🧮 Калькулятор" in text


@pytest.mark.asyncio
async def test_nsp_main_menu_hides_whieda_brand_and_calculator(nsp_tenant):
    with patch("app.telegram.catalog_browse._deliver_navigation_text", AsyncMock()) as deliver:
        result = await handle_main_menu(nsp_tenant, 1, trace_id="nsp-menu")
    assert result["route"] == "main_menu"
    text = deliver.await_args.args[1]
    assert "WHIEDA" not in text
    assert "Калькулятор" not in text
    assert "wwc.best" not in text


def test_product_actions_keyboard_has_catalog_and_menu():
    markup = product_actions_inline_keyboard("LOCAL-ACT")
    callbacks = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
    assert "nav:products" in callbacks
    assert "nav:menu" in callbacks
    assert "act:card:LOCAL-ACT" in callbacks


@pytest.mark.asyncio
async def test_nsp_catalog_products_uses_nsp_token(nsp_tenant, nsp_bot_binding):
    with patch("app.telegram.catalog_browse.render_catalog_page", AsyncMock(return_value=("Каталог NSP", None))):
        with patch("app.telegram.catalog_browse.send_telegram_text", AsyncMock()) as send:
            with binding_context_scope(nsp_bot_binding):
                result = await handle_catalog_products(nsp_tenant, 9, trace_id="nsp-cat")
    assert result["route"] == "catalog_products"
    assert send.await_args.kwargs["bot_token"] == "nsp-token"
    markup = send.await_args.kwargs["reply_markup"]
    labels = [row[0]["text"] for row in markup["keyboard"]]
    assert LABEL_CALCULATOR not in labels


def test_catalog_page_count_math():
    total = 14
    page_size = 8
    total_pages = math.ceil(total / page_size)
    assert total_pages == 2


def test_navigation_surfaces_do_not_import_cart_or_web_calculator():
    for name in ("catalog_browse.py", "navigation.py", "processor.py"):
        text = (TELEGRAM_DIR / name).read_text(encoding="utf-8")
        assert "from app.cart" not in text
        assert "app.cart" not in text
        assert "calculator_web_url" not in text.lower()
        assert "wwc.best" not in text
        assert "newcomer" not in text.lower()
        assert "?cart=" not in text
