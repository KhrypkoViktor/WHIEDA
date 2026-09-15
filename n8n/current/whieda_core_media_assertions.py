"""Shim: smoke scripts import parity helpers from platform-api."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "platform-api"))

from app.advisor.parity.media_assertions import (  # noqa: E402
    evaluate_media_for_case,
    media_is_empty,
    media_urls,
    product_sku,
)

__all__ = [
    "evaluate_media_for_case",
    "media_is_empty",
    "media_urls",
    "product_sku",
]
