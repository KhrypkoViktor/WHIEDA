"""Занятые адреса сайтов отсекаются сразу, при выборе имени (24.09.2026).

Елена Лисицина выбрала в боте elena, оплатила — а elena.wwc.best давно ведёт на
Елену Дацкевич (ref onlineelena). Бот сверял только ref_code и адрес пропустил.
"""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.site_requests import service


@asynccontextmanager
async def _conn(*_args, **_kwargs):
    yield object()


def test_issued_alias_is_taken_without_touching_db():
    fetch = AsyncMock(return_value=None)
    with patch.object(service, "fetch_one", fetch):
        assert asyncio.run(service.subdomain_taken(object(), "whieda", "elena", "a"))
        assert asyncio.run(service.subdomain_taken(object(), "whieda", "samtsova", "a"))
    fetch.assert_not_awaited()


def test_profile_subdomain_and_site_url_are_checked():
    fetch = AsyncMock(return_value={"taken": 1})
    with patch.object(service, "fetch_one", fetch):
        assert asyncio.run(service.subdomain_taken(object(), "whieda", "billteam", "a"))
    sql, params = fetch.await_args.args[1], fetch.await_args.args[2]
    assert "public_profile->>'subdomain'" in sql and "public_site_url" in sql
    assert params[:3] == ("billteam", "billteam", "https://billteam.wwc.best%")


def test_free_name_passes():
    with patch.object(service, "fetch_one", AsyncMock(return_value=None)):
        assert not asyncio.run(service.subdomain_taken(object(), "whieda", "lisicina", "a"))


def test_bot_rejects_taken_name_with_clear_message():
    with patch.object(service, "tenant_connection", _conn), patch.object(
        service, "fetch_one", AsyncMock(return_value=None)
    ):
        with pytest.raises(service.SiteRequestError) as exc:
            asyncio.run(service.set_site_request_subdomain("whieda", "telegram:whieda:1", "Elena"))
    assert "elena.wwc.best уже занят" in str(exc.value)
