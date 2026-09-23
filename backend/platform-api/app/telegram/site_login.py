"""Site links from the bot that sign the person in on arrival.

`with_site_login(url, …)` appends `#wwc-login=<challenge>.<nonce>` (see
content_access.service.create_bot_login). The site redeems it on any page
(src/components/BotLogin.astro). If the login can't be created, the plain URL
is returned — the link still works, the person just signs in the usual way.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from app.content_access.service import BOT_LOGIN_FRAGMENT, create_bot_login

logger = logging.getLogger(__name__)

_SITE_SUFFIX = ".wwc.best"


def _is_site(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "wwc.best" or host.endswith(_SITE_SUFFIX)


async def with_site_login(url: str, *, tenant_id: str, telegram_user_id: int | None) -> str:
    if not url or telegram_user_id is None or not _is_site(url):
        return url
    parts = urlsplit(url)
    return_to = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        token = await create_bot_login(tenant_id, telegram_user_id=int(telegram_user_id), return_to=return_to)
    except Exception:
        logger.warning("bot_login_link_failed", exc_info=True)
        return url
    base = url.split("#", 1)[0]
    return f"{base}#{BOT_LOGIN_FRAGMENT}={token}"
