"""Academy: access rules, bot commands, cabinet button, site API gate."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.academy.service import AcademyViewer, course_lock_reason
from app.settings import get_settings
from app.telegram.academy import academy_button_rows, is_academy_text, lesson_url

from tests.test_content_access import HOST, content_app  # noqa: F401  (fixture)

OWNER = AcademyViewer(telegram_user_id=1, is_preview_admin=True, partner_paid=False)
PAID = AcademyViewer(telegram_user_id=2, is_preview_admin=False, partner_paid=True)
UNPAID = AcademyViewer(telegram_user_id=3, is_preview_admin=False, partner_paid=False)
PRO_COURSE = {"access_rule": "pro"}


@pytest.fixture
def academy_closed(monkeypatch):
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def academy_opened(monkeypatch):
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_preview_shows_academy_only_to_owner(academy_closed):
    assert course_lock_reason(PRO_COURSE, OWNER, has_access_row=False) is None
    assert course_lock_reason(PRO_COURSE, PAID, has_access_row=False) == "academy_not_open"
    assert course_lock_reason(PRO_COURSE, UNPAID, has_access_row=False) == "academy_not_open"


def test_open_academy_needs_paid_pro_for_pro_courses(academy_opened):
    assert course_lock_reason(PRO_COURSE, PAID, has_access_row=False) is None
    assert course_lock_reason(PRO_COURSE, UNPAID, has_access_row=False) == "pro_required"
    assert course_lock_reason({"access_rule": "free"}, UNPAID, has_access_row=False) is None
    assert course_lock_reason({"access_rule": "purchase"}, PAID, has_access_row=False) == "purchase_required"
    assert course_lock_reason({"access_rule": "purchase"}, PAID, has_access_row=True) is None


@pytest.mark.parametrize(
    "text",
    ["начать обучение", "Академия", "/academy", "мой план", "коуч день 3", "коуч старт", "обучение"],
)
def test_old_training_commands_open_academy(text):
    assert is_academy_text(text)


@pytest.mark.parametrize("text", ["коуч", "коуч возражения", "коуч ответ 2 понимаю", "цена спирулина", "начать"])
def test_objection_practice_and_other_text_stay(text):
    assert not is_academy_text(text)


def test_lesson_url_points_to_preview_host(academy_closed):
    assert lesson_url("zapusk-wwc", "vhod") == "https://wwc.best/academy/?course=zapusk-wwc&lesson=vhod"


@pytest.mark.asyncio
async def test_cabinet_button_only_when_visible(academy_closed):
    with patch("app.telegram.academy.load_viewer", AsyncMock(return_value=OWNER)):
        assert await academy_button_rows("whieda", 1) == [[{"text": "🎓 Академия", "callback_data": "acad:home"}]]
    with patch("app.telegram.academy.load_viewer", AsyncMock(return_value=PAID)):
        assert await academy_button_rows("whieda", 2) == []
    with patch("app.telegram.academy.load_viewer", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await academy_button_rows("whieda", 2) == []


@pytest.mark.asyncio
async def test_site_api_requires_telegram_session(content_app):  # noqa: F811
    transport = ASGITransport(app=content_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/content-access/academy/courses", headers=HOST)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_site_api_lists_courses_for_owner(content_app, academy_closed):  # noqa: F811
    courses = [{"slug": "zapusk-wwc", "title": "Запуск WWC", "locked": False, "lessons_total": 13, "lessons_done": 2}]
    session = {
        "session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30), "telegram_user_id": 1,
    }
    transport = ASGITransport(app=content_app)
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), patch(
        "app.content_access.routes.validate_content_session", AsyncMock(return_value=session)
    ), patch("app.academy.routes.load_viewer", AsyncMock(return_value=OWNER)), patch(
        "app.academy.routes.list_courses", AsyncMock(return_value=courses)
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/content-access/academy/courses", headers=HOST)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["courses"][0]["slug"] == "zapusk-wwc"
    assert response.json()["open"] is True


@pytest.mark.asyncio
async def test_site_login_link_goes_into_the_fragment():
    from app.telegram.site_login import with_site_login

    with patch("app.telegram.site_login.create_bot_login", AsyncMock(return_value="cid.nonce")) as create:
        url = await with_site_login(
            "https://wwc.best/academy/?course=zapusk-wwc&lesson=vhod", tenant_id="whieda", telegram_user_id=7
        )
    assert url == "https://wwc.best/academy/?course=zapusk-wwc&lesson=vhod#wwc-login=cid.nonce"
    assert create.await_args.kwargs["return_to"] == "/academy/?course=zapusk-wwc&lesson=vhod"
    assert create.await_args.kwargs["telegram_user_id"] == 7


@pytest.mark.asyncio
async def test_site_login_skips_foreign_hosts_and_survives_failures():
    from app.telegram.site_login import with_site_login

    assert await with_site_login("https://t.me/x", tenant_id="whieda", telegram_user_id=7) == "https://t.me/x"
    assert await with_site_login("https://wwc.best/", tenant_id="whieda", telegram_user_id=None) == "https://wwc.best/"
    with patch("app.telegram.site_login.create_bot_login", AsyncMock(side_effect=RuntimeError("db"))):
        assert await with_site_login("https://lebedeva.wwc.best/", tenant_id="whieda", telegram_user_id=7) == "https://lebedeva.wwc.best/"
