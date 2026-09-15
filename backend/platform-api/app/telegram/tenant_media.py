"""Publish tenant media only as HTTPS URLs on our media host.

Never pass through Drive, wwc.best, localhost, package URLs, or another tenant.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.settings import get_settings

SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
LOCAL_MEDIA_RE = re.compile(
    r"^media/([A-Za-z0-9][A-Za-z0-9._-]{0,79})/"
    r"([A-Za-z0-9][A-Za-z0-9._-]{0,79})/"
    r"([A-Za-z0-9][A-Za-z0-9._-]{0,79})$"
)
HTTP_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
FORBIDDEN_HOST_FRAGMENTS = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "wwc.best",
    "drive.google",
    "docs.google",
    "googleusercontent",
    "googleapis.com",
    "duckdns.org",
)


def is_safe_path_segment(value: str) -> bool:
    if not value or not SEGMENT_RE.fullmatch(value):
        return False
    lowered = value.lower()
    if ".." in value or value.startswith(".") or value.endswith("."):
        return False
    if any(token in lowered for token in ("http:", "https:", "://")):
        return False
    return True


def _forbidden_host(host: str) -> bool:
    lowered = (host or "").lower().rstrip(".")
    return any(fragment in lowered for fragment in FORBIDDEN_HOST_FRAGMENTS)


def normalize_media_base_url(raw: str | None) -> str | None:
    text = str(raw or "").strip().rstrip("/")
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    if parsed.query or parsed.fragment:
        return None
    if _forbidden_host(parsed.hostname or ""):
        return None
    path = parsed.path or ""
    if ".." in path or "\\" in path:
        return None
    return text


def published_media_url(
    *,
    tenant_id: str,
    sku: str,
    filename: str,
    base_url: str | None = None,
) -> str | None:
    base = normalize_media_base_url(
        base_url if base_url is not None else get_settings().platform_tenant_media_base_url
    )
    if not base:
        return None
    if not is_safe_path_segment(tenant_id):
        return None
    if not is_safe_path_segment(sku):
        return None
    if not is_safe_path_segment(filename):
        return None
    url = f"{base}/{tenant_id}/{sku}/{filename}"
    parsed = urlparse(url)
    if parsed.scheme != "https" or _forbidden_host(parsed.hostname or ""):
        return None
    expected_suffix = f"/{tenant_id}/{sku}/{filename}"
    if not parsed.path.endswith(expected_suffix):
        return None
    if parsed.path.count("/") < 3:
        return None
    return url


def filename_from_local_ref(raw: str | None, *, tenant_id: str, sku: str) -> str | None:
    """Accept a bare filename or media/{tenant}/{sku}/{file}. Never an http(s) URL."""
    text = str(raw or "").strip().replace("\\", "/")
    if not text or "?" in text or "#" in text:
        return None
    if "://" in text or text.lower().startswith(("http:", "https:")):
        return None
    if is_safe_path_segment(text):
        return text
    match = LOCAL_MEDIA_RE.fullmatch(text)
    if not match:
        return None
    ref_tenant, ref_sku, filename = match.groups()
    if ref_tenant != tenant_id or ref_sku != sku:
        return None
    if not is_safe_path_segment(filename):
        return None
    return filename


def _filename_from_media(media: dict[str, Any], *, tenant_id: str, sku: str) -> str | None:
    for key in ("filename", "file"):
        raw = str(media.get(key) or "").strip()
        if raw:
            return raw
    for key in ("relative_path", "url", "photo_url"):
        parsed = filename_from_local_ref(media.get(key), tenant_id=tenant_id, sku=sku)
        if parsed:
            return parsed
    return None


def _sku_from_response(core_response: dict[str, Any], media: dict[str, Any]) -> str | None:
    product = core_response.get("product") if isinstance(core_response.get("product"), dict) else {}
    context = core_response.get("context") if isinstance(core_response.get("context"), dict) else {}
    for raw in (product.get("sku"), media.get("sku"), context.get("last_product_sku")):
        sku = str(raw or "").strip()
        if sku:
            return sku
    return None


def resolve_delivery_photo_url(
    core_response: dict[str, Any],
    *,
    tenant_id: str,
    base_url: str | None = None,
) -> str | None:
    media = core_response.get("media") if isinstance(core_response.get("media"), dict) else {}
    claimed_tenant = str(media.get("tenant_id") or "").strip()
    if claimed_tenant and claimed_tenant != tenant_id:
        return None
    sku = _sku_from_response(core_response, media)
    if not sku:
        return None
    filename = _filename_from_media(media, tenant_id=tenant_id, sku=sku)
    if not filename:
        return None
    return published_media_url(
        tenant_id=tenant_id,
        sku=sku,
        filename=filename,
        base_url=base_url,
    )


def sanitize_delivery_text(text: str, *, allowed_url: str | None = None) -> str:
    def _replace(match: re.Match[str]) -> str:
        url = match.group(0)
        if allowed_url and url.rstrip(".,);") == allowed_url:
            return url
        return ""

    cleaned = HTTP_URL_RE.sub(_replace, text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()
