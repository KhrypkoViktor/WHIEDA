"""Site API for the Academy (/api/v1/content-access/academy/...). Mounted under
the content-access prefix: the site nginx already proxies it to Core.

Identity is the content-access session (Telegram sign-in on the site): the
same person as in the bot, so progress is shared. Every response is private.

Student: courses, a course (modules, lessons with locks), a lesson, «Сделал»,
homework, media links, photo uploads for homework.
Author (``/author/…``): courses, modules, lessons, order, students, homework
review, Markdown preview, uploads (video, files, images). 403 ``not_author`` —
the person has no author cabinet.
Files: ``/media/files/<key>?u=&e=&s=`` — the same signed link nginx checks; Core
streams the file (Range) while ``PLATFORM_ACADEMY_MEDIA_VIA_API=true``.
"""

from __future__ import annotations

import mimetypes
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from app.academy.author import (
    author_course,
    author_courses,
    course_students,
    create_course,
    create_lesson,
    create_module,
    delete_lesson,
    delete_module,
    get_lesson,
    preview_markdown,
    reorder_lessons,
    reorder_modules,
    require_author,
    review_course,
    update_course,
    update_lesson,
    update_module,
)
from app.academy.homework import list_submissions, review_submission, submit_homework
from app.academy.media import CHUNK_SIZE, MediaError, get_media_store, verify
from app.academy.media_service import (
    AUTHOR_KINDS,
    STUDENT_KINDS,
    complete_upload,
    init_upload,
    media_url,
    put_chunk,
    upload_status,
)
from app.academy.service import (
    AcademyError,
    academy_visible,
    course_outline,
    lesson_detail,
    list_courses,
    load_viewer,
    set_lesson_done,
    viewer_profile,
)
from app.content_access.routes import _current_session
from app.tenancy import get_request_tenant

router = APIRouter(tags=["academy"])
P = "/api/v1/content-access/academy"


class LessonDoneBody(BaseModel):
    done: bool = True


class SubmissionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = ""
    media_ids: list[str] = []


class UploadInitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    size: int
    mime: str
    kind: str


class CourseCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    slug: str | None = None
    subtitle: str | None = None


class CoursePatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    subtitle: str | None = None
    description_md: str | None = None
    cover_media_id: str | None = None
    access_rule: str | None = None
    price: float | None = None
    currency: str | None = None
    status: str | None = None


class ModuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    unlock: dict[str, Any] | None = None


class OrderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order: list[str]


class LessonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    short_title: str | None = None
    body_md: str | None = None
    video: dict[str, Any] | None = None
    files: list[str] | None = None
    kind: str | None = None
    live_at: str | None = None
    live_url: str | None = None
    unlock: dict[str, Any] | None = None
    assignment: dict[str, Any] | None = None
    status: str | None = None
    module_id: str | None = None


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    comment: str | None = None


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    markdown: str = ""


