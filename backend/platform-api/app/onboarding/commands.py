"""Onboarding command parsing (Stage 5 — deterministic, no LLM)."""

from __future__ import annotations

import re
from typing import Any

COMMAND_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("plan", re.compile(r"^(?:мой\s+план|план\s+обучения)\s*$", re.I)),
    ("start", re.compile(r"^(?:начать\s+обучение|старт\s+обучения|начать)\s*$", re.I)),
    ("done", re.compile(r"^(?:сделал|сделала|готово|выполнил|выполнила)\s*$", re.I)),
    ("help", re.compile(r"^(?:нужна\s+помощь|помощь\s+наставнику)\s*$", re.I)),
    ("postpone", re.compile(r"^(?:перенести|отложить)\s*$", re.I)),
    ("mentor", re.compile(r"^(?:мой\s+наставник|наставник)\s*$", re.I)),
    ("pause_reminders", re.compile(r"^(?:остановить\s+напоминания|без\s+напоминаний)\s*$", re.I)),
    ("resume", re.compile(r"^(?:продолжить\s+обучение|возобновить)\s*$", re.I)),
]


def parse_onboarding_command(text: str) -> dict[str, Any] | None:
    source = str(text or "").strip()
    if not source:
        return None
    for command, pattern in COMMAND_PATTERNS:
        if pattern.match(source):
            payload: dict[str, Any] = {"command": command}
            if command == "help":
                help_match = re.match(r"^нужна\s+помощь\s*[:\-]?\s*(.+)$", source, re.I | re.S)
                if help_match:
                    payload["question_text"] = help_match.group(1).strip()[:500]
            return payload
    return None
