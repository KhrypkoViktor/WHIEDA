"""Import Tier-1 sources into golden corpus schema (read-only upstream)."""

from __future__ import annotations

import ast
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

DEFAULT_MUST_NOT = ["Traceback", "Dify", "не знаю", "нет в базе", "передам", "I need human review"]

CATALOG_PHRASES = (
    "какие есть товары",
    "какие товары",
    "какой есть товар",
    "какой товар есть",
    "что есть из товаров",
    "любой товар",
    "каталог",
    "список товаров",
    "📦 товары",
)

GREETING_RE = re.compile(
    r"^(привет|приве|здравств|здарова|здрасьте|хай|ха[йе]|добрый|hello|hi)\b",
    re.I,
)
CAPABILITY_RE = re.compile(
    r"(что ты умеешь|что умеешь|что можешь|что ты можешь|че\s+ты\s+може|"
    r"помощь|help|меню|команды)",
    re.I,
)
COMPANY_RE = re.compile(r"расскаж\w*\s+о\s+компан|о\s+компании|кто\s+такая\s+whieda", re.I)
BASKET_RE = re.compile(r"(посчитай|корзин|стартов|набор|подбор|подбери|калькулятор)", re.I)
BUSINESS_RE = re.compile(
    r"(заработ|pv\b|повтор|бинар|mlm|млм|маркетинг|доход|step|бонус|партнёр)",
    re.I,
)

# Mirror of backend/platform-api/tests/test_telegram_service_intent_fuzz.py::INTENT_CASES
SERVICE_INTENT_FUZZ_PROVENANCE = (
    "backend/platform-api/tests/test_telegram_service_intent_fuzz.py::INTENT_CASES"
)

INTENT_TO_CLASS: dict[str, str] = {
    "greeting": "greeting",
    "smalltalk_status": "greeting",
    "capabilities": "capabilities",
    "help": "capabilities",
}

INTENT_MUST_CONTAIN: dict[str, list[str]] = {
    "greeting": ["Здравств"],
    "smalltalk_status": ["на связи"],
    "capabilities": ["WHIEDA"],
    "help": ["товар"],
}

CLASS_MINIMUMS: dict[str, int] = {
    "greeting": 6,
    "capabilities": 8,
    "company": 4,
    "catalog": 6,
    "product_card": 20,
    "price": 18,
    "media": 15,
    "clarification": 12,
    "basket": 6,
    "business": 10,
    "safe_boundary": 10,
}

SMOKE_INTENT_TO_CLASS: dict[str, str] = {
    "product_overview": "product_card",
    "product_price": "price",
    "product_photo": "media",
    "product_video": "media",
    "product_document": "media",
    "product_compare": "product_card",
    "product_limitations": "safe_boundary",
    "clarify": "clarification",
    "business_faq": "business",
    "business_objection": "business",
    "safety_limited": "safe_boundary",
}

