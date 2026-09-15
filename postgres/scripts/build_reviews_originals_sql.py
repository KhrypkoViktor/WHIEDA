#!/usr/bin/env python3
"""Build per-review closed originals from 19_08.md + reviews.js.

Does not guess matches. Exact «Для сайта» first; six leftovers are explicit
overrides in OVERRIDES. Five 19_08 blocks have no public card and stay out of SQL.
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "03_Website" / "wwc-best" / "src" / "data" / "reviews.js"
SOURCE = Path(r"D:\Obsidian\WHIEDA\05_WHIEDA_RAG\Отзывы\отзывы для сайта_19_08.md")
SQL_OUT = ROOT / "postgres" / "sql" / "platform_content_access_reviews_v1.sql"
REGISTRY_OUT = ROOT / "postgres" / "sql" / "platform_content_access_reviews_registry_v1.json"
SKIP_SECTIONS = {"Итого по категориям", "Исключённые (абсолютный RED)"}
DISCLAIMER = (
    "Личные истории, как их рассказали люди. Это наблюдения участников, "
    "не медицинские заключения и не замена консультации врача."
)

# Six cards that do not exact-match after stripping the name suffix.
# Identity is the same person/facts, not a nearest-neighbour guess.
OVERRIDES: dict[str, dict] = {
    "insoles-2": {
        "source_index": 2,
        "status": "verified_wording_drift",
        "note": "Та же Баярма С.: 15 минут и отёки за 3 дня. В карточке в первой фразе добавлено «судорогами»; в «Для сайта» этого слова нет.",
    },
    "pads-8": {
        "source_index": 84,
        "status": "verified_punctuation_and_name_suffix",
        "note": "Тот же Валентин. Расхождение: «смачивал, чип носил» vs «смачивал чип, носил»; суффикс имени в «Для сайта» не снялся, потому что в поле Имя стоит «Валентин (мужской отзыв!)». Оригинал в файле пустой — в ключ идёт текст «Для сайта».",
    },
    "socks-1": {
        "source_index": 153,
        "status": "verified_product_name_drift",
        "note": "Та же Клавдия, Якутск: трёхдневные носки без запаха. Карточка: «Health Priority»; «Для сайта»: «носки с графеном». Оригинал в файле пустой — в ключ идёт текст «Для сайта».",
    },
    "sanqing-5": {
        "share_card_id": "palantin-4",
        "status": "shared_cross_category",
        "note": "Та же история Игоря Б. (Саньцин + палантин), укороченная карточка в категории Саньцин. RAW берётся из блока, уже сверенного с palantin-4.",
    },
    "liuwei-4": {
        "share_card_id": "bem-6",
        "status": "shared_cross_category",
        "note": "Та же Оюна Г. / мама 69 лет / БЭМ + Линчжи + Лювей + Спирулина. Укороченная карточка в чае Лювей. RAW из блока bem-6.",
    },
    "liuwei-5": {
        "share_card_id": "peptide-3",
        "status": "shared_cross_category",
        "note": "Тот же отзыв про сына и пептиды («волшебный напиток»). Карточка Лювей добавляет «и чай»; исходный блок — про пептиды. RAW из блока peptide-3.",
    },
}


def unescape_quotes(value: str) -> str:
    text = value.strip()
    if text.startswith("«") and text.endswith("»"):
        text = text[1:-1]
    return re.sub(r"\s+", " ", text).strip()


def normalize(value: str) -> str:
    text = unescape_quotes(value).lower().replace("ё", "е")
    text = text.replace("«", "").replace("»", "").replace('"', "").replace("'", "")
    return re.sub(r"\s+", " ", text).strip(" .,—-")


def strip_name_suffix(site: str, name: str) -> str:
    text = unescape_quotes(site)
    name = unescape_quotes(name)
    if name and normalize(text).endswith(normalize(name)):
        cut = len(text.rstrip()) - len(name)
        if cut > 0:
            return text[:cut].rstrip(" .,—-")
    return text


def parse_js_cards(text: str) -> list[dict]:
    cards = []
    for m in re.finditer(
        r"\{ id: '([^']+)', title: '((?:\\'|[^'])*)', body: '((?:\\'|[^'])*)', author: '((?:\\'|[^'])*)', city: '((?:\\'|[^'])*)', tags: \[([^\]]*)\]",
        text,
    ):
        tags = re.findall(r"'([^']+)'", m.group(6))
        cards.append(
            {
                "id": m.group(1),
                "title": m.group(2).replace("\\'", "'"),
                "body": m.group(3).replace("\\'", "'"),
                "author": m.group(4).replace("\\'", "'"),
                "city": m.group(5).replace("\\'", "'"),
                "tags": tags,
            }
        )
    return cards


def parse_source(text: str) -> list[dict]:
    parts = re.split(r"\n## ", text)
    items: list[dict] = []
    source_index = 0
    for part in parts[1:]:
        title, _, body = part.partition("\n")
        title = title.strip()
        if title in SKIP_SECTIONS:
            continue
        chunks = re.split(r"\n#{3,4} ", body)
        for chunk in chunks[1:]:
            name_m = re.search(r"\*\*Имя:\*\*\s*(.+)", chunk)
            orig_m = re.search(r"\*\*Оригинал:\*\*\s*«(.+?)»", chunk, re.S)
            site_m = re.search(r"\*\*Для сайта:\*\*\s*«(.+?)»", chunk, re.S)
            if not site_m and not orig_m:
                continue
            name = (name_m.group(1).strip() if name_m else "") or ""
            if name.lower().startswith("не указан"):
                name = ""
            original = unescape_quotes(orig_m.group(1)) if orig_m else ""
            site = unescape_quotes(site_m.group(1)) if site_m else ""
            source_index += 1
            heading = chunk.split("\n", 1)[0].strip()
            site_stripped = strip_name_suffix(site, name) if site else ""
            items.append(
                {
                    "source_index": source_index,
                    "section": title,
                    "heading": heading,
                    "name": name,
                    "site": site,
                    "site_stripped": site_stripped,
                    "original": original,
                    "norm": normalize(site_stripped or original),
                }
            )
    return items


def content_key(review_id: str) -> str:
    return f"review/{review_id}/original"


def raw_payload(src: dict) -> tuple[str, str]:
    if src.get("original"):
        return src["original"], "original"
    return src.get("site_stripped") or src.get("site") or "", "site_fallback"


def render_body_html(name: str, raw: str) -> str:
    who = html.escape(name) if name else "Архив"
    return f"<p>{html.escape(DISCLAIMER)}</p><p><strong>{who}</strong>. {html.escape(raw)}</p>"


def match_cards(cards: list[dict], sources: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    by_site: dict[str, list[dict]] = {}
    for item in sources:
        if item["norm"]:
            by_site.setdefault(item["norm"], []).append(item)

    matched: dict[str, dict] = {}
    unmatched: list[dict] = []
    for card in cards:
        n = normalize(card["body"])
        hits = by_site.get(n, [])
        src = None
        status = ""
        if len(hits) >= 1:
            src = hits[0]
            status = "exact_after_name_strip"
        else:
            prefix_hits = []
            for item in sources:
                sn = item["norm"]
                if not sn or len(n) < 48 or len(sn) < 48:
                    continue
                if n.startswith(sn) or sn.startswith(n) or n in sn or sn in n:
                    prefix_hits.append(item)
            unique = {item["source_index"]: item for item in prefix_hits}
            if len(unique) == 1:
                src = next(iter(unique.values()))
                status = "prefix_or_contains"
        if src:
            raw, raw_field = raw_payload(src)
            matched[card["id"]] = {
                "card": card,
                "source": src,
                "status": status,
                "note": "",
                "raw": raw,
                "raw_field": raw_field,
            }
        else:
            unmatched.append(card)
    return matched, unmatched


def apply_overrides(matched: dict[str, dict], unmatched: list[dict], sources: list[dict]) -> None:
    by_index = {item["source_index"]: item for item in sources}
    leftover_ids = {card["id"] for card in unmatched}
    for card in unmatched:
        spec = OVERRIDES.get(card["id"])
        if not spec:
            continue
        if spec.get("share_card_id"):
            parent = matched.get(spec["share_card_id"])
            if not parent:
                raise RuntimeError(f"override {card['id']} needs matched {spec['share_card_id']}")
            src = parent["source"]
        else:
            src = by_index.get(spec["source_index"])
            if not src:
                raise RuntimeError(f"override {card['id']} missing source_index {spec['source_index']}")
        raw, raw_field = raw_payload(src)
        matched[card["id"]] = {
            "card": card,
            "source": src,
            "status": spec["status"],
            "note": spec["note"],
            "raw": raw,
            "raw_field": raw_field,
        }
        leftover_ids.discard(card["id"])
    if leftover_ids:
        raise RuntimeError(f"unmatched cards without override: {sorted(leftover_ids)}")


def build_registry(cards: list[dict], sources: list[dict], matched: dict[str, dict]) -> dict:
    used_indexes = {row["source"]["source_index"] for row in matched.values()}
    unlinked = [item for item in sources if item["source_index"] not in used_indexes]
    rows = []
    for card in cards:
        row = matched[card["id"]]
        src = row["source"]
        rows.append(
            {
                "id": card["id"],
                "content_key": content_key(card["id"]),
                "public_title": card["title"],
                "public_body": card["body"],
                "public_author": card["author"],
                "public_city": card["city"],
                "public_tags": card["tags"],
                "source_index": src["source_index"],
                "source_section": src["section"],
                "source_heading": src["heading"],
                "source_name": src["name"],
                "site_text": src["site_stripped"] or src["site"],
                "raw": row["raw"],
                "raw_field": row["raw_field"],
                "status": row["status"],
                "note": row["note"],
            }
        )
    return {
        "source_file": str(SOURCE),
        "public_cards": len(cards),
        "source_blocks": len(sources),
        "mapped_keys": len(rows),
        "unlinked_source_blocks": [
            {
                "source_index": item["source_index"],
                "section": item["section"],
                "heading": item["heading"],
                "name": item["name"],
                "site": item["site_stripped"] or item["site"],
                "original": item["original"],
                "status": "no_public_card",
                "note": "Блок есть в 19_08.md, публичной карточки на /reviews/ нет. В базу не грузим.",
            }
            for item in unlinked
        ],
        "overrides": OVERRIDES,
        "rows": rows,
    }


def build_sql(rows: list[dict]) -> str:
    values = []
    for row in rows:
        key = row["content_key"]
        title = row["public_title"].replace("'", "''")
        body = render_body_html(row["source_name"], row["raw"])
        if "$rev$" in body:
            raise RuntimeError(f"dollar-quote collision in {key}")
        values.append(
            "  ('whieda', "
            f"'{key}', "
            "'telegram_verified', "
            "'published', "
            "'review', "
            f"'{title}', "
            f"$rev${body}$rev$)"
        )
    joined = ",\n".join(values)
    return f"""-- Per-review closed originals for /reviews/.
