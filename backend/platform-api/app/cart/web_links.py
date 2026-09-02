"""Public calculator / snapshot URLs. Opaque ids only — never SKU, owner, or prices."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse

CALCULATOR_ORIGIN = "https://wwc.best"
CALCULATOR_PATH = "/price/"
CALCULATOR_WEB_URL = f"{CALCULATOR_ORIGIN}{CALCULATOR_PATH}?calc=1"


def canonical_calculator_path() -> str:
    return f"{CALCULATOR_PATH}?calc=1"


def calculator_web_url(*, cart_session_id: str | None = None) -> str:
    if not cart_session_id:
        return CALCULATOR_WEB_URL
    query = urlencode({"calc": "1", "cart": str(cart_session_id)})
    return f"{CALCULATOR_ORIGIN}{CALCULATOR_PATH}?{query}"


def snapshot_share_path(token: str) -> str:
    return f"{CALCULATOR_PATH}?{urlencode({'snap': str(token)})}"


def parse_cart_launch(url: str) -> dict[str, str | None]:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=False)
    cart = (params.get("cart") or [None])[0] or None
    snap = (params.get("snap") or [None])[0] or None
    if cart:
        return {"cart_session_id": cart, "snapshot_token": None}
    return {"cart_session_id": None, "snapshot_token": snap}
