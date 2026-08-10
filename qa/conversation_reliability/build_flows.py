#!/usr/bin/env python3
"""Generate whieda_conversation_flows_v1.jsonl (24+ flows, 80+ turns)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "whieda_conversation_flows_v1.jsonl"

MEDIA_NONE = {"photo": "none", "video_count_min": 0, "document_count_min": 0}
MEDIA_ALLOW = {"photo": "allow", "video_count_min": 0, "document_count_min": 0}
MEDIA_PHOTO = {"photo": "required", "video_count_min": 0, "document_count_min": 0}
MEDIA_VIDEO = {"photo": "allow", "video_count_min": 1, "document_count_min": 0}
MEDIA_DOC = {"photo": "allow", "video_count_min": 0, "document_count_min": 1}
PROHIB = ["Traceback"]


def flow(flow_id, name, priority, session, turns, terminal_context=None):
    return {
        "flow_id": flow_id,
        "name": name,
        "priority": priority,
        "session": session,
        "country": "BY",
        "terminal_context": terminal_context or turns[-1].get("expected_context"),
        "turns": turns,
    }


def t(n, inp, mode, product=None, must_contain=None, media=None, ctx=None, gap=None, must_not=None):
    return {
        "turn": n,
        "input": inp,
        "expected_mode": mode,
        "expected_product": product,
        "must_contain": must_contain or [],
        "must_not_contain": (must_not or []) + PROHIB,
        "expected_media": media or MEDIA_NONE,
        "expected_context": ctx or {},
        **({"expected_gap_kind": gap} if gap else {}),
    }


FLOWS = [
    flow(
        "CONV-F01-card-price",
        "card_to_price",
        "P0",
        "conv-f01",
        [
            t(1, "расскажи про активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(2, "цена", "structured_price", "Активатор", ["BYN"], MEDIA_NONE, {"last_product_name": "Активатор"}),
            t(3, "сколько pv", "structured_price", "Активатор", ["PV"], MEDIA_NONE, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F02-card-photo",
        "card_to_photo",
        "P0",
        "conv-f02",
        [
            t(1, "активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(2, "фото", "structured_photo", "Активатор", ["фото"], MEDIA_PHOTO, {"last_product_name": "Активатор"}),
            t(3, "цена", "structured_price", "Активатор", ["BYN"], MEDIA_NONE, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F03-card-video",
        "card_to_video",
        "P0",
        "conv-f03",
        [
            t(1, "активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(2, "видео", "structured_video", "Активатор", ["Видео"], MEDIA_VIDEO, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F04-card-cert",
        "card_to_certificate",
        "P0",
        "conv-f04",
        [
            t(1, "активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(2, "сертификат", "structured_certificate", "Активатор", ["Материалы"], MEDIA_DOC, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F05-card-details",
        "card_to_details",
        "P0",
        "conv-f05",
        [
            t(1, "активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(2, "подробнее", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F06-typo-card-price",
        "typo_activator_card_price",
        "P0",
        "conv-f06",
        [
            t(1, "ативатор", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(2, "цена", "structured_price", "Активатор", ["BYN"], MEDIA_NONE, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F07-ambiguous-select-price",
        "ambiguous_activator_select_price",
        "P0",
        "conv-f07",
        [
            t(1, "активатор", "clarification", None, ["PRO"], MEDIA_NONE, {"last_product_name": None}),
            t(2, "активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(3, "цена", "structured_price", "Активатор", ["BYN"], MEDIA_NONE, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F08-pasta-select-card",
        "pasta_select_card",
        "P0",
        "conv-f08",
        [
            t(1, "паста", "clarification", None, ["паст"], MEDIA_NONE, {}),
            t(2, "полын", "structured_card", "полын", ["полын"], MEDIA_ALLOW, {"last_product_name": "полын"}),
        ],
    ),
    flow(
        "CONV-F09-belt-select-photo",
        "belt_select_photo",
        "P0",
        "conv-f09",
        [
            t(1, "пояс", "clarification", None, ["пояс"], MEDIA_NONE, {}),
            t(2, "магнитный пояс", "structured_card", "пояс", ["пояс"], MEDIA_ALLOW, {"last_product_name": "пояс"}),
            t(3, "фото", "structured_photo", "пояс", ["фото"], MEDIA_PHOTO, {"last_product_name": "пояс"}),
        ],
    ),
    flow(
        "CONV-F10-red-clarify-price",
        "red_clarify_price",
        "P0",
        "conv-f10",
        [
            t(1, "красный", "clarification", None, ["эликсир"], MEDIA_NONE, {}),
            t(2, "цена красного эликсира", "structured_price", "Фохоу", ["BYN"], MEDIA_NONE, {"last_product_name": "Фохоу"}),
        ],
    ),
    flow(
        "CONV-F11-green-elixir",
        "green_elixir_price",
        "P0",
        "conv-f11",
        [
            t(1, "зеленый эликсир", "structured_price", "Саньцин", ["BYN"], MEDIA_NONE, {"last_product_name": "Саньцин"}),
            t(2, "фото", "structured_photo", "Саньцин", ["фото"], MEDIA_PHOTO, {"last_product_name": "Саньцин"}),
        ],
    ),
    flow(
        "CONV-F12-blue-elixir",
        "blue_elixir_price",
        "P0",
        "conv-f12",
        [
            t(1, "синий эликсир", "structured_price", "Драгоцен", ["BYN"], MEDIA_NONE, {"last_product_name": "Драгоцен"}),
            t(2, "карточка", "structured_card", "Драгоцен", ["Драгоцен"], MEDIA_ALLOW, {"last_product_name": "Драгоцен"}),
        ],
    ),
    flow(
        "CONV-F13-compare-left-price",
        "comparison_left_price",
        "P0",
        "conv-f13",
        [
            t(1, "сравни активатор клеток и активатор pro", "structured_comparison_layer", "Активатор", ["PRO"], MEDIA_NONE, {"last_product_name": "Активатор"}),
            t(2, "цена активатора клеток", "structured_price", "Активатор", ["BYN"], MEDIA_NONE, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F14-compare-right-photo",
        "comparison_right_photo",
        "P0",
        "conv-f14",
        [
            t(1, "сравни активатор клеток и активатор pro", "structured_comparison_layer", "Активатор", ["PRO"], MEDIA_NONE, {"last_product_name": "Активатор"}),
            t(2, "фото pro", "structured_photo", "PRO", ["фото"], MEDIA_PHOTO, {"last_product_name": "PRO"}),
        ],
    ),
    flow(
        "CONV-F15-switch-product-price",
        "explicit_product_switch",
        "P0",
        "conv-f15",
        [
            t(1, "активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(2, "цена сауны", "structured_price", "Ба-Гуа", ["Ба-Гуа"], MEDIA_NONE, {"last_product_name": "Ба-Гуа"}, must_not=["Magic"]),
            t(3, "цена", "structured_price", "Ба-Гуа", ["BYN"], MEDIA_NONE, {"last_product_name": "Ба-Гуа"}),
        ],
    ),
    flow(
        "CONV-F16-cart-three",
        "cart_three_products",
        "P0",
        "conv-f16",
        [
            t(1, "посчитай: активатор, бэм, ба-гуа", "structured_cart", None, ["PV"], MEDIA_NONE, {}),
            t(2, "цена активатора", "structured_price", "Активатор", ["BYN"], MEDIA_NONE, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F17-cart-unknown",
        "cart_unknown_item",
        "P0",
        "conv-f17",
        [
            t(1, "посчитай: несуществующий товар xyz", "clarification", None, ["несуществующий"], MEDIA_NONE, {}),
        ],
    ),
    flow(
        "CONV-F18-promo-price",
        "promotion_and_price",
        "P1",
        "conv-f18",
        [
            t(1, "какие акции сейчас", "structured_promotion", None, ["акци"], MEDIA_NONE, {}),
            t(2, "цена активатора", "structured_price", "Активатор", ["BYN"], MEDIA_NONE, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F19-no-photo",
        "no_photo_guided",
        "P0",
        "conv-f19",
        [
            t(1, "товар без фото", "structured_card", "без фото", ["без фото"], MEDIA_ALLOW, {"last_product_name": "без фото"}),
            t(2, "фото", "clarification", "без фото", [], MEDIA_NONE, {"last_product_name": "без фото"}, gap="missing_resource"),
        ],
    ),
    flow(
        "CONV-F20-no-cert",
        "no_certificate_guided",
        "P0",
        "conv-f20",
        [
            t(1, "товар без сертификата", "structured_card", "без сертификата", ["без сертификата"], MEDIA_ALLOW, {}),
            t(2, "сертификат", "structured_certificate", "без сертификата", ["не добавлен"], MEDIA_NONE, {}),
        ],
    ),
    flow(
        "CONV-F21-bare-price",
        "bare_price_unknown_followup",
        "P0",
        "conv-f21",
        [t(1, "цена", "clarification", None, [], MEDIA_NONE, {}, gap="unknown_followup")],
    ),
    flow(
        "CONV-F22-bare-video",
        "bare_video_unknown_followup",
        "P0",
        "conv-f22",
        [t(1, "видео", "clarification", None, [], MEDIA_NONE, {}, gap="unknown_followup")],
    ),
    flow(
        "CONV-F23-medical-boundary",
        "medical_boundary_after_product",
        "P0",
        "conv-f23",
        [
            t(1, "активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(2, "как лечить гипертонию схема лечения", "clarification", None, ["уточн"], MEDIA_NONE, {}, gap="medical_or_safety_boundary"),
        ],
    ),
    flow(
        "CONV-F24-external-topic",
        "external_topic_no_product_reuse",
        "P0",
        "conv-f24",
        [
            t(1, "активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(2, "курс доллара сегодня", "clarification", None, [], MEDIA_NONE, {}, gap="unsupported_topic", must_not=["1750"]),
        ],
    ),
    flow(
        "CONV-F25-session-isolation-a",
        "session_a_sets_context",
        "P0",
        "conv-f25a",
        [
            t(1, "активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
            t(2, "цена", "structured_price", "Активатор", ["BYN"], MEDIA_NONE, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F25-session-isolation-b",
        "session_b_no_inherited_context",
        "P0",
        "conv-f25b",
        [
            t(1, "цена", "clarification", None, [], MEDIA_NONE, {}, gap="unknown_followup"),
        ],
    ),
    flow(
        "CONV-F26-invalid-selection",
        "clarification_invalid_selection",
        "P0",
        "conv-f26",
        [
            t(1, "активатор", "clarification", None, ["PRO"], MEDIA_NONE, {}),
            t(2, "совсем другой товар xyz", "knowledge_gap", None, ["уточн"], MEDIA_NONE, {}),
            t(3, "активатор pro", "structured_card", "PRO", ["PRO"], MEDIA_ALLOW, {"last_product_name": "PRO"}),
        ],
    ),
    flow(
        "CONV-F27-cart-followup",
        "cart_then_clarification",
        "P1",
        "conv-f27",
        [
            t(1, "посчитай: активатор, активатор", "structured_cart", "Активатор", ["PV"], MEDIA_NONE, {}),
            t(2, "цена активатора", "structured_price", "Активатор", ["BYN"], MEDIA_NONE, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F28-wen-card-price",
        "wentun_card_price",
        "P1",
        "conv-f28",
        [
            t(1, "вэнтун", "structured_card", "Вэнтун", ["Вэнтун"], MEDIA_ALLOW, {"last_product_name": "Вэнтун"}),
            t(2, "цена", "structured_price", "Вэнтун", ["BYN"], MEDIA_NONE, {"last_product_name": "Вэнтун"}),
            t(3, "сертификат", "structured_certificate", "Вэнтун", ["Материалы"], MEDIA_DOC, {"last_product_name": "Вэнтун"}),
            t(4, "фото", "structured_photo", "Вэнтун", ["фото"], MEDIA_PHOTO, {"last_product_name": "Вэнтун"}),
        ],
    ),
    flow(
        "CONV-F29-peptide-card",
        "peptide_card_price",
        "P1",
        "conv-f29",
        [
            t(1, "соевый пептид", "structured_card", "пептид", ["пептид"], MEDIA_ALLOW, {"last_product_name": "пептид"}),
            t(2, "цена", "structured_price", "пептид", ["BYN"], MEDIA_NONE, {"last_product_name": "пептид"}),
        ],
    ),
    flow(
        "CONV-F30-bem-chain",
        "bem_card_photo_price",
        "P1",
        "conv-f30",
        [
            t(1, "что такое бэм", "structured_card", "Magic", ["Magic"], MEDIA_ALLOW, {"last_product_name": "Magic"}),
            t(2, "фото", "structured_photo", "Magic", ["фото"], MEDIA_PHOTO, {"last_product_name": "Magic"}),
            t(3, "цена", "structured_price", "Magic", ["BYN"], MEDIA_NONE, {"last_product_name": "Magic"}),
        ],
    ),
    flow(
        "CONV-F31-bag-chain",
        "bagua_card_price_photo",
        "P1",
        "conv-f31",
        [
            t(1, "сауна ба-гуа", "structured_card", "Ба-Гуа", ["Ба-Гуа"], MEDIA_ALLOW, {"last_product_name": "Ба-Гуа"}),
            t(2, "цена", "structured_price", "Ба-Гуа", ["BYN"], MEDIA_NONE, {"last_product_name": "Ба-Гуа"}),
            t(3, "фото", "structured_photo", "Ба-Гуа", ["фото"], MEDIA_PHOTO, {"last_product_name": "Ба-Гуа"}),
        ],
    ),
    flow(
        "CONV-F32-faq-pv",
        "business_faq_pv",
        "P1",
        "conv-f32",
        [
            t(1, "что такое pv", "structured_business_faq", None, ["PV"], MEDIA_NONE, {}),
            t(2, "активатор клеток", "structured_card", "Активатор", ["Активатор"], MEDIA_ALLOW, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F33-objection-mlm",
        "business_objection",
        "P1",
        "conv-f33",
        [
            t(1, "это mlm", "structured_business_objection", None, ["WHIEDA"], MEDIA_NONE, {}),
            t(2, "цена активатора", "structured_price", "Активатор", ["BYN"], MEDIA_NONE, {"last_product_name": "Активатор"}),
        ],
    ),
    flow(
        "CONV-F34-events",
        "events_then_product",
        "P1",
        "conv-f34",
        [
            t(1, "ближайшие события", "structured_event", None, ["эфир"], MEDIA_NONE, {}),
            t(2, "активатор pro", "structured_card", "PRO", ["PRO"], MEDIA_ALLOW, {"last_product_name": "PRO"}),
            t(3, "цена", "structured_price", "PRO", ["BYN"], MEDIA_NONE, {"last_product_name": "PRO"}),
        ],
    ),
    flow(
        "CONV-F35-community",
        "community_resource",
        "P1",
        "conv-f35",
        [
            t(1, "сообщество whieda", "structured_community", None, ["канал"], MEDIA_NONE, {}),
        ],
    ),
    flow(
        "CONV-F36-starter-basket",
        "starter_basket",
        "P1",
        "conv-f36",
        [
            t(1, "подбери стартовую корзину на 500 pv", "structured_starter_basket", None, ["корзин"], MEDIA_NONE, {}),
        ],
    ),
    flow(
        "CONV-F37-pro-full",
        "pro_card_video_price",
        "P1",
        "conv-f37",
        [
            t(1, "активатор pro", "structured_card", "PRO", ["PRO"], MEDIA_ALLOW, {"last_product_name": "PRO"}),
            t(2, "видео", "structured_video", "PRO", ["Видео"], MEDIA_NONE, {"last_product_name": "PRO"}),
            t(3, "цена", "structured_price", "PRO", ["BYN"], MEDIA_NONE, {"last_product_name": "PRO"}),
        ],
    ),
    flow(
        "CONV-F38-help-greeting",
        "service_greeting_help",
        "P1",
        "conv-f38",
        [
            t(1, "привет", "structured_business", None, ["Здравств"], MEDIA_NONE, {}),
            t(2, "помощь", "structured_business", None, ["товар"], MEDIA_NONE, {}),
        ],
    ),
]


def main() -> None:
    lines = [json.dumps(item, ensure_ascii=False) for item in FLOWS]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    turns = sum(len(f["turns"]) for f in FLOWS)
    print(f"Wrote {len(FLOWS)} flows, {turns} turns -> {OUT}")


if __name__ == "__main__":
    main()
