"""Ключи доступа (WebAuthn / passkey) как способ входа на сайт — таблицы V35 (08.11.2026).

Человек в ядре по-прежнему = telegram_user_id: ключ регистрируется из кабинета под действующей сессией,
вход ключом создаёт ту же сессию и ту же cookie, что и вход через Telegram. Открытый ключ и счётчик
подписей лежат в ``passkey_credentials``, секрет остаётся на устройстве человека.

Защита:
  * challenge одноразовый и живёт 10 минут: строка ``passkey_challenges`` гасится атомарно
    (``update … where used_at is null returning``) своей транзакцией до проверки подписи — повторить ответ
    нельзя, даже если проверка не прошла;
  * регистрация принимает только challenge, выданный этому же человеку (``telegram_user_id`` в строке);
  * origin и RP id берутся из настроек (точный список, без масок): ``PLATFORM_WEBAUTHN_*``;
  * любая ошибка проверки наружу — один код ``passkey_failed`` (401), без подробностей: чужой и неизвестный
    ключ неотличимы; подробность — в журнал (без ключей и подписей);
  * уменьшившийся счётчик подписей (клон ключа) библиотека считает ошибкой — вход отклоняется.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import webauthn
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.content_access.devices import device_label
from app.db import fetch_all, fetch_one, tenant_connection
from app.login_identities.rules import error, sha256_hex
from app.settings import get_settings

logger = logging.getLogger("whieda.login_identities.passkeys")

CHALLENGE_TTL = timedelta(minutes=10)
LABEL_MAX_LEN = 60
# Сколько ключей может быть у одного человека: телефон, компьютер, запасной — с запасом.
MAX_PASSKEYS_PER_PERSON = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def user_handle(tenant_id: str, telegram_user_id: int) -> bytes:
    """Стабильный непрозрачный user.id (32 байта): telegram_user_id в нём не читается."""
    return hashlib.sha256(f"wwc-passkey:{tenant_id}:{int(telegram_user_id)}".encode("utf-8")).digest()


def passkey_label(user_agent: str | None) -> str:
    """«iPhone», «Windows», «Android» — только система из подписи устройства."""
    return device_label(user_agent).split(" · ", 1)[0][:LABEL_MAX_LEN]


def _challenge_of(credential: dict[str, Any]) -> bytes:
    """challenge из clientDataJSON ответа браузера (base64url) — по нему находим выданную строку."""
    try:
        client_data = json.loads(base64url_to_bytes(str(credential["response"]["clientDataJSON"])))
        return base64url_to_bytes(str(client_data["challenge"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise error("passkey_failed", reason="client_data") from exc


def _credential_dict(body: dict[str, Any]) -> dict[str, Any]:
    credential = body.get("credential") if isinstance(body.get("credential"), dict) else body
    if not isinstance(credential, dict) or not credential.get("id") or not isinstance(credential.get("response"), dict):
        raise error("passkey_failed", reason="shape")
    return {k: v for k, v in credential.items() if k not in ("label", "challenge_id", "return_to")}


def _challenge_id_of(body: dict[str, Any]) -> str | None:
    """challenge_id из тела (его отдают options): если прислан, обязан совпасть со строкой challenge."""
    raw = str(body.get("challenge_id") or "").strip()
    if not raw:
        return None
    try:
        return str(uuid.UUID(raw))
    except ValueError as exc:
        raise error("passkey_failed", reason="challenge_id") from exc


def _options_json(options: Any) -> dict[str, Any]:
    return json.loads(webauthn.options_to_json(options))


async def _store_challenge(
    conn: Any, tenant_id: str, *, kind: str, challenge: bytes, telegram_user_id: int | None, host: str
) -> str:
    row = await fetch_one(
        conn,
        """
        insert into passkey_challenges (tenant_id, kind, challenge_hash, challenge, telegram_user_id, host, expires_at)
        values (%s, %s, %s, %s, %s, %s, %s)
        returning challenge_id
        """,
        (tenant_id, kind, sha256_hex(bytes_to_base64url(challenge)), challenge, telegram_user_id, host[:200], _now() + CHALLENGE_TTL),
    )
    return str(row["challenge_id"])


async def _take_challenge(
    conn: Any, tenant_id: str, *, kind: str, challenge: bytes, telegram_user_id: int | None, challenge_id: str | None = None
) -> bytes:
    """Атомарно забрать challenge (одноразово, не просрочен, свой тип и свой человек) или passkey_failed."""
    row = await fetch_one(
        conn,
        """
        update passkey_challenges set used_at = now()
        where tenant_id = %s and challenge_hash = %s and kind = %s and used_at is null and expires_at > now()
          and telegram_user_id is not distinct from %s
          and (%s::uuid is null or challenge_id = %s::uuid)
        returning challenge
        """,
        (tenant_id, sha256_hex(bytes_to_base64url(challenge)), kind, telegram_user_id, challenge_id, challenge_id),
    )
    if not row:
        raise error("passkey_failed", reason="challenge")
    return bytes(row["challenge"])


async def count_passkeys(conn: Any, tenant_id: str, telegram_user_id: int) -> int:
    row = await fetch_one(
        conn,
        "select count(*) as n from passkey_credentials where tenant_id = %s and telegram_user_id = %s and revoked_at is null",
        (tenant_id, int(telegram_user_id)),
    )
    return int(row["n"]) if row else 0


# ---- регистрация (под сессией) --------------------------------------------------------------------


async def registration_options(tenant_id: str, *, telegram_user_id: int, host: str = "") -> dict[str, Any]:
    settings = get_settings()
    async with tenant_connection(tenant_id) as conn:
        if await count_passkeys(conn, tenant_id, telegram_user_id) >= MAX_PASSKEYS_PER_PERSON:
            raise error("passkey_failed", reason="too_many")
        name_row = await fetch_one(
            conn,
            "select display_name from lead_actors where tenant_id = %s and telegram_user_id = %s limit 1",
            (tenant_id, int(telegram_user_id)),
        )
        existing = await fetch_all(
            conn,
            "select credential_id from passkey_credentials where tenant_id = %s and telegram_user_id = %s and revoked_at is null",
            (tenant_id, int(telegram_user_id)),
        )
        display_name = str((name_row or {}).get("display_name") or "").strip() or "WWC"
        challenge = secrets.token_bytes(32)
        options = webauthn.generate_registration_options(
            rp_id=settings.platform_webauthn_rp_id,
            rp_name=settings.platform_webauthn_rp_name,
            user_id=user_handle(tenant_id, telegram_user_id),
            user_name=display_name,
            user_display_name=display_name,
            challenge=challenge,
            authenticator_selection=AuthenticatorSelectionCriteria(
                # Ключ, который находится без ввода имени (discoverable credential).
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=bytes(r["credential_id"])) for r in existing],
        )
        challenge_id = await _store_challenge(
            conn, tenant_id, kind="passkey_reg", challenge=challenge, telegram_user_id=int(telegram_user_id), host=host
        )
    return {**_options_json(options), "challenge_id": challenge_id}


async def register(
    tenant_id: str, *, telegram_user_id: int, body: dict[str, Any], user_agent: str | None = None
) -> dict[str, Any]:
    """Проверить ответ браузера и сохранить ключ: {id, label}. Ошибки: passkey_failed."""
    settings = get_settings()
    credential = _credential_dict(body)
    challenge = _challenge_of(credential)
    challenge_id = _challenge_id_of(body)
    label = str(body.get("label") or "").strip()[:LABEL_MAX_LEN] or passkey_label(user_agent)
    # Challenge гасится отдельной транзакцией и ДО проверки: неудачная попытка его не возвращает.
    async with tenant_connection(tenant_id) as conn:
        expected = await _take_challenge(
            conn, tenant_id, kind="passkey_reg", challenge=challenge, telegram_user_id=int(telegram_user_id),
            challenge_id=challenge_id,
        )
    try:
        verified = webauthn.verify_registration_response(
            credential=credential,
            expected_challenge=expected,
            expected_rp_id=settings.platform_webauthn_rp_id,
            expected_origin=list(settings.parsed_webauthn_origins()),
            require_user_verification=False,
        )
    except Exception as exc:  # библиотека бросает набор своих исключений; наружу — один код
        logger.info(
            "login_passkey_register_rejected",
            extra={"tenant_id": tenant_id, "telegram_user_id": int(telegram_user_id), "reason": type(exc).__name__},
        )
        raise error("passkey_failed", reason="verify") from exc
    transports = credential["response"].get("transports")
    transports = [str(t)[:20] for t in transports][:8] if isinstance(transports, list) else []
    async with tenant_connection(tenant_id) as conn:
        try:
            row = await fetch_one(
                conn,
                """
                insert into passkey_credentials
                  (tenant_id, telegram_user_id, credential_id, public_key, sign_count, transports, label)
                values (%s, %s, %s, %s, %s, %s, %s)
                returning passkey_id
                """,
                (
                    tenant_id, int(telegram_user_id), verified.credential_id, verified.credential_public_key,
                    int(verified.sign_count), transports, label,
                ),
            )
        except psycopg.errors.UniqueViolation as exc:
            # Этот ключ уже зарегистрирован (у этого или другого человека): второй раз не принимаем.
            raise error("passkey_failed", reason="duplicate") from exc
    passkey_id = str(row["passkey_id"])
    logger.info(
        "login_passkey_registered",
        extra={"tenant_id": tenant_id, "telegram_user_id": int(telegram_user_id), "passkey_id": passkey_id},
    )
    return {"id": passkey_id, "label": label}


# ---- вход (без сессии) ----------------------------------------------------------------------------


async def authentication_options(tenant_id: str, *, host: str = "") -> dict[str, Any]:
    settings = get_settings()
    challenge = secrets.token_bytes(32)
    # allowCredentials пустой: браузер сам предлагает ключи этого сайта (discoverable credentials).
    options = webauthn.generate_authentication_options(
        rp_id=settings.platform_webauthn_rp_id,
        challenge=challenge,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    async with tenant_connection(tenant_id) as conn:
        challenge_id = await _store_challenge(
            conn, tenant_id, kind="passkey_auth", challenge=challenge, telegram_user_id=None, host=host
        )
    # challenge_id — для сайта, который хочет вернуть его в verify; сопоставление держится на самом challenge,
    # а не на cookie браузера.
    return {**_options_json(options), "challenge_id": challenge_id}


async def authenticate(tenant_id: str, *, body: dict[str, Any]) -> dict[str, Any]:
    """Проверить подпись и счётчик; {telegram_user_id, passkey_id}. Ошибки: passkey_failed (401)."""
    settings = get_settings()
    credential = _credential_dict(body)
    challenge = _challenge_of(credential)
    challenge_id = _challenge_id_of(body)
    try:
        credential_id = base64url_to_bytes(str(credential.get("rawId") or credential["id"]))
    except (ValueError, TypeError) as exc:
        raise error("passkey_failed", reason="credential_id") from exc
    # Challenge гасится отдельной транзакцией и ДО проверки подписи (повторить ответ нельзя).
    async with tenant_connection(tenant_id) as conn:
        expected = await _take_challenge(
            conn, tenant_id, kind="passkey_auth", challenge=challenge, telegram_user_id=None, challenge_id=challenge_id
        )
        stored = await fetch_one(
            conn,
            """
            select passkey_id, telegram_user_id, public_key, sign_count
            from passkey_credentials
            where tenant_id = %s and credential_id = %s and revoked_at is null
            limit 1
            """,
            (tenant_id, credential_id),
        )
    if not stored:
        logger.info("login_passkey_unknown_credential", extra={"tenant_id": tenant_id})
        raise error("passkey_failed", reason="unknown_credential")
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential,
            expected_challenge=expected,
            expected_rp_id=settings.platform_webauthn_rp_id,
            expected_origin=list(settings.parsed_webauthn_origins()),
            credential_public_key=bytes(stored["public_key"]),
            credential_current_sign_count=int(stored["sign_count"]),
            require_user_verification=False,
        )
    except Exception as exc:
        logger.info(
            "login_passkey_rejected",
            extra={"tenant_id": tenant_id, "passkey_id": str(stored["passkey_id"]), "reason": type(exc).__name__},
        )
        raise error("passkey_failed", reason="verify") from exc
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            """
            update passkey_credentials set sign_count = %s, last_used_at = now()
            where tenant_id = %s and passkey_id = %s returning passkey_id
            """,
            (int(verified.new_sign_count), tenant_id, stored["passkey_id"]),
        )
    logger.info(
        "login_passkey_signed_in",
        extra={"tenant_id": tenant_id, "telegram_user_id": int(stored["telegram_user_id"]), "passkey_id": str(stored["passkey_id"])},
    )
    return {"telegram_user_id": int(stored["telegram_user_id"]), "passkey_id": str(stored["passkey_id"])}


# ---- кабинет: список и удаление ---------------------------------------------------------------------


async def list_passkeys(tenant_id: str, telegram_user_id: int) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select passkey_id, label, created_at, last_used_at
            from passkey_credentials
            where tenant_id = %s and telegram_user_id = %s and revoked_at is null
            order by created_at
            """,
            (tenant_id, int(telegram_user_id)),
        )
    return [
        {
            "id": str(r["passkey_id"]),
            "label": str(r.get("label") or "") or "Ключ доступа",
            "created_at": r["created_at"].isoformat(),
            "last_used_at": r["last_used_at"].isoformat() if r.get("last_used_at") else None,
        }
        for r in rows
    ]


async def delete_passkey(tenant_id: str, *, telegram_user_id: int, passkey_id: str) -> bool:
    """Отозвать свой ключ; чужой или несуществующий — False (ручка отвечает 404)."""
    try:
        uuid.UUID(str(passkey_id))
    except ValueError:
        return False
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update passkey_credentials set revoked_at = now()
            where tenant_id = %s and passkey_id = %s::uuid and telegram_user_id = %s and revoked_at is null
            returning passkey_id
            """,
            (tenant_id, str(passkey_id), int(telegram_user_id)),
        )
    if row:
        logger.info(
            "login_passkey_revoked",
            extra={"tenant_id": tenant_id, "telegram_user_id": int(telegram_user_id), "passkey_id": str(passkey_id)},
        )
    return bool(row)
