"""Per-chat Telegram update ordering and update_id deduplication (single Core process).

This module serializes handling of Telegram updates that share the same chat,
while allowing different chats to run concurrently. It is intentionally
in-memory and scoped to one Core container — see TELEGRAM_CHAT_SEQUENCER_V1.md
for a durable upgrade path.

Нажатия кнопок (04.11.2026): отпечаток кнопки — ``callback_data`` + сообщение, под которым она
нажата (одна и та же «Пропустить» на следующем шаге — другое сообщение, а не повторный тап);
повторный тап по той же кнопке того же сообщения, пока первый ещё обрабатывается, отбрасывается
сразу, не ожидая очереди чата (``drop_inflight_duplicate``).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

DEFAULT_IDLE_TTL_SEC = 3600.0
DEFAULT_MAX_CHAT_ENTRIES = 512
DEFAULT_UPDATE_ID_TTL_SEC = 24 * 3600.0
DEFAULT_MAX_UPDATE_IDS = 5000
DEFAULT_MESSAGE_FINGERPRINT_TTL_SEC = 12.0


@dataclass(frozen=True)
class SequencerResult:
    duplicate: bool
    value: object | None = None
    # Сколько обновление ждало очередь чата и сколько работал обработчик (мс) — в журнал, чтобы
    # «кнопка отвечает 5 секунд» разбиралась по цифрам: очередь за предыдущим обновлением или сам обработчик.
    wait_ms: float = 0.0
    run_ms: float = 0.0


class ChatUpdateSequencer:
    def __init__(
        self,
        *,
        idle_ttl_sec: float = DEFAULT_IDLE_TTL_SEC,
        max_chat_entries: int = DEFAULT_MAX_CHAT_ENTRIES,
        update_id_ttl_sec: float = DEFAULT_UPDATE_ID_TTL_SEC,
        max_update_ids: int = DEFAULT_MAX_UPDATE_IDS,
        message_fingerprint_ttl_sec: float = DEFAULT_MESSAGE_FINGERPRINT_TTL_SEC,
    ) -> None:
        self._idle_ttl_sec = idle_ttl_sec
        self._max_chat_entries = max_chat_entries
        self._update_id_ttl_sec = update_id_ttl_sec
        self._max_update_ids = max_update_ids
        self._message_fingerprint_ttl_sec = message_fingerprint_ttl_sec
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._last_access: dict[str, float] = {}
        self._processed_updates: dict[str, float] = {}
        self._recent_message_fingerprints: dict[tuple[str, str], float] = {}
        # Отпечатки, которые обрабатываются прямо сейчас (только у вызовов с drop_inflight_duplicate).
        self._inflight_fingerprints: set[tuple[str, str]] = set()
        self._meta_lock = asyncio.Lock()

    async def run_ordered(
        self,
        chat_key: str,
        update_id: int | None,
        handler: Callable[[], Awaitable[T]],
        *,
        message_fingerprint: str | None = None,
        namespace: str = "default",
        drop_inflight_duplicate: bool = False,
        on_accepted: Callable[[], None] | None = None,
    ) -> SequencerResult:
        """Выполнить ``handler`` в очереди чата; повтор того же update_id или отпечатка — ``duplicate``.

        ``drop_inflight_duplicate`` — повтор отпечатка, пока первый ещё обрабатывается (или ждёт очереди
        чата), отбрасывается сразу, а не после ожидания блокировки: так повторный тап по кнопке
        получает ответ немедленно. Без флага (текстовые сообщения) поведение прежнее.
        ``on_accepted`` — вызывается один раз, когда обновление принято (не дубль до очереди) и до ожидания
        очереди чата: сюда ставят «бот печатает», чтобы человек видел реакцию, даже если чат занят."""
        scoped_chat_key = f"{namespace}:{chat_key}"
        update_key = f"{namespace}:{update_id}" if update_id is not None else None
        inflight_key: tuple[str, str] | None = None
        if message_fingerprint:
            if drop_inflight_duplicate:
                if not await self._claim_inflight_fingerprint(scoped_chat_key, message_fingerprint):
                    return SequencerResult(duplicate=True, value=None)
                inflight_key = (scoped_chat_key, message_fingerprint)
            elif await self._is_duplicate_fingerprint(scoped_chat_key, message_fingerprint):
                return SequencerResult(duplicate=True, value=None)
        try:
            if update_key is not None and await self._is_duplicate(update_key):
                return SequencerResult(duplicate=True, value=None)
            if on_accepted is not None:
                on_accepted()

            queued_at = time.monotonic()
            lock = await self._chat_lock(scoped_chat_key)
            async with lock:
                started_at = time.monotonic()
                wait_ms = (started_at - queued_at) * 1000
                if message_fingerprint and await self._is_duplicate_fingerprint(
                    scoped_chat_key, message_fingerprint
                ):
                    return SequencerResult(duplicate=True, value=None, wait_ms=wait_ms)
                if update_key is not None and await self._is_duplicate(update_key):
                    return SequencerResult(duplicate=True, value=None, wait_ms=wait_ms)
                try:
                    value = await handler()
                except Exception:
                    raise
                else:
                    if update_key is not None:
                        await self._remember_update(update_key)
                    if message_fingerprint:
                        await self._remember_fingerprint(scoped_chat_key, message_fingerprint)
                    return SequencerResult(
                        duplicate=False,
                        value=value,
                        wait_ms=wait_ms,
                        run_ms=(time.monotonic() - started_at) * 1000,
                    )
                finally:
                    await self._touch_chat(scoped_chat_key)
        finally:
            if inflight_key is not None:
                self._inflight_fingerprints.discard(inflight_key)

    async def _chat_lock(self, chat_key: str) -> asyncio.Lock:
        async with self._meta_lock:
            lock = self._chat_locks.get(chat_key)
            if lock is None:
                lock = asyncio.Lock()
                self._chat_locks[chat_key] = lock
            self._last_access[chat_key] = time.monotonic()
            self._cleanup_idle_locked()
            return lock

    async def _touch_chat(self, chat_key: str) -> None:
        async with self._meta_lock:
            self._last_access[chat_key] = time.monotonic()

    async def _is_duplicate(self, update_key: str) -> bool:
        async with self._meta_lock:
            self._cleanup_updates_locked(time.monotonic())
            return update_key in self._processed_updates

    async def _remember_update(self, update_key: str) -> None:
        async with self._meta_lock:
            now = time.monotonic()
            self._processed_updates[update_key] = now
            self._cleanup_updates_locked(now)

    async def _is_duplicate_fingerprint(self, chat_key: str, fingerprint: str) -> bool:
        async with self._meta_lock:
            now = time.monotonic()
            self._cleanup_fingerprints_locked(now)
            return (chat_key, fingerprint) in self._recent_message_fingerprints

    async def _claim_inflight_fingerprint(self, chat_key: str, fingerprint: str) -> bool:
        """Занять отпечаток для обработки; ``False`` — он недавно обработан или обрабатывается сейчас."""
        async with self._meta_lock:
            now = time.monotonic()
            self._cleanup_fingerprints_locked(now)
            key = (chat_key, fingerprint)
            if key in self._recent_message_fingerprints or key in self._inflight_fingerprints:
                return False
            self._inflight_fingerprints.add(key)
            return True

    async def _remember_fingerprint(self, chat_key: str, fingerprint: str) -> None:
        async with self._meta_lock:
            now = time.monotonic()
            self._recent_message_fingerprints[(chat_key, fingerprint)] = now
            self._cleanup_fingerprints_locked(now)

    def _cleanup_idle_locked(self) -> None:
        now = time.monotonic()
        if len(self._chat_locks) <= self._max_chat_entries:
            return
        stale = [
            key
            for key, seen in self._last_access.items()
            if now - seen > self._idle_ttl_sec and key in self._chat_locks
        ]
        for key in stale:
            lock = self._chat_locks.get(key)
            if lock and lock.locked():
                continue
            self._chat_locks.pop(key, None)
            self._last_access.pop(key, None)

    def _cleanup_updates_locked(self, now: float) -> None:
        expired = [
            key
            for key, seen in self._processed_updates.items()
            if now - seen > self._update_id_ttl_sec
        ]
        for key in expired:
            self._processed_updates.pop(key, None)
        if len(self._processed_updates) <= self._max_update_ids:
            return
        overflow = len(self._processed_updates) - self._max_update_ids
        for key in sorted(self._processed_updates, key=self._processed_updates.get)[:overflow]:
            self._processed_updates.pop(key, None)

    def _cleanup_fingerprints_locked(self, now: float) -> None:
        expired = [
            key
            for key, seen in self._recent_message_fingerprints.items()
            if now - seen > self._message_fingerprint_ttl_sec
        ]
        for key in expired:
            self._recent_message_fingerprints.pop(key, None)


def build_message_fingerprint(
    *, text: str = "", callback_data: str = "", message_id: int | str | None = None
) -> str | None:
    """Отпечаток обновления для защиты от дублей. У кнопки — ``callback_data`` и сообщение с кнопкой
    (``message_id``): тот же тап по той же кнопке того же сообщения — дубль, а та же «Пропустить» под
    новым сообщением на следующем шаге — нет."""
    data = str(callback_data or "").strip()
    raw = data or str(text or "").strip().lower()
    if not raw:
        return None
    if data and message_id not in (None, ""):
        raw = f"{data}\x00m{message_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


_sequencer: ChatUpdateSequencer | None = None


def get_chat_sequencer() -> ChatUpdateSequencer:
    global _sequencer
    if _sequencer is None:
        _sequencer = ChatUpdateSequencer()
    return _sequencer


def reset_chat_sequencer_for_tests() -> None:
    global _sequencer
    _sequencer = ChatUpdateSequencer()
