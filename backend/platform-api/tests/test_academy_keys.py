"""Academy «полка» (25.09.2026): access keys, author rights, bot texts.

No database and no real Telegram: the key store and the send functions are mocked.
The live-SQL proof is tests/test_academy_shelf_postgres.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.academy import keys
from app.academy.keys import (
    KEY_ALPHABET,
    KEY_LENGTH,
    AcademyKeyError,
    IssuedKeys,
    RedeemResult,
    course_start_link,
    generate_key_code,
    is_valid_key_code,
    parse_course_start_token,
)
from app.academy.service import AcademyError, AcademyViewer, _author_contact_from_row, course_lock_reason
from app.settings import get_settings
from app.telegram.academy import (
    handle_course_start_token,
    handle_keys_command,
    handle_my_courses,
    is_academy_payment,
    is_academy_text,
    notify_academy_payment,
    purchase_lock_text,
    try_handle_academy_text,
)
from app.telegram.bindings import binding_context_scope
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import TelegramMessage

STUDENT = AcademyViewer(telegram_user_id=3, is_preview_admin=False, partner_paid=False)


# --- codes -------------------------------------------------------------------


def test_key_alphabet_has_no_lookalikes():
    for ch in "o0l1":
        assert ch not in KEY_ALPHABET
    assert len(set(KEY_ALPHABET)) == len(KEY_ALPHABET) == 32


def test_generated_codes_use_the_alphabet_and_length():
    codes = [generate_key_code() for _ in range(2000)]
    assert all(len(code) == KEY_LENGTH == 12 for code in codes)
    assert all(set(code) <= set(KEY_ALPHABET) for code in codes)
    assert all(is_valid_key_code(code) for code in codes)
    assert len(set(codes)) == len(codes)  # 32^12 variants: no repeats in a batch


@pytest.mark.parametrize("code", ["", "abc", "abcdefghijk0", "abcdefghijkl", "ABCDEFGHIJKM", "abcdefghijkmn"])
def test_invalid_codes(code):
    assert not is_valid_key_code(code)


def test_course_start_token_parsing():
    assert parse_course_start_token("ref_abc") is None
    assert parse_course_start_token("site_x") is None
    assert parse_course_start_token("course_abcdefghjkmn") == "abcdefghjkmn"
    assert parse_course_start_token("course_ABCDEFGHJKMN") == "abcdefghjkmn"
    assert parse_course_start_token("course_bad") == ""
    assert course_start_link("@WHIEDA_Advisor_bot", "abcdefghjkmn") == (
        "https://t.me/WHIEDA_Advisor_bot?start=course_abcdefghjkmn"
    )


# --- access rule -------------------------------------------------------------


@pytest.fixture
def academy_opened(monkeypatch):
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_key_or_purchase_opens_pro_course_for_non_pro(academy_opened):
    assert course_lock_reason({"access_rule": "pro"}, STUDENT, has_access_row=False) == "pro_required"
    assert course_lock_reason({"access_rule": "pro"}, STUDENT, has_access_row=True) is None
    assert course_lock_reason({"access_rule": "purchase"}, STUDENT, has_access_row=True) is None


def test_author_contact_prefers_username_and_adds_site():
    contact = _author_contact_from_row({"telegram_username": "@igor_wwc", "ref_code": "igoref", "public_profile": {}})
    assert contact == {"telegram": "igor_wwc", "site_url": "https://igoref.wwc.best"}
    assert _author_contact_from_row({"telegram_username": None, "ref_code": None}) is None


# --- issue rights (mocked store) ---------------------------------------------


def _fake_store(course: dict | None, *, shelf: bool):
    """fetch_one that answers the three queries issue_keys makes."""

    async def fetch_one(conn, query, params=()):
        if "from academy_courses" in query:
            return course
        if "from academy_shelf" in query:
            return {"ok": 1} if shelf else None
        if "insert into academy_access_keys" in query:
            return {"code": params[2]}
        raise AssertionError(query)

    @asynccontextmanager
    async def connection(tenant_id):
        yield object()

    return fetch_one, connection


async def _issue(course, *, shelf, by="igor", as_admin=False, count=3):
    fetch_one, connection = _fake_store(course, shelf=shelf)
    with patch.object(keys, "fetch_one", fetch_one), patch.object(keys, "tenant_connection", connection):
        return await keys.issue_keys("whieda", "kurs", by, count, as_admin=as_admin)


PUBLISHED = {"course_id": "c1", "slug": "kurs", "title": "Курс", "status": "published", "author_actor_id": "igor"}


@pytest.mark.asyncio
async def test_author_with_paid_shelf_gets_keys():
    issued = await _issue(PUBLISHED, shelf=True)
    assert issued.course_slug == "kurs" and len(issued.codes) == 3
    assert all(is_valid_key_code(code) for code in issued.codes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("course", "shelf", "by", "as_admin", "count", "error"),
    [
        (PUBLISHED, True, "igor", False, 0, "bad_count"),
        (PUBLISHED, True, "igor", False, 101, "bad_count"),
        (None, True, "igor", False, 3, "course_not_found"),
        (PUBLISHED, True, "someone-else", False, 3, "not_author"),
        ({**PUBLISHED, "author_actor_id": None}, True, "igor", False, 3, "not_author"),
        (PUBLISHED, False, "igor", False, 3, "shelf_expired"),
        ({**PUBLISHED, "status": "draft"}, True, "igor", False, 3, "course_not_published"),
    ],
)
async def test_issue_refusals(course, shelf, by, as_admin, count, error):
    with pytest.raises(AcademyKeyError) as exc:
        await _issue(course, shelf=shelf, by=by, as_admin=as_admin, count=count)
    assert exc.value.code == error


@pytest.mark.asyncio
async def test_owner_issues_without_author_or_shelf():
    issued = await _issue({**PUBLISHED, "author_actor_id": None}, shelf=False, by="owner", as_admin=True, count=100)
    assert len(issued.codes) == 100


# --- bot texts ---------------------------------------------------------------


@pytest.mark.parametrize("text", ["ключи", "ключи zapusk-wwc 5", "Ключи moy-kurs", "мои курсы", "Мои курсы"])
def test_author_commands_are_academy_texts(text):
    assert is_academy_text(text)


@pytest.mark.parametrize("text", ["ключ", "ключи от квартиры", "мои курсы валют", "курсы"])
def test_other_texts_are_not_author_commands(text):
    assert not is_academy_text(text)


def test_purchase_lock_names_the_author_not_support():
    text = purchase_lock_text({"telegram": "igor_wwc", "site_url": "https://igoref.wwc.best"})
    assert "@igor_wwc" in text and "поддержк" not in text and "/start course_" in text
    assert "igoref.wwc.best" in purchase_lock_text({"telegram": None, "site_url": "https://igoref.wwc.best"})
    assert "поддержк" in purchase_lock_text(None)  # курс платформы без автора


def _msg(text: str, user_id: int = 700) -> TelegramMessage:
    return TelegramMessage(
        chat_id=user_id, user_id=user_id, message_id=1, text=text, chat_type="private", file_id=None, raw={}
    )


@pytest.fixture
def sent(whieda_bot_binding):
    send = AsyncMock(return_value={"ok": True})
    with binding_context_scope(whieda_bot_binding), patch("app.telegram.academy.send_telegram_text", send):
        yield send


@pytest.mark.asyncio
async def test_keys_command_lists_links(whieda_tenant, sent):
    issued = IssuedKeys("kurs", "Курс Игоря", ["abcdefghjkmn", "bcdefghjkmnp", "cdefghjkmnpq"])
    with patch("app.telegram.academy.issue_keys_for_telegram", AsyncMock(return_value=issued)) as issue, patch(
        "app.telegram.academy.preview_admin_ids", return_value=frozenset()
    ):
        result = await try_handle_academy_text(whieda_tenant, _msg("ключи kurs 3"), trace_id="k1")
    assert result["route"] == "academy_keys" and result["count"] == 3
    issue.assert_awaited_once_with(
        "whieda", "kurs", 700, 3, is_admin=False, issued_for_message="whieda-test-binding:700:1"
    )
    text = sent.await_args.kwargs["text"]
    assert "«Курс Игоря»" in text
    assert text.count("https://t.me/WHIEDA_Advisor_bot?start=course_") == 3


@pytest.mark.asyncio
async def test_more_than_twenty_keys_go_as_a_file(whieda_tenant, sent):
    codes = [generate_key_code() for _ in range(25)]
    issued = IssuedKeys("kurs", "Курс", codes)
    file_send = AsyncMock(return_value={"ok": True})
    with patch("app.telegram.academy.issue_keys_for_telegram", AsyncMock(return_value=issued)), patch(
        "app.telegram.academy.preview_admin_ids", return_value=frozenset()
    ), patch("app.telegram.academy._send_text_file", file_send):
        result = await handle_keys_command(whieda_tenant, _msg("ключи kurs 25"), "kurs", 25, trace_id="k2")
    assert result["status"] == "issued_file"
    sent.assert_not_awaited()
    chat_id, filename, body, caption = file_send.await_args.args
    assert chat_id == 700 and filename.startswith("keys-kurs-") and filename.endswith(".txt")
    assert body.count("?start=course_") == 25 and all(code in body for code in codes)


@pytest.mark.asyncio
async def test_keys_refusal_is_explained(whieda_tenant, sent):
    with patch(
        "app.telegram.academy.issue_keys_for_telegram", AsyncMock(side_effect=AcademyKeyError("shelf_expired"))
    ), patch("app.telegram.academy.preview_admin_ids", return_value=frozenset()):
        result = await handle_keys_command(whieda_tenant, _msg("ключи kurs 3"), "kurs", 3, trace_id="k3")
    assert result["status"] == "shelf_expired"
    assert "Полка Академии не оплачена" in sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_my_courses_shows_counts_without_names(whieda_tenant, sent):
    stats = [
        {"slug": "kurs", "title": "Курс Игоря", "status": "published", "access_rule": "purchase",
         "author_actor_id": "igor", "keys_total": 3, "keys_capacity": 3, "keys_used": 1, "students": 1},
    ]
    with patch("app.telegram.academy.author_actor_ids", AsyncMock(return_value=["igor"])), patch(
        "app.telegram.academy.author_course_stats", AsyncMock(return_value=stats)
    ), patch("app.telegram.academy.shelf_paid_until", AsyncMock(return_value=None)), patch(
        "app.telegram.academy.preview_admin_ids", return_value=frozenset()
    ):
        result = await handle_my_courses(whieda_tenant, _msg("мои курсы"), trace_id="m1")
    assert result["route"] == "academy_my_courses"
    text = sent.await_args.kwargs["text"]
    assert "погашено 1 из 3" in text and "учеников: 1" in text and "опубликован" in text
    assert "Полка Академии не оплачена" in text


@pytest.mark.asyncio
async def test_my_courses_for_non_author_falls_back(whieda_tenant, sent):
    with patch("app.telegram.academy.author_actor_ids", AsyncMock(return_value=[])), patch(
        "app.telegram.academy.preview_admin_ids", return_value=frozenset()
    ):
        assert await handle_my_courses(whieda_tenant, _msg("мои курсы"), trace_id="m2") is None
    sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_course_key_opens_course_with_lesson_button(whieda_tenant, sent):
    outline = {"course": {"slug": "kurs"}, "lessons": [{"slug": "vvedenie", "position": 1, "done": False}]}
    with patch("app.telegram.academy.redeem_key", AsyncMock(return_value=RedeemResult("opened", "kurs", "Курс Игоря"))), patch(
        "app.telegram.academy.load_viewer", AsyncMock(return_value=STUDENT)
    ), patch("app.telegram.academy.course_outline", AsyncMock(return_value=outline)), patch(
        "app.telegram.academy.with_site_login", AsyncMock(side_effect=lambda url, **_: url)
    ):
        result = await handle_course_start_token(whieda_tenant, _msg("/start course_abcdefghjkmn"), "course_abcdefghjkmn", trace_id="s1")
    assert result["status"] == "opened"
    kwargs = sent.await_args.kwargs
    assert "Курс «Курс Игоря» открыт" in kwargs["text"]
    button = kwargs["reply_markup"]["inline_keyboard"][0][0]
    assert button["url"].endswith("/academy/?course=kurs&lesson=vvedenie")


@pytest.mark.asyncio
async def test_start_course_key_twice_says_already_open(whieda_tenant, sent):
    with patch(
        "app.telegram.academy.redeem_key", AsyncMock(return_value=RedeemResult("already_open", "kurs", "Курс"))
    ), patch("app.telegram.academy.load_viewer", AsyncMock(return_value=STUDENT)), patch(
        "app.telegram.academy.course_outline", AsyncMock(side_effect=AcademyError(404, "course_not_found"))
    ):
        result = await handle_course_start_token(whieda_tenant, _msg("/start course_abcdefghjkmn"), "course_abcdefghjkmn", trace_id="s2")
    assert result["status"] == "already_open"
    assert "уже открыт" in sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_start_course_bad_or_used_key(whieda_tenant, sent):
    result = await handle_course_start_token(whieda_tenant, _msg("/start course_x"), "course_x", trace_id="s3")
    assert result["status"] == "key_not_found"
    with patch("app.telegram.academy.redeem_key", AsyncMock(side_effect=AcademyKeyError("key_exhausted"))):
        result = await handle_course_start_token(whieda_tenant, _msg("/start course_abcdefghjkmn"), "course_abcdefghjkmn", trace_id="s4")
    assert result["status"] == "key_exhausted"
    assert "уже использован" in sent.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_processor_routes_course_start_token(whieda_tenant, whieda_bot_binding):
    update = {"message": {"text": "/start course_abcdefghjkmn", "chat": {"id": 100, "type": "private"}, "from": {"id": 200}}}
    handler = AsyncMock(return_value={"ok": True, "route": "academy_key"})
    with patch("app.telegram.processor.handle_course_start_token", handler), patch(
        "app.telegram.processor.handle_start_token", AsyncMock()
    ) as identity:
        result = await process_core_telegram_update(whieda_tenant, update, "c1", binding=whieda_bot_binding)
    assert result["route"] == "academy_key"
    assert handler.await_args.args[2] == "course_abcdefghjkmn"
    identity.assert_not_called()


@pytest.mark.asyncio
async def test_keys_zero_is_refused_not_one(whieda_tenant, sent):
    issue = AsyncMock(side_effect=AcademyKeyError("bad_count"))
    with patch("app.telegram.academy.issue_keys_for_telegram", issue), patch(
        "app.telegram.academy.preview_admin_ids", return_value=frozenset()
    ):
        result = await try_handle_academy_text(whieda_tenant, _msg("ключи kurs 0"), trace_id="k0")
    assert result["status"] == "bad_count"
    assert issue.await_args.args[3] == 0


def test_academy_payment_lines_are_recognised():
    assert is_academy_payment({"product_code": "academy_shelf"})
    assert is_academy_payment({"product_code": "course_neuro"})
    assert not is_academy_payment({"product_code": "platform_subscription"})
    assert not is_academy_payment(None)


@pytest.mark.asyncio
async def test_shelf_payment_notice_names_the_shelf_term_not_the_site(sent):
    payment = {"tenant_id": "whieda", "product_code": "academy_shelf",
               "period_end": datetime(2026, 12, 25, 10, tzinfo=timezone.utc)}
    await notify_academy_payment(payment, chat_id=7001, title="Полка Академии на 3 месяца")
    text = sent.await_args.kwargs["text"]
    assert "Полка Академии оплачена до 25.12.2026" in text and "ключи" in text
    assert "Сайт:" not in text and "Доступ до" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(("course_slug", "expected"), [("neuro", "Курс открыт в Академии"), (None, "как только будет готов")])
async def test_course_payment_notice(sent, course_slug, expected):
    @asynccontextmanager
    async def connection(tenant_id):
        yield object()

    payment = {"tenant_id": "whieda", "product_code": "course_neuro", "period_end": datetime.now(timezone.utc)}
    with patch("app.telegram.academy.tenant_connection", connection), patch(
        "app.telegram.academy.course_slug_for_product", AsyncMock(return_value=course_slug)
    ):
        await notify_academy_payment(payment, chat_id=6001, title="Курс «Нейросети»")
    text = sent.await_args.kwargs["text"]
    assert expected in text and "Осталось дней" not in text


@pytest.mark.asyncio
async def test_file_send_failure_falls_back_to_messages(whieda_tenant, sent):
    """Таймаут sendDocument не роняет обработчик: ключи уже созданы, уходят сообщениями."""
    codes = [generate_key_code() for _ in range(25)]
    issued = IssuedKeys("kurs", "Курс", codes)
    with patch("app.telegram.academy.issue_keys_for_telegram", AsyncMock(return_value=issued)), patch(
        "app.telegram.academy.preview_admin_ids", return_value=frozenset()
    ), patch("httpx.AsyncClient.post", AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))):
        result = await handle_keys_command(whieda_tenant, _msg("ключи kurs 25"), "kurs", 25, trace_id="kf")
    assert result["ok"] and result["status"] == "issued_file"
    texts = [call.kwargs["text"] for call in sent.await_args_list]
    assert len(texts) == 2 and sum(text.count("?start=course_") for text in texts) == 25


@pytest.mark.asyncio
async def test_expired_author_shelf_sends_student_to_the_author(whieda_tenant, sent):
    error = AcademyKeyError("author_shelf_expired", {"author_contact": {"telegram": "igor_wwc", "site_url": None}})
    with patch("app.telegram.academy.redeem_key", AsyncMock(side_effect=error)):
        result = await handle_course_start_token(
            whieda_tenant, _msg("/start course_abcdefghjkmn"), "course_abcdefghjkmn", trace_id="se"
        )
    assert result["status"] == "author_shelf_expired"
    text = sent.await_args.kwargs["text"]
    assert "Автор курса не продлил размещение — напишите ему: @igor_wwc" in text and "не потрачен" in text


def test_bot_dates_are_moscow():
    from app.telegram.academy import _date

    assert _date(datetime(2026, 12, 24, 22, 30, tzinfo=timezone.utc)) == "25.12.2026"
