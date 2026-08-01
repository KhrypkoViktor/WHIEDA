#!/usr/bin/env python3
"""Prepare an auditable candidate queue from Claude batch exports.

Nothing in this script writes to Google Sheets, Postgres, n8n or Dify.  It
turns raw candidates into reviewable CSV files, and only marks aliases as
safe when their product mapping is unambiguous.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(r"D:\Projects\WHIEDA")
SOURCE = ROOT / "RAG" / "1 компиляция. диалоги с врачами"
OUT = ROOT / "n8n" / "live-exports" / "2026-07-26"
OUT.mkdir(parents=True, exist_ok=True)

# Mapping is intentionally small and conservative.  A candidate has to point
# to one known runtime SKU without relying on medical context or guesswork.
PRODUCTS = (
    ("M015-00", "Активатор клеток", ("активатор", "активатор клеток")),
    ("EU-N000021-24", "Набор Массажёра Magic Foherb 3.0 (TUV)", ("бэм", "биоэнергомассажер", "биоэнергомассажёр", "magic foherb")),
    ("EU-N000024-24", "Набор прибора Вэнтун 1.0", ("вэнтун", "вентун", "wentong", "wentung")),
    ("M014-00", 'МИНИСАУНА "БА-ГУА"', ("ба-гуа", "ба гуа", "багуа", "минисауна", "сауна")),
    ("D013", "Стельки с анионами", ("стельки", "полустельки", "коррекционные стельки")),
    ("D014", "Высокотехнологичные компьютерные очки", ("очки", "графеновые очки", "компьютерные очки")),
    ("T015", "Энергетический палантин", ("палантин", "энергетический палантин")),
    ("F028-00", "Капсулы Линчжи Фохоу", ("линчжи", "капсулы линчжи")),
    ("F031-00", "Чай Лювэй", ("лювей", "лювэй", "чай лювей", "чай лювэй")),
    ("F038-00", "Низкомолекулярный соевый пептид", ("пептид", "пептиды", "соевый пептид")),
    ("F001-02", "Эликсир Фохоу", ("фохоу", "эликсир фохоу")),
    ("F002-02", "Эликсир 3 Драгоценности", ("три драгоценности", "3 драгоценности")),
    ("F003-02", "Эликсир Саньцин", ("саньцин", "синцин")),
)

MEDICAL_RE = re.compile(
    r"онкол|беремен|лактац|диагноз|лечен|терапи|противопоказ|тромб|кров[ьи]|"
    r"псориаз|аутоиммун|химио|инфаркт|инсульт|болезн|воспален|опухол|"
    r"почк|печен|сердц|давлен|диализ|имплант|антикоагул|вирус|герпес|"
    r"сустав|боль|раны|ожог|температур|менстру|месячн|гинекол|кож[аи]|ребен|ребён|"
    r"аутиз|цистит|вагин|дозиров|ветеринар|здоровь|ингаля|безопасност",
    re.I,
)
UNOFFICIAL_RE = re.compile(
    r"неофициаль|не описан|не является|сторонн|не входит|не идентифицирован|"
    r"неясно|предположительно|требует проверки|off-label|вне назначения|"
    r"альтернативн|протокол|схем[аы] при[её]ма|реанимац",
    re.I,
)


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace("ё", "е")).strip(" .,!?:;\"'")


def load_jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def resolve_product(alias: str, canonical: str):
    haystack = normalized(f"{alias} {canonical}")
    hits = []
    for sku, name, signals in PRODUCTS:
        if any(normalized(signal) in haystack for signal in signals):
            hits.append((sku, name))
    unique = {(sku, name) for sku, name in hits}
    return next(iter(unique)) if len(unique) == 1 else None


def split_aliases(value: str):
    return [normalized(part) for part in re.split(r"\s*/\s*|\s*\|\s*|\s*,\s*", value) if normalized(part)]


def priority(source_id: str, alias: str):
    # Direct short names are more valuable for routing than long variants.
    return 95 if len(alias) <= 18 and source_id.startswith("RAWCHAT") else 80


def main():
    aliases = load_jsonl(SOURCE / "batch_aliases.jsonl")
    rows = []
    rejected = []
    seen = set()

    for item in aliases:
        alias_source = item.get("alias", "")
        canonical = item.get("canonical_term", "")
        quote = item.get("raw_quote", "")
        combined = f"{canonical}\n{quote}"
        resolution = resolve_product(alias_source, canonical)
        is_risky = bool(MEDICAL_RE.search(combined) or UNOFFICIAL_RE.search(combined))

        for alias in split_aliases(alias_source):
            key = (alias, resolution[0] if resolution else "")
            if not resolution or is_risky or key in seen:
                rejected.append({
                    "source_id": item.get("source_id", ""),
                    "alias": alias,
                    "canonical_term": canonical,
                    "reason": "medical_or_unofficial_context" if is_risky else "ambiguous_or_unknown_product",
                    "raw_quote": quote,
                })
                continue
            seen.add(key)
            sku, name = resolution
            rows.append({
                "project_id": "whieda",
                "alias": alias,
                "canonical_sku": sku,
                "canonical_name": name,
                "match_type": "candidate_alias",
                "priority": priority(item.get("source_id", ""), alias),
                "active": "FALSE",
                "answer_scope": "price+description",
                "review_status": "candidate_needs_owner_approval",
                "source_id": item.get("source_id", ""),
                "notes": f"Claude candidate. Quote: {quote[:280]}",
                "updated_at": date.today().isoformat(),
            })

    alias_path = OUT / "WHIEDA_safe_alias_candidates_2026-07-26.csv"
    with alias_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda x: (x["canonical_name"], -int(x["priority"]), x["alias"])))

    rejected_path = OUT / "WHIEDA_alias_candidates_not_for_auto_publish_2026-07-26.csv"
    with rejected_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["source_id", "alias", "canonical_term", "reason", "raw_quote"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rejected)

    # The outstanding candidate regression cases become a role/owner queue.
    report = json.loads((OUT / "WHIEDA_claude_candidate_regression.json").read_text(encoding="utf-8"))
    queue = []
    for case in report.get("results", []):
        if case.get("classification") == "covered_sql":
            continue
        question = case.get("question", "")
        low = normalized(question)
        if MEDICAL_RE.search(question):
            owner, target, status = "medical_reviewer", "medical_review", "do_not_publish"
        elif any(word in low for word in ("достав", "налич", "склад", "маркетплейс", "поддел", "оригинал")):
            owner, target, status = "admin", "operations_or_brand_faq", "needs_fact_check"
        elif any(word in low for word in ("ржав", "слом", "качеств", "состав", "формул")):
            owner, target, status = "product_owner", "product_quality_faq", "needs_fact_check"
        else:
            owner, target, status = "business_leader", "business_or_objection_faq", "needs_fact_check"
        queue.append({
            "candidate_id": f"CC-REG-{len(queue)+1:03}",
            "question": question,
            "actual_route": case.get("route", ""),
            "owner": owner,
            "target_layer": target,
            "publication": status,
            "priority": "P1" if owner != "medical_reviewer" else "P2",
            "source": "Claude candidate regression 2026-07-26",
        })

    queue_path = OUT / "WHIEDA_candidate_triage_queue_2026-07-26.csv"
    with queue_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["candidate_id", "question", "actual_route", "owner", "target_layer", "publication", "priority", "source"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(queue)

    summary = {
        "generated_at": date.today().isoformat(),
        "source_alias_candidates": len(aliases),
        "safe_alias_candidates": len(rows),
        "not_for_auto_publish": len(rejected),
        "regression_triage_items": len(queue),
        "regression_by_owner": dict(Counter(row["owner"] for row in queue)),
        "outputs": [str(alias_path), str(rejected_path), str(queue_path)],
        "publication_policy": "No candidate is automatically published to Product_Aliases or runtime.",
    }
    (OUT / "WHIEDA_candidate_triage_summary_2026-07-26.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
