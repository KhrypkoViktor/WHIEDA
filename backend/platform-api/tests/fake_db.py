"""Подмена базы для тестов «сколько раз код в неё ходит» (24.10.2026).

``FakeDb`` считает открытые подключения (каждое — это BEGIN + COMMIT на проводе) и выполненные
запросы; ответ на запрос даёт функция ``answer(query, params)``. Настоящий SQL здесь не
выполняется — его проверяют ``*_postgres.py``; здесь проверяется число заходов и поведение вокруг них.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Answer = Callable[[str, Any], list[dict[str, Any]]]


class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self._conn = conn
        self._rows: list[dict[str, Any]] = []

    async def __aenter__(self) -> "FakeCursor":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, query: Any, params: Any = None) -> None:
        text = " ".join(str(query).split())
        self._conn.statements.append(text)
        self._conn.params.append(params)
        self._rows = self._conn.answer(text, params)

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class FakeConn:
    def __init__(self, answer: Answer, tenant_id: str | None = None) -> None:
        self.answer = answer
        self.tenant_id = tenant_id
        self.statements: list[str] = []
        self.params: list[Any] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


class _Open:
    def __init__(self, db: "FakeDb", tenant_id: str | None) -> None:
        self._db = db
        self._tenant_id = tenant_id

    async def __aenter__(self) -> FakeConn:
        return self._db.open(self._tenant_id)

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakeDb:
    def __init__(self, answer: Answer | None = None) -> None:
        self.answer: Answer = answer or (lambda query, params: [])
        self.conns: list[FakeConn] = []
        self.fail = False

    def open(self, tenant_id: str | None) -> FakeConn:
        if self.fail:
            raise RuntimeError("database unavailable")
        conn = FakeConn(lambda query, params: self.answer(query, params), tenant_id)
        self.conns.append(conn)
        return conn

    @property
    def opened(self) -> int:
        """Сколько раз взяли подключение (транзакцию)."""
        return len(self.conns)

    @property
    def statements(self) -> list[str]:
        return [text for conn in self.conns for text in conn.statements]

    # app.db.tenant_connection(tenant_id)
    def tenant_connection(self, tenant_id: str) -> _Open:
        return _Open(self, tenant_id)

    # AsyncConnectionPool.connection(timeout=...)
    def connection(self, timeout: Any = None) -> _Open:
        return _Open(self, None)
