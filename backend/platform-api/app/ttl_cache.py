"""Маленький кэш в памяти процесса с временем жизни записи (24.10.2026).

Тот же приём, что в ``app/academy/showcases.py`` (словарь + ``time.monotonic``), вынесенный
в одно место: арендатор по хосту, сессия входа, публичные ответы ``/public/ref`` и
``/theme-access/public``. Одна запись — значение и момент, до которого оно годится;
потолок размера защищает память от перебора ключей, вытесняется самая старая запись.

Один процесс uvicorn — один кэш; между процессами и контейнерами записи не делятся,
поэтому всё, что кэшируется, обязано быть безопасным на срок жизни записи.
"""

from __future__ import annotations

import time
import weakref
from typing import Any, Generic, Hashable, TypeVar

T = TypeVar("T")

# Все кэши процесса: тесты сбрасывают их одним вызовом, не зная, какие ещё появились.
_ALL_CACHES: "weakref.WeakSet[TtlCache[Any]]" = weakref.WeakSet()

_MISS: Any = object()


def _now() -> float:
    # Отдельная функция, чтобы тесты подменяли время без ожидания.
    return time.monotonic()


class TtlCache(Generic[T]):
    """Словарь ключ → (значение, до какого момента годится) с потолком числа записей."""

    def __init__(self, *, max_entries: int) -> None:
        self._max_entries = max(1, int(max_entries))
        self._items: dict[Hashable, tuple[float, T]] = {}
        _ALL_CACHES.add(self)

    def get(self, key: Hashable, default: Any = None) -> Any:
        """Значение, если запись есть и не устарела; иначе ``default``. Устаревшая запись удаляется."""
        item = self._items.get(key, _MISS)
        if item is _MISS:
            return default
        expires_at, value = item
        if _now() >= expires_at:
            self._items.pop(key, None)
            return default
        return value

    def set(self, key: Hashable, value: T, ttl: float) -> None:
        """Записать значение на ``ttl`` секунд; при переполнении уйдёт самая старая запись."""
        self._items.pop(key, None)  # перезапись ставит ключ в конец очереди вытеснения
        while len(self._items) >= self._max_entries:
            self._items.pop(next(iter(self._items)))
        self._items[key] = (_now() + float(ttl), value)

    def pop(self, key: Hashable) -> None:
        self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


def clear_all_caches() -> None:
    """Тесты: забыть всё, что запомнили кэши процесса (арендаторы, сессии, публичные ответы)."""
    for cache in list(_ALL_CACHES):
        cache.clear()
