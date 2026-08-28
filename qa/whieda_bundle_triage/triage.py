"""Offline triage of WHIEDA bundle staging candidates. No publish, no Core writes."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
REPO_ROOT = HERE.parents[1]
N8N_ALIASES = REPO_ROOT / "n8n" / "current" / "whieda_bundle_aliases_v1.json"

REQUIRED_COLUMNS = {
    "record_id",
    "название_ситуации",
    "основной_товар",
    "source_id",
    "publication_status",
}
DECISIONS = (
    "ready_for_owner_review",
    "duplicate",
    "needs_product_mapping",
    "needs_source",
    "blocked_claim",
    "archive",
)
SAFETY_PATTERNS = {
    "oncology": ("онколог", "рак", "опухол"),
    "surgery": ("альтернатива операции", "вместо операции", "после операции", "постоперац"),
    "glaucoma": ("глауком",),
    "abscess": ("абсцесс", "флюс"),
    "children": ("ребен", "дети", "детск"),
    "resuscitation": ("реанимац",),
}
TRIAGE_COLUMNS = (
    "record_id",
    "title",
    "decision",
    "canonical_candidate_id",
    "catalog_mapping",
    "unmapped_items",
    "confidence",
    "reason",
    "source_id",
    "source_locator",
    "raw_quote_ref",
    "safety_signals",
    "owner_question",
    "publication_status",
)
UNKNOWN_COLUMNS = (
    "record_id",
    "item_text",
    "role",
    "reason",
    "owner_question",
)
ACTIVE_BUNDLES = {
    "bundle_energy_immunity": {
        "must_contain": "батарейк",
        "phrases": [
            ("мало энергии", "backend/platform-api/tests/test_solution_bundle_runtime.py"),
            ("усталость", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("туман в голове", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("батарейка", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("железный иммунитет", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("нет сил", "WHIEDA_GROK_BUNDLE_CANDIDATE_TRIAGE_TASK_V1_2026-08-28.md"),
            ("сел ресурс", "WHIEDA_GROK_BUNDLE_CANDIDATE_TRIAGE_TASK_V1_2026-08-28.md"),
            ("хочу батарею на 100", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("батарейка на 100%", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("упала энергия", "WHIEDA_GROK_BUNDLE_CANDIDATE_TRIAGE_TASK_V1_2026-08-28.md"),
        ],
    },
    "bundle_vessels_belly": {
        "must_contain": "живот",
        "phrases": [
            ("тяжесть и вздутие после еды", "backend/platform-api/tests/test_solution_bundle_runtime.py"),
            ("вздутие", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("живот", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("кишечник", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("сосуды", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("лёгкий живот", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("чистые сосуды", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("вздутие после еды", "backend/platform-api/tests/test_solution_bundle_runtime.py"),
            ("живот и вздутие", "WHIEDA_GROK_BUNDLE_CANDIDATE_TRIAGE_TASK_V1_2026-08-28.md"),
            ("сосуды и живот", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
        ],
    },
    "bundle_shape_recovery": {
        "must_contain": "пептид",
        "phrases": [
            ("хочу похудеть и меньше сладкого", "WHIEDA_GROK_BUNDLE_CANDIDATE_TRIAGE_TASK_V1_2026-08-28.md"),
            ("стройность", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("вес", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("аппетит", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("пептид", "qa/no_blind_zone/whieda_no_blind_zone_cases_v1.jsonl#NBZ-P0-011"),
            ("соевый пептид", "n8n/current/whieda_bundle_aliases_v1.json"),
            ("меньше сладкого", "WHIEDA_GROK_BUNDLE_CANDIDATE_TRIAGE_TASK_V1_2026-08-28.md"),
            ("восстановление", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("рельеф", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
            ("протеин", "postgres/scripts/staging_seed_whieda_advisor_local_v1.sql"),
        ],
    },
}

ITEM_SPLIT = re.compile(r"\s*(?:,|;|/|\+| и )\s*", re.I)
PARENS = re.compile(r"\(([^)]*)\)")
CHIP_MARK = "чип"
SKIP_FRAGMENTS = {
    "день",
    "мл",
    "л",
    "шт",
    "красный",
    "зеленый",
    "синий",
}
BLOCKING_SIGNALS = {
    "oncology",
    "resuscitation",
    "glaucoma",
    "children",
    "surgery",
    "abscess",
    "condemned_by_source",
}


def normalized(value: str | None, *, keep_parens: bool = False) -> str:
    text = (value or "").strip().lower().replace("ё", "е")
    text = text.replace("«", " ").replace("»", " ").replace('"', " ").replace("'", " ")
    text = text.replace("-", " ").replace("—", " ")
    if not keep_parens:
        text = PARENS.sub(" ", text)
    return " ".join(text.split())


def split_items(value: str | None) -> list[str]:
    text = (value or "").strip()
    if not text or text in {"-", "—"}:
        return []
    parts = [
        part.strip()
        for part in ITEM_SPLIT.split(text)
        if part.strip() and part.strip() not in {"-", "—"}
    ]
    kept = []
    for part in parts or [text]:
        key = normalized(part)
        if not key or key in SKIP_FRAGMENTS or key.isdigit():
            continue
        kept.append(part)
    return kept


@dataclass
class Catalog:
    alias_to_sku: dict[str, str]
    sku_to_name: dict[str, str]

    def match(self, item: str) -> str | None:
        key = normalized(item)
        if not key or CHIP_MARK in key:
            return None
        if key in self.alias_to_sku:
            return self.alias_to_sku[key]
        tokens = key.split()
        for width in (3, 2, 1):
            if len(tokens) >= width:
                phrase = " ".join(tokens[-width:])
                if len(phrase) >= 4 and phrase in self.alias_to_sku:
                    return self.alias_to_sku[phrase]
        for alias, sku in self.alias_to_sku.items():
            if alias and len(alias) >= 8 and alias in key:
                return sku
        return None


def _index_alias(target: dict[str, str], alias: str, sku: str) -> None:
    key = normalized(alias)
    if key and sku and key not in target:
        target[key] = sku


def load_catalog(*, aliases_path: Path, products_path: Path | None) -> Catalog:
    aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
    alias_to_sku: dict[str, str] = {}
    sku_to_name: dict[str, str] = {}
    for row in aliases:
        sku = str(row.get("sku") or "").strip()
        name = str(row.get("name") or "").strip()
        alias = str(row.get("alias") or "").strip()
        if sku and alias:
            _index_alias(alias_to_sku, alias, sku)
        if sku and name:
            sku_to_name[sku] = name
            _index_alias(alias_to_sku, name, sku)
    if products_path and products_path.is_file():
        with products_path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                sku = str(row.get("sku") or "").strip()
                name = str(row.get("canonical_name") or "").strip()
                if sku and name:
                    sku_to_name[sku] = name
                    _index_alias(alias_to_sku, name, sku)
    extras = {
        "сюэчинфу": "F024-00",
        "саньцин": "F003-02",
        "эликсир саньцин": "F003-02",
        "три драгоценности": "F002-02",
        "3 драгоценности": "F002-02",
        "эликсир 3 драгоценности": "F002-02",
        "спирулина": "F036-00",
        "стельки": "D013",
        "графеновые очки": "D014",
        "сауна ба гуа": "M014-00",
        "минисауна ба гуа": "M014-00",
        "водородный стержень": "D017-00",
        "зубная паста": "EU-N000030-25",
        "цинк": "F007-00",
        "селен": "F007-00",
        "железо": "F007-00",
    }
    for alias, sku in extras.items():
        _index_alias(alias_to_sku, alias, sku)
    return Catalog(alias_to_sku=alias_to_sku, sku_to_name=sku_to_name)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if not rows:
        raise ValueError("empty candidate file")
    missing = sorted(REQUIRED_COLUMNS - set(rows[0]))
    if missing:
        raise ValueError(f"missing columns: {missing}")
    return rows


def _safety_hit(blob: str, pattern: str) -> bool:
    if pattern == "рак":
        return bool(re.search(r"(?<![а-я])рак(?![а-я])", blob))
    return pattern in blob


def safety_signals(row: dict[str, str]) -> list[str]:
    blob = normalized(
        " ".join(
            str(row.get(key) or "")
            for key in (
                "название_ситуации",
                "основной_запрос",
                "логика_связки",
                "порядок_применения",
                "ограничения",
                "ожидаемый_результат",
                "raw_quote",
                "block_reason",
            )
        ),
        keep_parens=True,
    )
    hits = []
    for name, patterns in SAFETY_PATTERNS.items():
        if any(_safety_hit(blob, pattern) for pattern in patterns):
            hits.append(name)
    title = normalized(row.get("название_ситуации"), keep_parens=True)
    if "осужден" in title:
        hits.append("condemned_by_source")
    if any(token in title or token in blob for token in ("животн", "кошк", "питомц")):
        hits.append("animal_use")
    return hits


def cluster_key(row: dict[str, str]) -> str:
    title = normalized(row.get("название_ситуации"), keep_parens=True)
    query = normalized(row.get("основной_запрос"), keep_parens=True)
    blob = f"{title} {query}"
    if "реанимац" in blob:
        return "cluster:reanimation"
    if (
        ("регуляц" in title and any(token in title for token in ("восстанов", "очист", "очищен")))
        or re.search(r"\bров\b", title)
        or "3 этапн" in title
    ):
        return "cluster:rov"
    if "гайморит" in blob or "лор воспален" in blob:
        return "cluster:lor"
    if (
        "совмещ" in blob
        or "совместимости приборов" in blob
        or "протокол совместимости" in blob
        or (
            "в один день" in blob
            and any(token in blob for token in ("прибор", "сауна", "бэм", "вентун"))
        )
    ):
        return "cluster:devices"
    if any(token in blob for token in ("животн", "кошк", "питомц")):
        return "cluster:animal"
    return f"solo:{row.get('record_id')}"


def _pass_rank(row: dict[str, str]) -> int:
    text = str(row.get("extraction_pass") or "")
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else 0


def pick_canonical(members: list[dict[str, str]]) -> str:
    scored: list[tuple[int, str]] = []
    for row in members:
        score = 0
        authority = normalized(row.get("authority_level"))
        title = normalized(row.get("название_ситуации"))
        status = normalized(row.get("статус"))
        if "official_company" in authority:
            score += 10
        if "официальн" in title or "официальн" in status:
            score += 5
        if (row.get("source_locator") or "").strip():
            score += 3
        if (row.get("owner_approved") or "").strip().lower() in {"да", "yes"}:
            score += 1
        score += min(_pass_rank(row), 9)
        scored.append((score, str(row["record_id"])))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1]


@dataclass
class TriageResult:
    rows_read: int
    triage: list[dict[str, str]]
    unknown: list[dict[str, str]]
    regression: list[dict[str, Any]]
    counts: dict[str, int] = field(default_factory=dict)
    source_path: str = ""
    source_missing_note: str = ""


def match_item(catalog: Catalog, item: str) -> str | None:
    sku = catalog.match(item)
    if sku:
        return sku
    for inner in PARENS.findall(item):
        sku = catalog.match(inner)
        if sku:
            return sku
    return None


def map_items(row: dict[str, str], catalog: Catalog) -> tuple[list[str], list[tuple[str, str]]]:
    mapped: list[str] = []
    unknown: list[tuple[str, str]] = []
    for item in split_items(row.get("основной_товар")):
        sku = match_item(catalog, item)
        if sku:
            mapped.append(f"{sku}={catalog.sku_to_name.get(sku) or sku}")
        else:
            unknown.append((item, "primary"))
    for item in split_items(row.get("доп_товары")):
        sku = match_item(catalog, item)
        if sku:
            mapped.append(f"{sku}={catalog.sku_to_name.get(sku) or sku}")
        else:
            unknown.append((item, "additional"))
    seen: set[str] = set()
    uniq: list[str] = []
    for item in mapped:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq, unknown


def triage_rows(rows: list[dict[str, str]], catalog: Catalog, *, source_path: Path) -> TriageResult:
    clusters: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        clusters[cluster_key(row)].append(row)
    canonical_of: dict[str, str] = {}
    for key, members in clusters.items():
        if key.startswith("solo:") or len(members) == 1:
            continue
        canonical_of[key] = pick_canonical(members)

    triage: list[dict[str, str]] = []
    unknown_rows: list[dict[str, str]] = []
    for row in rows:
        record_id = str(row.get("record_id") or "").strip()
        signals = safety_signals(row)
        mapping, unknown = map_items(row, catalog)
        locator = (row.get("source_locator") or "").strip()
        key = cluster_key(row)
        canonical = canonical_of.get(key, "")
        owner_q = ""
        confidence = "medium"
        reasons: list[str] = []

        if "animal_use" in signals:
            decision = "archive"
            reasons.append("запись про животное, не человеческий консультант")
            owner_q = "Архивировать ветеринарные BUNDLE-0001/0002 или держать отдельным контуром?"
        elif any(name in signals for name in BLOCKING_SIGNALS):
            decision = "blocked_claim"
            confidence = "high"
            reasons.append("safety-сигнал из RAW: " + ",".join(s for s in signals if s != "animal_use"))
            owner_q = "Подтвердить, что этот набор остаётся blocked_raw и не идёт в бот?"
        elif canonical and canonical != record_id:
            decision = "duplicate"
            confidence = "high"
            reasons.append(f"тот же смысл, что {canonical}")
            owner_q = f"Оставить каноном {canonical} и закрыть {record_id}?"
        elif not locator:
            decision = "needs_source"
            confidence = "high"
            reasons.append("пустой source_locator")
            owner_q = "Есть ли точный locator/цитата для этой записи?"
        elif unknown:
            decision = "needs_product_mapping"
            reasons.append("есть позиции без уверенного SKU")
            owner_q = "Какой SKU у неясных позиций, или выкинуть их из набора?"
        else:
            decision = "ready_for_owner_review"
            confidence = "medium" if mapping else "low"
            reasons.append("SKU сопоставимы, публикация по-прежнему blocked_raw")
            owner_q = "Можно ли ставить в очередь owner-review без публикации?"

        raw_ref = ""
        if locator:
            raw_ref = f"{row.get('source_id') or ''}#{locator}"
        elif row.get("source_id"):
            raw_ref = str(row.get("source_id"))

        triage.append(
            {
                "record_id": record_id,
                "title": (row.get("название_ситуации") or "").strip(),
                "decision": decision,
                "canonical_candidate_id": canonical if decision == "duplicate" else "",
                "catalog_mapping": ";".join(mapping),
                "unmapped_items": ";".join(item for item, _role in unknown),
                "confidence": confidence,
                "reason": "; ".join(reasons),
                "source_id": (row.get("source_id") or "").strip(),
                "source_locator": locator,
                "raw_quote_ref": raw_ref,
                "safety_signals": ",".join(signals),
                "owner_question": owner_q,
                "publication_status": (row.get("publication_status") or "").strip(),
            }
        )
        for item, role in unknown:
            unknown_rows.append(
                {
                    "record_id": record_id,
                    "item_text": item,
                    "role": role,
                    "reason": "нет точного alias/canonical_name match; SKU не угадывался",
                    "owner_question": f"Что это за позиция {item!r} в {record_id}?",
                }
            )

    regression: list[dict[str, Any]] = []
    for bundle_id, spec in ACTIVE_BUNDLES.items():
        for index, (phrase, ref) in enumerate(spec["phrases"], start=1):
            regression.append(
                {
                    "case_id": f"{bundle_id}_{index:02d}",
                    "user_text": phrase,
                    "expected_bundle_id": bundle_id,
                    "must_contain": [spec["must_contain"]],
                    "source": {"ref": ref},
                }
            )

    counts = Counter(item["decision"] for item in triage)
    try:
        source_display = str(source_path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        source_display = str(source_path)
    note = ""
    if len(rows) != 51:
        note = (
            f"source_missing: expected 51 advisor_bundle_staging_records; "
            f"read {len(rows)} from {source_display}. "
            "No live Postgres dump in this worktree; extra rows were not invented."
        )
    return TriageResult(
        rows_read=len(rows),
        triage=triage,
        unknown=unknown_rows,
        regression=regression,
        counts=dict(counts),
        source_path=source_display,
        source_missing_note=note,
    )


def write_tsv(path: Path, rows: list[dict[str, str]], columns: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in columns})


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_report(path: Path, result: TriageResult) -> None:
    counts = result.counts
    questions = _top_owner_questions(result)
    lines = [
        "# Bundle candidate triage — local report",
        "",
        "## Counts",
        "",
        f"- rows actually read: **{result.rows_read}**",
        "- expected staging rows: **51**",
        f"- source file: `{result.source_path}`",
        f"- ready_for_owner_review: **{counts.get('ready_for_owner_review', 0)}**",
        f"- duplicate: **{counts.get('duplicate', 0)}**",
        f"- needs_product_mapping: **{counts.get('needs_product_mapping', 0)}**",
        f"- needs_source: **{counts.get('needs_source', 0)}**",
        f"- blocked_claim: **{counts.get('blocked_claim', 0)}**",
        f"- archive: **{counts.get('archive', 0)}**",
        f"- unknown catalog items: **{len(result.unknown)}**",
        f"- active-bundle regression cases: **{len(result.regression)}**",
        "",
        "## source_missing",
        "",
        result.source_missing_note or "51-row dump was present; no source_missing.",
        "",
        "## 10 highest-leverage owner questions",
        "",
    ]
    for index, question in enumerate(questions, start=1):
        lines.append(f"{index}. {question}")
    lines.extend(
        [
            "",
            "## Что не опубликовано",
            "",
            "- Ни одна RAW-заготовка не переведена в `approved` и не отправлена в бот.",
            "- Три уже активных runtime-набора не менялись.",
            "- Нет SQL, import/publish, Google Sheets, live Postgres, Telegram, Docker, deploy.",
            "- `qa/whieda_bundle_triage/` содержит только разметку и offline-проверки.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _top_owner_questions(result: TriageResult) -> list[str]:
    questions = [
        "Где выгрузка 51 строки `advisor_bundle_staging_records`? В worktree есть только 25 строк `09_BUNDLE_CANDIDATES.tsv`; недостающие 26 не выдумывались.",
        "Архивировать BUNDLE-0001/0002 (животные) или держать отдельным ветеринарным контуром?",
        "Подтвердить вечный `blocked_raw` для схемы «Реанимация» (BUNDLE-0010/0015/0030) — официально осуждена.",
        "Канон 3-этапной РОВ — BUNDLE-0016 (официальные дозы 12.12.2025)? Закрыть 0004/0009/0014/0029 как duplicate?",
        "Канон совместимости приборов — BUNDLE-0022? Закрыть BUNDLE-0007 как duplicate?",
        "Канон ЛОР/гайморит — BUNDLE-0017? Закрыть BUNDLE-0005 как duplicate?",
        "Что за «чип из прокладки» (BUNDLE-0003/0017/0028) — отдельный SKU, расходник D003, или выкинуть из набора?",
        "Маппить или выкинуть неясные позиции: Коэнзим Q10, водородная вода, Детокс Идеал-1, витамин D3, минералы?",
        "BUNDLE-0008 (глаукома) и BUNDLE-0021 (в запросе названы глаукома/катаракта) остаются blocked?",
        "BUNDLE-0018 (аденоиды у ребёнка / альтернатива операции) остаётся blocked_claim?",
    ]
    extra = []
    for row in result.triage:
        q = (row.get("owner_question") or "").strip()
        if q and q not in questions and q not in extra:
            extra.append(f"{row['record_id']}: {q}")
        if len(questions) + len(extra) >= 10:
            break
    return questions[:10]


def default_input_path() -> Path:
    fixture = FIXTURES / "09_BUNDLE_CANDIDATES.tsv"
    if fixture.is_file():
        return fixture
    raise FileNotFoundError("09_BUNDLE_CANDIDATES.tsv not found; pass --input")


def default_aliases_path() -> Path:
    if N8N_ALIASES.is_file():
        return N8N_ALIASES
    return FIXTURES / "aliases.json"


def default_products_path() -> Path | None:
    fixture = FIXTURES / "products.tsv"
    return fixture if fixture.is_file() else None
