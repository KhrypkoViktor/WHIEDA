#!/usr/bin/env python3
"""One-shot patch: parity corpus V2.2 expectations."""

from __future__ import annotations

import json
from pathlib import Path

CORPUS = Path(__file__).resolve().parent / "core_local_parity_cases_v2.jsonl"

CARD_MODES = frozenset({"structured_card", "structured_photo", "structured_video", "structured_certificate"})
SERVICE_MODES = frozenset(
    {
        "structured_business",
        "structured_business_faq",
        "structured_business_objection",
        "structured_price",
        "structured_promotion",
        "structured_event",
        "structured_community",
        "clarification",
        "knowledge_gap",
        "structured_comparison_layer",
        "structured_comparison",
        "structured_cart",
    }
)

CASE_PATCHES: dict[str, dict] = {
    "PARITY-P0-010": {
        "rationale": "Product card may include primary_image_url in media (WHIEDA UX).",
        "expected_media": {"photo": "allow", "video_count_min": 0, "document_count_min": 0},
    },
    "PARITY-P0-011": {
        "rationale": "PRO card may include photo in media payload.",
        "expected_media": {"photo": "allow", "video_count_min": 0, "document_count_min": 0},
    },
    "PARITY-P0-012": {
        "rationale": "BEM card may include photo in media payload.",
        "expected_media": {"photo": "allow", "video_count_min": 0, "document_count_min": 0},
    },
    "PARITY-P0-013": {
        "rationale": "Product card may include photo in media payload.",
        "expected_media": {"photo": "allow", "video_count_min": 0, "document_count_min": 0},
    },
    "PARITY-P0-014": {
        "rationale": "Product card may include photo in media payload.",
        "expected_media": {"photo": "allow", "video_count_min": 0, "document_count_min": 0},
    },
    "PARITY-P0-020": {
        "rationale": "Use structured zero-price assertion, not substring 0 BYN.",
        "must_not_contain": ["Traceback"],
        "price_assertions": {"forbid_zero_amounts": True},
    },
    "PARITY-P0-030": {
        "rationale": "Typo аtivator resolves to base Activator card, not clarification.",
        "expected_media": {"photo": "allow", "video_count_min": 0, "document_count_min": 0},
    },
    "PARITY-P0-064": {
        "rationale": "Bare activator on whieda tenant requires base vs PRO clarification.",
        "expected_mode": "clarification",
        "expected_product": None,
        "expected_context": {"last_product_name": None},
    },
    "PARITY-P0-072": {
        "rationale": "Unknown cart item returns clarification with explicit unknown name.",
        "expected_mode": "clarification",
        "must_contain": ["несуществующий"],
    },
    "PARITY-P0-080": {
        "rationale": "wwc.best + activator must not leak Acme; ambiguous base vs PRO.",
        "expected_mode": "clarification",
        "expected_product": None,
        "must_contain": ["PRO"],
        "must_not_contain": ["Acme", "Traceback"],
        "expected_context": {"last_product_name": None},
    },
    "PARITY-P0-081": {
        "rationale": "acme.test.local tenant resolves isolated Acme activator product.",
        "expected_media": {"photo": "allow", "video_count_min": 0, "document_count_min": 0},
    },
    "PARITY-P0-091": {
        "rationale": "Missing price returns structured_price with honest text, never zero.",
        "must_contain": ["не указана"],
        "must_not_contain": ["Traceback"],
        "price_assertions": {"forbid_zero_amounts": True},
    },
    "PARITY-P0-092": {
        "rationale": "Missing certificate returns structured_certificate with honest text.",
        "must_contain": ["не добавлен"],
    },
    "PARITY-P0-093": {
        "rationale": "Unknown product name uses knowledge_gap, not generic clarification.",
        "must_contain": ["уточните"],
    },
}


def main() -> None:
    lines = [ln for ln in CORPUS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    cases = [json.loads(ln) for ln in lines]
    by_id = {c["case_id"]: c for c in cases}

    for case_id, patch in CASE_PATCHES.items():
        if case_id not in by_id:
            raise SystemExit(f"missing case {case_id}")
        by_id[case_id].update(patch)

    for case in cases:
        mode = case.get("expected_mode")
        if case["case_id"] in CASE_PATCHES:
            continue
        media = case.get("expected_media") or {}
        if mode in CARD_MODES and media.get("photo") == "none":
            media["photo"] = "allow"
            case["expected_media"] = media
            case.setdefault("rationale", "Product card may include photo in media (allow).")
        if mode == "structured_photo" and media.get("photo") == "none":
            media["photo"] = "required"
            case["expected_media"] = media

    if "PARITY-P0-053" not in by_id:
        pass  # comparison→price covered by pytest + P0-050 pattern

    # Remove legacy false-positive zero price substring from all cases
    for case in cases:
        mnc = case.get("must_not_contain") or []
        if "0 BYN" in mnc:
            case["must_not_contain"] = [t for t in mnc if t != "0 BYN"]
            case.setdefault("price_assertions", {"forbid_zero_amounts": True})

    CORPUS.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
        encoding="utf-8",
    )
    p0 = sum(1 for c in cases if c.get("priority") == "P0")
    print(f"Patched {CORPUS.name}: {len(cases)} cases, P0={p0}")


if __name__ == "__main__":
    main()
