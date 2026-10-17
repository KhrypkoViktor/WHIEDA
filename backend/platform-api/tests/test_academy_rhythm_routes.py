"""Ритм курса на сайте: GET/POST ``/academy/rhythm/today`` для экрана «Сегодня» приложения WWC (17.10.2026).

Контракт (решение лида): ``{"member", "course_slug", "day", "marks": {post, person, five}, "streak",
"broken", "lesson": {slug, title, minutes, url} | null}``; не участнику — 200 с нулями и ``lesson: null``;
POST ``{"field": ...}`` — тот же объект, 403 ``rhythm_not_member``, 400 ``bad_field``; ссылка урока —
путь сайта без входа через бот. Данные и серия — уже проверенный слой ``app.academy.rhythm``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.academy import rhythm
from app.academy.service import AcademyError, AcademyViewer

from tests.test_content_access import HOST, content_app  # noqa: F401  (fixture)

P = "/api/v1/content-access/academy"
URL = f"{P}/rhythm/today"
COURSE = "online-start-4w"
D = date(2026, 10, 18)
STUDENT = AcademyViewer(telegram_user_id=9001, is_preview_admin=False, partner_paid=False)
SESSION = {
    "session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
    "expires_at": datetime.now(timezone.utc) + timedelta(days=30), "telegram_user_id": 9001,
}
OUTLINE = {
    "course": {"slug": COURSE, "next_lesson": "profil"},
    "lessons": [
        {"slug": "start", "title": "Самое сложное — начать", "minutes": 5, "locked": False},
        {"slug": "profil", "title": "Профиль за вечер", "minutes": 12, "locked": False},
    ],
}
NO_MARKS = {"post": False, "person": False, "five": False}


@pytest.fixture
def signed_in(monkeypatch):
    for name in ("PLATFORM_RHYTHM_COURSE", "PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "PLATFORM_BILLING_OWNER_TELEGRAM_ID"):
        monkeypatch.delenv(name, raising=False)
    from app.settings import get_settings

    get_settings.cache_clear()
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), patch(
        "app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)
    ), patch("app.academy.routes.load_viewer", AsyncMock(return_value=STUDENT)), patch.object(
        rhythm, "local_today", lambda now=None: D
    ):
        yield
    get_settings.cache_clear()


async def call(app, method, path, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers=HOST, **kw)


@pytest.mark.asyncio
async def test_member_gets_the_card_with_the_site_lesson_link(content_app, signed_in):  # noqa: F811
    state = {"marks": {"post": True, "person": False, "five": True}, "streak": 3, "broken": False}
    with patch.object(rhythm, "is_member", AsyncMock(return_value=True)) as member, \
         patch.object(rhythm, "card_state", AsyncMock(return_value=state)) as card, \
         patch.object(rhythm, "course_outline", AsyncMock(return_value=OUTLINE)) as outline:
        response = await call(content_app, "GET", URL)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "ok": True,
        "member": True,
        "course_slug": COURSE,
        "day": "2026-10-18",
        "marks": {"post": True, "person": False, "five": True},
        "streak": 3,
        "broken": False,
        "lesson": {
            "slug": "profil", "title": "Профиль за вечер", "minutes": 12,
            "url": "/academy/?course=online-start-4w&lesson=profil",
        },
    }
    assert member.await_args.args == ("whieda", COURSE, 9001)
    assert card.await_args.args == ("whieda", 9001, D, D)
    assert outline.await_args.args[:2] == ("whieda", COURSE)


@pytest.mark.asyncio
async def test_a_broken_streak_and_no_open_lesson(content_app, signed_in):  # noqa: F811
    state = {"marks": NO_MARKS, "streak": 0, "broken": True}
    closed = AsyncMock(side_effect=AcademyError(403, "course_locked"))
    with patch.object(rhythm, "is_member", AsyncMock(return_value=True)), \
         patch.object(rhythm, "card_state", AsyncMock(return_value=state)), \
         patch.object(rhythm, "course_outline", closed):
        body = (await call(content_app, "GET", URL)).json()
    assert (body["member"], body["streak"], body["broken"], body["lesson"]) == (True, 0, True, None)
    assert body["marks"] == NO_MARKS


@pytest.mark.asyncio
async def test_not_a_member_is_200_with_zeros_not_403(content_app, signed_in):  # noqa: F811
    with patch.object(rhythm, "is_member", AsyncMock(return_value=False)), \
         patch.object(rhythm, "card_state", AsyncMock()) as card, \
         patch.object(rhythm, "course_outline", AsyncMock()) as outline:
        response = await call(content_app, "GET", URL)
    assert response.status_code == 200
    assert response.json() == {
        "ok": True, "member": False, "course_slug": COURSE, "day": "2026-10-18",
        "marks": NO_MARKS, "streak": 0, "broken": False, "lesson": None,
    }
    card.assert_not_awaited()
    outline.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_blank_course_setting_means_nobody_is_in_the_rhythm(content_app, signed_in, monkeypatch):  # noqa: F811
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_RHYTHM_COURSE", " ")
    get_settings.cache_clear()
    with patch.object(rhythm, "is_member", AsyncMock(return_value=True)) as member:
        got = await call(content_app, "GET", URL)
        post = await call(content_app, "POST", URL, json={"field": "post"})
    assert got.json()["member"] is False and got.json()["course_slug"] == ""
    assert post.status_code == 403 and post.json()["error"] == "rhythm_not_member"
    member.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_toggles_today_and_returns_the_same_object(content_app, signed_in):  # noqa: F811
    state = {"marks": {"post": False, "person": True, "five": False}, "streak": 1, "broken": False}
    with patch.object(rhythm, "is_member", AsyncMock(return_value=True)), \
         patch.object(rhythm, "toggle_mark", AsyncMock(return_value={"person": True})) as toggle, \
         patch.object(rhythm, "card_state", AsyncMock(return_value=state)), \
         patch.object(rhythm, "course_outline", AsyncMock(return_value=OUTLINE)):
        response = await call(content_app, "POST", URL, json={"field": "person"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert toggle.await_args.args == ("whieda", 9001, D, "person")  # день — по rhythm.local_today()
    body = response.json()
    assert set(body) == {"ok", "member", "course_slug", "day", "marks", "streak", "broken", "lesson"}
    assert body["marks"] == {"post": False, "person": True, "five": False} and body["day"] == "2026-10-18"
    assert body["lesson"]["url"] == "/academy/?course=online-start-4w&lesson=profil"


@pytest.mark.asyncio
async def test_post_by_a_non_member_is_403_and_writes_nothing(content_app, signed_in):  # noqa: F811
    with patch.object(rhythm, "is_member", AsyncMock(return_value=False)), \
         patch.object(rhythm, "toggle_mark", AsyncMock()) as toggle:
        response = await call(content_app, "POST", URL, json={"field": "post"})
    assert response.status_code == 403
    assert response.json()["error"] == "rhythm_not_member"
    toggle.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"field": "streak"}, {"field": ""}, {"field": 5}, {"field": None}, {"other": "post"}, {}])
async def test_post_with_a_bad_field_is_400(content_app, signed_in, body):  # noqa: F811
    with patch.object(rhythm, "is_member", AsyncMock(return_value=True)), \
         patch.object(rhythm, "toggle_mark", AsyncMock()) as toggle:
        response = await call(content_app, "POST", URL, json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "bad_field"
    toggle.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_without_a_body_is_400_bad_field(content_app, signed_in):  # noqa: F811
    with patch.object(rhythm, "toggle_mark", AsyncMock()) as toggle:
        response = await call(content_app, "POST", URL)
    assert response.status_code == 400 and response.json()["error"] == "bad_field"
    toggle.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_a_session_both_routes_ask_for_sign_in(content_app):  # noqa: F811
    with patch("app.content_access.routes.read_session_cookie", return_value=None):
        got = await call(content_app, "GET", URL)
        post = await call(content_app, "POST", URL, json={"field": "post"})
    assert got.status_code == 401 and post.status_code == 401


@pytest.mark.asyncio
async def test_lesson_of_day_takes_the_link_from_the_builder(monkeypatch):
    monkeypatch.delenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", raising=False)
    monkeypatch.delenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", raising=False)
    from app.settings import get_settings

    get_settings.cache_clear()
    with patch.object(rhythm, "course_outline", AsyncMock(return_value=OUTLINE)):
        async_builder = AsyncMock(return_value="https://academy.example/x")
        by_bot = await rhythm.lesson_of_day("whieda", COURSE, 9001, async_builder)
        by_site = await rhythm.lesson_of_day("whieda", COURSE, 9001, lambda slug: rhythm.site_lesson_path(COURSE, slug))
    assert async_builder.await_args.args == ("profil",)
    assert by_bot == {"slug": "profil", "title": "Профиль за вечер", "minutes": 12, "url": "https://academy.example/x"}
    assert by_site["url"] == "/academy/?course=online-start-4w&lesson=profil"
    # Курс пройден или следующий урок закрыт — урока дня нет, ссылка не строится.
    done = {"course": {"next_lesson": None}, "lessons": OUTLINE["lessons"]}
    shut = {"course": {"next_lesson": "profil"}, "lessons": [{**OUTLINE["lessons"][1], "locked": True}]}
    builder = AsyncMock()
    for outline in (done, shut):
        with patch.object(rhythm, "course_outline", AsyncMock(return_value=outline)):
            assert await rhythm.lesson_of_day("whieda", COURSE, 9001, builder) is None
    builder.assert_not_awaited()
    get_settings.cache_clear()


def test_site_lesson_path_escapes_the_slugs():
    assert rhythm.site_lesson_path("a b", "c&d") == "/academy/?course=a+b&lesson=c%26d"


@pytest.mark.asyncio
async def test_the_bot_keeps_its_own_lesson_link():
    from app.telegram import rhythm as bot_rhythm

    with patch.object(rhythm, "course_outline", AsyncMock(return_value=OUTLINE)), \
         patch.object(bot_rhythm, "course_lesson_url", AsyncMock(return_value="https://wwc.best/academy/?course=c&lesson=profil")) as url:
        lesson = await bot_rhythm.lesson_of_day("whieda", COURSE, 9001)
    assert url.await_args.args == ("whieda", COURSE, "profil")
    assert lesson["url"] == "https://wwc.best/academy/?course=c&lesson=profil" and lesson["slug"] == "profil"
