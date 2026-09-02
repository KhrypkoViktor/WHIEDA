"""Per-chat Telegram update ordering and update_id deduplication (single Core process).

This module serializes handling of Telegram updates that share the same chat,
while allowing different chats to run concurrently. It is intentionally
in-memory and scoped to one Core container — see TELEGRAM_CHAT_SEQUENCER_V1.md
for a durable upgrade path.
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
        self._meta_lock = asyncio.Lock()

    async def run_ordered(
        self,
        chat_key: str,
        update_id: int | None,
        handler: Callable[[], Awaitable[T]],
        *,
        message_fingerprint: str | None = None,
        namespace: str = "default",
    ) -> SequencerResult:
        scoped_chat_key = f"{namespace}:{chat_key}"
        update_key = f"{namespace}:{update_id}" if update_id is not None else None
        if message_fingerprint and await self._is_duplicate_fingerprint(
            scoped_chat_key, message_fingerprint
        ):
            return SequencerResult(duplicate=True, value=None)
        if update_key is not None and await self._is_duplicate(update_key):
            return SequencerResult(duplicate=True, value=None)

        lock = await self._chat_lock(scoped_chat_key)
        async with lock:
            if message_fingerprint and await self._is_duplicate_fingerprint(
                scoped_chat_key, message_fingerprint
            ):
                return SequencerResult(duplicate=True, value=None)
            if update_key is not None and await self._is_duplicate(update_key):
                return SequencerResult(duplicate=True, value=None)
            try:
                value = await handler()
            except Exception:
                raise
            else:
                if update_key is not None:
                    await self._remember_update(update_key)
                if message_fingerprint:
                    await self._remember_fingerprint(scoped_chat_key, message_fingerprint)
                return SequencerResult(duplicate=False, value=value)
            finally:
                await self._touch_chat(scoped_chat_key)

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


def build_message_fingerprint(*, text: str = "", callback_data: str = "") -> str | None:
    raw = str(callback_data or "").strip() or str(text or "").strip().lower()
    if not raw:
        return None
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
