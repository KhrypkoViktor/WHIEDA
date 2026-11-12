"""Трекер привычек: ``/api/v1/content-access/habits`` (ТЗ §7).

Сессия и 401 — как у ``app.academy.routes._viewer``; ответы с ``Cache-Control: private, no-store``
(и ошибки тоже). Временный допуск — ``PLATFORM_HABITS_ALLOW``: ID через запятую, ``*`` — всем
вошедшим, пусто — только владелец и супер-админы. Не допущенному ``GET /habits`` отвечает
200 ``{ok: true, enabled: false}``, остальные ручки — 403 ``habits_disabled``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.academy.service import preview_admin_ids
from app.content_access.routes import _current_session
from app.habits import service
from app.habits.rules import HabitsError
from app.settings import get_settings
from app.tenancy import get_request_tenant

router = APIRouter(tags=["habits"])
P = "/api/v1/content-access/habits"
NO_STORE = {"Cache-Control": "private, no-store"}


class HabitBody(BaseModel):
    """Поля любого типа: проверку и коды ошибок (``bad_*``) делают правила, а не 422 pydantic."""

    model_config = ConfigDict(extra="ignore")
    template: Any = None
    title: Any = None
    rhythm: Any = None
    goal: Any = None
    params: Any = None
    tz: Any = None
    done: Any = None
    due: Any = None
    start: Any = None
    achieved: Any = None


def enabled_for(telegram_user_id: int) -> bool:
    if int(telegram_user_id) in preview_admin_ids():
        return True
    allow = get_settings().parsed_habits_allow()
    return allow is None or int(telegram_user_id) in allow


def _error(status: int, payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, **payload}, headers=NO_STORE)


async def _serve(
    request: Request,
    response: Response,
    work: Callable[[str, int], Awaitable[dict[str, Any]]],
    *,
    soft_gate: bool = False,
) -> Any:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        session = await _current_session(request)
        uid = session.get("telegram_user_id")
        if uid is None:
            raise HTTPException(status_code=401, detail={"error": "telegram_session_required"})
        tenant_id = get_request_tenant(request).tenant_id
        if not enabled_for(int(uid)):
            if soft_gate:
                return {"ok": True, "enabled": False}
            raise HabitsError(403, "habits_disabled")
        return await work(tenant_id, int(uid))
    except HabitsError as exc:
        return _error(exc.status, {"error": exc.code})
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        return _error(exc.status_code, detail)


def _given(body: HabitBody | None, *names: str) -> dict[str, Any]:
    """Только поля, которые клиент прислал (для PATCH «не прислал — не трогаем»)."""
    if body is None:
        return {}
    return {name: getattr(body, name) for name in names if name in body.model_fields_set}


_ALL = ("template", "title", "rhythm", "goal", "params", "tz")


@router.get(P)
async def habits_list(request: Request, response: Response, tz: str | None = None) -> Any:
    return await _serve(request, response, lambda t, u: service.list_habits(t, u, tz), soft_gate=True)


@router.get(f"{P}/progress")
async def habits_progress(request: Request, response: Response, tz: str | None = None) -> Any:
    return await _serve(request, response, lambda t, u: service.progress(t, u, tz))


# Цель (ТЗ §7): маршруты стоят до ``/{habit_id}``, чтобы «goal» не принимался за id привычки.


@router.get(f"{P}/goal")
async def goal_get(request: Request, response: Response, tz: str | None = None) -> Any:
    return await _serve(request, response, _with_ok(lambda t, u: service.get_goal(t, u, tz)))


@router.put(f"{P}/goal")
async def goal_put(request: Request, response: Response, body: HabitBody | None = None) -> Any:
    given = _given(body, "title", "due", "start", "tz")
    return await _serve(request, response, _with_ok(lambda t, u: service.put_goal(t, u, given)))


@router.post(f"{P}/goal/answer")
async def goal_answer(request: Request, response: Response, body: HabitBody | None = None) -> Any:
    given = _given(body, "achieved", "tz")
    return await _serve(
        request, response, _with_ok(lambda t, u: service.answer_goal(t, u, given.get("achieved"), given.get("tz")))
    )


@router.delete(f"{P}/goal")
async def goal_delete(request: Request, response: Response) -> Any:
    return await _serve(request, response, service.delete_goal)


@router.post(P)
async def habit_add(request: Request, response: Response, body: HabitBody | None = None) -> Any:
    return await _serve(request, response, _with_ok(lambda t, u: service.add_habit(t, u, _given(body, *_ALL))))


@router.patch(f"{P}/{{habit_id}}")
async def habit_update(habit_id: str, request: Request, response: Response, body: HabitBody | None = None) -> Any:
    return await _serve(
        request, response, _with_ok(lambda t, u: service.update_habit(t, u, habit_id, _given(body, *_ALL)))
    )


@router.post(f"{P}/{{habit_id}}/pause")
async def habit_pause(habit_id: str, request: Request, response: Response, body: HabitBody | None = None) -> Any:
    tz = _given(body, "tz").get("tz")
    return await _serve(request, response, _with_ok(lambda t, u: service.pause_habit(t, u, habit_id, tz)))


@router.post(f"{P}/{{habit_id}}/resume")
async def habit_resume(habit_id: str, request: Request, response: Response, body: HabitBody | None = None) -> Any:
    tz = _given(body, "tz").get("tz")
    return await _serve(request, response, _with_ok(lambda t, u: service.resume_habit(t, u, habit_id, tz)))


@router.delete(f"{P}/{{habit_id}}")
async def habit_delete(habit_id: str, request: Request, response: Response) -> Any:
    return await _serve(request, response, lambda t, u: service.delete_habit(t, u, habit_id))


@router.put(f"{P}/{{habit_id}}/marks/{{day}}")
async def habit_mark(
    habit_id: str, day: str, request: Request, response: Response, body: HabitBody | None = None
) -> Any:
    given = _given(body, "done", "tz")
    return await _serve(
        request, response,
        _with_ok(lambda t, u: service.set_mark(t, u, habit_id, day, given.get("done"), given.get("tz"))),
    )


def _with_ok(work: Callable[[str, int], Awaitable[dict[str, Any]]]) -> Callable[[str, int], Awaitable[dict[str, Any]]]:
    """Привычка в ответе — плоский объект ТЗ §7 плюс ``ok: true``."""

    async def run(tenant_id: str, uid: int) -> dict[str, Any]:
        return {"ok": True, **await work(tenant_id, uid)}

    return run
