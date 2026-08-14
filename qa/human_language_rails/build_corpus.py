#!/usr/bin/env python3
"""Build whieda_human_language_rails_v1.jsonl from approved local QA sources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
CORPUS_PATH = OUT_DIR / "whieda_human_language_rails_v1.jsonl"
FLOWS_PATH = OUT_DIR / "flows_v1.jsonl"

FORBIDDEN = [
    "не знаю",
    "нет в базе",
    "не смог обработать",
    "передам на проверку",
    "needs human review",
    "Traceback",
]

UNIVERSAL_MENU_MUST_CONTAIN_ALL = [
    "Я лучше всего помогаю с товарами WHIEDA",
    "Выберите направление",
]

SURFACE_GAP_MODES = frozenset(
    {"greeting", "capabilities", "structured_business", "smalltalk_status", "help", None}
)

SURFACE_GAP_TEXTS = frozenset(
    {
        "привет",
        "здарова",
        "хай",
        "привет!",
        "добрый день",
        "здрасьte",
        "привт",
        "ку",
        "пока",
        "hi",
        "hello",
        "ghbdtn",
        "приве",
        "че ты можеь?",
        "что можешь",
        "что умеешь",
        "можешь?",
        "чё ты можешь",
        "шо умеешь",
        "помощь",
        "help",
        "как дела",
        "ты живой",
        "спасибо",
        "ок",
        "нет",
        "ну",
        "э",
        "хм",
        "ййй",
        "??",
        "...",
        "qwerty",
        "a?",
        "123",
    }
)

POLICY_GAP_TEXTS = frozenset({"болят колени", "болит спина"})

PENDING_FIXTURE = OUT_DIR / "fixtures" / "pending_assertions.jsonl"

_case_seq = 0
_flow_seq = 0


def _next_case_id(priority: str = "P0") -> str:
    global _case_seq
    _case_seq += 1
    return f"HLR-{priority}-{_case_seq:03d}"


def _next_flow_id() -> str:
    global _flow_seq
    _flow_seq += 1
    return f"HLR-FLOW-{_flow_seq:03d}"


def _ctx(messages: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"role": role, "text": text} for role, text in messages]


def _norm(text: str) -> str:
    return str(text or "").casefold().strip()


def _resolve_acceptance(row: dict[str, Any]) -> str:
    explicit = row.get("acceptance_status")
    if explicit in {"accepted", "pending_surface", "pending_policy"}:
        return explicit
    text = _norm(str(row.get("user_text") or ""))
    if text in POLICY_GAP_TEXTS:
        return "pending_policy"
    rail = str(row.get("expected_rail") or "")
    mode = row.get("expected_mode")
    if rail == "universal_menu":
        if mode in SURFACE_GAP_MODES or text in SURFACE_GAP_TEXTS:
            return "pending_surface"
        return "accepted"
    return "accepted"


def _finalize_assertion(row: dict[str, Any]) -> dict[str, Any]:
    row["acceptance_status"] = _resolve_acceptance(row)
    if row["acceptance_status"] == "accepted" and row.get("expected_rail") == "universal_menu":
        row["must_contain_all"] = list(UNIVERSAL_MENU_MUST_CONTAIN_ALL)
        # The visible rail is the contract here.  Internally Core can record the
        # same menu as either a clarification or a knowledge gap.
        row["allowed_modes"] = ["clarification", "knowledge_gap"]
    elif row["acceptance_status"] == "pending_surface":
        row.pop("must_contain_all", None)
    return row


def _assertion(
    *,
    flow_id: str,
    turn_index: int,
    user_text: str,
    expected_rail: str,
    source_kind: str,
    source_ref: str,
    rationale: str,
    priority: str = "P0",
    context_before: list[dict[str, str]] | None = None,
    expected_mode: str | None = None,
    must_contain_any: list[str] | None = None,
    must_contain_all: list[str] | None = None,
    acceptance_status: str | None = None,
    expected_context_transition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "case_id": _next_case_id(priority),
        "priority": priority,
        "flow_id": flow_id,
        "turn_index": turn_index,
        "turn_role": "assertion",
        "user_text": user_text,
        "context_before": context_before or [],
        "expected_rail": expected_rail,
        "must_not_contain": list(FORBIDDEN),
        "source": {"kind": source_kind, "ref": source_ref},
        "rationale": rationale,
    }
    if expected_mode:
        row["expected_mode"] = expected_mode
    if must_contain_any:
        row["must_contain_any"] = must_contain_any
    if must_contain_all:
        row["must_contain_all"] = must_contain_all
    if acceptance_status:
        row["acceptance_status"] = acceptance_status
    if expected_context_transition:
        row["expected_context_transition"] = expected_context_transition
    return _finalize_assertion(row)


def _setup(flow_id: str, turn_index: int, user_text: str, source_ref: str) -> dict[str, Any]:
    return {
        "case_id": _next_case_id(),
        "flow_id": flow_id,
        "turn_index": turn_index,
        "turn_role": "setup",
        "user_text": user_text,
        "source": {"kind": "conversation_reliability", "ref": source_ref},
    }


def build_all() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    flow_registry: dict[str, dict[str, Any]] = {}

    def register_flow(flow_id: str, *, name: str, tags: list[str], source_ref: str) -> None:
        flow_registry[flow_id] = {"name": name, "tags": tags, "source_ref": source_ref}

    def add_flow(name: str, turns: list[dict[str, Any]], *, tags: list[str], source_ref: str) -> str:
        flow_id = _next_flow_id()
        register_flow(flow_id, name=name, tags=tags, source_ref=source_ref)
        cases.extend(turns)
        return flow_id

    # --- Multi-turn: product context follow-ups (10+ flows) ---
    conv_ref = "qa/conversation_reliability/whieda_conversation_flows_v1.jsonl"
    followup_specs = [
        ("card_to_price", "расскажи про активатор клеток", "цена", "structured_price", ["BYN"], "CONV-F01"),
        ("card_to_photo", "активатор клеток", "фото", "structured_photo", ["фото"], "CONV-F02"),
        ("card_to_video", "активатор клеток", "видео", "structured_video", ["Видео"], "CONV-F03"),
        ("card_to_cert", "активатор клеток", "сертификат", "structured_certificate", ["Материалы"], "CONV-F04"),
        ("typo_to_price", "ативатор", "цена", "structured_price", ["BYN"], "CONV-F06"),
        ("wentun_chain", "вэнтун", "цена", "structured_price", ["BYN"], "CONV-F28"),
        ("wentun_photo", "вэнтун", "фото", "structured_photo", ["фото"], "CONV-F28"),
        ("bem_chain", "что такое бэм", "цена", "structured_price", ["BYN"], "CONV-F30"),
        ("bagua_chain", "сауна ба-гуа", "фото", "structured_photo", ["фото"], "CONV-F31"),
        ("pro_video_price", "активатор pro", "видео", "structured_video", ["Видео"], "CONV-F37"),
        ("compare_price", "сравни активатор клеток и активатор pro", "цена активатора клеток", "structured_price", ["BYN"], "CONV-F13"),
        ("cart_mutate", "посчитай: активатор, бэм", "цена активатора", "structured_price", ["BYN"], "CONV-F16"),
    ]
    for name, setup_text, follow, mode, markers, conv_id in followup_specs:
        fid = _next_flow_id()
        ctx = _ctx([("user", setup_text)])
        register_flow(fid, name=name, tags=["context_followup", "direct_answer"], source_ref=f"{conv_ref}:{conv_id}")
        cases.append(_setup(fid, 1, setup_text, f"{conv_ref}:{conv_id}"))
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=2,
                user_text=follow,
                expected_rail="direct_answer",
                expected_mode=mode,
                must_contain_any=markers,
                context_before=ctx,
                source_kind="conversation_reliability",
                source_ref=f"{conv_ref}:{conv_id}",
                rationale=f"Contextual {follow} after product setup must stay on structured route.",
                expected_context_transition={
                    "sets": [],
                    "requires": [] if name == "cart_mutate" else ["last_product_context"],
                },
            )
        )

    # --- Multi-turn: ambiguous product selection ---
    amb_specs = [
        ("activator_pick", "активатор", "активатор клеток", "CONV-F07"),
        ("pasta_pick", "паста", "полын", "CONV-F08"),
        ("belt_pick", "пояс", "магнитный пояс", "CONV-F09"),
        ("red_elixir", "красный", "цена красного эликсира", "CONV-F10"),
        ("pro_select", "активатор", "активатор pro", "CONV-F26"),
    ]
    for name, t1, t2, conv_id in amb_specs:
        fid = _next_flow_id()
        register_flow(fid, name=name, tags=["product_choices", "multi_turn"], source_ref=f"{conv_ref}:{conv_id}")
        cases.append(_setup(fid, 1, t1, f"{conv_ref}:{conv_id}"))
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=1,
                user_text=t1,
                expected_rail="product_choices",
                expected_mode="clarification",
                must_contain_any=["уточн", "нужна"] if t1 == "красный" else [t1.split()[0][:4]],
                source_kind="conversation_reliability",
                source_ref=f"{conv_ref}:{conv_id}",
                rationale="Ambiguous nickname must open a compact product choice, not an error.",
            )
        )
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=2,
                user_text=t2,
                expected_rail="direct_answer",
                expected_mode="structured_card" if "цена" not in t2 else "structured_price",
                must_contain_any=["BYN"] if "цена" in t2 else [t2.split()[-1][:4]],
                context_before=_ctx([("user", t1)]),
                source_kind="conversation_reliability",
                source_ref=f"{conv_ref}:{conv_id}",
                rationale="After disambiguation user must reach a structured product answer.",
            )
        )

    # --- Multi-turn: vague -> useful (20+ flows) ---
    tg_ref = "qa/telegram_experience/whieda_telegram_experience_flows_v1.jsonl"
    vague_specs = [
        ("greet_then_product", "привет", "активатор клеток", "TG-SVC-GREET-HI", "structured_card", ["Активатор"]),
        ("cap_then_price", "че ты можешь?", "цена активатора", "TG-PRES-CAP", "structured_price", ["BYN"]),
        ("help_then_bem", "помощь", "бэм", "TG-ROUTE-HELP", "structured_card", ["Magic"]),
        ("catalog_then_card", "какие есть товары?", "спирулина", "TG-PRES-CATALOG-ASK", "structured_card", ["Спирулин"]),
        ("smalltalk_then_oos", "как дела", "пивка хочешь", "TG-SVC-OOS-SMALLTALK", "clarification", ["сценар"]),
        ("typo_greet_product", "приве", "ативатор", "TG-PRES-GREET", "structured_card", ["Активатор"]),
        ("mozh_product", "можешь?", "вэнтун", "TG-SVC-CAP-MOZH", "structured_card", ["Вэнтун"]),
        ("slang_cap_price", "а что моешь", "сколько стоит активатор", "TG-SVC-CAP-SLANG", "structured_price", ["BYN"]),
    ]
    for name, t1, t2, tg_id, mode, markers in vague_specs:
        fid = _next_flow_id()
        rail2 = "direct_answer" if mode not in {"clarification"} else "universal_menu"
        markers2 = markers if rail2 != "universal_menu" else markers + ["WHIEDA", "сценар"]
        register_flow(fid, name=name, tags=["vague_to_useful"], source_ref=f"{tg_ref}:{tg_id}")
        cases.append(_setup(fid, 1, t1, f"{tg_ref}:{tg_id}"))
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=1,
                user_text=t1,
                expected_rail="universal_menu",
                expected_mode="structured_business",
                must_contain_any=["WHIEDA", "товар", "Могу", "Здравств"],
                source_kind="telegram_experience",
                source_ref=f"{tg_ref}:{tg_id}",
                rationale="Vague first turn must land on a helpful WHIEDA menu rail, not a miss.",
            )
        )
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=2,
                user_text=t2,
                expected_rail=rail2,
                expected_mode=mode,
                must_contain_any=markers2,
                context_before=_ctx([("user", t1)]),
                source_kind="telegram_experience",
                source_ref=f"{tg_ref}:{tg_id}",
                rationale="Second turn after menu/greeting should reach a useful product route.",
            )
        )

    # --- Task selection flows ---
    task_flows = [
        ("starter_basket", "подбери стартовую корзину на 500 pv", "structured_starter_basket", ["корзин"], "CONV-F36"),
        ("basket_clarify", "подбери корзину", "clarification", ["бюджет"], "NBZ-P1-040"),
        ("gift_home", "что подарить маме на день рождения", "clarification", ["подар"], "TG-SAFE-ADVICE"),
        ("salon_device", "что взять мастеру в салон", "clarification", ["подобрать"], "TG-SAFE-ADVICE"),
        ("recovery_goal", "что для восстановления после нагрузки", "clarification", ["цель"], "TG-SAFE-ADVICE"),
        ("sleep_goal", "плохо сплю что посоветуете", "clarification", ["сон"], "TG-SAFE-ADVICE"),
        ("knee_discomfort", "болят колени", "clarification", ["не ставлю диагноз"], "TG-SAFE-DISCOMFORT"),
        ("back_discomfort", "болит спина", "clarification", ["не ставлю диагноз"], "TG-SAFE-SPINE"),
        ("want_advice", "хочу совет", "clarification", ["подобрать товар"], "TG-SAFE-ADVICE"),
        ("home_start", "с чего начать дома", "clarification", ["старт"], "TG-ROUTE-START"),
        ("office_neck", "что для офиса и шеи", "clarification", ["подобрать"], "TG-SAFE-ADVICE"),
        ("energy_pick", "что взять для энергии", "clarification", ["цель"], "TG-SAFE-ADVICE"),
        ("skin_care", "что для ухода за кожей", "clarification", ["космет"], "TG-SAFE-ADVICE"),
        ("hair_care", "что для волос посоветуете", "clarification", ["шампун"], "TG-SAFE-ADVICE"),
        ("digestion_pick", "что выбрать для пищеварения", "clarification", ["цель"], "TG-SAFE-ADVICE"),
        ("home_device", "нужен прибор для дома", "clarification", ["прибор"], "TG-SAFE-ADVICE"),
        ("gift_woman", "подарок женщине что лучше", "clarification", ["подар"], "TG-SAFE-ADVICE"),
        ("sport_recovery", "что спортсмену для восстановления", "clarification", ["восстанов"], "TG-SAFE-ADVICE"),
        ("budget_300", "подбор на 300 pv", "clarification", ["бюджет"], "NBZ-P1-040"),
        ("budget_byn", "что купить на 500 byn", "clarification", ["бюджет"], "NBZ-P1-040"),
        ("starter_help", "помогите выбрать старт", "clarification", ["старт"], "TG-ROUTE-START"),
        ("dont_know", "не знаю что выбрать", "clarification", ["подобрать"], "TG-SAFE-ADVICE"),
        ("family_home", "что для семьи дома", "clarification", ["дом"], "TG-SAFE-ADVICE"),
        ("travel_pick", "что взять в поездку", "clarification", ["подобрать"], "TG-SAFE-ADVICE"),
        ("winter_warm", "мерзну зимой что посоветуете", "clarification", ["цель"], "TG-SAFE-ADVICE"),
        ("feet_tired", "ноги устают что подобрать", "clarification", ["стельк"], "TG-SAFE-ADVICE"),
        ("eye_strain", "глаза устают от экрана", "clarification", ["очк"], "TG-SAFE-ADVICE"),
        ("business_start", "с чего начать бизнес whieda", "clarification", ["старт"], "TG-ROUTE-START"),
        ("routine_help", "собери рутину для дома", "clarification", ["подбор"], "TG-SAFE-ADVICE"),
    ]
    nbz_ref = "qa/no_blind_zone/whieda_no_blind_zone_cases_v1.jsonl"
    for name, text, mode, markers, src in task_flows:
        fid = _next_flow_id()
        kind = "no_blind_zone" if src.startswith("NBZ") else "telegram_experience"
        ref = f"{nbz_ref}:{src}" if kind == "no_blind_zone" else f"{tg_ref}:{src}"
        register_flow(fid, name=name, tags=["task_selection"], source_ref=ref)
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=1,
                user_text=text,
                expected_rail="task_selection",
                expected_mode=mode,
                must_contain_any=["подбер", "направлен"],
                acceptance_status="pending_policy" if text in POLICY_GAP_TEXTS else None,
                source_kind=kind,
                source_ref=ref,
                rationale="Goal-first request should route to compact selection by direction, not a blind error.",
            )
        )

    # --- Multi-turn task selection (goal clarify) ---
    task_mt = [
        ("pick_unknown", "не знаю что выбрать", "для дома", ["дом", "подобрать"]),
        ("pick_budget", "помогите подобрать", "до 500 pv", ["PV", "бюджет"]),
        ("pick_gift", "нужен подарок", "маме на день рождения", ["подар"]),
        ("pick_sleep", "плохо сплю", "система для сна", ["сон"]),
        ("pick_salon", "для салона", "бэм или активатор", ["Magic", "Активатор"]),
        ("pick_legs", "ноги устают", "стельки", ["стельк"]),
        ("pick_office", "работаю в офисе", "очки или палантин", ["очк", "палант"]),
        ("pick_start", "я новичок", "стартовая корзина", ["старт", "корзин"]),
        ("pick_cosmetics", "хочу косметику", "fundesee", ["Fundesee", "космет"]),
        ("pick_device", "нужен прибор", "активатор или вентун", ["Активатор", "Вэнтун"]),
    ]
    contract_ref = "WHIEDA_ADVISOR_EXPERIENCE_CONTRACT_V1_2026-08-13.md"
    # A person who has named two real products is no longer asking for a vague
    # direction: comparison is the shortest useful next answer.  A unique
    # selected product is likewise a product-choice, not a restart of the
    # whole goal interview.
    task_followup_contract = {
        "бэм или активатор": ("direct_answer", "structured_comparison_layer", ["Magic", "Активатор"]),
        "стельки": ("product_choices", "clarification", ["стельк"]),
        "очки или палантин": ("direct_answer", "structured_comparison_layer", ["очк", "палант"]),
        "стартовая корзина": ("task_selection", "clarification", ["корзин", "бюджет"]),
        "активатор или вентун": ("direct_answer", "structured_comparison_layer", ["Активатор", "Вэнтун"]),
    }
    for name, t1, t2, markers in task_mt:
        fid = _next_flow_id()
        register_flow(fid, name=name, tags=["task_selection", "multi_turn"], source_ref=contract_ref)
        cases.append(_setup(fid, 1, t1, contract_ref))
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=1,
                user_text=t1,
                expected_rail="task_selection",
                expected_mode="clarification",
                must_contain_any=["подбер", "направлен"],
                source_kind="advisor_experience_contract",
                source_ref=contract_ref,
                rationale="Goal-only first turn should open compact direction selection.",
            )
        )
        followup_rail, followup_mode, followup_markers = task_followup_contract.get(
            t2, ("task_selection", "clarification", ["подбер", "направлен"])
        )
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=2,
                user_text=t2,
                expected_rail=followup_rail,
                expected_mode=followup_mode,
                must_contain_any=followup_markers,
                context_before=_ctx([("user", t1)]),
                source_kind="advisor_experience_contract",
                source_ref=contract_ref,
                rationale=(
                    "A concrete pair should be compared directly."
                    if followup_mode == "structured_comparison_layer"
                    else "Follow-up narrows the selection route without hard failure."
                ),
            )
        )

    # --- Single-turn direct_answer (bulk) ---
    direct_singles = [
        ("активатор клеток", "structured_card", ["Активатор"], "TG-PRES-CARD-ACT"),
        ("цена активатора", "structured_price", ["BYN"], "TG-PRES-PRICE"),
        ("фото активатора", "structured_photo", ["фото"], "TG-PRES-PHOTO"),
        ("видео активатора", "structured_video", ["видеo", "видео"], "TG-PRES-VIDEO"),
        ("сертификат активатора", "structured_certificate", ["http", "Материалы"], "TG-PRES-CERT"),
        ("сравни активатор и pro", "structured_comparison_layer", ["PRO"], "TG-PRES-COMPARE"),
        ("Посчитай: активатор, бэм", "structured_cart", ["PV", "BYN"], "TG-CART-CALC-REMOVE"),
        ("бэм", "structured_card", ["Magic"], "TG-PRES-CARD-BEM"),
        ("ативатор", "structured_card", ["Активатор"], "NBZ-P0-007"),
        ("цена активатор клеток", "structured_price", ["1750", "BYN"], "NBZ-P0-008"),
        ("зеленый эликсир", "structured_price", ["BYN"], "CONV-F11"),
        ("синий эликсир", "structured_price", ["BYN"], "CONV-F12"),
        ("вэнтун", "structured_card", ["Вэнтун"], "CONV-F28"),
        ("соевый пептид", "structured_card", ["пептид"], "CONV-F29"),
        ("что такое pv", "structured_business_faq", ["PV"], "CONV-F32"),
        ("это mlm", "structured_business_objection", ["WHIEDA"], "CONV-F33"),
        ("ближайшие события", "structured_event", ["WHIEDA"], "CONV-F34"),
        ("сообщество whieda", "structured_community", ["канал"], "CONV-F35"),
        ("калькулятор", "structured_business", ["Посчитай"], "TG-ROUTE-CALC"),
        ("расскажи о компании", "structured_business", ["WHIEDA"], "TG-ROUTE-COMPANY"),
        ("как заработать?", "structured_business", ["доход"], "TG-ROUTE-INCOME"),
        ("какие акции сейчас", "structured_promotion", ["акци"], "CONV-F18"),
        ("сравни активатор клеток и активатор клеток pro", "structured_comparison_layer", ["PRO"], "NBZ-P1-039"),
        ("цена сауны", "structured_price", ["Ба-Гуа", "BYN"], "CONV-F15"),
        ("активatr", "clarification", ["Активатор"], "NBZ-P0-007"),
        ("спирулина таблетки", "structured_card", ["Спирулин"], "CONV-F29"),
    ]
    for text, mode, markers, src in direct_singles:
        fid = _next_flow_id()
        kind = "telegram_experience" if src.startswith("TG") else ("no_blind_zone" if src.startswith("NBZ") else "conversation_reliability")
        ref_base = {"telegram_experience": tg_ref, "no_blind_zone": nbz_ref, "conversation_reliability": conv_ref}[kind]
        ref = f"{ref_base}:{src}"
        register_flow(fid, name=f"single_{src}", tags=["direct_answer"], source_ref=ref)
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=1,
                user_text=text,
                expected_rail="product_choices" if text == "активatr" else "direct_answer",
                expected_mode=mode,
                must_contain_any=markers,
                source_kind=kind,
                source_ref=ref,
                rationale=(
                    "A typo for an intentionally ambiguous product family must offer a clear choice."
                    if text == "активatr"
                    else "Clear product or business intent should produce structured answer."
                ),
            )
        )

    # --- Single-turn product_choices ---
    choice_singles = [
        ("пептид", ["пептид"], "NBZ-P0-011"),
        ("pro", ["pro"], "NBZ-P0-014"),
        ("эликсир", ["эликсир"], "NBZ-P0-012"),
        ("стельки", ["стельк"], "NBZ-P0-010"),
        ("очки", ["очк"], "TG-PRES-CARD-ACT"),
        ("маска", ["маск", "Fundesee"], "catalog_experience"),
        ("гель", ["гель", "Foherb"], "catalog_experience"),
        ("шампунь", ["шампун"], "catalog_experience"),
        ("набор", ["набор"], "catalog_experience"),
        ("бад", ["уточн", "капсул"], "NBZ-P0-010"),
        ("кофе", ["кофе", "корди"], "CONV-F34"),
        ("капсулы", ["капсул"], "NBZ-P0-011"),
        ("чай", ["чай"], "CONV-F31"),
        ("линчжи", ["линчж"], "CONV-F28"),
        ("прокладки", ["проклад"], "NBZ-P0-009"),
    ]
    cat_ref = "qa/catalog_experience/CATALOG_EXPERIENCE_MATRIX_2026-08-14.csv"
    for text, markers, src in choice_singles:
        fid = _next_flow_id()
        kind = "catalog_experience" if src == "catalog_experience" else ("no_blind_zone" if src.startswith("NBZ") else "conversation_reliability")
        ref = cat_ref if kind == "catalog_experience" else (f"{nbz_ref}:{src}" if kind == "no_blind_zone" else f"{conv_ref}:{src}")
        register_flow(fid, name=f"choice_{text}", tags=["product_choices"], source_ref=ref)
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=1,
                user_text=text,
                expected_rail="direct_answer" if text in {"линчжи", "прокладки"} else "product_choices",
                expected_mode="structured_card" if text in {"линчжи", "прокладки"} else "clarification",
                must_contain_any=markers,
                source_kind=kind,
                source_ref=ref,
                rationale=(
                    "A unique, well-known product name should open its card immediately."
                    if text in {"линчжи", "прокладки"}
                    else "Under-specified product nickname must show understandable choices."
                ),
            )
        )

    # --- Single-turn universal_menu ---
    uni_singles = [
        ("привет", ["Здравств", "WHIEDA"], "TG-SVC-GREET-HI", "structured_business"),
        ("здарова", ["Здравств"], "test_telegram_service_intent_fuzz.py", "greeting"),
        ("хай", ["Здравств"], "test_telegram_service_intent_fuzz.py", "greeting"),
        ("че ты можеь?", ["Могу", "WHIEDA"], "TG-PRES-CAP-TYPO", "structured_business"),
        ("что можешь", ["WHIEDA", "Могу"], "test_telegram_service_intent_fuzz.py", "capabilities"),
        ("что умеешь", ["WHIEDA"], "test_telegram_service_intent_fuzz.py", "capabilities"),
        ("можешь?", ["Могу"], "TG-SVC-CAP-MOZH", "structured_business"),
        ("помощь", ["товар"], "TG-ROUTE-HELP", "structured_business"),
        ("как дела", ["на связи"], "test_telegram_service_intent_fuzz.py", "smalltalk_status"),
        ("ты живой", ["на связи"], "test_telegram_service_intent_fuzz.py", "smalltalk_status"),
        ("расскажи про xyzunknown123", ["каталог", "Выберите направление"], "TG-DATA-MISSING-PRODUCT", "knowledge_gap"),
        ("погода в минске", ["WHIEDA", "Выберите направление"], "NBZ-P1-023", "clarification"),
        ("как инвестировать в биржу", ["WHIEDA"], "NBZ-P1-022", "clarification"),
        ("пивка хочешь?", ["сценар", "WHIEDA"], "TG-ORDER-CAP-OOS", "clarification"),
        ("курс доллара сегодня", ["WHIEDA"], "CONV-F24", "clarification"),
        ("qwerty", ["WHIEDA", "товар"], "test_telegram_service_intent_fuzz.py", None),
        ("...", ["WHIEDA", "товар"], "test_telegram_service_intent_fuzz.py", None),
        ("а?", ["WHIEDA", "товар"], "test_telegram_service_intent_fuzz.py", None),
        ("123", ["WHIEDA"], "test_telegram_service_intent_fuzz.py", None),
        ("ghbdtn", ["WHIEDA"], "test_telegram_service_intent_fuzz.py", None),
        ("привет!", ["Здравств"], "TG-SVC-GREET-HI", "structured_business"),
        ("добрый день", ["Здравств"], "TG-SVC-GREET-DAY", "structured_business"),
        ("здрасьte", ["Здравств"], "test_telegram_service_intent_fuzz.py", "greeting"),
        ("расскажи про qwertyunknown999", ["каталог"], "NBZ-P0-003", "knowledge_gap"),
        ("magic unknown product test999", ["Выберите направление"], "NBZ-P0-006", "knowledge_gap"),
        ("сравни фейк1 и фейк2", ["товар"], "NBZ-P0-021", "clarification"),
    ]
    fuzz_ref = "backend/platform-api/tests/test_telegram_service_intent_fuzz.py"
    for text, markers, src, mode in uni_singles:
        fid = _next_flow_id()
        if src.endswith(".py"):
            kind, ref = "service_intent_fuzz", f"{fuzz_ref}:INTENT_CASES"
        elif src.startswith("NBZ"):
            kind, ref = "no_blind_zone", f"{nbz_ref}:{src}"
        elif src.startswith("CONV"):
            kind, ref = "conversation_reliability", f"{conv_ref}:{src}"
        else:
            kind, ref = "telegram_experience", f"{tg_ref}:{src}"
        comparison_missing_pair = text == "сравни фейк1 и фейк2"
        register_flow(
            fid,
            name=f"uni_{text[:20]}",
            tags=["product_choices"] if comparison_missing_pair else ["universal_menu"],
            source_ref=ref,
        )
        row = _assertion(
            flow_id=fid,
            turn_index=1,
            user_text=text,
            expected_rail=(
                "product_choices"
                if comparison_missing_pair or text in {"цена", "фото", "видео", "сертификат", "подробнее", "сколько стоит"}
                else "universal_menu"
            ),
            expected_mode=mode,
            must_contain_any=markers,
            source_kind=kind,
            source_ref=ref,
            rationale="Off-topic, greeting, fragment or unknown product must not hard-fail.",
        )
        cases.append(row)

    # --- Extra malformed direct/choice singles to hit malformed quota ---
    extra_malformed = [
        ("фnbdtn активатор", "direct_answer", "structured_card", ["Активатор"], "NBZ-P0-007"),
        ("активatr", "product_choices", "clarification", ["Активатор"], "NBZ-P0-007"),
        ("бэмчик", "direct_answer", "structured_card", ["Magic"], "TG-PRES-CARD-BEM"),
        ("вентун", "direct_answer", "structured_card", ["Вэнтун"], "CONV-F28"),
        ("спирулинa", "direct_answer", "structured_card", ["Спирулин"], "CONV-F29"),
        ("цена?", "product_choices", "clarification", ["товар"], "NBZ-P0-015"),
        ("цена", "product_choices", "clarification", ["товар"], "NBZ-P0-015"),
        ("фото", "product_choices", "clarification", ["товар"], "NBZ-P0-017"),
        ("фотка", "product_choices", "clarification", ["товар"], "NBZ-P0-017"),
        ("видео", "product_choices", "clarification", ["товар"], "NBZ-P0-016"),
        ("видос", "product_choices", "clarification", ["товар"], "NBZ-P0-016"),
        ("сертификат", "product_choices", "clarification", ["товар"], "NBZ-P0-019"),
        ("сертификатик", "universal_menu", "knowledge_gap", ["товар"], "NBZ-P0-019"),
        ("подробнее", "product_choices", "clarification", ["товар"], "NBZ-P0-018"),
        ("сколько стоит", "product_choices", "clarification", ["товар"], "NBZ-P1-041"),
        ("сравни", "product_choices", "clarification", ["товар"], "NBZ-P0-021"),
        ("пасту", "product_choices", "clarification", ["паст"], "NBZ-P0-009"),
        ("поис", "product_choices", "clarification", ["пояс"], "NBZ-P0-013"),
        ("актив", "product_choices", "clarification", ["активатор"], "NBZ-P0-010"),
        ("привт", "universal_menu", "structured_business", ["Здравств"], "TG-PRES-GREET"),
        ("чё ты можешь", "universal_menu", "structured_business", ["Могу"], "TG-PRES-CAP"),
        ("шо умеешь", "universal_menu", "capabilities", ["WHIEDA"], fuzz_ref),
        ("ку", "universal_menu", "greeting", ["WHIEDA"], fuzz_ref),
        ("ййй", "universal_menu", None, ["WHIEDA"], fuzz_ref),
        ("??", "universal_menu", None, ["WHIEDA"], fuzz_ref),
        ("нет", "universal_menu", None, ["WHIEDA"], fuzz_ref),
        ("ок", "universal_menu", None, ["WHIEDA"], fuzz_ref),
        ("спасибо", "universal_menu", "smalltalk_status", ["WHIEDA", "на связи"], fuzz_ref),
        ("пока", "universal_menu", "greeting", ["WHIEDA"], fuzz_ref),
        ("ну", "universal_menu", None, ["WHIEDA"], fuzz_ref),
        ("э", "universal_menu", None, ["WHIEDA"], fuzz_ref),
        ("хм", "universal_menu", None, ["WHIEDA"], fuzz_ref),
        ("help", "universal_menu", "help", ["товар"], fuzz_ref),
        ("hi", "universal_menu", "greeting", ["WHIEDA"], fuzz_ref),
        ("hello", "universal_menu", "greeting", ["WHIEDA"], fuzz_ref),
    ]
    for text, rail, mode, markers, src in extra_malformed:
        fid = _next_flow_id()
        if src.endswith(".py"):
            kind, ref = "service_intent_fuzz", f"{fuzz_ref}:INTENT_CASES"
        elif src.startswith("NBZ"):
            kind, ref = "no_blind_zone", f"{nbz_ref}:{src}"
        elif src.startswith("CONV"):
            kind, ref = "conversation_reliability", f"{conv_ref}:{src}"
        elif src.startswith("TG"):
            kind, ref = "telegram_experience", f"{tg_ref}:{src}"
        else:
            kind, ref = "service_intent_fuzz", f"{fuzz_ref}:INTENT_CASES"
        register_flow(fid, name=f"malformed_{text}", tags=["malformed"], source_ref=ref)
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=1,
                user_text=text,
                expected_rail=rail,
                expected_mode=mode,
                must_contain_any=markers,
                source_kind=kind,
                source_ref=ref,
                rationale="Messy human wording must still land on a useful rail.",
            )
        )

    # --- Context isolation singles (direct_answer with context) ---
    for text, mode, markers, ctx_text, src in [
        ("цена", "structured_price", ["BYN"], "активатор клеток", "NBZ-P0-028"),
        ("фото", "structured_photo", ["фото"], "активатор клеток", "NBZ-P0-029"),
    ]:
        fid = _next_flow_id()
        ref = f"{nbz_ref}:{src}"
        register_flow(fid, name=f"ctx_{text}", tags=["context_followup"], source_ref=ref)
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=1,
                user_text=text,
                expected_rail="direct_answer",
                expected_mode=mode,
                must_contain_any=markers,
                context_before=_ctx([("user", ctx_text)]),
                source_kind="no_blind_zone",
                source_ref=ref,
                rationale="Bare follow-up with product context must resolve structurally.",
            )
        )

    # --- Accepted universal_menu bulk (unknown product / unsupported topic) ---
    accepted_uni_bulk = [
        ("расскажи про несуществующий товар xyzabc", "knowledge_gap", "NBZ-P0-002"),
        ("цена товара xyzunknown555", "knowledge_gap", "NBZ-P0-004"),
        ("расскажи про квантовую физику", "knowledge_gap", "NBZ-P1-024"),
        ("расскажи про unknown123product", "knowledge_gap", "NBZ-P1-042"),
        ("расскажи про международную логистику whieda", "clarification", "NBZ-P1-036"),
        ("что такое суперфейковый продукт abc123", "clarification", "NBZ-P0-005"),
        ("расскажи про whieda космический корабль", "knowledge_gap", "NBZ-P0-001"),
        ("есть ли товар zzznotfound777", "knowledge_gap", "NBZ-P0-003"),
        ("что за продукт fakeitem888", "knowledge_gap", "NBZ-P0-006"),
        ("расскажи про несуществующий активатор xyz", "knowledge_gap", "NBZ-P0-002"),
        ("цена на товар notreal999", "knowledge_gap", "NBZ-P0-004"),
        ("расскажи про абракадабра123", "knowledge_gap", "NBZ-P1-042"),
        ("что такое фейковый бад test000", "knowledge_gap", "NBZ-P0-005"),
        ("расскажи про несуществующий пояс qwe", "knowledge_gap", "NBZ-P0-002"),
        ("есть ли whieda суперпылесос", "knowledge_gap", "NBZ-P0-001"),
        ("расскажи про несуществующий шампунь abc", "knowledge_gap", "NBZ-P0-003"),
        ("цена на magic unknown sku 404", "knowledge_gap", "NBZ-P0-004"),
        ("расскажи про несуществующий крем xyz", "knowledge_gap", "NBZ-P1-042"),
        ("что за товар not_in_catalog_55", "knowledge_gap", "NBZ-P0-006"),
        ("расскажи про несуществующий набор qqq", "knowledge_gap", "NBZ-P0-002"),
        ("есть ли товар phantomsku123", "knowledge_gap", "NBZ-P0-003"),
        ("расскажи про несуществующий чай zzz", "knowledge_gap", "NBZ-P1-042"),
        ("цена phantom product 007", "knowledge_gap", "NBZ-P0-004"),
        ("расскажи про несуществующий гель www", "knowledge_gap", "NBZ-P0-002"),
        ("что такое несуществующий девайс x1", "knowledge_gap", "NBZ-P0-005"),
        ("расскажи про несуществующий бальзам y2", "knowledge_gap", "NBZ-P0-003"),
        ("есть ли товар ghostsku999", "knowledge_gap", "NBZ-P0-006"),
        ("расскажи про несуществующий сироп z3", "knowledge_gap", "NBZ-P1-042"),
        ("цена на ghost product 111", "knowledge_gap", "NBZ-P0-004"),
        ("расскажи про несуществующий спрей a4", "knowledge_gap", "NBZ-P0-002"),
        ("что за продукт voidsku777", "knowledge_gap", "NBZ-P0-005"),
        ("расскажи про несуществующий комплекс b5", "knowledge_gap", "NBZ-P0-003"),
        ("есть ли товар nullitem000", "knowledge_gap", "NBZ-P0-006"),
        ("расскажи про несуществующий напиток c6", "knowledge_gap", "NBZ-P1-042"),
        ("цена на void product 222", "knowledge_gap", "NBZ-P0-004"),
        ("расскажи про несуществующий фильтр d7", "knowledge_gap", "NBZ-P0-002"),
        ("что такое fake sku e8", "knowledge_gap", "NBZ-P0-005"),
        ("расскажи про несуществующий массажер f9", "knowledge_gap", "NBZ-P0-003"),
        ("есть ли товар missing999", "knowledge_gap", "NBZ-P0-006"),
    ]
    for text, mode, src in accepted_uni_bulk:
        fid = _next_flow_id()
        if src == "SGF-004":
            kind, ref = "human_language_rails_backlog", "qa/human_language_rails/HUMAN_LANGUAGE_RAILS_BACKLOG.md:SGF-004"
        else:
            kind, ref = "no_blind_zone", f"{nbz_ref}:{src}"
        register_flow(fid, name=f"accepted_uni_{src}", tags=["universal_menu", "accepted"], source_ref=ref)
        cases.append(
            _assertion(
                flow_id=fid,
                turn_index=1,
                user_text=text,
                expected_rail="universal_menu",
                expected_mode=mode,
                acceptance_status="accepted",
                must_contain_any=["каталог"] if mode == "knowledge_gap" else ["WHIEDA"],
                source_kind=kind,
                source_ref=ref,
                rationale="Unknown product or unsupported topic must return durable universal menu markers.",
            )
        )

    # This is a valid catalog-browse request, not an unknown product.  It has
    # its own surface and must not be forced through the universal fallback.
    fid = _next_flow_id()
    catalog_ref = "qa/human_language_rails/HUMAN_LANGUAGE_RAILS_BACKLOG.md:SGF-004"
    register_flow(fid, name="catalog_any_product", tags=["catalog", "accepted"], source_ref=catalog_ref)
    cases.append(
        _assertion(
            flow_id=fid,
            turn_index=1,
            user_text="покажи любой товар",
            expected_rail="direct_answer",
            expected_mode="structured_business",
            acceptance_status="accepted",
            must_contain_any=["каталог"],
            source_kind="human_language_rails_backlog",
            source_ref=catalog_ref,
            rationale="A request to browse the catalog must open the catalog rail, not a fallback.",
        )
    )

    deduped = _dedupe_cases(cases)
    flows_meta = _reconcile_flow_metadata(deduped, flow_registry)
    return deduped, flows_meta


def _reconcile_flow_metadata(
    cases: list[dict[str, Any]], flow_registry: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    from collections import defaultdict

    by_flow: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cases:
        by_flow[str(row.get("flow_id") or "")].append(row)
    out: list[dict[str, Any]] = []
    for fid in sorted(by_flow):
        turns = by_flow[fid]
        reg = flow_registry.get(fid, {})
        out.append(
            {
                "flow_id": fid,
                "name": reg.get("name", fid),
                "turn_count": len(turns),
                "assertion_count": sum(1 for t in turns if t.get("turn_role") == "assertion"),
                "tags": reg.get("tags", []),
                "source_ref": reg.get("source_ref", ""),
                "multi_turn": len(turns) >= 2,
            }
        )
    return out


def _dedupe_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for row in cases:
        if row.get("turn_role") != "assertion":
            out.append(row)
            continue
        key = (
            str(row.get("user_text") or "").casefold().strip(),
            json.dumps(row.get("context_before") or [], ensure_ascii=False, sort_keys=True),
            str(row.get("expected_rail") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def write_outputs(cases: list[dict[str, Any]], flows_meta: list[dict[str, Any]]) -> None:
    PENDING_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    pending = [
        row
        for row in cases
        if row.get("turn_role") == "assertion"
        and row.get("acceptance_status") in {"pending_surface", "pending_policy"}
    ]
    with CORPUS_PATH.open("w", encoding="utf-8") as handle:
        for row in cases:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with FLOWS_PATH.open("w", encoding="utf-8") as handle:
        for row in flows_meta:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    with PENDING_FIXTURE.open("w", encoding="utf-8") as handle:
        for row in pending:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    cases, flows_meta = build_all()
    write_outputs(cases, flows_meta)
    assertions = sum(1 for c in cases if c.get("turn_role") == "assertion")
    accepted = sum(1 for c in cases if c.get("turn_role") == "assertion" and c.get("acceptance_status") == "accepted")
    pending = assertions - accepted
    flow_ids = {c["flow_id"] for c in cases}
    print(
        f"Wrote {len(cases)} turns ({assertions} assertions: {accepted} accepted, {pending} pending) "
        f"across {len(flow_ids)} flows ({len(flows_meta)} metadata rows)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
