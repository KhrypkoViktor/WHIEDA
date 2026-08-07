"""SQL coach: first-week route and objection practice (Structure Basic, no Dify)."""

from __future__ import annotations

import re
from typing import Any

FIRST_WEEK_DAYS: list[tuple[str, str, str]] = [
    (
        "День 1. Твоя опора",
        "Выбери один товар, который знаешь лучше всего. Прочитай его карточку, цену и ограничения. "
        "Сформулируй одну честную личную причину, почему он тебе интересен.",
        "Напиши: коуч день 2",
    ),
    (
        "День 2. Короткое знакомство",
        "Составь список из трёх людей, с которыми можно спокойно поговорить. Не продавай: спроси, "
        "какая тема им сейчас ближе — товар, самочувствие в быту или дополнительный доход.",
        "Напиши: коуч день 3",
    ),
    (
        "День 3. Один понятный материал",
        "Отправь одному человеку только один подходящий материал: карточку, фото или видео. "
        "Не присылай длинный каталог и не обещай результат.",
        "Напиши: коуч день 4",
    ),
    (
        "День 4. Учимся слушать",
        "Возьми один реальный вопрос или сомнение собеседника. Сначала признай его и задай уточняющий вопрос. "
        "Для тренировки можно написать: коуч.",
        "Напиши: коуч день 5",
    ),
    (
        "День 5. Разбираем продукт",
        "Сравни два варианта только по утверждённым параметрам: задача, комплектация, цена и ограничения. "
        "Если данных нет, не угадывай.",
        "Напиши: коуч день 6",
    ),
    (
        "День 6. Бизнес без обещаний",
        "Изучи PV, повторную покупку и условия маркетинг-плана. Твоя задача — объяснить механику, "
        "а не обещать человеку доход или срок.",
        "Напиши: коуч день 7",
    ),
    (
        "День 7. Подводим итоги",
        "Отметь: с кем поговорил, какой вопрос повторялся и где не хватило ответа. "
        "Это и есть твой следующий учебный план. Неясные вопросы бот отправит в gap-очередь.",
        "Дальше: коуч — тренировка возражений.",
    ),
]


def parse_coach_command(text: str) -> dict[str, Any] | None:
    source = str(text or "").strip()
    if re.match(r"^/?(?:коуч|coach)\s+(?:продолжить|дальше|текущий\s+день)\s*$", source, re.I):
        return {"kind": "first_week_continue"}
    if re.match(r"^/?(?:коуч|coach)\s+(?:готово|выполнил|выполнила|сделал|сделала)\s*$", source, re.I):
        return {"kind": "first_week_complete"}
    answer_match = re.match(r"^/?(?:коуч|coach)\s+ответ\s+(\d+)\s+(.+)$", source, re.I | re.S)
    if answer_match:
        return {
            "kind": "feedback",
            "objection_number": int(answer_match.group(1)),
            "draft": answer_match.group(2).strip(),
        }
    if re.match(r"^/?(?:коуч|coach)\s+(?:старт|7\s*дней|первые\s+7\s+дней)\s*$", source, re.I):
        return {"kind": "first_week_menu"}
    day_match = re.match(r"^/?(?:коуч|coach)\s+день\s+([1-7])\s*$", source, re.I)
    if day_match:
        return {"kind": "first_week_day", "day": int(day_match.group(1))}
    match = re.match(r"^/?(?:коуч|coach)(?:\s+возражения)?(?:\s+(\d+))?\s*$", source, re.I)
    if not match:
        return None
    return {
        "kind": "practice",
        "objection_number": int(match.group(1)) if match.group(1) else None,
    }


def _first_week_context(day: int, stored: dict[str, Any], *, completed_through: int | None = None) -> dict[str, Any]:
    progress = stored.get("coach_first_week") if isinstance(stored.get("coach_first_week"), dict) else {}
    prev_completed = int(progress.get("completed_through") or 0)
    return {
        "coach_first_week": {
            "started": True,
            "current_day": day,
            "completed_through": completed_through if completed_through is not None else max(prev_completed, day - 1),
            "updated_at": "live",
        }
    }


