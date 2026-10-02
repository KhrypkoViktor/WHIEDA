"""Academy v2 HTTP: student, author and media routes; signed files through the API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.academy.media import CHUNK_SIZE, MediaError, signed_url
from app.academy.service import AcademyError, AcademyViewer
from app.settings import get_settings

from tests.test_content_access import HOST, content_app  # noqa: F401  (fixture)

P = "/api/v1/content-access/academy"
STUDENT = AcademyViewer(telegram_user_id=9001, is_preview_admin=False, partner_paid=False)
SESSION = {
    "session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
    "expires_at": datetime.now(timezone.utc) + timedelta(days=30), "telegram_user_id": 9001,
}


@pytest.fixture
def signed_in():
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), patch(
        "app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)
    ), patch("app.academy.routes.load_viewer", AsyncMock(return_value=STUDENT)):
        yield


async def call(app, method, path, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers={**HOST, **kw.pop("headers", {})}, **kw)


# ---- student --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_course_page_shows_a_locked_course_outline(content_app, signed_in):  # noqa: F811
    outline = {"course": {"slug": "akvarel", "locked": True, "lock_reason": "purchase_required"}, "modules": [], "lessons": []}
    with patch("app.academy.routes.course_outline", AsyncMock(return_value=outline)) as service:
        response = await call(content_app, "GET", f"{P}/courses/akvarel")
    assert response.status_code == 200 and response.json()["course"]["locked"] is True
    assert service.await_args.kwargs == {"allow_locked": True}
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_locked_lesson_is_403_with_reason(content_app, signed_in):  # noqa: F811
    error = AcademyError(403, "lesson_locked", {"lock_reason": "date:2026-10-10T07:00:00+00:00", "opens_at": "x"})
    with patch("app.academy.routes.lesson_detail", AsyncMock(side_effect=error)):
        response = await call(content_app, "GET", f"{P}/courses/akvarel/lessons/d")
    assert response.status_code == 403
    assert response.json()["error"] == "lesson_locked"
    assert response.json()["lock_reason"] == "date:2026-10-10T07:00:00+00:00"


@pytest.mark.asyncio
async def test_submission_route(content_app, signed_in):  # noqa: F811
    result = {"ok": True, "submission": {"status": "submitted"}}
    with patch("app.academy.routes.submit_homework", AsyncMock(return_value=result)) as service:
        response = await call(content_app, "POST", f"{P}/courses/akvarel/lessons/c/submission",
                              json={"text": "готово", "media_ids": ["m1"]})
    assert response.status_code == 200 and response.json()["submission"]["status"] == "submitted"
    assert service.await_args.kwargs == {"text": "готово", "media_ids": ["m1"]}
    with patch("app.academy.routes.submit_homework", AsyncMock(side_effect=AcademyError(409, "already_accepted"))):
        refused = await call(content_app, "POST", f"{P}/courses/akvarel/lessons/c/submission", json={"text": "x"})
    assert refused.status_code == 409 and refused.json()["error"] == "already_accepted"


@pytest.mark.asyncio
async def test_media_url_route(content_app, signed_in):  # noqa: F811
    with patch("app.academy.routes.media_url", AsyncMock(return_value={"url": "/academy-media/x?u=1"})):
        ok = await call(content_app, "GET", f"{P}/media/0f8fad5b-d9cb-469f-a165-70867728950e/url")
    assert ok.status_code == 200 and ok.json()["url"] == "/academy-media/x?u=1"
    with patch("app.academy.routes.media_url", AsyncMock(side_effect=MediaError(403, "media_forbidden"))):
        no = await call(content_app, "GET", f"{P}/media/0f8fad5b-d9cb-469f-a165-70867728950e/url")
    assert no.status_code == 403 and no.json()["error"] == "media_forbidden"


@pytest.mark.asyncio
async def test_student_uploads_only_photos(content_app, signed_in):  # noqa: F811
    with patch("app.academy.routes.init_upload", AsyncMock(return_value={"media_id": "m"})) as service:
        response = await call(content_app, "POST", f"{P}/media/init",
                              json={"name": "фото.jpg", "size": 10, "mime": "image/jpeg", "kind": "image"})
    assert response.status_code == 200
    assert service.await_args.kwargs["allowed_kinds"] == ("image",)
    assert service.await_args.kwargs["require_course_access"] is True


@pytest.mark.asyncio
async def test_chunk_body_goes_to_the_service_and_big_chunks_are_refused(content_app, signed_in):  # noqa: F811
    with patch("app.academy.routes.put_chunk", AsyncMock(return_value={"chunk": 3})) as service:
        response = await call(content_app, "PUT", f"{P}/media/m1/chunks/3", content=b"abc")
        assert response.status_code == 200
        assert service.await_args.args[2:] == ("m1", 3, b"abc")
        too_big = await call(content_app, "PUT", f"{P}/media/m1/chunks/0", content=b"x" * (CHUNK_SIZE + 1))
    assert too_big.status_code == 413 and too_big.json()["error"] == "chunk_too_large"
    assert service.await_count == 1


@pytest.mark.asyncio
async def test_author_routes_need_a_session(content_app):  # noqa: F811
    response = await call(content_app, "GET", f"{P}/author/courses")
    assert response.status_code == 401


# ---- author ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_author_cabinet_is_403_for_non_authors(content_app, signed_in):  # noqa: F811
    with patch("app.academy.routes.author_courses", AsyncMock(side_effect=AcademyError(403, "not_author"))):
        response = await call(content_app, "GET", f"{P}/author/courses")
    assert response.status_code == 403 and response.json()["error"] == "not_author"


@pytest.mark.asyncio
async def test_author_course_patch_passes_only_given_fields_and_refuses_unknown(content_app, signed_in):  # noqa: F811
    with patch("app.academy.routes.update_course", AsyncMock(return_value={"ok": True})) as service:
        response = await call(content_app, "PATCH", f"{P}/author/courses/akvarel",
                              json={"price": 250, "currency": "BYN", "cover_media_id": None})
        assert response.status_code == 200
        assert service.await_args.args[3] == {"price": 250, "currency": "BYN", "cover_media_id": None}
        typo = await call(content_app, "PATCH", f"{P}/author/courses/akvarel", json={"titel": "x"})
    assert typo.status_code == 422


@pytest.mark.asyncio
async def test_reorder_is_not_mistaken_for_a_module_id(content_app, signed_in):  # noqa: F811
    with patch("app.academy.routes.reorder_modules", AsyncMock(return_value={"ok": True})) as modules, patch(
        "app.academy.routes.update_module", AsyncMock()
    ) as update, patch("app.academy.routes.reorder_lessons", AsyncMock(return_value={"ok": True})) as lessons:
        first = await call(content_app, "PATCH", f"{P}/author/courses/akvarel/modules/reorder", json={"order": ["a", "b"]})
        second = await call(content_app, "PATCH", f"{P}/author/courses/akvarel/modules/m1/lessons/reorder",
                            json={"order": ["l1"]})
    assert (first.status_code, second.status_code) == (200, 200)
    assert modules.await_args.args[3] == ["a", "b"] and lessons.await_args.args[3:] == ("m1", ["l1"])
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_author_lesson_routes(content_app, signed_in):  # noqa: F811
    with patch("app.academy.routes.create_lesson", AsyncMock(return_value={"ok": True})) as create, patch(
        "app.academy.routes.update_lesson", AsyncMock(return_value={"ok": True})
    ) as update, patch("app.academy.routes.delete_lesson", AsyncMock(return_value={"ok": True})) as delete:
        created = await call(content_app, "POST", f"{P}/author/courses/akvarel/modules/m1/lessons",
                             json={"title": "Урок", "assignment": {"prompt_md": "Фото", "required": True}})
        patched = await call(content_app, "PATCH", f"{P}/author/courses/akvarel/modules/m1/lessons/l1",
                             json={"unlock": None, "kind": "live"})
        deleted = await call(content_app, "DELETE", f"{P}/author/courses/akvarel/modules/m1/lessons/l1")
    assert (created.status_code, patched.status_code, deleted.status_code) == (200, 200, 200)
    assert create.await_args.args[3:] == ("m1", {"title": "Урок", "assignment": {"prompt_md": "Фото", "required": True}})
    assert update.await_args.args[3:] == ("l1", {"unlock": None, "kind": "live"})
    assert delete.await_args.args[3] == "l1"


@pytest.mark.asyncio
async def test_author_media_init_allows_video_and_files(content_app, signed_in):  # noqa: F811
    with patch("app.academy.routes.require_author", AsyncMock()), patch(
        "app.academy.routes.init_upload", AsyncMock(return_value={"media_id": "m"})
    ) as service:
        response = await call(content_app, "POST", f"{P}/author/media/init",
                              json={"name": "урок.mov", "size": 10, "mime": "video/quicktime", "kind": "video"})
    assert response.status_code == 200
    assert service.await_args.kwargs["allowed_kinds"] == ("image", "file", "video")
    with patch("app.academy.routes.require_author", AsyncMock(side_effect=AcademyError(403, "not_author"))):
        refused = await call(content_app, "POST", f"{P}/author/media/init",
                             json={"name": "x.mp4", "size": 1, "mime": "video/mp4", "kind": "video"})
    assert refused.status_code == 403


@pytest.mark.asyncio
async def test_review_and_inbox_routes(content_app, signed_in):  # noqa: F811
    with patch("app.academy.routes.review_submission", AsyncMock(return_value={"ok": True, "status": "returned"})) as review, \
            patch("app.academy.routes.list_submissions", AsyncMock(return_value=[{"submission_id": "s"}])) as inbox:
        reviewed = await call(content_app, "POST", f"{P}/author/submissions/s1/review",
                              json={"status": "returned", "comment": "Добавьте тени"})
        listed = await call(content_app, "GET", f"{P}/author/submissions?status=submitted&course=akvarel")
    assert reviewed.status_code == 200 and review.await_args.kwargs == {"status": "returned", "comment": "Добавьте тени"}
    assert listed.json() == {"ok": True, "submissions": [{"submission_id": "s"}]}
    assert inbox.await_args.kwargs == {"status": "submitted", "course_slug": "akvarel"}


# ---- signed files through the API (until nginx serves them) -------------------------------------


@pytest.fixture
def media_files(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_MEDIA_SIGNING_SECRET", "f" * 40)
    monkeypatch.setenv("PLATFORM_ACADEMY_MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("PLATFORM_ACADEMY_MEDIA_VIA_API", "true")
    get_settings.cache_clear()
    key = "whieda/academy/0f8fad5b-d9cb-469f-a165-70867728950e/720.mp4"
    target = tmp_path / key
    target.parent.mkdir(parents=True)
    target.write_bytes(b"0123456789")
    yield key
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_signed_file_streams_with_range(content_app, media_files):  # noqa: F811
    url = signed_url(media_files, 9001)
    assert url.startswith(f"{P}/media/files/")
    whole = await call(content_app, "GET", url)
    assert whole.status_code == 200 and whole.content == b"0123456789"
    assert whole.headers["content-type"] == "video/mp4"
    assert whole.headers["x-content-type-options"] == "nosniff"
    part = await call(content_app, "GET", url, headers={"range": "bytes=2-5"})
    assert part.status_code == 206 and part.content == b"2345"
    assert part.headers["content-range"] == "bytes 2-5/10"


@pytest.mark.asyncio
async def test_signed_file_refuses_tampering(content_app, media_files):  # noqa: F811
    url = signed_url(media_files, 9001)
    for bad in (
        url.replace("u=9001", "u=9002"),
        url.replace(".mp4?", ".mp3?"),
        url[:-2] + "xx",
        url.split("?")[0],
    ):
        response = await call(content_app, "GET", bad)
        assert response.status_code in (403, 404), bad
    expired = signed_url(media_files, 9001, now=1_000_000_000)
    assert (await call(content_app, "GET", expired)).status_code == 403


@pytest.mark.asyncio
async def test_owner_reviews_a_course_on_the_site_too(content_app, signed_in):  # noqa: F811
    with patch("app.academy.routes.review_course", AsyncMock(return_value={"ok": True, "status": "draft"})) as review:
        response = await call(content_app, "POST", f"{P}/author/courses/akvarel/review",
                              json={"status": "returned", "comment": "Уберите обещания"})
    assert response.status_code == 200 and response.json()["status"] == "draft"
    assert review.await_args.kwargs == {"slug": "akvarel", "decision": "returned", "note": "Уберите обещания"}
