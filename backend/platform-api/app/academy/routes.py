"""Site API for the Academy (/api/v1/content-access/academy/...). Mounted under
the content-access prefix: the site nginx already proxies it to Core.

Identity is the content-access session (Telegram sign-in on the site): the
same person as in the bot, so progress is shared. Every response is private.

Student: courses, a course (modules, lessons with locks), a lesson, «Сделал»,
the lesson's test (V35), homework, media links, photo uploads for homework, and the
rhythm card «Сегодня» (``/rhythm/today``, 17.10.2026: marks, streak, lesson of the day).
V39 (19.10.2026): ``POST /app/installed`` — the installed app tells the site it runs (a mini-course
opens for free after a test and the app, ``app.academy.unlock``); the test answers 429
``quiz_cooldown`` with ``retry_at`` during the pause after a failed attempt.
Author (``/author/…``): courses, modules, lessons, order, students, homework
review, Markdown preview, uploads (video, files, images). 403 ``not_author`` —
the person has no author cabinet.
Files: ``/media/files/<key>?u=&e=&s=`` — the same signed link nginx checks; Core
streams the file (Range) while ``PLATFORM_ACADEMY_MEDIA_VIA_API=true``.
"""

from __future__ import annotations

import hmac
import mimetypes
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from app.academy.ad_leads import SheetUnavailable, flags_key, lead_flags
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
from app.academy.rhythm import today_payload, toggle_today
from app.academy.service import (
    AcademyError,
    academy_visible,
    course_outline,
    lesson_detail,
    list_courses,
    load_viewer,
    set_lesson_done,
    submit_quiz,
    viewer_profile,
)
from app.academy.showcases import public_payload, showcase_for_request
from app.academy.threads import (
    author_thread,
    get_profile,
    lesson_works,
    post_author_message,
    post_student_message,
    save_profile,
    set_seen,
)
from app.academy.unlock import mark_app_installed
from app.content_access.routes import _current_session
from app.tenancy import get_request_tenant

router = APIRouter(tags=["academy"])
P = "/api/v1/content-access/academy"


class LessonDoneBody(BaseModel):
    done: bool = True


class QuizAnswersBody(BaseModel):
    """Ответы на тест урока (V35): {"q1": "b", …} — id вопроса → id варианта."""

    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str] = {}


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
    # Витрина и работы группы (V32).
    works_public: bool | None = None
    review_days: int | None = None
    landing_url: str | None = None
    teaser: dict[str, Any] | None = None
    # Только владелец / preview-админ (V35): рубрика course | mini и бесплатный доступ
    # {"club": true, "courses": ["online-start-4w"]}; null — снять.
    kind: str | None = None
    free_for: dict[str, Any] | None = None


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
    # Тест урока (V35): объект — заменить, null — убрать. Блок ```quiz в body_md — то же.
    quiz: dict[str, Any] | None = None


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    comment: str | None = None


class MessageBody(BaseModel):
    """Сообщение нити домашки (V32): текст и файлы; about_media_id — замечание к фото ученика."""

    model_config = ConfigDict(extra="forbid")
    text: str = ""
    media_ids: list[str] = []
    about_media_id: str | None = None
    # После «Вернули»: True — исправления (работа снова на проверке), False — вопрос; None — по фото.
    resubmit: bool | None = None


class SeenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seen: bool = True


class ProfileBody(BaseModel):
    """Анкета ученика (V32): видна преподавателю и группе (имя и Instagram)."""

    model_config = ConfigDict(extra="forbid")
    name: str = ""
    city: str = ""
    experience: str = ""
    goal: str = ""
    instagram: str = ""


class RhythmMarkBody(BaseModel):
    """Галочка дня ритма: ``post`` | ``person`` | ``five``; другое значение — 400 ``bad_field``."""

    field: Any = None


class AppInstalledBody(BaseModel):
    """Отметка приложения (V39): ``platform`` — android | ios | desktop; другое значение или пусто — не указана."""

    platform: Any = None


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


@router.get(f"{P}/showcase")
async def showcase_site(request: Request, response: Response) -> dict[str, Any]:
    """Оформление витрины до входа (V29): бренд, токены, бот, ссылки. Без сессии."""
    response.headers["Cache-Control"] = "public, max-age=300"
    tenant = get_request_tenant(request)
    showcase = await showcase_for_request(request, tenant.tenant_id, allow_preview=True)
    return {"ok": True, "showcase": await public_payload(showcase)}


@router.get(f"{P}/showcase/lead-flags")
async def showcase_lead_flags(request: Request, key: str = "") -> JSONResponse:
    """«В боте / нет» по строкам таблицы заявок (Виктор, 18.10.2026): для скрипта таблицы, по ключу
    ``leads_sheet.flags_key`` витрины хоста. Только id заявок. Без сессии и без превью витрины."""
    headers = {"Cache-Control": "no-store"}
    tenant = get_request_tenant(request)
    showcase = await showcase_for_request(request, tenant.tenant_id)
    wanted = flags_key(showcase.support)
    if not wanted:
        return JSONResponse({"ok": False, "error": "flags_not_configured"}, status_code=404, headers=headers)
    # Ключ — заголовком X-Flags-Key (не попадает в журнал запросов), ?key= — запасной путь.
    given = request.headers.get("x-flags-key") or key
    if not hmac.compare_digest(given.encode(), wanted.encode()):
        return JSONResponse({"ok": False, "error": "bad_key"}, status_code=403, headers=headers)
    try:
        flags = await lead_flags(tenant.tenant_id, showcase)
    except SheetUnavailable:
        return JSONResponse({"ok": False, "error": "sheet_unavailable"}, status_code=503, headers=headers)
    return JSONResponse({"ok": True, **flags}, headers=headers)


