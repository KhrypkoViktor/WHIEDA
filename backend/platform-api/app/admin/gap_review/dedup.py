"""Dedup key builder for advisor gap review projections."""

from __future__ import annotations

import hashlib


def build_dedup_key(
    *,
    gap_kind: str,
    question_normalized: str,
    detected_product: str | None,
) -> str:
    product = str(detected_product or "").strip()
    raw = f"{gap_kind}|{question_normalized}|{product}"
    if len(raw) <= 160:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"gap:{digest[:48]}"
