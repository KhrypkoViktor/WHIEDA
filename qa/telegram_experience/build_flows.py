#!/usr/bin/env python3
"""Build Telegram experience acceptance corpus."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "whieda_telegram_experience_flows_v1.jsonl"

MEDIA_NONE = {"photo": "none", "video_count_min": 0, "document_count_min": 0}


def _flow(flow_id: str, category: str, session: str, turns: list[dict], *, priority: str = "P0") -> dict:
    return {
        "flow_id": flow_id,
        "name": flow_id,
        "category": category,
        "priority": priority,
        "session": session,
        "country": "BY",
        "turns": turns,
    }


def _turn(
    turn: int,
    input_text: str,
    expected_mode: str,
    must_contain: list[str],
    *,
    must_not_contain: list[str] | None = None,
    expected_gap_kind: str | None = None,
    expected_media: dict | None = None,
    max_latency_ms: int = 4000,
) -> dict:
    return {
        "turn": turn,
        "input": input_text,
        "expected_mode": expected_mode,
        "must_contain": must_contain,
        "must_not_contain": must_not_contain or ["Traceback", "I need human review"],
        "expected_media": expected_media or MEDIA_NONE,
        "max_latency_ms": max_latency_ms,
        **({"expected_gap_kind": expected_gap_kind} if expected_gap_kind else {}),
    }


def build_flows() -> list[dict]:
    flows: list[dict] = []

    flows.append(
        _flow(
            "TG-PRES-GREET",
            "structured_routes",
            "tg-pres-greet",
            [_turn(1, "приве", "structured_business", ["Здравств"])],
        )
    )
    flows.append(
        _flow(
            "TG-PRES-CAP",
            "structured_routes",
            "tg-pres-cap",
            [_turn(1, "че ты можешь?", "structured_business", ["цен"])],
        )
    )
    flows.append(
        _flow(
            "TG-PRES-CAP-TYPO",
            "structured_routes",
            "tg-pres-cap-typo",
            [_turn(1, "че ты можеь?", "structured_business", ["Могу", "цен"])],
            priority="P1",
        )
    )
    flows.append(
        _flow(
            "TG-PRES-CATALOG-ASK",
            "structured_routes",
            "tg-pres-catalog-ask",
            [_turn(1, "какие есть товары?", "structured_business", ["товар"])],
            priority="P1",
        )
    )
    flows.append(
        _flow(
            "TG-ORDER-CAP-OOS",
            "ordering",
            "tg-order-cap-oos",
            [
                _turn(1, "что можешь?", "structured_business", ["Могу", "цен"], must_not_contain=["пив"]),
                _turn(
                    2,
                    "пивка хочешь?",
                    "clarification",
                    ["сценар"],
                    must_not_contain=["Могу подсказать цену, PV, карточку"],
                    expected_gap_kind="unsupported_topic",
                ),
            ],
        )
    )
    flows.append(
        _flow(
            "TG-PRES-CARD-ACT",
            "presentation",
            "tg-pres-card-act",
            [
                _turn(
                    1,
                    "активатор клеток",
                    "structured_card",
                    ["Активатор клеток", "🔥 Коротко:"],
                    expected_media={"photo": "required", "video_count_min": 0, "document_count_min": 0},
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-PRES-PRICE",
            "presentation",
            "tg-pres-price",
            [_turn(1, "цена активатора", "structured_price", ["BYN"])],
        )
    )
    flows.append(
        _flow(
            "TG-PRES-PHOTO",
            "presentation",
            "tg-pres-photo",
            [
                _turn(
                    1,
                    "фото активатора",
                    "structured_photo",
                    ["фото", "Активатор"],
                    expected_media={"photo": "required", "video_count_min": 0, "document_count_min": 0},
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-PRES-VIDEO",
            "presentation",
            "tg-pres-video",
            [
                _turn(
                    1,
                    "видео активатора",
                    "structured_video",
                    ["видео"],
                    expected_media={"photo": "none", "video_count_min": 1, "document_count_min": 0},
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-PRES-CERT",
            "presentation",
            "tg-pres-cert",
            [
                _turn(
                    1,
                    "сертификат активатора",
                    "structured_certificate",
                    ["http"],
                    expected_media={"photo": "none", "video_count_min": 0, "document_count_min": 1},
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-ROUTE-CALC",
            "structured_routes",
            "tg-route-calc",
            [_turn(1, "калькулятор", "structured_business", ["Посчитай:"])],
        )
    )
    flows.append(
        _flow(
            "TG-ROUTE-START",
            "structured_routes",
            "tg-route-start",
            [_turn(1, "какие виды входа?", "structured_starter_basket", ["старт"])],
        )
    )
    flows.append(
        _flow(
            "TG-ROUTE-COMPANY",
            "structured_routes",
            "tg-route-company",
            [
                _turn(
                    1,
                    "расскажи о компании",
                    "structured_business",
                    ["WHIEDA"],
                    must_not_contain=["каталог", "Traceback"],
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-ROUTE-INCOME",
            "structured_routes",
            "tg-route-income",
            [_turn(1, "как заработать?", "structured_business", ["доход"])],
        )
    )
    flows.append(
        _flow(
            "TG-SAFE-DISCOMFORT",
            "safe_gaps",
            "tg-safe-dis",
            [
                _turn(
                    1,
                    "болят колени",
                    "clarification",
                    ["не ставлю диагноз"],
                    expected_gap_kind="medical_or_safety_boundary",
                    must_not_contain=["каталог", "Traceback"],
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-CART-CALC-REMOVE",
            "structured_routes",
            "tg-cart-flow",
            [
                _turn(1, "Посчитай: активатор, бэм", "structured_cart", ["🛒", "BYN"]),
                _turn(2, "убери активатор", "structured_cart", ["Magic Foherb"], must_not_contain=["Traceback"]),
            ],
        )
    )
    flows.append(
        _flow(
            "TG-DATA-MISSING-PRODUCT",
            "data_gaps",
            "tg-data-miss",
            [
                _turn(
                    1,
                    "расскажи про xyzunknown123",
                    "knowledge_gap",
                    ["товар"],
                    expected_gap_kind="unknown_product",
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-PRES-CARD-BEM",
            "presentation",
            "tg-pres-card-bem",
            [
                _turn(
                    1,
                    "бэм",
                    "structured_card",
                    ["Magic Foherb", "🔥 Коротко:"],
                    expected_media={"photo": "required", "video_count_min": 0, "document_count_min": 0},
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-PRES-COMPARE",
            "presentation",
            "tg-pres-compare",
            [_turn(1, "сравни активатор и pro", "structured_comparison_layer", ["PRO"])],
        )
    )
    flows.append(
        _flow(
            "TG-PRES-ACTIVATOR-CHOICE",
            "presentation",
            "tg-pres-activator-choice",
            [
                _turn(1, "активатор", "clarification", ["Активатор клеток", "PRO"]),
                _turn(2, "обычный", "structured_card", ["Активатор клеток"]),
            ],
            priority="P1",
        )
    )
    flows.append(
        _flow(
            "TG-ROUTE-HELP",
            "structured_routes",
            "tg-route-help",
            [_turn(1, "помощь", "structured_business", ["товар"])],
        )
    )
    flows.append(
        _flow(
            "TG-SAFE-SPINE",
            "safe_gaps",
            "tg-safe-spine",
            [
                _turn(
                    1,
                    "болит спина",
                    "clarification",
                    ["не ставлю диагноз"],
                    expected_gap_kind="medical_or_safety_boundary",
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-SAFE-ADVICE",
            "safe_gaps",
            "tg-safe-advice",
            [
                _turn(
                    1,
                    "хочу совет",
                    "clarification",
                    ["подобрать товар"],
                    expected_gap_kind="medical_or_safety_boundary",
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-DATA-MISSING-CERT",
            "data_gaps",
            "tg-data-cert-gap",
            [
                _turn(
                    1,
                    "сертификат xyzunknown123",
                    "clarification",
                    ["товар"],
                    expected_gap_kind="unknown_followup",
                )
            ],
        )
    )
    flows.append(
        _flow(
            "TG-SVC-GREET-HI",
            "structured_routes",
            "tg-svc-greet-hi",
            [
                _turn(
                    1,
                    "привет",
                    "structured_business",
                    ["Здравств"],
                    must_not_contain=["Traceback", "каталог"],
                )
            ],
            priority="P1",
        )
    )
    flows.append(
        _flow(
            "TG-SVC-GREET-DAY",
            "structured_routes",
            "tg-svc-greet-day",
            [
                _turn(
                    1,
                    "добрый день",
                    "structured_business",
                    ["Здравств"],
                    must_not_contain=["Traceback"],
                )
            ],
            priority="P1",
        )
    )
    flows.append(
        _flow(
            "TG-SVC-CAP-SLANG",
            "structured_routes",
            "tg-svc-cap-slang",
            [_turn(1, "а что моешь", "structured_business", ["Могу"], must_not_contain=["Traceback"])],
            priority="P1",
        )
    )
    flows.append(
        _flow(
            "TG-SVC-CAP-MOZH",
            "structured_routes",
            "tg-svc-cap-mozh",
            [_turn(1, "можешь?", "structured_business", ["Могу"], must_not_contain=["Traceback"])],
            priority="P1",
        )
    )
    flows.append(
        _flow(
            "TG-SVC-CATALOG-SHOW",
            "structured_routes",
            "tg-svc-catalog-show",
            [
                _turn(
                    1,
                    "покажи любой товар",
                    "structured_business",
                    ["товар"],
                    must_not_contain=["Traceback", "xyzunknown"],
                )
            ],
            priority="P1",
        )
    )
    flows.append(
        _flow(
            "TG-SVC-OOS-SMALLTALK",
            "ordering",
            "tg-svc-oos-smalltalk",
            [
                _turn(
                    1,
                    "как дела",
                    "structured_business",
                    ["на связи"],
                    must_not_contain=["Могу подсказать цену, PV, карточку"],
                ),
                _turn(
                    2,
                    "пивка хочешь",
                    "clarification",
                    ["сценар"],
                    expected_gap_kind="unsupported_topic",
                    must_not_contain=["Могу подсказать цену, PV, карточку"],
                ),
            ],
            priority="P1",
        )
    )
    flows.append(
        _flow(
            "TG-SVC-ACTIVATOR-PRO",
            "presentation",
            "tg-svc-activator-pro",
            [
                _turn(1, "активатор", "clarification", ["Активатор", "PRO"]),
                _turn(2, "pro", "structured_card", ["PRO", "Активатор"]),
            ],
            priority="P1",
        )
    )
    return flows


def main() -> None:
    flows = build_flows()
    OUT.write_text(
        "\n".join(json.dumps(flow, ensure_ascii=False) for flow in flows) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(flows)} flows -> {OUT}")


if __name__ == "__main__":
    main()
