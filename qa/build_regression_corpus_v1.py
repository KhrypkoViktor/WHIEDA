#!/usr/bin/env python3
"""One-off maintainer script to build whieda_regression_cases_v1.jsonl (offline)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "qa" / "cases" / "whieda_regression_cases_v1.jsonl"

FORBIDDEN = ["Nordman", "I need human review", "Traceback"]
GLOBAL_NOT = FORBIDDEN.copy()

PRODUCTS = {
    "activator": "Активатор клеток",
    "activator_pro": "Активатор клеток PRO",
    "ventun": "Вэнтун",
    "bem": "Magic Foherb",
    "bagua": "Ба-Гуа",
    "glasses": "Очки",
    "insoles": "Стельки",
    "shawl": "Палантин",
    "belt": "Магнитный пояс",
    "linzhi": "Линчжи",
    "luwei": "Лювэй",
    "soy": "Соевый пептид",
    "fohou_red": "Эликсир Фохоу",
    "sancin_green": "Эликсир Саньцин",
    "treasures_blue": "Эликсир 3 Драгоценности",
    "rose_fohou": "Роза Фохоу",
    "paste": "зубная паста",
}


def case(
    case_id: str,
    group: str,
    priority: str,
    input_text: str,
    *,
    expected_mode: str,
    must_contain: list[str],
    must_not_contain: list[str] | None = None,
    context_before: list[str] | None = None,
    expected_product: str | None = None,
    source: str = "curated_qa",
) -> dict:
    return {
        "case_id": case_id,
        "group": group,
        "priority": priority,
        "input": input_text,
        "context_before": context_before or [],
        "expected_mode": expected_mode,
        "must_contain": must_contain,
        "must_not_contain": (must_not_contain or []) + GLOBAL_NOT,
        "expected_product": expected_product,
        "source": source,
    }


def build_cases() -> list[dict]:
    rows: list[dict] = []
    counters: dict[str, int] = {}

    def add(group: str, priority: str, input_text: str, mode: str, contain: list[str], product: str | None = None, ctx: list[str] | None = None, not_contain: list[str] | None = None, prefix: str | None = None, case_id: str | None = None):
        pref = prefix or {
            "catalog_card": "CAT",
            "catalog_price": "CPR",
            "aliases_typo": "ALI",
            "followup_context": "FUP",
            "photo_video_certificate": "MED",
            "comparison": "CMP",
            "cart_and_basket": "CRT",
            "business_faq": "BUS",
            "promotion_event": "PRM",
            "safety_and_clarification": "SAF",
        }[group]
        if not contain:
            contain = [product.split()[0] if product else "WHIEDA"]
        if case_id is None:
            counters[pref] = counters.get(pref, 0) + 1
            case_id = f"{pref}-{counters[pref]:03d}"
        rows.append(
            case(
                case_id,
                group,
                priority,
                input_text,
                expected_mode=mode,
                must_contain=contain,
                must_not_contain=not_contain,
                context_before=ctx,
                expected_product=product,
            )
        )

    card_inputs = [
        ("расскажи про активатор клеток", "Активатор", PRODUCTS["activator"]),
        ("что такое активатор клеток pro", "PRO", PRODUCTS["activator_pro"]),
        ("вэнтун", "Вэнтун", PRODUCTS["ventun"]),
        ("расскажи про прибор вэнтун", "Вэнтун", PRODUCTS["ventun"]),
        ("ба гуа", "Ба-Гуа", PRODUCTS["bagua"]),
        ("что такое сауна ба-гуа", "Ба-Гуа", PRODUCTS["bagua"]),
        ("magic foherb 3.0", "Magic", PRODUCTS["bem"]),
        ("что такое бэм", "Magic", PRODUCTS["bem"]),
        ("палантин", "Палантин", PRODUCTS["shawl"]),
        ("стельки с анионами", "Стельки", PRODUCTS["insoles"]),
        ("линчжи", "Линчжи", PRODUCTS["linzhi"]),
        ("чай лювэй", "Лювэй", PRODUCTS["luwei"]),
        ("соевый пептид", "Соевый", PRODUCTS["soy"]),
        ("очки для компьютера", "Очки", PRODUCTS["glasses"]),
        ("графеновые очки", "Очки", PRODUCTS["glasses"]),
        ("магнитный пояс", "пояс", PRODUCTS["belt"]),
        ("эликсир фохоу", "Фохоу", PRODUCTS["fohou_red"]),
        ("эликсир саньцин", "Саньцин", PRODUCTS["sancin_green"]),
        ("эликсир 3 драгоценности", "Драгоцен", PRODUCTS["treasures_blue"]),
        ("синий эликсир три драгоценности", "Драгоцен", PRODUCTS["treasures_blue"]),
        ("роза фохоу", "Роза", PRODUCTS["rose_fohou"]),
        ("зубная паста с полынью", "паста", "Паста"),
        ("паста цинфэн", "Цинфэн", "Паста Цинфэн"),
        ("спирулина whieda", "Спирулина", "Спирулина"),
        ("комплект активатор + pro", "комплект", PRODUCTS["activator"]),
        ("что входит в активатор pro", "PRO", PRODUCTS["activator_pro"]),
        ("расскажи про бэм 3.0", "Foherb", PRODUCTS["bem"]),
        ("что за прибор ventun", "Вэнтун", PRODUCTS["ventun"]),
        ("опиши палантин whieda", "Палантин", PRODUCTS["shawl"]),
        ("что делает соевый пептид", "пептид", PRODUCTS["soy"]),
        ("расскажи про линчжи гриб", "Линчжи", PRODUCTS["linzhi"]),
        ("что такое чай лювэй", "Лювэй", PRODUCTS["luwei"]),
        ("расскажи про стельки whieda", "Стельки", PRODUCTS["insoles"]),
        ("что такое компьютерные очки", "Очки", PRODUCTS["glasses"]),
        ("что за сауна для дома", "Ба-Гуа", PRODUCTS["bagua"]),
        ("расскажи про красный эликсир", "Фохоу", PRODUCTS["fohou_red"]),
        ("расскажи про магнитный пояс", "пояс", PRODUCTS["belt"]),
    ]
    for text, token, product in card_inputs:
        add("catalog_card", "P0", text, "structured_card", [token], product, prefix="CAT")

    price_inputs = [
        ("сколько стоит активатор клеток", "цена", PRODUCTS["activator"]),
        ("цена активатора", "PV", PRODUCTS["activator"]),
        ("повторка активатора клеток", "партнер", PRODUCTS["activator"]),
        ("сколько стоит активатор pro", "PRO", PRODUCTS["activator_pro"]),
        ("цена pro версии", "PV", PRODUCTS["activator_pro"]),
        ("сколько стоит вэнтун", "Вэнтун", PRODUCTS["ventun"]),
        ("цена вентуна", "цена", PRODUCTS["ventun"]),
        ("сколько стоит бэм", "Magic", PRODUCTS["bem"]),
        ("цена magic foherb", "PV", PRODUCTS["bem"]),
        ("сколько стоит ба-гуа", "Ба-Гуа", PRODUCTS["bagua"]),
        ("цена сауны", "цена", PRODUCTS["bagua"]),
        ("сколько стоят стельки", "Стельки", PRODUCTS["insoles"]),
        ("цена палантина", "Палантин", PRODUCTS["shawl"]),
        ("сколько стоят очки", "Очки", PRODUCTS["glasses"]),
        ("цена линчжи", "Линчжи", PRODUCTS["linzhi"]),
        ("сколько стоит чай лювэй", "Лювэй", PRODUCTS["luwei"]),
        ("цена соевого пептида", "пептид", PRODUCTS["soy"]),
        ("сколько стоит магнитный пояс", "пояс", PRODUCTS["belt"]),
        ("цена красного эликсира", "Фохоу", PRODUCTS["fohou_red"]),
        ("цена зелёного эликсира", "Саньцин", PRODUCTS["sancin_green"]),
        ("цена синего эликсира 3 драгоценности", "Драгоцен", PRODUCTS["treasures_blue"]),
        ("сколько стоит эликсир три драгоценности", "Драгоцен", PRODUCTS["treasures_blue"]),
        ("сколько стоит роза фохоу", "Роза", PRODUCTS["rose_fohou"]),
        ("цена зубной пасты", "паста", "Паста"),
        ("сколько стоит спирулина", "Спирулина", "Спирулина"),
        ("активатор клеток — какая розница", "розниц", PRODUCTS["activator"]),
        ("а повторка по активатору?", "партнер", PRODUCTS["activator"]),
        ("сколько pv у активатора", "PV", PRODUCTS["activator"]),
        ("цена для партнёра на бэм", "партнер", PRODUCTS["bem"]),
        ("сколько стоит комплект активатор + бэм", "PV", PRODUCTS["activator"]),
        ("цена очков для компьютера whieda", "Очки", PRODUCTS["glasses"]),
        ("сколько стоит сауна если я уже партнёр", "партнер", PRODUCTS["bagua"]),
        ("цена стелек с анионами", "Стельки", PRODUCTS["insoles"]),
        ("сколько стоит линчжи для партнёра", "партнер", PRODUCTS["linzhi"]),
        ("цена лювэй чая", "Лювэй", PRODUCTS["luwei"]),
        ("сколько стоит pro активатор для партнёра", "PRO", PRODUCTS["activator_pro"]),
    ]
    for text, token, product in price_inputs:
        add("catalog_price", "P0", text, "structured_price", [token], product, prefix="CPR")

    alias_inputs = [
        ("активатор", "Активатор", PRODUCTS["activator"]),
        ("ативатор", "Активатор", PRODUCTS["activator"]),
        ("расскажи про ативатор", "Активатор", PRODUCTS["activator"]),
        ("кампьютерные очки", "Очки", PRODUCTS["glasses"]),
        ("виедовские очки", "Очки", PRODUCTS["glasses"]),
        ("что такое ленжи", "Линчжи", PRODUCTS["linzhi"]),
        ("цена ленчжи", "Линчжи", PRODUCTS["linzhi"]),
        ("линьчжи", "Линчжи", PRODUCTS["linzhi"]),
        ("сколько стоит ба гоа", "Ба-Гуа", PRODUCTS["bagua"]),
        ("расскажи про бо гуа", "Ба-Гуа", PRODUCTS["bagua"]),
        ("что такое бэмчик", "Magic", PRODUCTS["bem"]),
        ("цена вэм", "Magic", PRODUCTS["bem"]),
        ("что такое wentun", "Вэнтун", PRODUCTS["ventun"]),
        ("расскажи про веетуна", "Вэнтун", PRODUCTS["ventun"]),
        ("цена вентум", "Вэнтун", PRODUCTS["ventun"]),
        ("что такое винтун", "Вэнтун", PRODUCTS["ventun"]),
        ("расскажи про вэнтум", "Вэнтун", PRODUCTS["ventun"]),
        ("что такое санцин", "Саньцин", PRODUCTS["sancin_green"]),
        ("цена санцын", "Саньцин", PRODUCTS["sancin_green"]),
        ("сяньцинь", "Саньцин", PRODUCTS["sancin_green"]),
        ("что такое фоху", "Фохоу", PRODUCTS["fohou_red"]),
        ("цена фохуа", "Фохоу", PRODUCTS["fohou_red"]),
        ("расскажи про фухоу", "Фохоу", PRODUCTS["fohou_red"]),
        ("полонтино", "Палантин", PRODUCTS["shawl"]),
        ("что такое полонтино", "Палантин", PRODUCTS["shawl"]),
        ("пептид", "уточн", PRODUCTS["soy"], ["диагноз"]),
        ("соевые пептиды", "Соевый", PRODUCTS["soy"]),
        ("magic foherb", "Magic", PRODUCTS["bem"]),
        ("бэм 3.0", "Foherb", PRODUCTS["bem"]),
        ("ventun прибор", "Вэнтун", PRODUCTS["ventun"]),
        ("активатор pro", "PRO", PRODUCTS["activator_pro"]),
        ("активатор клеток rpo", "PRO", PRODUCTS["activator_pro"]),
        ("стельки анионы", "Стельки", PRODUCTS["insoles"]),
        ("лювей чай", "Лювэй", PRODUCTS["luwei"]),
        ("лювэи", "Лювэй", PRODUCTS["luwei"]),
        ("графен очки", "Очки", PRODUCTS["glasses"]),
        ("анионовые стельки", "Стельки", PRODUCTS["insoles"]),
    ]
    for item in alias_inputs:
        text, token, product = item[:3]
        not_c = item[3] if len(item) > 3 else None
        mode = "structured_price" if "цена" in text.lower() or text.startswith("сколько") else "clarification" if token == "уточн" else "structured_card"
        add("aliases_typo", "P0" if "активатор" in text or "ативатор" in text else "P1", text, mode, [token], product, not_contain=not_c, prefix="ALI")

    followups = [
        (["расскажи про активатор"], "а сколько стоит?", "structured_price", ["PV"], PRODUCTS["activator"]),
        (["расскажи про активатор"], "а повторка?", "structured_price", ["партнер"], PRODUCTS["activator"]),
        (["активатор клеток"], "дай фото", "structured_photo", [], PRODUCTS["activator"]),
        (["активатор клеток"], "дай видео", "structured_video", ["Видео"], PRODUCTS["activator"]),
        (["активатор клеток"], "сертификат", "structured_certificate", ["сертиф"], PRODUCTS["activator"]),
        (["вэнтун"], "расскажи подробнее", "structured_card", ["Вэнтун"], PRODUCTS["ventun"]),
        (["бэм"], "а цена?", "structured_price", ["PV"], PRODUCTS["bem"]),
        (["ба-гуа"], "сколько стоит?", "structured_price", ["цена"], PRODUCTS["bagua"]),
        (["линчжи"], "дай фото", "structured_photo", [], PRODUCTS["linzhi"]),
        (["очки"], "цена?", "structured_price", ["Очки"], PRODUCTS["glasses"]),
        (["активатор pro"], "чем отличается?", "structured_comparison", ["PRO"], PRODUCTS["activator_pro"]),
        (["активатор pro"], "сколько стоит?", "structured_price", ["PRO"], PRODUCTS["activator_pro"]),
        (["палантин"], "материалы", "structured_product_detail", ["Палантин"], PRODUCTS["shawl"]),
        (["стельки"], "расскажи подробнее", "structured_card", ["Стельки"], PRODUCTS["insoles"]),
        (["соевый пептид"], "а для партнёра?", "structured_price", ["партнер"], PRODUCTS["soy"]),
        (["лювэй"], "цена", "structured_price", ["Лювэй"], PRODUCTS["luwei"]),
        (["красный эликсир"], "цена красного эликсира", "structured_price", ["Фохоу"], PRODUCTS["fohou_red"]),
        (["зелёный эликсир"], "сколько стоит", "structured_price", ["Саньцин"], PRODUCTS["sancin_green"]),
        (["активатор", "а цена?"], "дай фото", "structured_photo", [], PRODUCTS["activator"]),
        (["бэм", "цена"], "дай видео", "structured_video", ["Видео"], PRODUCTS["bem"]),
        (["вэнтун"], "сертификат есть?", "structured_certificate", ["сертиф"], PRODUCTS["ventun"]),
        (["активатор pro"], "дай фото pro", "structured_photo", [], PRODUCTS["activator_pro"]),
        (["ба-гуа"], "видео сауны", "structured_video", ["Видео"], PRODUCTS["bagua"]),
        (["магнитный пояс"], "цена пояса", "structured_price", ["пояс"], PRODUCTS["belt"]),
        (["роза фохоу"], "расскажи подробнее", "structured_card", ["Роза"], PRODUCTS["rose_fohou"]),
        (["активатор"], "материалы по активатору", "structured_product_detail", ["Активатор"], PRODUCTS["activator"]),
        (["очки", "цена"], "дай фото очков", "structured_photo", [], PRODUCTS["glasses"]),
        (["линчжи", "фото"], "видео", "structured_video", ["Видео"], PRODUCTS["linzhi"]),
        (["спирулина"], "сколько стоит", "structured_price", ["Спирулина"], "Спирулина"),
        (["активатор pro", "цена"], "повторка pro", "structured_price", ["партнер"], PRODUCTS["activator_pro"]),
    ]
    for ctx, text, mode, contain, product in followups:
        add("followup_context", "P0", text, mode, contain, product, ctx=ctx, prefix="FUP")

    media_inputs = [
        ("дай фото активатора клеток", "structured_photo", PRODUCTS["activator"]),
        ("фото активатора pro", "structured_photo", PRODUCTS["activator_pro"]),
        ("дай фото вэнтуна", "structured_photo", PRODUCTS["ventun"]),
        ("фото бэма", "structured_photo", PRODUCTS["bem"]),
        ("дай фото ба-гуа", "structured_photo", PRODUCTS["bagua"]),
        ("фото стелек", "structured_photo", PRODUCTS["insoles"]),
        ("дай фото палантина", "structured_photo", PRODUCTS["shawl"]),
        ("фото очков", "structured_photo", PRODUCTS["glasses"]),
        ("дай фото линчжи", "structured_photo", PRODUCTS["linzhi"]),
        ("фото лювэй", "structured_photo", PRODUCTS["luwei"]),
        ("дай видео активатора клеток", "structured_video", PRODUCTS["activator"]),
        ("видео pro активатора", "structured_video", PRODUCTS["activator_pro"]),
        ("дай видео вэнтуна", "structured_video", PRODUCTS["ventun"]),
        ("видео про бэм", "structured_video", PRODUCTS["bem"]),
        ("сертификат активатора", "structured_certificate", PRODUCTS["activator"]),
        ("сертификат на вэнтун", "structured_certificate", PRODUCTS["ventun"]),
        ("дай сертификат бэма", "structured_certificate", PRODUCTS["bem"]),
        ("материалы активатора", "structured_product_detail", PRODUCTS["activator"]),
        ("материалы pro", "structured_product_detail", PRODUCTS["activator_pro"]),
        ("где видео про королеву активаторов", "structured_video", PRODUCTS["activator"]),
        ("есть видео про новый активатор pro", "structured_video", PRODUCTS["activator_pro"]),
        ("дай фото красного эликсира", "structured_photo", PRODUCTS["fohou_red"]),
        ("сертификат санцин", "structured_certificate", PRODUCTS["sancin_green"]),
        ("фото розы фохоу", "structured_photo", PRODUCTS["rose_fohou"]),
        ("видео стелек whieda", "structured_video", PRODUCTS["insoles"]),
    ]
    for text, mode, product in media_inputs:
        if mode == "structured_photo":
            contain = ["фото"]
        elif mode == "structured_video":
            contain = ["Видео"]
        elif mode == "structured_certificate":
            contain = ["сертиф"]
        else:
            contain = [product.split()[0]]
        add("photo_video_certificate", "P0", text, mode, contain, product, prefix="MED")

    compare_inputs = [
        ("чем отличается активатор pro от обычного", "PRO", PRODUCTS["activator_pro"]),
        ("активатор или pro что лучше", "PRO", PRODUCTS["activator"]),
        ("сравни активатор и pro", "PRO", PRODUCTS["activator_pro"]),
        ("бэм или вэнтун", "Magic", PRODUCTS["bem"]),
        ("сравни бэм и ба-гуа", "Ба-Гуа", PRODUCTS["bagua"]),
        ("стельки или очки что выбрать", "Очки", PRODUCTS["glasses"]),
        ("линчжи или лювэй", "Линчжи", PRODUCTS["linzhi"]),
        ("красный или зелёный эликсир", "Фохоу", PRODUCTS["fohou_red"]),
        ("фохоу vs санцин", "Саньцин", PRODUCTS["sancin_green"]),
        ("активатор pro vs комплект", "комплект", PRODUCTS["activator_pro"]),
        ("сравни палантин и пояс", "Палантин", PRODUCTS["shawl"]),
        ("очки whieda или обычные", "Очки", PRODUCTS["glasses"]),
        ("соевый пептид или спирулина", "пептид", PRODUCTS["soy"]),
        ("чем pro лучше базового активатора", "PRO", PRODUCTS["activator_pro"]),
        ("вэнтун или бэм для дома", "Вэнтун", PRODUCTS["ventun"]),
        ("сравни синий эликсир 3 драгоценности и фохоу", "Драгоцен", PRODUCTS["treasures_blue"]),
        ("красный эликсир и роза фохоу", "Роза", PRODUCTS["rose_fohou"]),
        ("активатор + бэм или только pro", "PRO", PRODUCTS["activator_pro"]),
        ("стельки и пояс вместе", "пояс", PRODUCTS["belt"]),
        ("сравни цены активатора и pro", "PRO", PRODUCTS["activator_pro"]),
    ]
    for text, token, product in compare_inputs:
        add("comparison", "P1", text, "structured_comparison", [token], product, prefix="CMP")

    cart_inputs = [
        ("посчитай: активатор, бэм, ба-гуа", "корзин", None),
        ("корзина активатор + pro", "PV", PRODUCTS["activator"]),
        ("сколько pv если взять активатор и бэм", "PV", PRODUCTS["activator"]),
        ("набор активатор pro и вэнтун", "PRO", PRODUCTS["activator_pro"]),
        ("посчитай стельки и очки", "PV", PRODUCTS["insoles"]),
        ("корзина линчжи + лювэй", "Линчжи", PRODUCTS["linzhi"]),
        ("сколько будет три эликсира", "эликсир", PRODUCTS["fohou_red"]),
        ("посчитай активатор повторка и pro", "партнер", PRODUCTS["activator"]),
        ("стартовый набор whieda", "набор", None),
        ("starter basket активатор бэм", "корзин", PRODUCTS["activator"]),
        ("посчитай: активатор, стельки, очки", "PV", PRODUCTS["activator"]),
        ("корзина для новичка", "набор", None),
        ("сколько pv корзина pro + бэм", "PV", PRODUCTS["activator_pro"]),
        ("посчитай палантин и пояс", "PV", PRODUCTS["shawl"]),
        ("набор эликсиры все три", "эликсир", PRODUCTS["fohou_red"]),
        ("посчитай активатор x2", "PV", PRODUCTS["activator"]),
        ("корзина партнёра активатор бэм ба-гуа", "партнер", PRODUCTS["activator"]),
        ("сколько стоит набор активатор + аксессуары", "PV", PRODUCTS["activator"]),
        ("посчитай: вэнтун, бэм, стельки", "PV", PRODUCTS["ventun"]),
        ("starter basket для старта бизнеса", "набор", None),
    ]
    for text, token, product in cart_inputs:
        mode = "structured_starter_basket" if "starter" in text or "набор" in text or "нович" in text else "structured_cart"
        add("cart_and_basket", "P1", text, mode, [token], product, prefix="CRT")

    business_inputs = [
        ("что такое повторка", "Повтор", None),
        ("что такое бинарный бонус", "Бинар", None),
        ("это же млм", "MLM", None),
        ("как стать партнёром", "партнер", None),
        ("сколько можно заработать", "доход", None),
        ("как работает маркeting plan", "маркет", None),
        ("что такое pv", "PV", None),
        ("как оформить заказ партнёра", "заказ", None),
        ("возражение: это пирамида", "партнер", None),
        ("как объяснить клиенту активатор", "Активатор", PRODUCTS["activator"]),
        ("сколько нужно для ранга", "ранг", None),
        ("что такое квалификация", "квалиф", None),
        ("как начать первую неделю", "недел", None),
        ("возражение дорого", "цен", None),
        ("как работает доставка", "достав", None),
        ("можно ли вернуть товар", "возврат", None),
        ("что такое autoshop", "auto", None),
        ("как пригласить друга", "реферал", None),
        ("бизнес с whieda с чего начать", "старт", None),
        ("что такое статус партнёра", "статус", None),
    ]
    for text, token, product in business_inputs:
        mode = "structured_business_objection" if "возраж" in text or "млм" in text or "пирамид" in text else "structured_business_faq"
        add("business_faq", "P1", text, mode, [token], product, prefix="BUS")

    promo_inputs = [
        ("какие акции сейчас", "акци", None),
        ("есть ли промо на активатор", "акци", PRODUCTS["activator"]),
        ("скидка на pro", "PRO", PRODUCTS["activator_pro"]),
        ("мероприятие whieda когда", "меропр", None),
        ("конференция whieda", "конфер", None),
        ("community whieda", "сообщ", None),
        ("чат партнёров", "чат", None),
        ("новость про акцию на бэм", "акци", PRODUCTS["bem"]),
        ("promotion event whieda", "акци", None),
        ("когда следующий ивент", "ивент", None),
        ("акция на стартовый набор", "набор", None),
        ("промо на эликсиры", "эликсир", PRODUCTS["fohou_red"]),
        ("скидка партнёрам на вэнтун", "Вэнтун", PRODUCTS["ventun"]),
        ("распродажа палантинов", "Палантин", PRODUCTS["shawl"]),
        ("бонусная акция месяца", "бонус", None),
    ]
    for text, token, product in promo_inputs:
        mode = "structured_event" if any(x in text for x in ("меропр", "конфер", "ивент")) else (
            "structured_community" if any(x in text for x in ("community", "чат", "сообщ")) else "structured_promotion"
        )
        add("promotion_event", "P2", text, mode, [token], product, prefix="PRM")

    safety_inputs = [
        ("как лечить диагноз активатором", "не заменяет", None, ["лечени", "диагноз"]),
        ("можно ли активатор при кардиостимуляторе", "огранич", PRODUCTS["activator"], ["гарант"]),
        ("паста", "уточн", None, ["диагноз"]),
        ("красный", "уточн", PRODUCTS["fohou_red"], ["лечени"]),
        ("расскажи подробнее", "уточн", None, ["Traceback"]),
        ("что такое nordman", "не знаю", None, ["Nordman"]),
        ("гарантируете излечение активатором", "не", None, ["гарант", "излечен"]),
        ("можно ли детям активатор без врача", "врач", PRODUCTS["activator"], ["назнач"]),
        ("лечит ли бэм диабет", "не заменяет", PRODUCTS["bem"], ["лечит"]),
        ("опасно ли вэнтун при беременности", "огранич", PRODUCTS["ventun"], ["гарант"]),
        ("что будет если пить эликсир при онкологии", "врач", PRODUCTS["fohou_red"], ["лечени"]),
        ("подходит ли ба-гуа при гипертонии", "огранич", PRODUCTS["bagua"], ["гарант"]),
        ("можно ли заменить лекарство соевым пептидом", "не заменяет", PRODUCTS["soy"], ["лекарств"]),
        ("диагноз и схема лечения активатором", "не", None, ["схема", "диагноз"]),
        ("неизвестный продукт xyz123", "не знаю", None, ["Traceback"]),
    ]
    for text, token, product, not_c in safety_inputs:
        mode = "clarification" if text in {"паста", "красный", "расскажи подробнее"} else "knowledge_gap"
        pri = "P0" if mode == "clarification" or "леч" in text or "диагноз" in text else "P1"
        add("safety_and_clarification", pri, text, mode, [token], product, not_contain=not_c, prefix="SAF")

    # Multi-question cases (extra, distributed)
    multi = [
        ("catalog_card", "P1", "расскажи про активатор и сколько стоит", "structured_card", ["Активатор"], PRODUCTS["activator"]),
        ("catalog_price", "P1", "цена активатора и есть ли pro", "structured_price", ["PV"], PRODUCTS["activator"]),
        ("aliases_typo", "P1", "ативатор pro цена", "structured_price", ["PRO"], PRODUCTS["activator_pro"]),
        ("followup_context", "P0", "а цена?", "structured_price", ["PV"], PRODUCTS["activator"], ["активатор"]),
        ("photo_video_certificate", "P1", "фото и видео активатора", "structured_photo", ["фото"], PRODUCTS["activator"]),
        ("comparison", "P2", "активатор pro или бэм что взять", "structured_comparison", ["PRO"], PRODUCTS["activator_pro"]),
        ("cart_and_basket", "P1", "посчитай активатор, бэм, ба-гуа и скажи pv", "structured_cart", ["PV"], PRODUCTS["activator"]),
        ("business_faq", "P2", "pv и повторка — объясни", "structured_business_faq", ["PV"], None),
        ("promotion_event", "P2", "акция и ивент на этой неделе", "structured_promotion", ["акци"], None),
        ("safety_and_clarification", "P0", "паста и цена", "clarification", ["уточн"], None, [], ["диагноз"]),
    ]
    for item in multi:
        group, pri, inp, mode, contain, product = item[:6]
        ctx = item[6] if len(item) > 6 else None
        not_c = item[7] if len(item) > 7 else None
        add(group, pri, inp, mode, contain, product, ctx=ctx, not_contain=not_c)

    return rows


def main() -> int:
    cases = build_cases()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for row in cases:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {len(cases)} cases to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
