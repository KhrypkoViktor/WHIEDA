"""Routing contracts for an unfinished partner-site request."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.site_requests import try_handle_site_request_message
from app.telegram.update_parser import parse_telegram_message


@pytest.mark.asyncio
async def test_slash_command_is_not_captured_by_open_site_request(whieda_tenant):
    message = parse_telegram_message(
        {
            "message": {
                "message_id": 53,
                "text": "/products",
                "chat": {"id": 8001, "type": "private"},
                "from": {"id": 8001},
            }
        }
    )
    assert message is not None
    with patch("app.telegram.site_requests._actor", AsyncMock()) as actor:
        result = await try_handle_site_request_message(
            whieda_tenant, message, trace_id="site-command-bypass"
        )
    assert result is None
    actor.assert_not_awaited()