@router.get(f"{P}/courses")
async def courses_site(request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    showcase = await showcase_for_request(request, tenant_id, allow_preview=True)
    return {
        "ok": True,
        "open": academy_visible(viewer),
        "showcase": showcase.showcase_id,
        "courses": await list_courses(tenant_id, viewer, showcase),
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


@router.post(f"{P}/courses/{{slug}}/lessons/{{lesson_slug}}/quiz")
async def lesson_quiz_site(
    slug: str, lesson_slug: str, body: QuizAnswersBody, request: Request, response: Response
) -> dict[str, Any]:
    """Попытка теста урока (V35): баллы и подсказки; сдан — урок отмечен."""
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(submit_quiz(tenant_id, slug, lesson_slug, viewer, answers=body.answers))


@router.post(f"{P}/courses/{{slug}}/lessons/{{lesson_slug}}/submission")
async def submission_site(
    slug: str, lesson_slug: str, body: SubmissionBody, request: Request, response: Response
) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(
        submit_homework(tenant_id, slug, lesson_slug, viewer, text=body.text, media_ids=body.media_ids)
    )


@router.post(f"{P}/courses/{{slug}}/lessons/{{lesson_slug}}/submission/messages")
async def submission_message_site(
    slug: str, lesson_slug: str, body: MessageBody, request: Request, response: Response
) -> dict[str, Any]:
    """Вопрос или исправления ученика в своей нити домашки (V32)."""
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(
        post_student_message(
            tenant_id, slug, lesson_slug, viewer, text=body.text, media_ids=body.media_ids, resubmit=body.resubmit
        )
    )


@router.get(f"{P}/courses/{{slug}}/lessons/{{lesson_slug}}/works")
async def lesson_works_site(slug: str, lesson_slug: str, request: Request, response: Response) -> dict[str, Any]:
    """Работы группы: все нити урока с именами и ответами преподавателя (works_public)."""
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(lesson_works(tenant_id, slug, lesson_slug, viewer))


@router.get(f"{P}/profile")
async def profile_site(request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(get_profile(tenant_id, viewer))


@router.put(f"{P}/profile")
async def profile_save_site(body: ProfileBody, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(save_profile(tenant_id, viewer, body.model_dump()))


@router.get(f"{P}/rhythm/today")
async def rhythm_today_site(request: Request, response: Response) -> dict[str, Any]:
    """Карточка «Сегодня» (ритм курса, экран /app/): галочки дня, серия, урок дня.
    Не участник курса — ``member: false`` и нули (200), чтобы экран просто скрыл карточку."""
    tenant_id, viewer = await _viewer(request, response)
    return await today_payload(tenant_id, viewer.telegram_user_id)


@router.post(f"{P}/rhythm/today")
async def rhythm_today_toggle_site(
    request: Request, response: Response, body: RhythmMarkBody | None = None
) -> dict[str, Any]:
    """Переключить галочку сегодняшнего дня; тот же объект, что в GET. 400 ``bad_field``,
    403 ``rhythm_not_member``."""
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(toggle_today(tenant_id, viewer.telegram_user_id, body.field if body else None))


@router.post(f"{P}/app/installed")
async def app_installed_site(
    request: Request, response: Response, body: AppInstalledBody | None = None
) -> dict[str, Any]:
    """Сайт открыт как установленное приложение WWC (V39): отметка человека, раз в сутки на устройство.
    ``{"ok": true, "installed": true, "first_seen_at": …}``; без входа — 401 ``telegram_session_required``.
    Курс, для которого это было последним условием, открывается и сообщает об этом в бот."""
    tenant_id, viewer = await _viewer(request, response)
    return await mark_app_installed(tenant_id, viewer.telegram_user_id, body.platform if body else None)


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


@router.get(f"{A}/submissions/{{submission_id}}")
async def author_submission(submission_id: str, request: Request, response: Response) -> dict[str, Any]:
    """Карточка работы с нитью и анкетой ученика; открытие помечает её просмотренной (V32)."""
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(author_thread(tenant_id, submission_id, viewer))


@router.post(f"{A}/submissions/{{submission_id}}/messages")
async def author_message(submission_id: str, body: MessageBody, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(
        post_author_message(
            tenant_id, submission_id, viewer,
            text=body.text, media_ids=body.media_ids, about_media_id=body.about_media_id,
        )
    )


@router.post(f"{A}/submissions/{{submission_id}}/seen")
async def author_submission_seen(submission_id: str, body: SeenBody, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await _guard(set_seen(tenant_id, submission_id, viewer, seen=body.seen))


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
