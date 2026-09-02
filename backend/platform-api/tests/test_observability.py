from __future__ import annotations

import logging

from app.observability import configure_logging


def test_http_client_request_urls_are_not_logged_at_info_level() -> None:
    configure_logging("INFO")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
