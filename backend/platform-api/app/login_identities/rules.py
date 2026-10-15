"""Чистые правила входа через Google/Max по коду владельца (без базы и сети)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

PROVIDERS = ("google", "max")
# Telegram — всегда и не отвязывается: на нём держится человек в ядре.
REVOCABLE_PROVIDERS = frozenset(PROVIDERS)

CODE_DIGITS = 6
CODE_TTL = timedelta(hours=24)
PENDING_TTL = timedelta(minutes=15)
# Одноразовая ссылка входа для браузера после Google/Max (create_bot_login).
BROWSER_LOGIN_TTL_MINUTES = 10
# Попыток ввести код на одну чужую личность за окно PENDING_TTL: код шестизначный,
# без предела его можно было бы перебрать подписанным initData Max.
MAX_CODE_ATTEMPTS = 5

GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPE = "openid email profile"

LINK_FRAGMENT = "wwc-link"
LINK_ERROR_FRAGMENT = "wwc-link-error"

# Браузер, начавший Google-вход: cookie на семью .wwc.best (или ключ, который сайт держит в sessionStorage
# для хостов вне семьи). Callback и «ввести код» принимаются только из этого браузера.
OAUTH_COOKIE_NAME = "wwc_oauth"
OAUTH_COOKIE_TTL_SEC = int(PENDING_TTL.total_seconds())
BROWSER_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class LoginIdentityError(Exception):
    """Ошибка с машинным кодом для ручек и бота (``code``) и подробностями без секретов."""

    def __init__(self, code: str, *, status_code: int = 400, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.details = details


ERROR_STATUS = {
    "code_invalid": 400,
    "code_expired": 410,
    "code_used": 409,
    "code_locked": 429,
    "identity_taken": 409,
    "pending_invalid": 404,
    "pending_expired": 410,
    "google_disabled": 503,
    "google_denied": 400,
    "google_state_invalid": 400,
    "google_token_invalid": 400,
    "email_unverified": 400,
    "invalid_provider": 400,
    "provider_not_revocable": 400,
    "invalid_return_to": 400,
    "forbidden": 403,
    "partner_not_found": 404,
    "partner_ambiguous": 409,
    "oauth_browser_mismatch": 400,
    "browser_key_required": 400,
    "code_required": 400,
    "code_unavailable": 503,
}


def error(code: str, **details: Any) -> LoginIdentityError:
    return LoginIdentityError(code, status_code=ERROR_STATUS.get(code, 400), **details)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---- код владельца --------------------------------------------------------------------------------

_CODE_SEPARATORS_RE = re.compile(r"[\s \-–—.]+")


def generate_code() -> str:
    """Шесть цифр, ведущие нули возможны («048 213»)."""
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def normalize_code(raw: Any) -> str | None:
    """«483 921», «483-921», «483921» → «483921»; всё остальное — None."""
    value = _CODE_SEPARATORS_RE.sub("", str(raw or "").strip())
    if len(value) != CODE_DIGITS or not value.isdigit():
        return None
    return value


def format_code(code: str) -> str:
    return f"{code[:3]} {code[3:]}"


def code_hash(tenant_id: str, code: str, secret: str | None = None) -> str:
    """Хеш кода с тенантом; с серверным секретом — HMAC-SHA256 (6 цифр иначе перебираются по дампу базы).

    Без секрета (локальная разработка) — обычный sha256; какой секрет брать, решает service._code_hash."""
    message = f"{tenant_id}:{code}"
    if secret:
        return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return sha256_hex(message)


def new_browser_key() -> str:
    return secrets.token_urlsafe(32)


def normalize_browser_key(raw: Any) -> str | None:
    value = str(raw or "").strip()
    return value if BROWSER_KEY_RE.fullmatch(value) else None


def browser_key_matches(provided: Any, stored_hash: Any) -> bool:
    key = normalize_browser_key(provided)
    if not key or not stored_hash:
        return False
    return secrets.compare_digest(sha256_hex(key), str(stored_hash))


def mask_email(email: Any) -> str:
    """evgenia@example.com → e***a@example.com (экран «привязываем Google …»)."""
    value = str(email or "").strip()
    if "@" not in value:
        return ""
    local, domain = value.rsplit("@", 1)
    if len(local) <= 2:
        return f"{local[:1]}***@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


def code_expires_at(now: datetime | None = None) -> datetime:
    return (now or utcnow()) + CODE_TTL


def pending_expires_at(now: datetime | None = None) -> datetime:
    return (now or utcnow()) + PENDING_TTL


def normalize_provider(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value not in PROVIDERS:
        raise error("invalid_provider")
    return value


# ---- Google id_token ------------------------------------------------------------------------------


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Полезная нагрузка JWT без проверки подписи.

    id_token приходит в ответе на обмен кода по TLS прямо от Google с нашим client secret —
    подпись в этом случае Google проверять не требует (OpenID Connect, «Obtain user information
    from the ID token»); проверяем iss, aud, exp, nonce и email_verified.
    """
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise error("google_token_invalid")
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, UnicodeDecodeError) as exc:
        raise error("google_token_invalid") from exc
    if not isinstance(payload, dict):
        raise error("google_token_invalid")
    return payload


