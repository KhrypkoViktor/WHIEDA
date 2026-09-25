"""Second pass: match the Kazakh list to the master catalogue by hand-checked
aliases (the European rule: never match on a similar name alone — every
non-identical pair is listed for the owner), then report the three lists."""
from __future__ import annotations

import io
import json
import re
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Kazakh page name -> master canonical name. Every pair here differs only in
# case, spacing, quotes or a dropped brand word; each one is shown to the owner.
ALIASES = {
    "КАПСУЛЫ ЛИНЧЖИ ФОХОУ": "Капсулы линчжи ФОХОУ",
    'МИНИСАУНА "БА-ГУА"': "МИНИСАУНА БА-ГУА",
    "Набор прибора вэнтун 1.0": "Набор прибора Вэнтун 1.0",
    "Водородный стержень (2 шт)": "Водородный стержень (2шт)",
    "Массажёр magic FOHERB 3.0 (tuv)": "Набор Массажёра Magic Foherb 3.0 (TUV)",
    "АКТИВАТОР КЛЕТОК Pro": "Активатор клеток PRO (комплект)",
    "Активатор клеток pro set": "Активатор клеток PRO (комплект)",
    "Омолаживающая маска с лифтинг эффектом": "Омолаживающая маска с лифтинг эффектом Fundesee",
    "Омолаживающий спрей с лифтинг эффектом": "Омолаживающий спрей с лифтинг эффектом Fundesee",
    "Увлажняющий гель": "Увлажняющий гель Fundesee",
}

PRE_ORDER = "Алдын ала сату "  # Kazakh «pre-order», a page marker, not a name


def norm(name: str) -> str:
    text = unicodedata.normalize("NFKC", str(name or "")).strip().lower()
    text = text.replace("ё", "е").replace("«", "").replace("»", "").replace('"', "")
    text = re.sub(r"[^\w\s\-+*.()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def num(value) -> float | None:
    if value in (None, "", "None"):
        return None
    return float(str(value).replace(" ", "").replace(",", "."))


def same(a, b) -> bool:
    x, y = num(a), num(b)
    if x is None or y is None:
        return True  # nothing to compare
    return abs(x - y) < 0.001


def main() -> None:
    kz = [
        dict(zip(header, line.split("\t")))
        for header, line in (
            (io.open(HERE / "kz_catalog.tsv", encoding="utf-8").readline().rstrip("\n").split("\t"), row)
            for row in io.open(HERE / "kz_catalog.tsv", encoding="utf-8").read().splitlines()[1:]
        )
    ]
    master = json.load(io.open(HERE / "master_catalog.json", encoding="utf-8"))["rows"]
    by_master = {norm(r["canonical_name"]): r for r in master}

    matched, renamed, price_diffs, only_kz, used = [], [], [], [], set()
    for row in kz:
        name = row["name"].strip()
        plain = name[len(PRE_ORDER):] if name.startswith(PRE_ORDER) else name
        target = ALIASES.get(plain, plain)
        m = by_master.get(norm(target))
        if not m:
            only_kz.append(row)
            continue
        used.add(m["sku"])
        if norm(plain) != norm(m["canonical_name"]):
            renamed.append((name, m["canonical_name"], m["sku"]))
        problems = []
        for label, kz_value, master_value in (
            ("первичка W$", row["first_w"], m["retail_w"]),
            ("первичка BYN", row["first_byn"], m["retail_price_byn"]),
            ("PV", row["first_pv"], m["partner_points"]),
            ("вторичка W$", row["repeat_w"], m["partner_w"]),
            ("вторичка BYN", row["repeat_byn"], m["partner_price_byn"]),
        ):
            if not same(kz_value, master_value):
                problems.append(f"{label}: Казахстан {kz_value}, мастер {master_value}")
            if num(master_value) is None and num(kz_value) is not None:
                problems.append(f"{label}: в мастере пусто, в Казахстане {kz_value}")
        if problems:
            price_diffs.append((name, m["sku"], "; ".join(problems)))
        matched.append((name, m["sku"]))

    only_master = [m for m in master if m["sku"] not in used]

    report = {
        "товаров в казахском прайсе": len(kz),
        "в мастер-каталоге": len(master),
        "совпали": len(matched),
        "из них с другим названием": len(renamed),
        "с расхождением цен/PV": len(price_diffs),
        "нет в мастере (новые)": len(only_kz),
        "нет в Казахстане": len(only_master),
    }
    lines = ["список\tказахское название\tмастер / sku\tподробности"]
    for name, canonical, sku in sorted(renamed):
        lines.append(f"другое название\t{name}\t{canonical} / {sku}\t")
    for name, sku, problem in sorted(price_diffs):
        lines.append(f"расхождение\t{name}\t{sku}\t{problem}")
    for row in sorted(only_kz, key=lambda r: r["name"]):
        lines.append(
            f"новый товар\t{row['name'].strip()}\t\tпервичка {row['first_w'] or '—'} W$ / {row['first_byn'] or '—'}, "
            f"вторичка {row['repeat_w'] or '—'} W$ / {row['repeat_byn'] or '—'}, "
            f"PV {row['first_pv'] or row['repeat_pv'] or '—'}, {row['stock']}"
        )
    for m in sorted(only_master, key=lambda r: r["canonical_name"]):
        lines.append(f"нет в Казахстане\t\t{m['canonical_name']} / {m['sku']}\tпервичка {m['retail_w']} W$")
    io.open(HERE / "kz_report.tsv", "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    io.open(HERE / "kz_report_head.json", "w", encoding="utf-8", newline="\n").write(
        json.dumps(report, ensure_ascii=False, indent=1)
    )
    print(json.dumps(report, ensure_ascii=False, indent=1))


main()
