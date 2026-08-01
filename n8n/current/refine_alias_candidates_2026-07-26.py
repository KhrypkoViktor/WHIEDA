"""Classify staged aliases as direct name variants or manual-review items.

This never activates aliases or changes production.  It protects the product
router from raw-chat phrases such as accessories, procedure verbs and folklore.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

ROOT = Path(r"D:\Projects\WHIEDA")
OUT = ROOT / "n8n" / "live-exports" / "2026-07-26"
SOURCE = OUT / "WHIEDA_safe_alias_candidates_2026-07-26.csv"

# Only direct names and obvious misspellings are candidates for future runtime.
# All accessory / mode / component references stay in review even if raw chats
# loosely attach them to a product.
ALLOW = {
    "Активатор клеток": {"ак", "активатор"},
    "Высокотехнологичные компьютерные очки": {"виедовские очки", "графеновые очки", "очки (графеновые)"},
    "Капсулы Линчжи Фохоу": {"ленжи", "ленчжи", "линджи", "линжи", "линч4", "линчжи", "линчу", "линьчжи", "линьджи (орфографические варианты)"},
    'МИНИСАУНА "БА-ГУА"': {"ба гуа", "ба-гоа", "багуа", "бо гуа", "сауна ба-гуа", "сауна багуа", "ба-гуа (вариативное написание)"},
    "Набор Массажёра Magic Foherb 3.0 (TUV)": {"magic foherb", "бэм", "бэмчик", "вэм"},
    "Набор прибора Вэнтун 1.0": {"wentun", "wентун", "веетуна", "вен ум", "вентум", "вентун", "вентуни", "винтун", "вэнтум", "вэнтун", "вэнтун1.0"},
    "Низкомолекулярный соевый пептид": {"пептиды", "соевый пептид"},
    "Стельки с анионами": {"наши стельки", "полустельки", "стельки"},
    "Чай Лювэй": {"чай лювэй"},
    "Эликсир 3 Драгоценности": {"3 драгоценности", "три драгоценности"},
    "Эликсир Саньцин": {"санцин", "санцын", "саньцин", "сяньцинь"},
    "Эликсир Фохоу": {"фохоу", "фоху", "фохуа", "фухоу"},
    "Энергетический палантин": {"палантин", "полонтино"},
}


def main():
    rows = list(csv.DictReader(SOURCE.open(encoding="utf-8-sig")))
    decisions = []
    for row in rows:
        approved = row["alias"].casefold() in {x.casefold() for x in ALLOW.get(row["canonical_name"], set())}
        decisions.append({
            **row,
            "decision": "direct_name_variant" if approved else "manual_review_only",
            "reason": "direct product name or obvious typo" if approved else "accessory, procedure, broad term, or uncertain product mapping",
            "decided_at": date.today().isoformat(),
        })
    path = OUT / "WHIEDA_alias_candidate_decisions_2026-07-26.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(decisions[0]))
        writer.writeheader(); writer.writerows(decisions)
    direct = sum(row["decision"] == "direct_name_variant" for row in decisions)
    print({"total": len(decisions), "direct_name_variants": direct, "manual_review_only": len(decisions)-direct, "path": str(path)})


if __name__ == "__main__":
    main()