def check_id_token_claims(
    claims: dict[str, Any], *, client_id: str, nonce_hash: str, now: datetime | None = None
) -> dict[str, Any]:
    """{sub, email, name} из проверенных claims или LoginIdentityError."""
    current = now or utcnow()
    if str(claims.get("iss") or "") not in GOOGLE_ISSUERS:
        raise error("google_token_invalid", reason="iss")
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if client_id not in [str(a) for a in audiences]:
        raise error("google_token_invalid", reason="aud")
    try:
        exp = int(claims.get("exp") or 0)
    except (TypeError, ValueError):
        exp = 0
    if exp <= int(current.timestamp()):
        raise error("google_token_invalid", reason="exp")
    received_nonce = str(claims.get("nonce") or "")
    if not received_nonce or not secrets.compare_digest(sha256_hex(received_nonce), nonce_hash):
        raise error("google_token_invalid", reason="nonce")
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise error("google_token_invalid", reason="sub")
    email = str(claims.get("email") or "").strip().lower() or None
    verified = claims.get("email_verified")
    if email and verified not in (True, "true"):
        raise error("email_unverified")
    name = str(claims.get("name") or "").strip() or None
    return {"sub": sub, "email": email, "name": name}


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge S256)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


# ---- кабинет: способы входа -----------------------------------------------------------------------

PROVIDER_TITLES = {"telegram": "Telegram", "google": "Google", "max": "Max"}


def method_label(provider: str, row: dict[str, Any] | None = None) -> str:
    row = row or {}
    if provider == "google":
        return str(row.get("email") or "").strip() or "Google"
    if provider == "max":
        return str(row.get("display_name") or "").strip() or "Max"
    return "Telegram"


def login_methods_payload(identities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Telegram всегда первый; дальше активные привязки в порядке появления."""
    methods: list[dict[str, Any]] = [{"provider": "telegram", "label": "Telegram", "linked_at": None}]
    for row in identities:
        linked_at = row.get("linked_at")
        methods.append(
            {
                "provider": str(row["provider"]),
                "label": method_label(str(row["provider"]), row),
                "linked_at": linked_at.isoformat() if hasattr(linked_at, "isoformat") else linked_at,
            }
        )
    return methods


# ---- команды владельца в Telegram-боте ------------------------------------------------------------

_CODE_CMD_RE = re.compile(r"^(?:код\s+входа|/logincode)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_METHODS_CMD_RE = re.compile(r"^(?:способы\s+входа|/loginmethods)\s+(.+)$", re.IGNORECASE | re.DOTALL)
_UNLINK_CMD_RE = re.compile(r"^(?:отвязать|/unlink)\s+(google|max)\s+(.+)$", re.IGNORECASE | re.DOTALL)


def parse_owner_command(text: str) -> tuple[str, str | None, str] | None:
    """('issue' | 'methods' | 'unlink', provider | None, target) или None, если это не команда."""
    value = " ".join(str(text or "").split())
    if not value:
        return None
    if match := _CODE_CMD_RE.fullmatch(value):
        return "issue", None, match.group(1).strip()
    if match := _METHODS_CMD_RE.fullmatch(value):
        return "methods", None, match.group(1).strip()
    if match := _UNLINK_CMD_RE.fullmatch(value):
        return "unlink", match.group(1).lower(), match.group(2).strip()
    return None


def is_owner_command_candidate(text: str) -> bool:
    return parse_owner_command(text) is not None
