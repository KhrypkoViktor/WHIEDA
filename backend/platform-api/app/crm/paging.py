"""Keyset pagination of WWC CRM lists (people, pipeline columns, a card's history).

A cursor is opaque for the site: base64url of ``{"k": <list kind>, "v": [...]}``
with the sort key of the last row returned. Decoding checks the kind and the
type of every value, so a cursor of another list or a hand-made one is a 400
``invalid_cursor``, never a 500 or an SQL error. Pure functions, no I/O.

A cursor travels in the URL, and URLs reach access logs. So it never holds a
person's data: the «by name» cursor is only the last card's id, and the
server reads that card's name back (``queries.list_contacts``).
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import date, datetime
from typing import Any

from app.crm.rules import CrmRuleError

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
# GET /contacts without limit/cursor/sort/tag — the v1 site (until the v2 app ships):
# up to 300 people in one answer and the old «contacts» key next to «items».
LEGACY_LIMIT = 300
PIPELINE_FIRST_PAGE = 20

CONTACT_SORTS: tuple[str, ...] = ("updated", "name", "next")

# list kind → value types of its sort key (the last one is always the row id).
SHAPES: dict[str, tuple[str, ...]] = {
    "updated": ("ts", "uuid"),
    "name": ("uuid",),  # no name in a URL: the server looks the card's name up
    "next": ("date?", "uuid"),
    "pipeline": ("int", "ts", "uuid"),
    "activities": ("ts", "uuid"),
}
_TEXT_MAX = 300


def clamp_limit(value: Any, default: int = DEFAULT_LIMIT) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise CrmRuleError("invalid_limit") from None
    return max(1, min(MAX_LIMIT, number))


def _encode_value(kind: str, value: Any) -> Any:
    if kind in ("ts", "date", "date?"):
        return value.isoformat() if value is not None else None
    if kind == "uuid":
        return str(value)
    if kind == "int":
        return int(value)
    return str(value)


def encode_cursor(kind: str, values: list[Any] | tuple[Any, ...]) -> str:
    shape = SHAPES[kind]
    if len(values) != len(shape):
        raise ValueError(f"cursor {kind} needs {len(shape)} values")
    raw = json.dumps({"k": kind, "v": [_encode_value(t, v) for t, v in zip(shape, values)]}, ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_value(kind: str, value: Any) -> Any:
    if kind == "date?" and value is None:
        return None
    if kind != "int" and not isinstance(value, str):
        # uuid.UUID(5) raises AttributeError, fromisoformat(5) TypeError: refuse early.
        raise ValueError("not a string")
    if kind == "ts":
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("naive timestamp")
        return parsed
    if kind == "date?":
        return None if value is None else date.fromisoformat(value)
    if kind == "uuid":
        return str(uuid.UUID(value))
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("not an int")
        return value
    if not isinstance(value, str) or len(value) > _TEXT_MAX:
        raise ValueError("bad text")
    return value


def decode_cursor(raw: str | None, kind: str) -> list[Any] | None:
    """None — the first page; otherwise the typed sort key of the last row seen."""
    if raw is None or raw == "":
        return None
    try:
        text = str(raw)
        padded = text + "=" * (-len(text) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if not isinstance(data, dict) or data.get("k") != kind:
            raise ValueError("other list")
        values = data.get("v")
        shape = SHAPES[kind]
        if not isinstance(values, list) or len(values) != len(shape):
            raise ValueError("bad values")
        return [_decode_value(t, v) for t, v in zip(shape, values)]
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, binascii.Error):
        raise CrmRuleError("invalid_cursor") from None


def page(rows: list[dict[str, Any]], limit: int, kind: str, key: Any) -> tuple[list[dict[str, Any]], str | None]:
    """``rows`` were read with ``limit + 1``: the extra row only says «there is more»."""
    items = rows[:limit]
    if len(rows) <= limit or not items:
        return items, None
    return items, encode_cursor(kind, key(items[-1]))
