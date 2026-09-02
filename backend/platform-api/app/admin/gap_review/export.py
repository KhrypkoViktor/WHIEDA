"""Human-readable exports for advisor gap review queue."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Iterable

from app.admin.gap_review.constants import OWNER_ROLE_LABELS, PRIORITY_LABELS

SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Медицинская проверка", ("medical_or_safety_boundary",)),
    ("Бизнес и формулировки", ("ambiguous_product", "unknown_product", "unsupported_topic")),
    ("Недостающие материалы", ("missing_resource",)),
    ("Решения владельца", ("unknown_followup",)),
)


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)[:16]


def _who_decides(row: dict[str, Any]) -> str:
    role = row.get("owner_role")
    name = str(row.get("owner_name") or "").strip()
    label = OWNER_ROLE_LABELS.get(str(role or ""), str(role or "—"))
    return f"{label}{(': ' + name) if name else ''}"


def _what_needed(row: dict[str, Any]) -> str:
    mapping = {
        "medical_review": "Проверка медицинской формулировки",
        "safety_review": "Проверка безопасности",
        "resource_link": "Добавить или проверить материал",
        "clarification_rule": "Уточнить правило неоднозначности",
        "alias": "Проверить алиас / название товара",
        "business_faq": "Добавить бизнес-FAQ",
        "intent_gap": "Проверить сценарий ответа",
        "none": "Посмотреть и решить",
    }
    note = str(row.get("operator_note") or "").strip()
    base = mapping.get(str(row.get("candidate_type") or ""), "Посмотреть и решить")
    if note:
        return f"{base}. Заметка: {note[:240]}"
    return base


def _priority_label(row: dict[str, Any]) -> str:
    return PRIORITY_LABELS.get(str(row.get("priority") or ""), str(row.get("priority") or ""))


def _topic(row: dict[str, Any]) -> str:
    product = str(row.get("detected_product") or "").strip()
    question = str(row.get("question_normalized") or "")
    if product:
        return f"{question} ({product})"
    return question


def export_markdown(rows: Iterable[dict[str, Any]], *, tenant_id: str) -> str:
    items = list(rows)
    open_items = [r for r in items if r.get("status") not in ("resolved", "rejected")]
    closed_items = [r for r in items if r.get("status") in ("resolved", "rejected")]
    lines = [
        f"# Пакет проверки gap-вопросов WHIEDA ({tenant_id})",
        "",
        "Читаемая сводка для волонтёра. Без технических ID и контактов пользователей.",
        "",
    ]
    for title, kinds in SECTIONS:
        section_rows = [
            r for r in open_items if str(r.get("gap_kind") or "") in kinds
        ]
        if not section_rows:
            continue
        lines.extend([f"## {title}", ""])
        lines.append(
            "| Приоритет | Тема / вопрос | Повторы | Последний раз | Кто решает | Что нужно |"
        )
        lines.append("| --- | --- | ---: | --- | --- | --- |")
        for row in section_rows:
            lines.append(
                "| {prio} | {topic} | {rep} | {seen} | {who} | {need} |".format(
                    prio=_priority_label(row),
                    topic=_topic(row).replace("|", "/"),
                    rep=int(row.get("event_count") or 0),
                    seen=_iso(row.get("last_seen_at")),
                    who=_who_decides(row).replace("|", "/"),
                    need=_what_needed(row).replace("|", "/"),
                )
            )
        lines.append("")
    lines.extend(
        [
            "## Закрытые пункты (кратко)",
            "",
            f"Всего закрыто или отклонено: **{len(closed_items)}**.",
            "",
        ]
    )
    if closed_items:
        lines.append("| Тема / вопрос | Статус | Повторы |")
        lines.append("| --- | --- | ---: |")
        for row in closed_items[:30]:
            lines.append(
                f"| {_topic(row).replace('|', '/')} | {row.get('status')} | {int(row.get('event_count') or 0)} |"
            )
    return "\n".join(lines) + "\n"


def export_csv(rows: Iterable[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "priority",
            "topic",
            "repeats",
            "last_seen",
            "who_decides",
            "what_needed",
            "status",
            "gap_kind",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                _priority_label(row),
                _topic(row),
                int(row.get("event_count") or 0),
                _iso(row.get("last_seen_at")),
                _who_decides(row),
                _what_needed(row),
                row.get("status"),
                row.get("gap_kind"),
            ]
        )
    return buffer.getvalue()
