from __future__ import annotations

from typing import Any


def format_leader_digest_message(digest: dict[str, Any]) -> str:
    metrics = digest.get("metrics") or {}
    actions = digest.get("attention_actions") or []
    lines = [
        "Недельный отчёт",
        f"Посетители: {metrics.get('route_visitors', 0)}",
        f"Лиды: {metrics.get('new_leads', 0)}",
        f"Эскалации: {metrics.get('open_escalations', 0)}",
    ]
    for item in actions:
        if item and item != "—":
            lines.append(str(item))
    return "\n".join(lines)