def _day_response(day: int, answer_mode: str, prefix: str | None, stored: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    title, body, next_hint = FIRST_WEEK_DAYS[day - 1]
    parts = [part for part in [prefix, title, "", body, "", next_hint, "", "Когда сделаешь — напиши: коуч готово."] if part is not None]
    return "\n".join(parts), answer_mode, _first_week_context(day, stored)


def build_coach_feedback(objection: dict[str, Any], draft: str) -> tuple[str, str]:
    text = str(draft or "").strip()
    lower = text.lower()
    has_question = bool(re.search(r"\?|\b(что|какой|какая|какие|почему|как|сколько|когда)\b", text, re.I))
    has_empathy = bool(re.search(r"\b(понимаю|согласен|справедлив|нормальный|логичный|верно)\b", lower))
    has_income_promise = bool(re.search(r"\b(гарантир|заработа|доход|окупит|отобьет|без риска|точно получ)\b", lower))
    has_argumentative = bool(re.search(r"\b(не пирамида|не развод|ты не прав|это ерунда|точно не так)\b", lower))
    checks = [
        f"{'✓' if has_empathy else '—'} Сначала признал сомнение человека",
        f"{'✓' if has_question else '—'} Есть уточняющий вопрос",
        f"{'⚠️' if has_income_promise else '✓'} Нет обещаний дохода, окупаемости или отсутствия риска",
        f"{'⚠️' if has_argumentative else '✓'} Нет спора с человеком и категоричных заверений",
    ]
    improvements: list[str] = []
    if not has_empathy:
        improvements.append("Начни с признания сомнения: «понимаю» или «нормальный вопрос».")
    if not has_question:
        improvements.append("Добавь один вопрос, чтобы понять настоящую причину сомнения.")
    if has_income_promise:
        improvements.append("Убери обещание дохода или окупаемости: этого нельзя гарантировать.")
    if has_argumentative:
        improvements.append("Не доказывай, что человек неправ. Сначала уточни, что именно его настораживает.")
    answer_parts = [
        f"Разбор: {str(objection.get('title') or '').strip()}",
        "",
        "Твой ответ:",
        text,
        "",
        "Проверка:",
        *checks,
        "",
    ]
    if improvements:
        answer_parts.append("Что улучшить:\n- " + "\n- ".join(improvements))
    else:
        answer_parts.append(
            "Хорошая основа. Теперь в живом разговоре не спеши с объяснением: "
            "сначала дождись ответа на свой вопрос."
        )
    answer_parts.extend(
        [
            "",
            f"Ориентир по утверждённой базе: {str(objection.get('first_reply') or '').strip()}",
            f"Следующий шаг: {str(objection.get('next_step') or '').strip()}",
        ]
    )
    return "\n".join(answer_parts), "direct_coach_objection_feedback"


def build_coach_response(
    command: dict[str, Any],
    objections: list[dict[str, Any]],
    stored: dict[str, Any],
) -> tuple[str, str, dict[str, Any]] | None:
    progress = stored.get("coach_first_week") if isinstance(stored.get("coach_first_week"), dict) else {}
    current_day = min(7, max(1, int(progress.get("current_day") or 1)))
    started = bool(progress.get("started"))

    kind = command["kind"]
    if kind == "first_week_menu":
        text, _mode, ctx = _day_response(
            1, "direct_coach_first_week_start", "Первые 7 дней партнёра. Начинаем с первого шага.", stored
        )
        return text, "direct_coach_first_week_start", ctx
    if kind == "first_week_continue":
        if not started:
            text, _mode, ctx = _day_response(1, "direct_coach_first_week_start", "Маршрут ещё не начат.", stored)
        else:
            text, _mode, ctx = _day_response(
                current_day, "direct_coach_first_week_continue", f"Продолжаем: день {current_day} из 7.", stored
            )
        return text, _mode, ctx
    if kind == "first_week_complete":
        if not started:
            text, _mode, ctx = _day_response(1, "direct_coach_first_week_start", "Сначала начни маршрут.", stored)
            return text, _mode, ctx
        if current_day >= 7:
            return (
                "Первые 7 дней завершены. Хорошая работа. Дальше можно перейти к тренировке возражений: напиши «коуч».",
                "direct_coach_first_week_complete",
                {
                    "coach_first_week": {
                        "started": True,
                        "current_day": 7,
                        "completed_through": 7,
                        "finished": True,
                        "updated_at": "live",
                    }
                },
            )
        next_day = current_day + 1
        title, body, next_hint = FIRST_WEEK_DAYS[next_day - 1]
        text = "\n".join(
            ["Отлично. Шаг отмечен.", "", title, "", body, "", next_hint, "", "Когда сделаешь — напиши: коуч готово."]
        )
        return text, "direct_coach_first_week_complete", _first_week_context(next_day, stored, completed_through=next_day - 1)
    if kind == "first_week_day":
        text, mode, ctx = _day_response(command["day"], "direct_coach_first_week_day", None, stored)
        return text, mode, ctx

    if not objections:
        return None

    objection_number = command.get("objection_number")
    if kind == "feedback":
        objection = objections[objection_number - 1] if objection_number and objection_number <= len(objections) else None
        if not objection:
            return (
                "Такого номера нет. Напиши «коуч», чтобы увидеть список ситуаций.",
                "direct_coach_objections_clarify",
                {},
            )
        text, mode = build_coach_feedback(objection, str(command.get("draft") or ""))
        return text, mode, {}

    if not objection_number:
        menu = "\n".join(f"{index + 1}. {str(row.get('title') or '').strip()}" for index, row in enumerate(objections))
        text = "\n".join(
            [
                "Тренировка возражений",
                "",
                "Выбери ситуацию:",
                menu,
                "",
                "Напиши: коуч 1, коуч 2 и так далее. Я дам реплику клиента и ориентир для разбора.",
            ]
        )
        return text, "direct_coach_objections_menu", {}

    objection = objections[objection_number - 1] if objection_number <= len(objections) else None
    if not objection:
        return (
            "Такого номера нет. Напиши «коуч», чтобы увидеть список ситуаций.",
            "direct_coach_objections_clarify",
            {},
        )
    title = str(objection.get("title") or "").strip()
    text = "\n".join(
        [
            f"Тренировка: {title}",
            "",
            f"Клиент: «{title}».",
            "",
            "Твоя задача: спокойно ответь своими словами и задай один уточняющий вопрос. "
            "Не спорь и не обещай доход или результат.",
            "",
            f"Ориентир для самопроверки: {str(objection.get('first_reply') or '').strip()}",
            "",
            f"Что выяснить дальше: {str(objection.get('clarify') or '').strip()}",
            "",
            f"Не делать: {str(objection.get('do_not_say') or '').strip()}",
            "",
            f"Когда ответишь, пришли одной строкой: коуч ответ {objection_number} [твой текст].",
        ]
    )
    return text, "direct_coach_objection_practice", {}
