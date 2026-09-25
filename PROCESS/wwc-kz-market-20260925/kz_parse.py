"""Parse the Kazakh office price list the owner pasted (22–25.09.2026) and
compare it with the master catalogue the bot answers from.

Output: kz_catalog.tsv (one row per product, first + repeat purchase) and
kz_compare.tsv (three lists, as with the European prices: matched / only in KZ /
only in master). Nothing is written to the database.
"""
from __future__ import annotations

import io
import json
import re
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "kz_raw.txt"
MASTER = HERE / "master_catalog.json"

MONEY = re.compile(r"^([\d\s]+(?:[.,]\d+)?)\s*W\$\((\s*[\d\s]+(?:[.,]\d+)?)\s*BYN\)$")
PV = re.compile(r"^([\d\s]+(?:[.,]\d+)?)\s*PV$")
INT = re.compile(r"^\d+$")
OUT_OF_STOCK = "Нет в наличии"


def num(value: str) -> float:
    return float(value.replace(" ", "").replace(" ", "").replace(",", "."))


def fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def norm(name: str) -> str:
    text = unicodedata.normalize("NFKC", str(name or "")).lower()
    text = text.replace("ё", "е").replace("«", "").replace("»", "").replace('"', "")
    text = re.sub(r"алдын ала сату", "", text)  # Kazakh «pre-order» prefix on the page
    text = re.sub(r"[^\w\s\-+*.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_block(lines: list[str], *, repeat: bool) -> list[dict]:
    items: list[dict] = []
    current: dict | None = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        money = MONEY.match(line)
        pv = PV.match(line)
        if money and current:
            current["w"] = num(money.group(1))
            current["byn"] = num(money.group(2))
        elif pv and current:
            current["pv"] = num(pv.group(1))
        elif line == OUT_OF_STOCK and current:
            current["stock"] = "нет в наличии"
        elif INT.match(line) and current:
            current.setdefault("extra", []).append(int(line))
        else:
            if line.startswith("Барлығ"):  # page footer «total»
                continue
            current = {"name": line, "stock": "есть", "extra": []}
            items.append(current)
    for item in items:
        extra = item.get("extra") or []
        # Repeat rows carry one more number than the first-purchase rows: the
        # page prints PV, then this number, then the «1» stock marker.
        item["fourth"] = extra[0] if repeat and extra and extra[0] != 1 else None
    return items


def main() -> None:
    text = io.open(RAW, encoding="utf-8").read().splitlines()
    first_lines: list[str] = []
    repeat_lines: list[str] = []
    target = None
    for line in text:
        if line.startswith("#### ПЕРВИЧКА"):
            target = first_lines
            continue
        if line.startswith("#### ВТОРИЧКА"):
            target = repeat_lines
            continue
        if target is not None:
            target.append(line)

    first = parse_block(first_lines, repeat=False)
    repeat = parse_block(repeat_lines, repeat=True)
    by_name_first = {norm(i["name"]): i for i in first}
    by_name_repeat = {norm(i["name"]): i for i in repeat}
    assert len(by_name_first) == len(first), "duplicate name in the first-purchase list"
    assert len(by_name_repeat) == len(repeat), "duplicate name in the repeat list"

    master = json.load(io.open(MASTER, encoding="utf-8"))["rows"]
    by_name_master = {norm(r["canonical_name"]): r for r in master}

    kz_rows = []
    for key in sorted(set(by_name_first) | set(by_name_repeat)):
        f = by_name_first.get(key)
        r = by_name_repeat.get(key)
        kz_rows.append(
            {
                "name": (f or r)["name"].strip(),
                "first_w": fmt(f["w"]) if f else "",
                "first_byn": fmt(f["byn"]) if f else "",
                "first_pv": fmt(f["pv"]) if f else "",
                "repeat_w": fmt(r["w"]) if r else "",
                "repeat_byn": fmt(r["byn"]) if r else "",
                "repeat_pv": fmt(r["pv"]) if r else "",
                "repeat_fourth": str(r.get("fourth") or "") if r else "",
                "stock": (r or f).get("stock", "есть"),
                "in_first": "да" if f else "нет",
                "in_repeat": "да" if r else "нет",
            }
        )

    io.open(HERE / "kz_catalog.tsv", "w", encoding="utf-8", newline="\n").write(
        "\n".join(
            ["\t".join(kz_rows[0].keys())]
            + ["\t".join(str(v) for v in row.values()) for row in kz_rows]
        )
        + "\n"
    )

    matched, only_kz, only_master, diffs = [], [], [], []
    for row in kz_rows:
        key = norm(row["name"])
        m = by_name_master.get(key)
        if not m:
            only_kz.append(row)
            continue
        matched.append((row, m))
        problems = []
        pairs = [
            ("первичка W$", row["first_w"], m["retail_w"]),
            ("первичка BYN", row["first_byn"], m["retail_price_byn"]),
            ("PV", row["first_pv"], m["partner_points"]),
            ("вторичка W$", row["repeat_w"], m["partner_w"]),
            ("вторичка BYN", row["repeat_byn"], m["partner_price_byn"]),
        ]
        for label, kz_value, master_value in pairs:
            if kz_value == "" or master_value in (None, ""):
                continue
            if abs(num(str(kz_value)) - num(str(master_value))) > 0.001:
                problems.append(f"{label}: Казахстан {kz_value} / мастер {master_value}")
        if row["name"].strip() != str(m["canonical_name"]).strip():
            problems.append(f"название: «{row['name'].strip()}» / «{m['canonical_name']}»")
        if problems:
            diffs.append((row["name"].strip(), m["sku"], "; ".join(problems)))

    kz_keys = {norm(r["name"]) for r in kz_rows}
    for m in master:
        if norm(m["canonical_name"]) not in kz_keys:
            only_master.append(m)

    lines = ["список\tназвание\tsku\tподробности"]
    for name, sku, problem in sorted(diffs):
        lines.append(f"расхождение\t{name}\t{sku}\t{problem}")
    for row in only_kz:
        lines.append(
            f"только Казахстан\t{row['name']}\t\tпервичка {row['first_w'] or '—'} W$, "
            f"вторичка {row['repeat_w'] or '—'} W$, PV {row['first_pv'] or row['repeat_pv'] or '—'}, {row['stock']}"
        )
    for m in only_master:
        lines.append(f"только мастер\t{m['canonical_name']}\t{m['sku']}\tпервичка {m['retail_w']} W$")
    io.open(HERE / "kz_compare.tsv", "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")

    print(
        json.dumps(
            {
                "первичка": len(first),
                "вторичка": len(repeat),
                "всего товаров в Казахстане": len(kz_rows),
                "в мастере": len(master),
                "совпали по названию": len(matched),
                "расхождения": len(diffs),
                "только Казахстан": len(only_kz),
                "только мастер": len(only_master),
            },
            ensure_ascii=False,
            indent=1,
        )
    )


main()