-- Source: 19_08.md + reviews.js, mapped via platform_content_access_reviews_registry_v1.json
-- Public cards stay diagnosis-free. Each key is served only after Telegram access.
-- Replaces the single blob review/archive/originals.

begin;

insert into content_access_materials (
  tenant_id, content_key, scope, status, kind, title, body_html
) values
{joined}
on conflict (tenant_id, content_key) do update
set scope = excluded.scope,
    status = excluded.status,
    kind = excluded.kind,
    title = excluded.title,
    body_html = excluded.body_html,
    updated_at = now();

update content_access_materials
set status = 'archived',
    updated_at = now()
where tenant_id = 'whieda'
  and content_key = 'review/archive/originals';

commit;
"""


def main() -> int:
    cards = parse_js_cards(JS.read_text(encoding="utf-8"))
    sources = parse_source(SOURCE.read_text(encoding="utf-8"))
    if len(cards) != 156:
        raise RuntimeError(f"expected 156 public cards, got {len(cards)}")
    matched, unmatched = match_cards(cards, sources)
    apply_overrides(matched, unmatched, sources)
    if len(matched) != len(cards):
        raise RuntimeError(f"mapped {len(matched)} / {len(cards)}")
    registry = build_registry(cards, sources, matched)
    REGISTRY_OUT.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SQL_OUT.write_text(build_sql(registry["rows"]), encoding="utf-8")
    print(
        json.dumps(
            {
                "cards": len(cards),
                "sources": len(sources),
                "keys": len(registry["rows"]),
                "overrides": len(OVERRIDES),
                "unlinked": len(registry["unlinked_source_blocks"]),
                "registry": str(REGISTRY_OUT),
                "sql": str(SQL_OUT),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
