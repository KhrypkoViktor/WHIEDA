"""Build company knowledge pack rows from read-only sources."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from constants import SNAPSHOT_REL, TOPICS
from layers import apply_audience_layers, question_audience_layer
from sources import (
    ADVISOR_CONTRACT,
    OBSIDIAN_PACK9,
    PRODUCT_DIRECTION,
    SourceBundle,
    read_doc_lines,
)

# Pack 9 table rows: (category, fact, obsidian_line, pack_label)
PACK9_ROWS: list[tuple[str, str, int, str]] = [
    (
        "company_overview",
        "Международная Ассоциация WHIEDA (World Health Industry and Economic Development Alliance) — глобальная организация со штаб-квартирой в Китае.",
        9,
        "company_stated",
    ),
    (
        "company_overview",
        "Развитие компании поддерживается на государственном уровне и осуществляется в рамках международной инициативы КНР «Один пояс, один путь».",
        10,
        "company_stated",
    ),
    (
        "production",
        "25% акций производственных мощностей принадлежит государству (КНР).",
        11,
        "company_stated",
    ),
    (
        "company_overview",
        "Деятельность компании охватывает более 50 стран на 4 континентах (Европа, Азия, Африка, Северная и Южная Америка, включая рынок СНГ).",
        12,
        "company_stated",
    ),
    (
        "production",
        "В структуру холдинга входят собственные производственные базы, в том числе Fohow (Tianjin) Pharmaceutical Co., Ltd., с автоматизированными линиями.",
        16,
        "company_stated",
    ),
    (
        "production",
        "Продукция и производство сертифицированы по международным стандартам: GMP, ISO9000, HACCP; заявлены сертификаты FDA (США), Халяль и Кошер.",
        20,
        "company_stated",
    ),
    (
        "leadership",
        "Главный консультант по ТКМ — профессор Чжан Данин; в его честь Международный астрономический союз назвал астероид №8311.",
        17,
        "company_stated",
    ),
]


def _snap_ref(bundle: SourceBundle, file_name: str) -> str:
    return f"{SNAPSHOT_REL.format(snapshot_id=bundle.snapshot_id)}/{file_name}"


def _fact(
    fact_id: str,
    topic: str,
    *,
    question_examples: str,
    fact_text: str,
    answer_scope: str,
    source_kind: str,
    source_ref: str,
    source_locator: str,
    provenance_status: str,
    confidence: str,
    owner_status: str,
    notes: str = "",
) -> dict[str, str]:
    return {
        "fact_id": fact_id,
        "topic": topic,
        "audience_layer": "",
        "question_examples": question_examples,
        "fact_text": fact_text,
        "answer_scope": answer_scope,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "source_locator": source_locator,
        "provenance_status": provenance_status,
        "confidence": confidence,
        "owner_status": owner_status,
        "notes": notes,
    }


def build_facts(bundle: SourceBundle) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seq = 1

    def next_id(prefix: str) -> str:
        nonlocal seq
        fid = f"CK-{prefix}-{seq:03d}"
        seq += 1
        return fid

    # --- company_overview (structured + docs) ---
    cap = next(x for x in bundle.layers["capability_responses"] if x.get("response_id") == "service-greeting")
    rows.append(
        _fact(
            next_id("OV"),
            "company_overview",
            question_examples="кто ты | что умеешь | чем поможешь",
            fact_text=str(cap.get("answer_text") or "").replace("⏎", " "),
            answer_scope="advisor_scope",
            source_kind="structured_snapshot",
            source_ref=_snap_ref(bundle, "capability_responses.tsv"),
            source_locator="row service-greeting / answer_text",
            provenance_status="verified",
            confidence="high",
            owner_status="accepted",
            notes=str(cap.get("owner_state") or ""),
        )
    )

    pd_lines = read_doc_lines(PRODUCT_DIRECTION)
    for line_no, text in enumerate(pd_lines, start=1):
        if text.startswith("WHIEDA — не справочник"):
            rows.append(
                _fact(
                    next_id("OV"),
                    "company_overview",
                    question_examples="что такое WHIEDA | что вы строите",
                    fact_text=text.strip(),
                    answer_scope="product_vision",
                    source_kind="internal_doc",
                    source_ref="WHIEDA_PRODUCT_DIRECTION.md",
                    source_locator=f"line {line_no}",
                    provenance_status="verified",
                    confidence="high",
                    owner_status="accepted",
                )
            )
        if text.startswith("Мы строим единую систему роста партнёрской структуры"):
            rows.append(
                _fact(
                    next_id("OV"),
                    "company_overview",
                    question_examples="как устроен путь партнёра | откуда приходят люди",
                    fact_text="WHIEDA строит единую систему роста партнёрской структуры: контент → сайт → советник → контакт → Telegram → материалы → наставник.",
                    answer_scope="product_vision",
                    source_kind="internal_doc",
                    source_ref="WHIEDA_PRODUCT_DIRECTION.md",
                    source_locator=f"line {line_no}",
                    provenance_status="verified",
                    confidence="high",
                    owner_status="accepted",
                )
            )

    contract_lines = read_doc_lines(ADVISOR_CONTRACT)
    for line_no, text in enumerate(contract_lines, start=1):
        if "Компания | история, люди" in text:
            rows.append(
                _fact(
                    next_id("OV"),
                    "company_overview",
                    question_examples="что советник должен знать о компании",
                    fact_text="Сценарий «Компания» в контракте опыта: история, люди, лидеры, производство, география, политика бренда — источник: утверждённый company layer (ещё не подключён к runtime).",
                    answer_scope="advisor_contract",
                    source_kind="internal_doc",
                    source_ref="backend/platform-api/docs/WHIEDA_ADVISOR_EXPERIENCE_CONTRACT_V1_2026-08-12.md",
                    source_locator=f"line {line_no}",
                    provenance_status="verified",
                    confidence="high",
                    owner_status="accepted",
                    notes="company layer planned; not live in Telegram yet",
                )
            )
            break

    # --- leadership (explicit people from structured snapshot) ---
    for idx, row in enumerate(bundle.layers["structure_owners"], start=2):
        code = str(row.get("structure_code") or "")
        rows.append(
            _fact(
                next_id("LD"),
                "leadership",
                question_examples=f"кто лидер структуры {code} | наставник {code}",
                fact_text=(
                    f"{row.get('leader_name')} — leader структуры {code}, "
                    f"Telegram @{row.get('leader_username')}, status {row.get('status')}"
                ),
                answer_scope="structure_leadership",
                source_kind="structured_snapshot",
                source_ref=_snap_ref(bundle, "structure_owners.tsv"),
                source_locator=f"row structure_code={code} (tsv line {idx})",
                provenance_status="verified",
                confidence="high",
                owner_status="accepted",
            )
        )

    for idx, row in enumerate(bundle.layers["partners_ref"], start=2):
        if str(row.get("partner_id") or "") != "nnm":
            continue
        rows.append(
            _fact(
                next_id("LD"),
                "leadership",
                question_examples="кто владелец whieda | кто владелец платформы",
                fact_text=f"Владелец платформы и продающего языка: {row.get('display_name')} (partners_ref nnm).",
                answer_scope="platform_leadership",
                source_kind="structured_snapshot",
                source_ref=_snap_ref(bundle, "partners_ref.tsv"),
                source_locator=f"row partner_id=nnm (tsv line {idx})",
                provenance_status="verified",
                confidence="high",
                owner_status="accepted",
                notes="Согласовано с WHIEDA_PRODUCT_DIRECTION.md (Виктор Хрипко)",
            )
        )
        break

    # --- history gaps ---
    rows.append(
        _fact(
            next_id("HIS"),
            "history",
            question_examples="когда основана WHIEDA | история компании | сколько лет компании",
            fact_text="",
            answer_scope="history",
            source_kind="owner_gap",
            source_ref="qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md",
            source_locator="section history",
            provenance_status="missing",
            confidence="low",
            owner_status="needs_owner_input",
            notes="Нет строки в structured snapshot и нет owner-approved history layer",
        )
    )

    # --- partner_business from business_faq ---
    seen_faq: set[str] = set()
    for idx, row in enumerate(bundle.layers["business_faq"], start=2):
        faq_id = str(row.get("faq_id") or "")
        if faq_id in seen_faq:
            continue
        seen_faq.add(faq_id)
        if str(row.get("review_status") or "") != "owner_approved":
            continue
        rows.append(
            _fact(
                next_id("BUS"),
                "partner_business",
                question_examples=str(row.get("aliases") or row.get("title") or ""),
                fact_text=str(row.get("answer_text") or "").strip(),
                answer_scope="business_faq",
                source_kind="structured_snapshot",
                source_ref=_snap_ref(bundle, "business_faq.tsv"),
                source_locator=f"row {faq_id} / answer_text (tsv line {idx})",
                provenance_status="verified",
                confidence="high",
                owner_status="accepted",
                notes=str(row.get("do_not_say") or "")[:200],
            )
        )

    # OBJ-03 origin rule (partner trust, not production certificate)
    for idx, row in enumerate(bundle.layers["business_objections"], start=2):
        if str(row.get("objection_id") or "") != "OBJ-03":
            continue
        rows.append(
            _fact(
                next_id("BUS"),
                "partner_business",
                question_examples="это китай | почему дорого | маркетплейс",
                fact_text=str(row.get("confirmed_answer_rule") or "").strip(),
                answer_scope="objection_handling",
                source_kind="structured_snapshot",
                source_ref=_snap_ref(bundle, "business_objections.tsv"),
                source_locator=f"row OBJ-03 / confirmed_answer_rule (tsv line {idx})",
                provenance_status="verified",
                confidence="high",
                owner_status="accepted",
                notes="Не заявлять сертификацию/уникальность без материалов SQL",
            )
        )
        break

    # --- technology ---
    for line_no, text in enumerate(pd_lines, start=1):
        if "Сайт строит интерфейс. Core хранит товары" in text:
            rows.append(
                _fact(
                    next_id("TEC"),
                    "technology",
                    question_examples="как устроена платформа | где хранятся цены",
                    fact_text=text.strip(),
                    answer_scope="platform_architecture",
                    source_kind="internal_doc",
                    source_ref="WHIEDA_PRODUCT_DIRECTION.md",
                    source_locator=f"line {line_no}",
                    provenance_status="verified",
                    confidence="high",
                    owner_status="accepted",
                )
            )
        if text.startswith("`RAW и источники → source distillate"):
            rows.append(
                _fact(
                    next_id("TEC"),
                    "technology",
                    question_examples="как готовится продуктовый контент | откуда берутся ответы",
                    fact_text="Конвейер контента: RAW → distillate → claim register → статья → карточка → SQL-ответы → RAG-фрагменты.",
                    answer_scope="content_pipeline",
                    source_kind="internal_doc",
                    source_ref="WHIEDA_PRODUCT_DIRECTION.md",
                    source_locator=f"line {line_no}",
                    provenance_status="verified",
                    confidence="high",
                    owner_status="accepted",
                )
            )

    cap_bus = next(x for x in bundle.layers["capability_responses"] if x.get("response_id") == "service-capabilities")
    rows.append(
        _fact(
            next_id("TEC"),
            "technology",
            question_examples="что умеет бот | какие функции",
            fact_text="Советник по capability_responses: товары, цены/PV, материалы, бизнес-FAQ, события — через SQL-ответы из structured master.",
            answer_scope="advisor_scope",
            source_kind="structured_snapshot",
            source_ref=_snap_ref(bundle, "capability_responses.tsv"),
            source_locator="row service-capabilities / answer_text (abbreviated summary)",
            provenance_status="verified",
            confidence="high",
            owner_status="accepted",
        )
    )

    # --- events ---
    for idx, row in enumerate(bundle.layers["events"], start=2):
        eid = str(row.get("event_id") or "")
        rows.append(
            _fact(
                next_id("EVT"),
                "events",
                question_examples="когда встреча | мероприятие минск | презентация whieda",
                fact_text=(
                    f"{row.get('title')}: {row.get('description')}; "
                    f"{row.get('city')}, {row.get('address')}; "
                    f"расписание {row.get('recurrence_rule') or row.get('starts_at')}; "
                    f"контакт {row.get('contact')}"
                ),
                answer_scope="events",
                source_kind="structured_snapshot",
                source_ref=_snap_ref(bundle, "events.tsv"),
                source_locator=f"row {eid} (tsv line {idx})",
                provenance_status="verified",
                confidence="high",
                owner_status="accepted",
            )
        )

    for idx, row in enumerate(bundle.layers["community_resources"], start=2):
        rid = str(row.get("resource_id") or "")
        rows.append(
            _fact(
                next_id("EVT"),
                "events",
                question_examples="канал whieda | новости сообщества",
                fact_text=f"{row.get('title')}: {row.get('description')} — {row.get('url')}",
                answer_scope="community",
                source_kind="structured_snapshot",
                source_ref=_snap_ref(bundle, "community_resources.tsv"),
                source_locator=f"row {rid} (tsv line {idx})",
                provenance_status="verified",
                confidence="high",
                owner_status="accepted",
            )
        )

    # --- contacts ---
    for idx, row in enumerate(bundle.layers["partners_ref"], start=2):
        pid = str(row.get("partner_id") or "")
        if pid != "nnm":
            continue
        rows.append(
            _fact(
                next_id("CON"),
                "contacts",
                question_examples="владелец платформы | к кому по сайту | ref nnm",
                fact_text=(
                    f"Платформенный профиль: {row.get('display_name')}, "
                    f"Telegram {row.get('telegram_username')}, сайт {row.get('public_site_url')}"
                ),
                answer_scope="platform_owner",
                source_kind="structured_snapshot",
                source_ref=_snap_ref(bundle, "partners_ref.tsv"),
                source_locator=f"row partner_id=nnm (tsv line {idx})",
                provenance_status="verified",
                confidence="high",
                owner_status="accepted",
            )
        )

    for idx, row in enumerate(bundle.layers["structure_owners"], start=2):
        code = str(row.get("structure_code") or "")
        rows.append(
            _fact(
                next_id("CON"),
                "contacts",
                question_examples=f"структура {code} | наставник {code} | invite",
                fact_text=(
                    f"Структура {code}: {row.get('leader_name')}, "
                    f"Telegram @{row.get('leader_username')}, "
                    f"бот {row.get('invite_link')}, статус {row.get('status')}"
                ),
                answer_scope="structure_routing",
                source_kind="structured_snapshot",
                source_ref=_snap_ref(bundle, "structure_owners.tsv"),
                source_locator=f"row structure_code={code} (tsv line {idx})",
                provenance_status="verified",
                confidence="high",
                owner_status="accepted",
            )
        )

    for idx, row in enumerate(bundle.layers["partners_ref"], start=2):
        pid = str(row.get("partner_id") or "")
        if pid == "nnm":
            continue
        loc = str(row.get("service_location") or row.get("country") or "GLOBAL")
        rows.append(
            _fact(
                next_id("CON"),
                "contacts",
                question_examples=f"консультант {row.get('display_name')} | ref {pid}",
                fact_text=(
                    f"{row.get('display_name')}: Telegram {row.get('telegram_username')}, "
                    f"ref {row.get('ref_url')}, локация {loc}"
                ),
                answer_scope="partner_contact",
                source_kind="structured_snapshot",
                source_ref=_snap_ref(bundle, "partners_ref.tsv"),
                source_locator=f"row partner_id={pid} (tsv line {idx})",
                provenance_status="verified",
                confidence="medium",
                owner_status="accepted",
                notes=str(row.get("notes") or "")[:160],
            )
        )

    rows.append(
        _fact(
            next_id("CON"),
            "contacts",
            question_examples="официальный email | телефон компании | юрлицо",
            fact_text="",
            answer_scope="corporate_contacts",
            source_kind="owner_gap",
            source_ref="qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md",
            source_locator="section contacts",
            provenance_status="missing",
            confidence="low",
            owner_status="needs_owner_input",
            notes="В structured snapshot нет корпоративного email/телефона",
        )
    )

    # --- production gap + pack9 company_stated ---
    rows.append(
        _fact(
            next_id("PRD"),
            "production",
            question_examples="где производят | адрес завода | страна производства",
            fact_text="",
            answer_scope="production_site",
            source_kind="owner_gap",
            source_ref="qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md",
            source_locator="section production",
            provenance_status="missing",
            confidence="low",
            owner_status="needs_owner_input",
            notes="Нет owner-approved production layer в structured snapshot",
        )
    )

    if OBSIDIAN_PACK9.is_file():
        for topic, fact_text, line_no, pack_label in PACK9_ROWS:
            rows.append(
                _fact(
                    next_id("PK9"),
                    topic if topic in TOPICS else "production",
                    question_examples="из пакета 9 компании",
                    fact_text=fact_text,
                    answer_scope="company_pack9",
                    source_kind="obsidian_rag",
                    source_ref=str(OBSIDIAN_PACK9),
                    source_locator=f"line {line_no}",
                    provenance_status="company_stated",
                    confidence="medium" if pack_label == "company_stated" else "low",
                    owner_status="needs_owner_input",
                    notes="Требует owner fact-check перед acceptance",
                )
            )
    else:
        bundle.unreadable.append(str(OBSIDIAN_PACK9))

    apply_audience_layers(rows)
    return rows


def build_leaders(bundle: SourceBundle) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    for idx, row in enumerate(bundle.layers["structure_owners"], start=2):
        code = str(row.get("structure_code") or "")
        rows.append(
            {
                "leader_id": f"LD-{code.upper()}",
                "display_name": str(row.get("leader_name") or ""),
                "role": f"structure owner ({code})",
                "region": "GLOBAL",
                "public_bio": f"Invite: {row.get('invite_link')}; status={row.get('status')}",
                "source_ref": _snap_ref(bundle, "structure_owners.tsv"),
                "source_locator": f"row structure_code={code} (line {idx})",
                "provenance_status": "verified",
                "owner_status": "accepted",
                "notes": str(row.get("owner_status") or ""),
            }
        )

    for idx, row in enumerate(bundle.layers["events"], start=2):
        contact = str(row.get("contact") or "").strip()
        if not contact:
            continue
        rows.append(
            {
                "leader_id": "LD-EVT-ELENA",
                "display_name": contact,
                "role": "event owner / contact (weekly Minsk presentation)",
                "region": "BY / Minsk",
                "public_bio": f"Owner field in event {row.get('event_id')}",
                "source_ref": _snap_ref(bundle, "events.tsv"),
                "source_locator": f"row {row.get('event_id')} contact column (line {idx})",
                "provenance_status": "verified",
                "owner_status": "accepted",
                "notes": "Same person as structure elena when routed",
            }
        )
        break

    for idx, row in enumerate(bundle.layers["partners_ref"], start=2):
        pid = str(row.get("partner_id") or "")
        if pid == "nnm":
            role = "platform owner / product language owner"
        elif pid == "harold":
            role = "service center contact (СЦ Минска)"
        else:
            role = "public partner consultant (test pilot)"
        rows.append(
            {
                "leader_id": f"LD-P-{pid.upper()}",
                "display_name": str(row.get("display_name") or ""),
                "role": role,
                "region": str(row.get("service_location") or row.get("country") or "GLOBAL"),
                "public_bio": str(row.get("notes") or "")[:200],
                "source_ref": _snap_ref(bundle, "partners_ref.tsv"),
                "source_locator": f"row partner_id={pid} (line {idx})",
                "provenance_status": "verified",
                "owner_status": "accepted" if pid == "nnm" else "pending_review",
                "notes": str(row.get("agreement_status") or "")[:120],
            }
        )

    if OBSIDIAN_PACK9.is_file():
        rows.append(
            {
                "leader_id": "LD-PK9-ZHANG",
                "display_name": "Чжан Данин",
                "role": "главный консультант по ТКМ (Pack 9, company_stated)",
                "region": "",
                "public_bio": "Профессор, основоположник нефрологии в ТКМ; астероид №8311.",
                "source_ref": str(OBSIDIAN_PACK9),
                "source_locator": "line 17",
                "provenance_status": "company_stated",
                "owner_status": "needs_owner_input",
                "notes": "Не в structured snapshot; только после owner approval",
            }
        )

    return rows


def build_questions(facts: list[dict[str, str]]) -> list[dict[str, object]]:
    by_topic: dict[str, list[str]] = defaultdict(list)
    accepted_ids: list[str] = []
    for fact in facts:
        if fact.get("owner_status") == "accepted" and fact.get("provenance_status") == "verified":
            accepted_ids.append(str(fact["fact_id"]))
        topic = str(fact.get("topic") or "")
        if fact.get("fact_text"):
            by_topic[topic].append(str(fact["fact_id"]))

    templates: list[tuple[str, str, str, list[str], str]] = [
        ("CKQ-001", "что такое WHIEDA", "company_overview", [], "WHIEDA_PRODUCT_DIRECTION.md"),
        ("CKQ-002", "чем вы отличаетесь от обычного бота", "company_overview", [], "capability_responses.tsv"),
        ("CKQ-003", "расскажи о компании whieda", "company_overview", [], "advisor_contract"),
        ("CKQ-004", "когда основана компания", "history", [], "owner_gap"),
        ("CKQ-005", "сколько лет whieda на рынке", "history", [], "owner_gap"),
        ("CKQ-006", "история whieda кратко", "history", [], "owner_gap"),
        ("CKQ-007", "кто владелец платформы", "leadership", [], "partners_ref.tsv"),
        ("CKQ-008", "кто такой виктор хрипко", "leadership", [], "structure_owners.tsv"),
        ("CKQ-009", "кто елена дatskevich", "leadership", [], "events.tsv"),
        ("CKQ-010", "кто консультанты whieda", "leadership", [], "partners_ref.tsv"),
        ("CKQ-011", "где производят товары whieda", "production", [], "owner_gap"),
        ("CKQ-012", "есть ли gmp сертификат", "production", [], OBSIDIAN_PACK9.name if OBSIDIAN_PACK9.is_file() else "owner_gap"),
        ("CKQ-013", "завод fohow где находится", "production", [], OBSIDIAN_PACK9.name if OBSIDIAN_PACK9.is_file() else "owner_gap"),
        ("CKQ-014", "как устроена платформа whieda", "technology", [], "WHIEDA_PRODUCT_DIRECTION.md"),
        ("CKQ-015", "откуда бот берёт цены", "technology", [], "WHIEDA_PRODUCT_DIRECTION.md"),
        ("CKQ-016", "что такое pv", "partner_business", [], "business_faq.tsv"),
        ("CKQ-017", "можно ли вывести кэшбэк", "partner_business", [], "business_faq.tsv"),
        ("CKQ-018", "что такое повторка", "partner_business", [], "business_faq.tsv"),
        ("CKQ-019", "степ гарантирован", "partner_business", [], "business_faq.tsv"),
        ("CKQ-020", "что такое бинарный бонус", "partner_business", [], "business_faq.tsv"),
        ("CKQ-021", "что выгоднее розница или регистрация", "partner_business", [], "business_faq.tsv"),
        ("CKQ-022", "можно посчитать доход", "partner_business", [], "business_faq.tsv"),
        ("CKQ-023", "когда выплаты", "partner_business", [], "business_faq.tsv"),
        ("CKQ-024", "что такое золотой треугольник", "partner_business", [], "business_faq.tsv"),
        ("CKQ-025", "как работают статусы", "partner_business", [], "business_faq.tsv"),
        ("CKQ-026", "это китайская компания", "partner_business", [], "business_objections.tsv"),
        ("CKQ-027", "это пирамида", "partner_business", [], "business_objections.tsv"),
        ("CKQ-028", "когда ближайшая встреча", "events", [], "events.tsv"),
        ("CKQ-029", "где проходит презентация в минске", "events", [], "events.tsv"),
        ("CKQ-030", "есть ли telegram канал whieda", "events", [], "community_resources.tsv"),
        ("CKQ-031", "как попасть в world club", "events", [], "community_resources.tsv"),
        ("CKQ-032", "как связаться с наставником", "contacts", [], "structure_owners.tsv"),
        ("CKQ-033", "telegram владельца платформы", "contacts", [], "partners_ref.tsv"),
        ("CKQ-034", "контакты sc минска", "contacts", [], "partners_ref.tsv"),
        ("CKQ-035", "официальный email whieda", "contacts", [], "owner_gap"),
        ("CKQ-036", "что умеет советник whieda", "company_overview", [], "capability_responses.tsv"),
        ("CKQ-037", "какой путь у нового партнёра", "company_overview", [], "WHIEDA_PRODUCT_DIRECTION.md"),
        ("CKQ-038", "есть ли company layer в боте", "company_overview", [], "advisor_contract"),
        ("CKQ-039", "как готовятся ответы о товарах", "technology", [], "WHIEDA_PRODUCT_DIRECTION.md"),
        ("CKQ-040", "чем pro отличается в бизнесе", "partner_business", [], "business_faq.tsv"),
        ("CKQ-041", "как записаться на четверг минск", "events", [], "events.tsv"),
        ("CKQ-042", "кто ведёт структуру elena", "leadership", [], "structure_owners.tsv"),
        ("CKQ-043", "ref страница анны лadной", "contacts", [], "partners_ref.tsv"),
        ("CKQ-044", "год основания fohow", "history", [], "owner_gap"),
        ("CKQ-045", "whieda и fohow одна компания", "company_overview", [], "owner_gap"),
        ("CKQ-046", "какие сертификаты у производства", "production", [], OBSIDIAN_PACK9.name if OBSIDIAN_PACK9.is_file() else "owner_gap"),
        ("CKQ-047", "сколько стран whieda", "company_overview", [], OBSIDIAN_PACK9.name if OBSIDIAN_PACK9.is_file() else "owner_gap"),
        ("CKQ-048", "что такое повторные покупки", "partner_business", [], "business_faq.tsv"),
    ]

    questions: list[dict[str, object]] = []
    for case_id, user_text, topic, _, source_ref in templates:
        topic_facts = [f for f in facts if f.get("topic") == topic and f.get("owner_status") == "accepted" and f.get("provenance_status") == "verified"]
        expected = [str(f["fact_id"]) for f in topic_facts[:3]]
        status = "accepted" if expected else "pending_owner"
        questions.append(
            {
                "case_id": case_id,
                "user_text": user_text,
                "audience_layer": question_audience_layer(case_id, topic, user_text),
                "expected_topic": topic,
                "expected_fact_ids": expected,
                "source_ref": source_ref,
                "acceptance_status": status,
            }
        )
    return questions


def build_owner_request(facts: list[dict[str, str]], bundle: SourceBundle) -> str:
    _ = facts, bundle
    return "\n".join(
        [
            "# WHIEDA — запрос владельцу",
            "",
            "Вставьте ответ прямо после двоеточия в каждой строке.",
            "",
            "Публичное описание (исправьте этот текст): WHIEDA — международная компания wellness и партнёрского развития. "
            "Мы работаем с продуктами для здоровья и поддерживаем партнёров в разных странах. "
            "Официальные новости и встречи публикуем в открытых каналах. "
            "Подробности о производстве и истории даём только по подтверждённым материалам. "
            "Связаться с нами можно через наставника или официальные контакты на сайте.",
            "",
            "Год основания:",
            "Где производят (страна, город):",
            "(необяз.) Ссылка на официальную историю или PDF с сертификатами:",
            "(необяз.) Email или URL страницы «Контакты»:",
            "(необяз.) Публичные представители (ФИО, роль):",
        ]
    )
