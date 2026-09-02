from __future__ import annotations

import re


def mask_contact(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 4:
        return "***"
    if "@" in text:
        local, _, domain = text.partition("@")
        if len(local) <= 2:
            masked_local = "*"
        else:
            masked_local = local[:2] + "***"
        return f"{masked_local}@{domain}"
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 6:
        return f"{text[:2]}***{text[-2:]}"
    return text[:2] + "***"
