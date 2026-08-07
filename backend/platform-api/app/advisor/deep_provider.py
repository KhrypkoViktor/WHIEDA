from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DeepAnswerProvider(ABC):
    @abstractmethod
    async def answer(self, tenant_id: str, question: str, context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class DisabledDeepAnswerProvider(DeepAnswerProvider):
    async def answer(self, tenant_id: str, question: str, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "answer_text": "Глубокий коуч сейчас недоступен. Задайте вопрос о конкретном товаре или цене.",
            "answer_mode": "fallback",
            "route": "structured",
            "product": None,
            "media": {"photo_url": None, "videos": [], "documents": []},
            "clarifications": [],
            "sources": [],
            "context": context,
            "error_id": None,
        }
