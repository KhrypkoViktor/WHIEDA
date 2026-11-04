"""Измерение нажатия кнопки бота на настоящей PostgreSQL (04.11.2026).

Общее для ``test_tap_fast_postgres.py`` и ``scripts/measure_tap_roundtrips.py``: подмена Bot API
(httpx.AsyncClient.post), счётчик транзакций и запросов (каждое взятое из пула подключение —
одна транзакция; запросы — выполнения курсора) и данные для четырёх сценариев нажатия.
Telegram не нужен, секретов нет: токен и id — выдуманные.
"""

from __future__ import annotations

import contextvars
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
import psycopg
from psycopg_pool import AsyncConnectionPool

from app.telegram.bindings import BotBindingContext
from app.tenancy import TenantContext

BINDING_ID = "whieda-advisor-bot"
BOT_TOKEN = "test-bot-token"
OWNER = 688931415
ADMIN = 2101187096
FORUM_CHAT = -1001234567890
USER = 5550001

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id)
values ('telegram:whieda:5550001', 'whieda', 'Анна', 5550001, '5550001');
insert into support_forums (tenant_id, binding_id, kind, chat_id, title, registered_by_telegram_user_id, reports_thread_id)
values ('whieda', 'whieda-advisor-bot', 'services', -1001234567890, 'WWC Support', 688931415, null),
       ('whieda', 'whieda-advisor-bot', 'site', -1001234567890, 'WWC Support', 688931415, 4242);
insert into partner_site_requests (tenant_id, actor_id, status, country_code, requested_subdomain)
values ('whieda', 'telegram:whieda:5550001', 'awaiting_photo', 'RU', 'anna-test');
"""


def whieda_binding() -> BotBindingContext:
    tenant = TenantContext(
        tenant_id="whieda",
        status="active",
        display_name="WHIEDA",
        entitlements={"structure_basic": True, "partner_leads": True, "site_support": True},
    )
    return BotBindingContext(
        binding_id=BINDING_ID,
        tenant=tenant,
        bot_token_ref="env:TAP_BOT_TOKEN",
        webhook_secret_ref="env:TAP_WEBHOOK_SECRET",
        bot_username="WHIEDA_Advisor_bot",
        status="active",
        processing_mode="core",
        bot_token=BOT_TOKEN,
        webhook_secret="tap-secret",
    )


def callback_update(
    data: str,
    *,
    update_id: int,
    message_id: int = 100,
    user: int = USER,
    chat: int | None = None,
    query_id: str | None = None,
    chat_type: str = "private",
) -> dict[str, Any]:
    """Нажатие кнопки под сообщением ``message_id``."""
    return {
        "update_id": update_id,
        "callback_query": {
            "id": query_id or f"q{update_id}",
            "from": {"id": user, "first_name": "Анна", "username": "anna_test"},
            "message": {"message_id": message_id, "chat": {"id": chat if chat is not None else user, "type": chat_type}},
            "data": data,
        },
    }


# ---- Bot API: подмена --------------------------------------------------------------------------


@dataclass
class TelegramCall:
    method: str
    payload: dict[str, Any]


@dataclass
class FakeTelegram:
    """Подменяет ``httpx.AsyncClient.post``: все вызовы Bot API пишутся в ``calls``."""

    calls: list[TelegramCall] = field(default_factory=list)
    fail_methods: set[str] = field(default_factory=set)
    delay_methods: dict[str, float] = field(default_factory=dict)
    _message_id: int = 9000

    def methods(self) -> list[str]:
        return [call.method for call in self.calls]

    def sent(self, method: str) -> list[TelegramCall]:
        return [call for call in self.calls if call.method == method]

    async def post(self, client: httpx.AsyncClient, url: Any, **kwargs: Any) -> httpx.Response:
        import asyncio

        method = str(url).rsplit("/", 1)[-1]
        payload = dict(kwargs.get("json") or {})
        self.calls.append(TelegramCall(method=method, payload=payload))
        request = httpx.Request("POST", str(url))
        if method in self.delay_methods:
            await asyncio.sleep(self.delay_methods[method])
        if method in self.fail_methods:
            raise httpx.ConnectError("telegram is down (test)", request=request)
        result: Any = True
        if method in {"sendMessage", "copyMessage"}:
            self._message_id += 1
            result = {"message_id": self._message_id}
        elif method == "createForumTopic":
            result = {"message_thread_id": 77, "name": payload.get("name")}
        return httpx.Response(200, json={"ok": True, "result": result}, request=request)

    def install(self, monkeypatch: Any) -> "FakeTelegram":
        fake = self

        async def post(client: httpx.AsyncClient, url: Any, **kwargs: Any) -> httpx.Response:
            return await fake.post(client, url, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        return self


# ---- Счётчик обращений к базе ------------------------------------------------------------------


@dataclass
class Transaction:
    """Одно подключение, взятое из пула: BEGIN … COMMIT. ``frames`` — кто его взял (для таблицы шагов)."""

    queries: int = 0
    frames: tuple[str, ...] = ()


_CURRENT: contextvars.ContextVar[Transaction | None] = contextvars.ContextVar("tap_tx", default=None)


def _app_frames(skip: int = 2) -> tuple[str, ...]:
    """Имена функций приложения в стеке (изнутри наружу): цепочка await-ов — настоящий стек."""
    names: list[str] = []
    frame = sys._getframe(skip)
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module.startswith("app.") and module not in {"app.db"}:
            names.append(f"{module.removeprefix('app.')}.{frame.f_code.co_name}")
        frame = frame.f_back
    return tuple(names)


class TransactionCounter:
    """Считает транзакции и запросы; ``transactions`` — по порядку. Пока открыт ``with``, пул и курсор подменены."""

    def __init__(self) -> None:
        self.transactions: list[Transaction] = []
        self.stray_queries = 0
        self._originals: tuple[Callable, Callable] | None = None

    @property
    def count(self) -> int:
        return len(self.transactions)

    @property
    def queries(self) -> int:
        return sum(tx.queries for tx in self.transactions) + self.stray_queries

    @property
    def round_trips(self) -> int:
        """Обращений к серверу по проводу: BEGIN + запросы (с set_config) + COMMIT у каждой транзакции с запросами."""
        return sum(tx.queries + 2 for tx in self.transactions if tx.queries) + self.stray_queries

    def reset(self) -> None:
        self.transactions.clear()
        self.stray_queries = 0

    def __enter__(self) -> "TransactionCounter":
        counter = self
        original_connection = AsyncConnectionPool.connection
        original_execute = psycopg.AsyncCursor.execute

        @asynccontextmanager
        async def connection(pool: AsyncConnectionPool, *args: Any, **kwargs: Any):
            tx = Transaction(frames=_app_frames())
            counter.transactions.append(tx)
            token = _CURRENT.set(tx)
            try:
                async with original_connection(pool, *args, **kwargs) as conn:
                    yield conn
            finally:
                _CURRENT.reset(token)

        async def execute(cursor: Any, *args: Any, **kwargs: Any):
            tx = _CURRENT.get()
            if tx is None:
                counter.stray_queries += 1
            else:
                tx.queries += 1
            return await original_execute(cursor, *args, **kwargs)

        AsyncConnectionPool.connection = connection  # type: ignore[method-assign]
        psycopg.AsyncCursor.execute = execute  # type: ignore[method-assign]
        self._originals = (original_connection, original_execute)
        return self

    def __exit__(self, *_exc: object) -> None:
        assert self._originals is not None
        AsyncConnectionPool.connection, psycopg.AsyncCursor.execute = self._originals  # type: ignore[method-assign]
        self._originals = None
