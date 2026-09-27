"""Вход на сайт из мини-приложения Telegram — чтобы сайт открылся в основном браузере.

Кнопка бота «Открыть сайт в браузере» (после подтверждения входа) — не обычная
ссылка, а мини-приложение (web_app): обычную ссылку Telegram открывает во
встроенном браузере, где нет ни истории, ни сохранённого входа (владелец,
27.09.2026). Мини-приложение — страница сайта /tg-open/ — присылает сюда
Telegram.WebApp.initData, подписанный токеном бота; мы проверяем подпись по
правилам Telegram и выдаём одноразовый вход (#wwc-login=…, create_bot_login).
Страница открывает его через Telegram.WebApp.openLink — во внешнем браузере.

Проверка подписи (core.telegram.org/bots/webapps, «Validating data»):
secret = HMAC_SHA256(key="WebAppData", msg=bot_token);
hash   = hex(HMAC_SHA256(key=secret, msg=data_check_string)), где
data_check_string — все поля кроме hash, отсортированные, «key=value» через \\n.
Поле signature (Bot API 8.0, проверка третьими сторонами) у разных клиентов
то входит в строку, то нет — принимаем оба варианта.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse

from fastapi import HTTPException

from app.content_access.service import BOT_LOGIN_FRAGMENT, create_bot_login, sanitize_return_to
from app.db import fetch_all, tenant_connection
from app.telegram.bindings import resolve_secret_ref

INIT_DATA_MAX_AGE_SEC = 15 * 60
_MAX_INIT_DATA_LEN = 4096


def _check_string(fields: dict[str, str], *, drop: Iterable[str]) -> str:
    skip = set(drop)
    return "\n".join(f"{key}={fields[key]}" for key in sorted(fields) if key not in skip)


def verify_init_data(init_data: str, bot_tokens: Iterable[str], *, now: float | None = None,
                     max_age_sec: int = INIT_DATA_MAX_AGE_SEC) -> dict[str, Any]:
    """Telegram user из initData или HTTPException 401/400. Токены — все боты тенанта."""
    raw = str(init_data or "")
    if not raw or len(raw) > _MAX_INIT_DATA_LEN:
        raise HTTPException(status_code=400, detail={"error": "invalid_init_data"})
    fields = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=False))
    received = fields.get("hash", "")
    if not received:
        raise HTTPException(status_code=400, detail={"error": "invalid_init_data"})
    candidates = [_check_string(fields, drop={"hash"}), _check_string(fields, drop={"hash", "signature"})]
    valid = False
    for token in bot_tokens:
        if not token:
            continue
        secret = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
        for check in candidates:
            expected = hmac.new(secret, check.encode("utf-8"), hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, received):
                valid = True
                break
        if valid:
            break
    if not valid:
        raise HTTPException(status_code=401, detail={"error": "init_data_signature_invalid"})
    try:
        auth_date = int(fields.get("auth_date") or 0)
    except ValueError:
        auth_date = 0
    current = time.time() if now is None else now
    if auth_date <= 0 or current - auth_date > max_age_sec or auth_date - current > 60:
        raise HTTPException(status_code=401, detail={"error": "init_data_expired"})
    try:
        user = json.loads(fields.get("user") or "{}")
    except ValueError:
        user = {}
    user_id = user.get("id") if isinstance(user, dict) else None
    if not isinstance(user_id, int) or user_id <= 0:
        raise HTTPException(status_code=400, detail={"error": "init_data_user_missing"})
    return user


async def tenant_bot_tokens(tenant_id: str) -> list[str]:
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select distinct to_jsonb(b)->>'bot_token_ref' as ref
            from tenant_bot_bindings b
            where b.tenant_id = %s and b.status = 'active'
            """,
            (tenant_id,),
        )
    tokens: list[str] = []
    for row in rows:
        ref = str(row.get("ref") or "")
        try:
            token = resolve_secret_ref(ref)
        except HTTPException:
            continue
        if token and token not in tokens:
            tokens.append(token)
    return tokens


async def webapp_login_url(tenant_id: str, *, init_data: str, return_to: str) -> str:
    """Абсолютная ссылка на страницу сайта с одноразовым входом во фрагменте."""
    safe = sanitize_return_to(return_to)
    if not safe.startswith("https://"):
        raise HTTPException(status_code=400, detail={"error": "invalid_return_to"})
    user = verify_init_data(init_data, await tenant_bot_tokens(tenant_id))
    parts = urlparse(safe)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    token = await create_bot_login(tenant_id, telegram_user_id=int(user["id"]), return_to=path)
    return f"https://{parts.netloc}{path}#{BOT_LOGIN_FRAGMENT}={token}"
