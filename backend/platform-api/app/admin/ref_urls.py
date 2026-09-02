"""Canonical public ref URLs for owner cabinet (WWC site contract)."""

from __future__ import annotations

WWC_PUBLIC_SITE_ORIGIN = "https://wwc.best"


def canonical_public_ref_url(ref_code: str | None) -> str | None:
    """Working public link: https://wwc.best/?ref={safe_ref_code}."""
    if not ref_code:
        return None
    safe = ref_code.strip().lower()
    if not safe:
        return None
    return f"{WWC_PUBLIC_SITE_ORIGIN}/?ref={safe}"