async def _viewer(request: Request, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    session = await _current_session(request)
    telegram_user_id = session.get("telegram_user_id")
    if telegram_user_id is None:
        raise HTTPException(status_code=401, detail={"error": "telegram_session_required"})
    tenant = get_request_tenant(request)
    return tenant.tenant_id, await load_viewer(tenant.tenant_id, int(telegram_user_id))


def _raise(exc: AcademyError | MediaError) -> None:
    # purchase_required несёт author_contact: доступ выдаёт автор курса (ключом).
    raise HTTPException(status_code=exc.status, detail={"error": exc.code, **exc.extra}) from exc


async def _guard(call):
    try:
        return await call
    except (AcademyError, MediaError) as exc:
        _raise(exc)


async def _chunk_body(request: Request) -> bytes:
    parts: list[bytes] = []
    size = 0
    async for part in request.stream():
        size += len(part)
        if size > CHUNK_SIZE:
            raise HTTPException(status_code=413, detail={"error": "chunk_too_large", "limit": CHUNK_SIZE})
        parts.append(part)
    return b"".join(parts)


# ---- student ---------------------------------------------------------------------------------


@router.get(f"{P}/courses")
async def courses_site(request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return {
        "ok": True,
        "open": academy_visible(viewer),
        "courses": await list_courses(tenant_id, viewer),
        "viewer": await viewer_profile(tenant_id, viewer.telegram_user_id),
    }


@router.get(f"{P}/courses/{{slug}}")
async def course_site(slug: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    outline = await _guard(course_outline(tenant_id, slug, viewer, allow_locked=True))
    return {"ok": True, **outline, "viewer": await viewer_profile(tenant_id, viewer.telegram_user_id)}


@router.get(f"{P}/courses/{{slug}}/lessons/{{lesson_slug}}")
async def lesson_site(slug: str, lesson_slug: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return {"ok": True, **await _guard(lesson_detail(tenant_id, slug, lesson_slug, viewer))}


@router.post(f"{P}/courses/{{slug}}/lessons/{{lesson_slug}}/done")
async def lesson_done_site(
    slug: str, lesson_slug: str, body: LessonDoneBody, request: Request, response: Response
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(set_lesson_done(tenant_id, slug, lesson_slug, viewer, done=body.done, source="site"))


@router.post(f"{P}/courses/{{slug}}/lessons/{{lesson_slug}}/submission")
async def submission_site(
    slug: str, lesson_slug: str, body: SubmissionBody, request: Request, response: Response
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(
        submit_homework(tenant_id, slug, lesson_slug, viewer, text=body.text, media_ids=body.media_ids)
    )


# ---- media: signed files (declared before /media/{id}) ----------------------------------------


@router.get(f"{P}/media/files/{{storage_key:path}}", include_in_schema=False)
async def media_file(storage_key: str, u: str = "", e: str = "", s: str = "") -> FileResponse:
    if not verify(storage_key, u, e, s):
        raise HTTPException(status_code=403, detail={"error": "signed_url_invalid"})
    try:
        path = get_media_store().path(storage_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found"}) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


@router.get(f"{P}/media/{{media_id}}/url")
async def media_url_site(media_id: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return {"ok": True, **await _guard(media_url(tenant_id, media_id, viewer))}


async def _init(body: UploadInitBody, request: Request, response: Response, kinds: tuple[str, ...], *, author: bool):
    tenant_id, viewer = await _viewer(request, response)
    if author:
        await _guard(require_author(tenant_id, viewer))
    return {
        "ok": True,
        **await _guard(
            init_upload(
                tenant_id, viewer, kind=body.kind, name=body.name, mime=body.mime, size=body.size, allowed_kinds=kinds,
                # Фото домашки — только у кого открыт хоть один курс (диск общий).
                require_course_access=not author,
            )
        ),
    }


async def _chunk(media_id: str, n: int, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    data = await _chunk_body(request)
    return {"ok": True, **await _guard(put_chunk(tenant_id, viewer, media_id, n, data))}


async def _complete(media_id: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return {"ok": True, **await _guard(complete_upload(tenant_id, viewer, media_id))}


async def _status(media_id: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return {"ok": True, **await _guard(upload_status(tenant_id, viewer, media_id))}


@router.post(f"{P}/media/init")
async def media_init_site(body: UploadInitBody, request: Request, response: Response) -> dict[str, Any]:
    """A student's photo for homework."""
    return await _init(body, request, response, STUDENT_KINDS, author=False)


@router.put(f"{P}/media/{{media_id}}/chunks/{{n}}")
async def media_chunk_site(media_id: str, n: int, request: Request, response: Response) -> dict[str, Any]:
    return await _chunk(media_id, n, request, response)


@router.post(f"{P}/media/{{media_id}}/complete")
async def media_complete_site(media_id: str, request: Request, response: Response) -> dict[str, Any]:
    return await _complete(media_id, request, response)


@router.get(f"{P}/media/{{media_id}}")
async def media_status_site(media_id: str, request: Request, response: Response) -> dict[str, Any]:
    return await _status(media_id, request, response)


# ---- author ------------------------------------------------------------------------------------

A = f"{P}/author"


@router.get(f"{A}/courses")
async def author_courses_site(request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return {"ok": True, **await _guard(author_courses(tenant_id, viewer))}


@router.post(f"{A}/courses")
async def author_create_course(body: CourseCreateBody, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(create_course(tenant_id, viewer, title=body.title, slug=body.slug, subtitle=body.subtitle))


@router.get(f"{A}/courses/{{slug}}")
async def author_course_site(slug: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(author_course(tenant_id, viewer, slug))


@router.patch(f"{A}/courses/{{slug}}")
async def author_update_course(slug: str, body: CoursePatchBody, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(update_course(tenant_id, viewer, slug, body.model_dump(exclude_unset=True)))


@router.get(f"{A}/courses/{{slug}}/students")
async def author_students(slug: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return {"ok": True, "students": await _guard(course_students(tenant_id, viewer, slug))}


@router.post(f"{A}/courses/{{slug}}/review")
async def owner_review_course(slug: str, body: ReviewBody, request: Request, response: Response) -> dict[str, Any]:
    """Премодерация на сайте (то же, что кнопки в боте): published | returned + причина."""
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(review_course(tenant_id, viewer, slug=slug, decision=body.status, note=body.comment))


@router.post(f"{A}/courses/{{slug}}/modules")
async def author_create_module(slug: str, body: ModuleBody, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(create_module(tenant_id, viewer, slug, body.model_dump(exclude_unset=True)))


@router.patch(f"{A}/courses/{{slug}}/modules/reorder")
async def author_reorder_modules(slug: str, body: OrderBody, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(reorder_modules(tenant_id, viewer, slug, body.order))


@router.patch(f"{A}/courses/{{slug}}/modules/{{module_id}}")
async def author_update_module(
    slug: str, module_id: str, body: ModuleBody, request: Request, response: Response
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(update_module(tenant_id, viewer, slug, module_id, body.model_dump(exclude_unset=True)))


@router.delete(f"{A}/courses/{{slug}}/modules/{{module_id}}")
async def author_delete_module(slug: str, module_id: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(delete_module(tenant_id, viewer, slug, module_id))


@router.post(f"{A}/courses/{{slug}}/modules/{{module_id}}/lessons")
async def author_create_lesson(
    slug: str, module_id: str, body: LessonBody, request: Request, response: Response
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(create_lesson(tenant_id, viewer, slug, module_id, body.model_dump(exclude_unset=True)))


@router.patch(f"{A}/courses/{{slug}}/modules/{{module_id}}/lessons/reorder")
async def author_reorder_lessons(
    slug: str, module_id: str, body: OrderBody, request: Request, response: Response
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(reorder_lessons(tenant_id, viewer, slug, module_id, body.order))


# Урок ищется по курсу и lesson_id; module_id в пути — для единообразия адресов
# (у уроков старого импорта без модуля подойдёт любой, например «-»).
@router.get(f"{A}/courses/{{slug}}/modules/{{module_id}}/lessons/{{lesson_id}}")
async def author_get_lesson(
    slug: str, module_id: str, lesson_id: str, request: Request, response: Response
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(get_lesson(tenant_id, viewer, slug, lesson_id))


@router.patch(f"{A}/courses/{{slug}}/modules/{{module_id}}/lessons/{{lesson_id}}")
async def author_update_lesson(
    slug: str, module_id: str, lesson_id: str, body: LessonBody, request: Request, response: Response
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(update_lesson(tenant_id, viewer, slug, lesson_id, body.model_dump(exclude_unset=True)))


@router.delete(f"{A}/courses/{{slug}}/modules/{{module_id}}/lessons/{{lesson_id}}")
async def author_delete_lesson(
    slug: str, module_id: str, lesson_id: str, request: Request, response: Response
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(delete_lesson(tenant_id, viewer, slug, lesson_id))


@router.get(f"{A}/submissions")
async def author_submissions(
    request: Request, response: Response, status: str = "submitted", course: str | None = None
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return {
        "ok": True,
        "submissions": await _guard(list_submissions(tenant_id, viewer, status=status, course_slug=course or None)),
    }


@router.post(f"{A}/submissions/{{submission_id}}/review")
async def author_review(submission_id: str, body: ReviewBody, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(review_submission(tenant_id, submission_id, viewer, status=body.status, comment=body.comment))


@router.post(f"{A}/preview")
async def author_preview(body: PreviewBody, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return {"ok": True, "html": await _guard(preview_markdown(tenant_id, viewer, body.markdown))}


@router.post(f"{A}/media/init")
async def author_media_init(body: UploadInitBody, request: Request, response: Response) -> dict[str, Any]:
    return await _init(body, request, response, AUTHOR_KINDS, author=True)


@router.put(f"{A}/media/{{media_id}}/chunks/{{n}}")
async def author_media_chunk(media_id: str, n: int, request: Request, response: Response) -> dict[str, Any]:
    return await _chunk(media_id, n, request, response)


@router.post(f"{A}/media/{{media_id}}/complete")
async def author_media_complete(media_id: str, request: Request, response: Response) -> dict[str, Any]:
    return await _complete(media_id, request, response)


@router.get(f"{A}/media/{{media_id}}")
async def author_media_status(media_id: str, request: Request, response: Response) -> dict[str, Any]:
    return await _status(media_id, request, response)