SMOKE_INTENT_TO_MODE: dict[str, str] = {
    "product_overview": "structured_card",
    "product_price": "structured_price",
    "product_photo": "structured_photo",
    "product_video": "structured_video",
    "product_document": "structured_certificate",
    "product_compare": "structured_comparison_layer",
    "product_limitations": "clarification",
    "clarify": "clarification",
    "business_faq": "structured_business_faq",
    "business_objection": "structured_business_objection",
    "safety_limited": "clarification",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _dedupe_key(case: dict[str, Any]) -> str:
    inp = case.get("input") or {}
    ctx = case.get("context_before") or {}
    blob = json.dumps(
        {
            "text": _normalize(inp.get("user_text", "")),
            "class": case.get("class"),
            "ctx": ctx,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _split_contains(value: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[|;]", str(value or "")) if p.strip()]
    return [p for p in parts if p.lower() not in {"dify", "answer"}]


def _default_media(photo: str = "none") -> dict[str, Any]:
    return {"photo": photo, "video_count_min": 0, "document_count_min": 0}


def _media_from_smoke(row: dict[str, str], mode: str) -> dict[str, Any]:
    photo_count = int(row.get("expected_photo_count") or "0")
    if mode == "structured_photo":
        return {"photo": "required" if photo_count else "allow", "video_count_min": 0, "document_count_min": 0}
    if mode == "structured_video":
        return {"photo": "none", "video_count_min": 1, "document_count_min": 0}
    if mode == "structured_certificate":
        return {"photo": "allow", "video_count_min": 0, "document_count_min": 1}
    if mode == "structured_card":
        return {"photo": "required" if photo_count else "allow", "video_count_min": 0, "document_count_min": 0}
    return _default_media()


def _gap_from_smoke(intent: str, row: dict[str, str]) -> str | None:
    if intent == "safety_limited":
        return "medical_or_safety_boundary"
    key = str(row.get("expected_clarification_key") or "")
    if key == "details_topic_unknown":
        return "unknown_followup"
    if intent == "clarify" and "ambiguity" in key:
        return "ambiguous_product"
    return None


def _class_from_business_mode(mode: str, text: str) -> str:
    if mode == "structured_business_faq" or mode == "structured_business_objection":
        return "business"
    if mode == "structured_starter_basket" or mode == "structured_cart":
        return "basket"
    if mode == "structured_event" or mode == "structured_community":
        return "company"
    normalized = _normalize(text)
    if normalized in CATALOG_PHRASES or normalized.rstrip("?") in CATALOG_PHRASES:
        return "catalog"
    if COMPANY_RE.search(text):
        return "company"
    if GREETING_RE.search(normalized) or normalized in {"как дела", "как ты", "ты живой"}:
        return "greeting"
    if CAPABILITY_RE.search(text) or normalized in {"помощь", "помоги", "help"}:
        return "capabilities"
    if BASKET_RE.search(text):
        return "basket"
    if BUSINESS_RE.search(text):
        return "business"
    return "capabilities"


def _mode_for_class(klass: str, base_mode: str, text: str) -> str:
    if klass == "catalog":
        return "navigation_catalog"
    if klass == "greeting" and base_mode == "structured_business":
        return "structured_business"
    return base_mode


def _context_transition_sets(turn: dict[str, Any]) -> dict[str, Any]:
    ctx = turn.get("expected_context") or {}
    sets: dict[str, Any] = {}
    for key in ("last_product_sku", "last_product_name", "pending_product_clarification"):
        if ctx.get(key) not in (None, ""):
            sets[key] = ctx[key]
    return sets


def _build_case(
    *,
    case_id: str,
    klass: str,
    user_text: str,
    mode: str,
    must_contain: list[str],
    must_not_contain: list[str] | None = None,
    gap_kind: str | None = None,
    expected_media: dict[str, Any] | None = None,
    context_before: dict[str, Any] | None = None,
    context_sets: dict[str, Any] | None = None,
    context_clears: list[str] | None = None,
    priority: str = "P0",
    source: dict[str, Any] | None = None,
    notes: str = "",
    country: str = "BY",
    session: str | None = None,
) -> dict[str, Any]:
    must_not = list(must_not_contain or DEFAULT_MUST_NOT)
    for forbidden in DEFAULT_MUST_NOT:
        if forbidden not in must_not:
            must_not.append(forbidden)
    safe_session = session or f"golden-{_normalize(case_id).replace(' ', '-')[:24]}"
    return {
        "case_id": case_id,
        "class": klass,
        "priority": priority,
        "source": source or {"kind": "manual"},
        "input": {
            "user_text": user_text.strip(),
            "surface": "telegram",
            "country": country,
            "session": safe_session,
        },
        "context_before": context_before or {},
        "expected": {
            "mode": mode,
            "gap_kind": gap_kind,
            "must_contain": [m for m in must_contain if m],
            "must_not_contain": must_not,
            "expected_media": expected_media or _default_media(),
        },
        "expected_context_transition": {
            "sets": context_sets or {},
            "clears": context_clears or [],
            "requires": {},
        },
        **({"notes": notes} if notes else {}),
    }


def import_smoke_cases(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    flow_groups: dict[str, list[dict[str, str]]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            if str(row.get("enabled", "TRUE")).upper() != "TRUE":
                continue
            case_id_src = str(row.get("case_id") or "").strip()
            intent = str(row.get("expected_intent") or "").strip()
            if intent == "review_command":
                continue
            klass = SMOKE_INTENT_TO_CLASS.get(intent)
            if not klass:
                continue
            mode = SMOKE_INTENT_TO_MODE.get(intent, "clarification")
            must_contain = _split_contains(row.get("expected_answer_contains") or "")
            if intent in {"product_photo"} and not must_contain:
                must_contain = ["фото"]
            if intent == "product_video" and not must_contain:
                must_contain = ["Видео"]
            if intent == "product_document" and not must_contain:
                must_contain = ["Материалы"]
            if intent == "safety_limited" and not must_contain:
                must_contain = ["не заменяет"]
            if intent == "product_limitations" and not must_contain:
                must_contain = ["Огранич"]
            if intent == "business_faq" and not must_contain:
                must_contain = ["WHIEDA"]
            if intent == "business_objection" and not must_contain:
                must_contain = ["WHIEDA"]
            if intent == "business_faq" and "повтор" in _normalize(text):
                must_contain = must_contain or ["повтор"]
            if intent == "business_faq" and "бинар" in _normalize(text):
                must_contain = must_contain or ["бинар"]
            if not must_contain and klass not in {"clarification", "safe_boundary"}:
                entity = str(row.get("expected_entity") or "").strip()
                if entity and klass == "product_card":
                    must_contain = ["Активатор"] if "ACT" in entity else ["BYN"] if klass == "price" else ["WHIEDA"]
            gap_kind = _gap_from_smoke(intent, row)
            media = _media_from_smoke(row, mode)
            conv_id = str(row.get("conversation_id") or "").strip()
            if conv_id:
                flow_groups.setdefault(conv_id, []).append(row)
                continue
            text = str(row.get("input_text") or "").strip()
            entity = str(row.get("expected_entity") or "").strip()
            sets: dict[str, Any] = {}
            if entity and klass in {"product_card", "price", "media"}:
                sets["last_product_sku"] = entity
            cases.append(
                _build_case(
                    case_id=f"GOLD-SMOKE-{case_id_src}",
                    klass=klass,
                    user_text=text,
                    mode=mode,
                    must_contain=must_contain,
                    gap_kind=gap_kind,
                    expected_media=media,
                    context_sets=sets,
                    priority=str(row.get("priority") or "P0"),
                    source={
                        "kind": "smoke_cases_raw",
                        "ref": case_id_src,
                        "provenance": str(row.get("source_id") or ""),
                    },
                )
            )
    flows: list[dict[str, Any]] = []
    for conv_id, rows in sorted(flow_groups.items()):
        rows.sort(key=lambda r: int(r.get("sequence_no") or 0))
        turns: list[dict[str, Any]] = []
        context_before: dict[str, Any] = {}
        for index, row in enumerate(rows, start=1):
            intent = str(row.get("expected_intent") or "")
            klass = SMOKE_INTENT_TO_CLASS.get(intent, "clarification")
            mode = SMOKE_INTENT_TO_MODE.get(intent, "clarification")
            must_contain = _split_contains(row.get("expected_answer_contains") or "")
            if not must_contain and intent == "product_photo":
                must_contain = ["фото"]
            turn_case = _build_case(
                case_id=f"GOLD-SMOKE-FLOW-{conv_id}-T{index}",
                klass=klass,
                user_text=str(row.get("input_text") or ""),
                mode=mode,
                must_contain=must_contain,
                gap_kind=_gap_from_smoke(intent, row),
                expected_media=_media_from_smoke(row, mode),
                context_before=dict(context_before),
                context_sets=_context_transition_sets({"expected_context": context_before}),
                priority=str(row.get("priority") or "P0"),
                source={"kind": "smoke_cases_raw", "ref": row.get("case_id"), "flow": conv_id},
            )
            turn_case["turn"] = index
            turns.append(turn_case)
            entity = str(row.get("expected_entity") or "").strip()
            if entity:
                context_before["last_product_sku"] = entity
        flows.append(
            {
                "flow_id": f"GOLD-FLOW-SMOKE-{conv_id}",
                "name": f"smoke_{conv_id}",
                "priority": turns[0]["priority"] if turns else "P0",
                "session": f"golden-smoke-{conv_id}",
                "country": "BY",
                "source": {"kind": "smoke_cases_raw", "ref": conv_id},
                "turns": turns,
            }
        )
    return cases, flows


def import_experience_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        flow = json.loads(line)
        flow_id = str(flow.get("flow_id") or "")
        category = str(flow.get("category") or "")
        for turn in flow.get("turns") or []:
            text = str(turn.get("input") or "").strip()
            mode = str(turn.get("expected_mode") or "")
            if flow_id == "TG-PRES-CATALOG-ASK" or _normalize(text).rstrip("?") in CATALOG_PHRASES:
                klass = "catalog"
                mode = "navigation_catalog"
            elif mode == "clarification":
                gap = turn.get("expected_gap_kind")
                klass = "safe_boundary" if gap in {"unsupported_topic", "medical_or_safety_boundary"} else "clarification"
            elif mode == "knowledge_gap":
                klass = "clarification"
            elif mode in {"structured_photo", "structured_video", "structured_certificate"}:
                klass = "media"
            elif mode == "structured_card":
                klass = "product_card"
            elif mode == "structured_price":
                klass = "price"
            elif mode == "structured_cart" or mode == "structured_starter_basket":
                klass = "basket"
            elif mode in {"structured_business_faq", "structured_business_objection"}:
                klass = "business"
            elif mode == "structured_business":
                klass = _class_from_business_mode(mode, text)
                mode = _mode_for_class(klass, mode, text)
            else:
                klass = _class_from_business_mode(mode, text)
            must_contain = list(turn.get("must_contain") or [])
            if not must_contain and klass == "catalog":
                must_contain = ["Каталог"]
            cases.append(
                _build_case(
                    case_id=f"GOLD-EXP-{flow_id}-T{turn.get('turn')}",
                    klass=klass,
                    user_text=text,
                    mode=mode,
                    must_contain=must_contain,
                    must_not_contain=list(turn.get("must_not_contain") or DEFAULT_MUST_NOT),
                    gap_kind=turn.get("expected_gap_kind"),
                    expected_media=turn.get("expected_media") or _default_media(),
                    priority=str(flow.get("priority") or "P0"),
                    source={"kind": "telegram_experience", "ref": f"{flow_id} turn {turn.get('turn')}", "category": category},
                )
            )
    return cases


def import_conversation(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    flows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        flow = json.loads(line)
        flow_id = str(flow.get("flow_id") or "")
        turns_out: list[dict[str, Any]] = []
        context_before: dict[str, Any] = {}
        for turn in flow.get("turns") or []:
            text = str(turn.get("input") or "").strip()
            mode = str(turn.get("expected_mode") or "")
            if mode == "clarification":
                gap = turn.get("expected_gap_kind")
                klass = "safe_boundary" if gap in {"unsupported_topic", "medical_or_safety_boundary"} else "clarification"
            elif mode in {"structured_photo", "structured_video", "structured_certificate"}:
                klass = "media"
            elif mode == "structured_card":
                klass = "product_card"
            elif mode == "structured_price":
                klass = "price"
            elif mode in {"structured_cart", "structured_starter_basket"}:
                klass = "basket"
            elif mode in {"structured_business_faq", "structured_business_objection"}:
                klass = "business"
            elif mode in {"structured_event", "structured_community", "structured_promotion"}:
                klass = "company"
            elif mode == "structured_comparison_layer":
                klass = "product_card"
            else:
                klass = _class_from_business_mode(mode, text)
            expected_ctx = turn.get("expected_context") or {}
            requires = {k: v for k, v in context_before.items() if v is not None}
            case = _build_case(
                case_id=f"GOLD-CONV-{flow_id}-T{turn.get('turn')}",
                klass=klass,
                user_text=text,
                mode=mode if klass != "catalog" else "navigation_catalog",
                must_contain=list(turn.get("must_contain") or []),
                must_not_contain=list(turn.get("must_not_contain") or DEFAULT_MUST_NOT),
                gap_kind=turn.get("expected_gap_kind"),
                expected_media=turn.get("expected_media") or _default_media(),
                context_before=dict(context_before),
                context_sets=_context_transition_sets(turn),
                priority=str(flow.get("priority") or "P0"),
                source={"kind": "conversation_reliability", "ref": f"{flow_id} turn {turn.get('turn')}"},
                session=f"golden-{flow.get('session') or flow_id}",
            )
            case["expected_context_transition"]["requires"] = requires
            case["turn"] = turn.get("turn")
            turns_out.append(case)
            cases.append({k: v for k, v in case.items() if k != "turn"})
            context_before = dict(expected_ctx) if expected_ctx else dict(context_before)
        flows.append(
            {
                "flow_id": f"GOLD-FLOW-{flow_id}",
                "name": str(flow.get("name") or flow_id),
                "priority": str(flow.get("priority") or "P0"),
                "session": f"golden-{flow.get('session') or flow_id}",
                "country": str(flow.get("country") or "BY"),
                "source": {"kind": "conversation_reliability", "ref": flow_id},
                "turns": turns_out,
            }
        )
    return cases, flows


def import_no_blind_zone(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        group = str(row.get("group") or "")
        gap = row.get("expected_gap_kind")
        if group in {"unknown_product", "ambiguous_product", "unknown_followup"}:
            klass = "clarification"
        elif group in {"unsupported_topic", "medical_or_safety_boundary"}:
            klass = "safe_boundary"
        else:
            continue
        mode = str(row.get("expected_mode") or "clarification")
        must_contain = list(row.get("must_contain") or [])
        cases.append(
            _build_case(
                case_id=f"GOLD-NBZ-{row.get('case_id')}",
                klass=klass,
                user_text=str(row.get("input") or ""),
                mode=mode,
                must_contain=must_contain,
                gap_kind=gap,
                priority=str(row.get("priority") or "P0"),
                source={"kind": "no_blind_zone", "ref": row.get("case_id"), "group": group},
                session=str(row.get("session") or f"golden-nbz-{row.get('case_id')}"),
            )
        )
    return cases


def import_catalog_navigation() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    phrases = list(CATALOG_PHRASES) + ["📦 Товары", "какие есть товары?"]
    for index, phrase in enumerate(phrases, start=1):
        cases.append(
            _build_case(
                case_id=f"GOLD-CAT-NAV-{index:02d}",
                klass="catalog",
                user_text=phrase,
                mode="navigation_catalog",
                must_contain=["Каталог"],
                source={"kind": "telegram_navigation", "ref": phrase},
            )
        )
    return cases


def _load_service_intent_cases(root: Path) -> list[tuple[str, str | None]]:
    path = root / "backend/platform-api/tests/test_telegram_service_intent_fuzz.py"
    if not path.is_file():
        return []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        value_node = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "INTENT_CASES":
            value_node = node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "INTENT_CASES":
                    value_node = node.value
                    break
        if value_node is not None:
            value = ast.literal_eval(value_node)
            return [(str(a), b if b is None else str(b)) for a, b in value]
    return []


def import_service_intent_fuzz(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    cases: list[dict[str, Any]] = []
    for index, (phrase, intent) in enumerate(_load_service_intent_cases(root), start=1):
        if intent is None:
            continue
        klass = INTENT_TO_CLASS.get(intent)
        if not klass:
            continue
        cases.append(
            _build_case(
                case_id=f"GOLD-FUZZ-{index:03d}",
                klass=klass,
                user_text=phrase,
                mode="structured_business",
                must_contain=list(INTENT_MUST_CONTAIN.get(intent, ["WHIEDA"])),
                source={
                    "kind": "service_intent_fuzz",
                    "ref": phrase,
                    "provenance": SERVICE_INTENT_FUZZ_PROVENANCE,
                },
            )
        )
    return cases


def import_business_extras() -> list[dict[str, Any]]:
    phrases = [
        ("📈 Бизнес", "structured_business", ["WHIEDA"]),
        ("маркетинг план", "structured_business_faq", ["WHIEDA"]),
        ("step bonus", "structured_business_faq", ["Step"]),
        ("сколько pv в активаторе", "structured_business_faq", ["PV"]),
    ]
    cases: list[dict[str, Any]] = []
    for index, (phrase, mode, must) in enumerate(phrases, start=1):
        cases.append(
            _build_case(
                case_id=f"GOLD-BUS-EXTRA-{index:02d}",
                klass="business",
                user_text=phrase,
                mode=mode,
                must_contain=must,
                source={"kind": "contract", "ref": phrase, "provenance": "golden_corpus_contract_v1"},
            )
        )
    return cases


def import_company_extras() -> list[dict[str, Any]]:
    phrases = [
        ("расскажи о компании whieda", ["WHIEDA"]),
        ("кто такая whieda", ["WHIEDA"]),
        ("🏢 О компании", ["WHIEDA"]),
        ("ближайшие события", ["эфир", "меропр"]),
    ]
    cases: list[dict[str, Any]] = []
    for index, (phrase, must) in enumerate(phrases, start=1):
        cases.append(
            _build_case(
                case_id=f"GOLD-CO-EXTRA-{index:02d}",
                klass="company",
                user_text=phrase,
                mode="structured_business" if "событ" not in phrase else "structured_event",
                must_contain=must,
                source={"kind": "contract", "ref": phrase, "provenance": "golden_corpus_contract_v1"},
            )
        )
    return cases


def dedupe_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    dropped: list[str] = []
    for case in cases:
        key = _dedupe_key(case)
        if key in seen:
            dropped.append(str(case.get("case_id")))
            continue
        seen.add(key)
        out.append(case)
    return out, dropped


def build_all(root: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    root = root or ROOT
    all_cases: list[dict[str, Any]] = []
    all_flows: list[dict[str, Any]] = []

    smoke_path = root / "n8n/current/source_batches/smoke_cases_sheet_v1/smoke_cases_raw.tsv"
    smoke_cases, smoke_flows = import_smoke_cases(smoke_path)
    all_cases.extend(smoke_cases)
    all_flows.extend(smoke_flows)

    exp_path = root / "qa/telegram_experience/whieda_telegram_experience_flows_v1.jsonl"
    if not exp_path.is_file():
        subprocess_build = root / "qa/telegram_experience/build_flows.py"
        if subprocess_build.is_file():
            subprocess.run([sys.executable, str(subprocess_build)], cwd=str(root), check=False)

    conv_path = root / "qa/conversation_reliability/whieda_conversation_flows_v1.jsonl"
    conv_cases, conv_flows = import_conversation(conv_path)
    all_cases.extend(conv_cases)
    all_flows.extend(conv_flows)

    nbz_path = root / "qa/no_blind_zone/whieda_no_blind_zone_cases_v1.jsonl"
    nbz_cases = import_no_blind_zone(nbz_path)
    all_cases.extend(nbz_cases)

    exp_cases = import_experience_cases(exp_path) if exp_path.is_file() else []
    all_cases.extend(exp_cases)
    fuzz_cases = import_service_intent_fuzz(root)
    bus_extra = import_business_extras()
    cat_nav = import_catalog_navigation()
    co_extra = import_company_extras()
    all_cases.extend(fuzz_cases)
    all_cases.extend(bus_extra)
    all_cases.extend(cat_nav)
    all_cases.extend(co_extra)

    deduped, dropped = dedupe_cases(all_cases)
    meta = {
        "sources": {
            "smoke_cases": len(smoke_cases),
            "experience": len(exp_cases),
            "conversation_cases": len(conv_cases),
            "no_blind_zone": len(nbz_cases),
            "service_intent_fuzz": len(fuzz_cases),
            "business_extras": len(bus_extra),
            "catalog_nav": len(cat_nav),
            "company_extras": len(co_extra),
            "dedupe_dropped": len(dropped),
        },
        "flows": len(all_flows),
        "cases_raw": len(all_cases),
        "cases_final": len(deduped),
    }
    return deduped, all_flows, meta
