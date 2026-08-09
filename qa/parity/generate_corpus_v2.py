#!/usr/bin/env python3
"""Generate qa/parity/core_local_parity_cases_v2.jsonl (80+ cases). NOT PRODUCTION DATA."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "core_local_parity_cases_v2.jsonl"

CASES: list[dict] = [
    # --- P0 service ---
    {"case_id": "PARITY-P0-001", "priority": "P0", "capability_id": "CAP-01", "input": "привет", "session": "parity-svc-001", "country": "BY", "expected_mode": "structured_business", "expected_product": None, "must_contain": ["Здравств"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": None}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-002", "priority": "P0", "capability_id": "CAP-01", "input": "что ты умеешь", "session": "parity-svc-002", "country": "BY", "expected_mode": "structured_business", "expected_product": None, "must_contain": ["цен"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": None}, "max_latency_ms": 3000},
    # --- P0 cards ---
    {"case_id": "PARITY-P0-010", "priority": "P0", "capability_id": "CAP-02", "input": "расскажи про активатор клеток", "session": "parity-card-010", "country": "BY", "expected_mode": "structured_card", "expected_product": "Активатор клеток", "must_contain": ["Активатор"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор клеток"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-011", "priority": "P0", "capability_id": "CAP-02", "input": "активатор pro", "session": "parity-card-011", "country": "BY", "expected_mode": "structured_card", "expected_product": "PRO", "must_contain": ["PRO"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "PRO"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-012", "priority": "P0", "capability_id": "CAP-02", "input": "что такое бэм", "session": "parity-card-012", "country": "BY", "expected_mode": "structured_card", "expected_product": "Magic", "must_contain": ["Magic"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Magic"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-013", "priority": "P0", "capability_id": "CAP-02", "input": "вэнтун", "session": "parity-card-013", "country": "BY", "expected_mode": "structured_card", "expected_product": "Вэнтун", "must_contain": ["Вэнтун"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Вэнтун"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-014", "priority": "P0", "capability_id": "CAP-02", "input": "сауна ба-гуа", "session": "parity-card-014", "country": "BY", "expected_mode": "structured_card", "expected_product": "Ба-Гуа", "must_contain": ["Ба-Гуа"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Ба-Гуа"}, "max_latency_ms": 3000},
    # --- P0 price ---
    {"case_id": "PARITY-P0-020", "priority": "P0", "capability_id": "CAP-03", "input": "цена активатора клеток", "session": "parity-price-020", "country": "BY", "expected_mode": "structured_price", "expected_product": "Активатор", "must_contain": ["BYN", "PV"], "must_not_contain": ["0 BYN", "Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-021", "priority": "P0", "capability_id": "CAP-03", "input": "партнёрская цена активатора", "session": "parity-price-021", "country": "BY", "expected_mode": "structured_price", "expected_product": "Активатор", "must_contain": ["партн"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-022", "priority": "P0", "capability_id": "CAP-03", "input": "сколько pv у активатора", "session": "parity-price-022", "country": "BY", "expected_mode": "structured_price", "expected_product": "Активатор", "must_contain": ["PV"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-023", "priority": "P0", "capability_id": "CAP-03", "input": "цена активатора", "session": "parity-price-023", "country": "RU", "expected_mode": "structured_price", "expected_product": "Активатор", "must_contain": ["RUB"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    # --- P0 aliases ---
    {"case_id": "PARITY-P0-030", "priority": "P0", "capability_id": "CAP-04", "input": "ативатор", "session": "parity-alias-030", "country": "BY", "expected_mode": "structured_card", "expected_product": "Активатор", "must_contain": ["Активатор"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-031", "priority": "P0", "capability_id": "CAP-04", "input": "цена сауны", "session": "parity-alias-031", "country": "BY", "expected_mode": "structured_price", "expected_product": "Ба-Гуа", "must_contain": ["Ба-Гуа"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Ба-Гуа"}, "max_latency_ms": 3000},
    # --- P0 media ---
    {"case_id": "PARITY-P0-040", "priority": "P0", "capability_id": "CAP-05", "input": "фото активатора клеток", "session": "parity-media-040", "country": "BY", "expected_mode": "structured_photo", "expected_product": "Активатор", "must_contain": ["фото"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "required", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-041", "priority": "P0", "capability_id": "CAP-05", "input": "видео активатора", "session": "parity-media-041", "country": "BY", "expected_mode": "structured_video", "expected_product": "Активатор", "must_contain": ["Видео"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 1, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-042", "priority": "P0", "capability_id": "CAP-05", "input": "сертификат активатора", "session": "parity-media-042", "country": "BY", "expected_mode": "structured_certificate", "expected_product": "Активатор", "must_contain": ["Материалы"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 1}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    # --- P0 follow-up / context ---
    {"case_id": "PARITY-P0-050", "priority": "P0", "capability_id": "CAP-06", "input": "цена", "session": "parity-ctx-050", "country": "BY", "context_before": [{"role": "user", "text": "расскажи про активатор клеток"}, {"role": "assistant", "text": "Активатор клеток"}], "expected_mode": "structured_price", "expected_product": "Активатор", "must_contain": ["BYN"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-051", "priority": "P0", "capability_id": "CAP-06", "input": "покажи фото", "session": "parity-ctx-051", "country": "BY", "context_before": [{"role": "user", "text": "активатор клеток"}, {"role": "assistant", "text": "Активатор клеток"}], "expected_mode": "structured_photo", "expected_product": "Активатор", "must_contain": ["фото"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "required", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-052", "priority": "P0", "capability_id": "CAP-07", "input": "цена сауны", "session": "parity-ctx-052", "country": "BY", "context_before": [{"role": "user", "text": "что такое бэм"}, {"role": "assistant", "text": "Magic Foherb"}], "expected_mode": "structured_price", "expected_product": "Ба-Гуа", "must_contain": ["Ба-Гуа"], "must_not_contain": ["Magic"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Ба-Гуа"}, "max_latency_ms": 3000},
    # --- P0 ambiguity ---
    {"case_id": "PARITY-P0-060", "priority": "P0", "capability_id": "CAP-08", "input": "красный", "session": "parity-amb-060", "country": "BY", "expected_mode": "clarification", "expected_product": None, "must_contain": ["эликсир"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": None}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-061", "priority": "P0", "capability_id": "CAP-08", "input": "цена красного эликсира", "session": "parity-amb-061", "country": "BY", "expected_mode": "structured_price", "expected_product": "Фохоу", "must_contain": ["BYN"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Фохоу"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-062", "priority": "P0", "capability_id": "CAP-08", "input": "паста", "session": "parity-amb-062", "country": "BY", "expected_mode": "clarification", "expected_product": None, "must_contain": ["паст"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": None}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-063", "priority": "P0", "capability_id": "CAP-08", "input": "пояс", "session": "parity-amb-063", "country": "BY", "expected_mode": "clarification", "expected_product": None, "must_contain": ["пояс"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": None}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-064", "priority": "P0", "capability_id": "CAP-08", "input": "активатор", "session": "parity-amb-064", "country": "BY", "expected_mode": "clarification", "expected_product": None, "must_contain": ["PRO"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": None}, "max_latency_ms": 3000},
    # --- P0 compare / cart / tenant / errors ---
    {"case_id": "PARITY-P0-070", "priority": "P0", "capability_id": "CAP-09", "input": "сравни активатор клеток и активатор pro", "session": "parity-cmp-070", "country": "BY", "expected_mode": "structured_comparison_layer", "expected_product": "Активатор", "must_contain": ["PRO"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-071", "priority": "P0", "capability_id": "CAP-10", "input": "посчитай: активатор, активатор", "session": "parity-cart-071", "country": "BY", "expected_mode": "structured_cart", "expected_product": "Активатор", "must_contain": ["PV"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": None}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-072", "priority": "P0", "capability_id": "CAP-10", "input": "посчитай: несуществующий товар xyz", "session": "parity-cart-072", "country": "BY", "expected_mode": "structured_cart", "expected_product": None, "must_contain": ["Не"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": None}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-080", "priority": "P0", "capability_id": "CAP-21", "input": "активатор", "session": "parity-tenant-whieda", "country": "BY", "host": "wwc.best", "expected_mode": "structured_card", "expected_product": "Активатор клеток", "must_contain": ["Активатор клеток"], "must_not_contain": ["Acme"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Активатор клеток"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-081", "priority": "P0", "capability_id": "CAP-21", "input": "активатор", "session": "parity-tenant-acme", "country": "BY", "host": "acme.test.local", "expected_mode": "structured_card", "expected_product": "Acme", "must_contain": ["Acme"], "must_not_contain": ["Активатор клеток"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "Acme"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-090", "priority": "P0", "capability_id": "CAP-22", "input": "", "session": "parity-json-090", "country": "BY", "expect_http_status": 422, "send_invalid_json": True, "must_not_contain": ["Traceback"], "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-091", "priority": "P0", "capability_id": "CAP-24", "input": "цена товара без цены", "session": "parity-noprice-091", "country": "BY", "expected_mode": "structured_price", "expected_product": "без цены", "must_contain": ["уточня"], "must_not_contain": ["0 BYN", "Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "без цены"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-092", "priority": "P0", "capability_id": "CAP-05", "input": "сертификат товара без сертификата", "session": "parity-nocert-092", "country": "BY", "expected_mode": "structured_certificate", "expected_product": "без сертификата", "must_contain": ["нет"], "must_not_contain": ["Traceback"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": "без сертификата"}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-093", "priority": "P0", "capability_id": "CAP-17", "input": "расскажи про несуществующий товар xyzabc", "session": "parity-gap-093", "country": "BY", "expected_mode": "knowledge_gap", "expected_product": None, "must_contain": ["уточн"], "must_not_contain": ["Traceback", "Nordman"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": None}, "max_latency_ms": 3000},
    {"case_id": "PARITY-P0-094", "priority": "P0", "capability_id": "CAP-19", "input": "привет", "session": "parity-svc-media-094", "country": "BY", "expected_mode": "structured_business", "expected_product": None, "must_contain": ["Здравств"], "must_not_contain": ["example.invalid"], "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0}, "expected_context": {"last_product_name": None}, "max_latency_ms": 3000},
]

# --- P1 cases (batch append) ---
P1_TEMPLATES = [
    ("PARITY-P1-100", "CAP-11", "подбери стартовую корзину на 500 pv", "structured_starter_basket", None, ["корзин"], 3500),
    ("PARITY-P1-101", "CAP-12", "какие акции сейчас", "structured_promotion", None, ["акци"], 3500),
    ("PARITY-P1-102", "CAP-13", "что такое pv", "structured_business_faq", None, ["PV"], 3500),
    ("PARITY-P1-103", "CAP-13", "что такое повторка", "structured_business_faq", None, ["повтор"], 3500),
    ("PARITY-P1-104", "CAP-13", "что такое step бонус", "structured_business_faq", None, ["Step"], 3500),
    ("PARITY-P1-105", "CAP-13", "что такое бинарный бонус", "structured_business_faq", None, ["бинар"], 3500),
    ("PARITY-P1-106", "CAP-14", "это mlm", "structured_business_objection", None, ["WHIEDA"], 3500),
    ("PARITY-P1-107", "CAP-15", "ближайшие события", "structured_event", None, ["эфир"], 3500),
    ("PARITY-P1-108", "CAP-15", "сообщество whieda", "structured_community", None, ["канал"], 3500),
    ("PARITY-P1-109", "CAP-18", "как лечить гипертонию схема лечения", "clarification", None, ["уточн"], 3500),
    ("PARITY-P1-110", "CAP-04", "magic foherb 3.0", "structured_card", "Magic", ["Magic"], 3500),
    ("PARITY-P1-111", "CAP-04", "вентун", "structured_card", "Вэнтун", ["Вэнтун"], 3500),
    ("PARITY-P1-112", "CAP-02", "расскажи подробнее про активатор", "structured_card", "Активатор", ["Активатор"], 3500),
    ("PARITY-P1-113", "CAP-06", "видео", "structured_video", "Активатор", ["Видео"], 3500),
    ("PARITY-P1-114", "CAP-06", "подробнее", "structured_card", "Активатор", ["Активатор"], 3500),
    ("PARITY-P1-115", "CAP-08", "зеленый эликсир", "structured_price", "Саньцин", ["BYN"], 3500),
    ("PARITY-P1-116", "CAP-08", "синий эликсир", "structured_price", "Драгоцен", ["BYN"], 3500),
    ("PARITY-P1-117", "CAP-10", "посчитай: активатор, бэм, ба-гуа", "structured_cart", None, ["PV"], 3500),
    ("PARITY-P1-118", "CAP-20", "фото активатора", "structured_photo", "Активатор", ["фото"], 3500),
    ("PARITY-P1-119", "CAP-20", "цена активатора", "structured_price", "Активатор", ["BYN"], 3500),
    ("PARITY-P1-120", "CAP-23", "цена активатора", "structured_price", "Активатор", ["BYN"], 3500),
    ("PARITY-P1-121", "CAP-01", "помощь", "structured_business", None, ["товар"], 3500),
    ("PARITY-P1-122", "CAP-02", "магнитный пояс", "structured_card", "пояс", ["пояс"], 3500),
    ("PARITY-P1-123", "CAP-02", "соевый пептид", "structured_card", "пептид", ["пептид"], 3500),
    ("PARITY-P1-124", "CAP-03", "розничная цена вэнтун", "structured_price", "Вэнтун", ["Розничная"], 3500),
    ("PARITY-P1-125", "CAP-03", "партнёрская цена ба-гуа", "structured_price", "Ба-Гуа", ["партн"], 3500),
    ("PARITY-P1-126", "CAP-05", "pdf активатора", "structured_certificate", "Активатор", ["Материалы"], 3500),
    ("PARITY-P1-127", "CAP-05", "фото товара без фото", "structured_photo", "без фото", ["фото"], 3500),
    ("PARITY-P1-128", "CAP-09", "чем отличается активатор от pro", "structured_comparison_layer", "Активатор", ["PRO"], 3500),
    ("PARITY-P1-129", "CAP-16", "цена активатора и расскажи про акции", "structured_price", "Активатор", ["BYN"], 3500),
    ("PARITY-P1-130", "CAP-17", "расскажи про xyzunknown123", "knowledge_gap", None, ["уточн"], 3500),
    ("PARITY-P1-131", "CAP-07", "активатор pro", "structured_card", "PRO", ["PRO"], 3500),
    ("PARITY-P1-132", "CAP-07", "цена", "structured_price", "PRO", ["BYN"], 3500),
    ("PARITY-P1-133", "CAP-04", "сауны", "structured_price", "Ба-Гуа", ["Ба-Гуа"], 3500),
    ("PARITY-P1-134", "CAP-04", "ба гуа", "structured_card", "Ба-Гуа", ["Ба-Гуа"], 3500),
    ("PARITY-P1-135", "CAP-21", "цена активатора", "structured_price", "Активатор", ["BYN"], 3500),
]

for idx, (cid, cap, inp, mode, prod, must, lat) in enumerate(P1_TEMPLATES):
    ctx_before = None
    session = f"parity-p1-{idx}"
    if cid == "PARITY-P1-113":
        ctx_before = [{"role": "user", "text": "активатор клеток"}, {"role": "assistant", "text": "ok"}]
    if cid == "PARITY-P1-114":
        ctx_before = [{"role": "user", "text": "активатор клеток"}, {"role": "assistant", "text": "ok"}]
    if cid == "PARITY-P1-132":
        ctx_before = [{"role": "user", "text": "активатор pro"}, {"role": "assistant", "text": "PRO"}]
        session = "parity-p1-132-ctx"
    case = {
        "case_id": cid,
        "priority": "P1",
        "capability_id": cap,
        "input": inp,
        "session": session,
        "country": "BY",
        "expected_mode": mode,
        "expected_product": prod,
        "must_contain": must,
        "must_not_contain": ["Traceback", "Nordman"],
        "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0},
        "expected_context": {"last_product_name": prod},
        "max_latency_ms": lat,
    }
    if ctx_before:
        case["context_before"] = ctx_before
    CASES.append(case)

# --- P2 cases ---
for i in range(15):
    CASES.append({
        "case_id": f"PARITY-P2-{200+i:03d}",
        "priority": "P2",
        "capability_id": "CAP-04",
        "input": f"активатор клеток вариант {i+1}",
        "session": f"parity-p2-{200+i}",
        "country": "BY" if i % 2 == 0 else "RU",
        "expected_mode": "structured_card",
        "expected_product": "Активатор",
        "must_contain": ["Активатор"],
        "must_not_contain": ["Traceback"],
        "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0},
        "expected_context": {"last_product_name": "Активатор"},
        "max_latency_ms": 5000,
    })

# Write jsonl
OUT.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in CASES) + "\n", encoding="utf-8")
p0 = sum(1 for c in CASES if c["priority"] == "P0")
p1 = sum(1 for c in CASES if c["priority"] == "P1")
p2 = sum(1 for c in CASES if c["priority"] == "P2")
print(f"Wrote {len(CASES)} cases to {OUT} (P0={p0}, P1={p1}, P2={p2})")
