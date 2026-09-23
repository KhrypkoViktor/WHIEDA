"""Site API for the Academy (/api/v1/content-access/academy/...). Mounted under
the content-access prefix: the site nginx already proxies it to Core.

Identity is the content-access session (Telegram sign-in on the site): the
same person as in the bot, so progress is shared. Every response is private.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.academy.service import (
    AcademyError,
    academy_visible,
    course_outline,
    lesson_detail,
    list_courses,
    load_viewer,
    set_lesson_done,
)
from app.content_access.routes import _current_session
from app.tenancy import get_request_tenant

router = APIRouter(tags=["academy"])


class LessonDoneBody(BaseModel):
    done: bool = True


async def _viewer(request: Request, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    session = await _current_session(request)
    telegram_user_id = session.get("telegram_user_id")
    if telegram_user_id is None:
        raise HTTPException(status_code=401, detail={"error": "telegram_session_required"})
    tenant = get_request_tenant(request)
    return tenant.tenant_id, await load_viewer(tenant.tenant_id, int(telegram_user_id))


def _raise(exc: AcademyError) -> None:
    raise HTTPException(status_code=exc.status, detail={"error": exc.code}) from exc


@router.get("/api/v1/content-access/academy/courses")
async def courses_site(request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return {"ok": True, "open": academy_visible(viewer), "courses": await list_courses(tenant_id, viewer)}


@router.get("/api/v1/content-access/academy/courses/{slug}")
async def course_site(slug: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    try:
        return {"ok": True, **await course_outline(tenant_id, slug, viewer)}
    except AcademyError as exc:
        _raise(exc)


@router.get("/api/v1/content-access/academy/courses/{slug}/lessons/{lesson_slug}")
async def lesson_site(slug: str, lesson_slug: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    try:
        return {"ok": True, **await lesson_detail(tenant_id, slug, lesson_slug, viewer)}
    except AcademyError as exc:
        _raise(exc)


@router.post("/api/v1/content-access/academy/courses/{slug}/lessons/{lesson_slug}/done")
async def lesson_done_site(
    slug: str, lesson_slug: str, body: LessonDoneBody, request: Request, response: Response
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    try:
        return await set_lesson_done(tenant_id, slug, lesson_slug, viewer, done=body.done, source="site")
    except AcademyError as exc:
        _raise(exc)
