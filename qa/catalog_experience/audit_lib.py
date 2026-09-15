"""Catalog experience audit helpers — offline read-only snapshot analysis."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_ROOT = ROOT / "n8n" / "live-exports" / "structured-master"
PLATFORM_API = ROOT / "backend" / "platform-api"
if str(PLATFORM_API) not in sys.path:
    sys.path.insert(0, str(PLATFORM_API))

from app.advisor.telegram_card import render_telegram_product_card  # noqa: E402

CARD_SECTIONS = (
    "what_it_is",
    "who_asks_about_it",
    "common_use_cases",
    "how_to_use_short",
    "what_to_expect_soft",
    "contraindications_short",
)

SECTION_HEADINGS = {
    "what_it_is": "🔥 Коротко:",
    "who_asks_about_it": "👥 Для кого:",
    "common_use_cases": "✅ Когда обычно рассматривают:",
    "how_to_use_short": "🧭 Как используют:",
    "what_to_expect_soft": "🧠 Почему интересен:",
    "contraindications_short": "⚠️ Ограничения:",
}

REQUIRED_MANIFEST_LAYERS = ("products", "aliases", "product_cards", "resources")
TELEGRAM_SAFE_CHARS = 4096
TELEGRAM_WARN_CHARS = 3500

PV_FIELD = "partner_points"


class SnapshotValidationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _is_active(value: str | None) -> bool:
    return str(value or "").strip().upper() in {"TRUE", "1", "YES"}


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def _has_price(value: str | None) -> bool:
    text = _clean(value)
    return bool(text and text not in {"-", "0", "0.0"})


def find_latest_valid_snapshot(root: Path = DEFAULT_SNAPSHOT_ROOT) -> Path:
    if not root.is_dir():
        raise SnapshotValidationError(f"snapshot root missing: {root}")
    candidates = sorted(
        [path for path in root.iterdir() if path.is_dir() and (path / "manifest.json").is_file()],
        key=lambda path: path.name,
        reverse=True,
    )
    errors: list[str] = []
    for candidate in candidates:
        try:
            verify_snapshot(candidate)
            return candidate
        except SnapshotValidationError as exc:
            errors.append(f"{candidate.name}: {exc}")
    detail = "; ".join(errors[:3]) if errors else "no candidates"
    raise SnapshotValidationError(f"no valid snapshot under {root}: {detail}")


def verify_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SnapshotValidationError(f"missing snapshot manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    layers = manifest.get("layers") or {}
    for layer_name in REQUIRED_MANIFEST_LAYERS:
        meta = layers.get(layer_name)
        if not meta:
            raise SnapshotValidationError(f"missing layer {layer_name}")
        rel = str(meta.get("file") or "")
        file_path = snapshot_dir / rel
        if not file_path.is_file():
            raise SnapshotValidationError(f"missing file {rel}")
        if _sha256(file_path) != str(meta.get("sha256") or ""):
            raise SnapshotValidationError(f"sha256 mismatch for {rel}")
    return manifest


def load_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    manifest = verify_snapshot(snapshot_dir)
    products = {_clean(row["sku"]): row for row in _read_tsv(snapshot_dir / "products.tsv") if _clean(row.get("sku"))}
    cards = {_clean(row["sku"]): row for row in _read_tsv(snapshot_dir / "product_cards.tsv") if _clean(row.get("sku"))}
    aliases_rows = _read_tsv(snapshot_dir / "aliases.tsv")
    resources_rows = _read_tsv(snapshot_dir / "resources.tsv")
    details_rows = (
        _read_tsv(snapshot_dir / "product_details.tsv")
        if (snapshot_dir / "product_details.tsv").is_file()
        else []
    )
    return {
        "snapshot_dir": snapshot_dir,
        "manifest": manifest,
        "products": products,
        "cards": cards,
        "aliases_rows": aliases_rows,
        "resources_rows": resources_rows,
        "details_rows": details_rows,
    }


def _aliases_by_sku(aliases_rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in aliases_rows:
        if not _is_active(row.get("active")):
            continue
        sku = _clean(row.get("canonical_sku"))
        if not sku:
            continue
        grouped.setdefault(sku, []).append(row)
    return grouped


def _resources_by_sku(resources_rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in resources_rows:
        if not _is_active(row.get("active")):
            continue
        sku = _clean(row.get("sku"))
        if not sku:
            continue
        grouped.setdefault(sku, []).append(row)
    return grouped


def _resource_counts(resources: list[dict[str, str]]) -> dict[str, int]:
    counts = {"image": 0, "video": 0, "certificate": 0, "pdf": 0, "docx": 0, "other": 0}
    for row in resources:
        kind = _clean(row.get("resource_type")).lower()
        if kind in counts:
            counts[kind] += 1
        else:
            counts["other"] += 1
    return counts


def _category(product: dict[str, str], card: dict[str, str] | None) -> str:
    for source in (card or {}, product):
        value = _clean(source.get("category"))
        if value:
            if value == "supplement":
                return "consumables"
            if value == "hygiene":
                return "home_wellness"
            return value
    name = _clean(product.get("canonical_name")).casefold()
    if any(token in name for token in ("маска", "гель", "спрей", "космет", "паста", "шампун")):
        return "cosmetics"
    if any(token in name for token in ("капсул", "чай", "эликсир", "спирулин", "кофе", "леденц")):
        return "consumables"
    if any(token in name for token in ("активатор", "вэнтун", "массаж", "сауна", "прибор", "набор")):
        return "device"
    if any(token in name for token in ("стельк", "носк", "пояс", "наколен", "шейн", "палантин", "проклад")):
        return "home_wellness"
    return "other"


def _count_rendered_sections(rendered: str) -> int:
    return sum(1 for heading in SECTION_HEADINGS.values() if heading in rendered)


def detect_renderer_defects(
    rendered: str,
    *,
    product: dict[str, str],
    card: dict[str, str] | None,
    has_primary_photo: bool,
    image_count: int,
) -> list[str]:
    defects: list[str] = []
    title = rendered.splitlines()[0].strip() if rendered else ""
    canonical = _clean((card or {}).get("canonical_name") or product.get("canonical_name"))
    sku = _clean(product.get("sku"))

    if canonical and title in {sku, "Товар"}:
        defects.append("empty_heading_or_fallback_label")
    if "**" in rendered:
        defects.append("literal_markdown_stars")
    if len(rendered) > TELEGRAM_SAFE_CHARS:
        defects.append("exceeds_telegram_safe_size")
    elif len(rendered) > TELEGRAM_WARN_CHARS:
        defects.append("near_telegram_size_limit")

    lines = [line.strip() for line in rendered.splitlines() if line.strip()]
    if len(lines) != len(set(lines)):
        defects.append("repeated_content")
    for line in lines:
        if line.startswith("• ") and (";" in line or "|" in line):
            defects.append("raw_delimiters_in_bullets")
            break
        if "|" in line and not line.startswith("http"):
            defects.append("raw_pipe_delimiter")
            break

    for heading in SECTION_HEADINGS.values():
        idx = rendered.find(heading)
        if idx == -1:
            continue
        chunk = rendered[idx + len(heading) : idx + len(heading) + 120].strip()
        if not chunk:
            defects.append("section_without_value")
            break

    if card and not has_primary_photo and image_count == 0:
        defects.append("photo_data_missing")

    return sorted(set(defects))


def grade_product(
    *,
    product: dict[str, str],
    card: dict[str, str] | None,
    alias_count: int,
    has_primary_photo: bool,
    retail_byn: bool,
    partner_byn: bool,
    pv_present: bool,
    rendered: str,
    rendered_sections: int,
    defects: list[str],
) -> str:
    if not card:
        return "blocked"
    if rendered_sections == 0:
        return "blocked"

    hard_defects = {d for d in defects if d not in {"near_telegram_size_limit", "photo_data_missing"}}
    if hard_defects:
        return "thin"

    showcase = (
        rendered_sections >= 3
        and has_primary_photo
        and retail_byn
        and partner_byn
        and pv_present
        and alias_count >= 1
        and len(rendered) <= TELEGRAM_WARN_CHARS
    )
    if showcase:
        return "showcase_ready"

    usable = rendered_sections >= 2 and (retail_byn or partner_byn) and pv_present and alias_count >= 1
    if usable:
        return "usable"

    return "thin"


def _missing_fields(
    product: dict[str, str],
    card: dict[str, str] | None,
    alias_count: int,
    resources: list[dict[str, str]],
) -> list[str]:
    missing: list[str] = []
    if not card:
        missing.append("product_cards:row_missing")
        return missing
    if not _clean(card.get("what_it_is")):
        missing.append("product_cards:what_it_is")
    for section in CARD_SECTIONS[1:]:
        if not _clean(card.get(section)):
            missing.append(f"product_cards:{section}")
    if alias_count == 0:
        missing.append("aliases:active_alias")
    if not _clean(card.get("primary_image_url")) and not any(
        _clean(r.get("resource_type")).lower() == "image" for r in resources
    ):
        missing.append("resources:primary_image")
    if not any(_clean(r.get("resource_type")).lower() == "video" for r in resources):
        missing.append("resources:video_optional")
    if not any(_clean(r.get("resource_type")).lower() in {"pdf", "certificate", "docx"} for r in resources):
        missing.append("resources:certificate_or_pdf_optional")
    if not _has_price(product.get("retail_price_byn")):
        missing.append("products:retail_price_byn")
    if not _has_price(product.get("partner_price_byn")):
        missing.append("products:partner_price_byn")
    if not _has_price(product.get(PV_FIELD)):
        missing.append("products:partner_points")
    return missing


def _redact_preview(rendered: str, limit: int = 160) -> str:
    text = re.sub(r"https?://\S+", "[url]", rendered)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


@dataclass
class ProductAuditRow:
    sku: str
    canonical_name: str
    category: str
    alias_count: int
    alias_examples: str
    has_card: bool
    has_primary_photo: bool
    extra_photo_count: int
    video_count: int
    cert_pdf_count: int
    retail_byn: bool
    partner_byn: bool
    pv_present: bool
    section_coverage: str
    presentation_grade: str
    missing_fields: str
    source_refs: str
    rendered_length: int
    rendered_sections: int
    renderer_defects: str
    rendered_preview: str = ""

    def to_csv_dict(self) -> dict[str, str]:
        return {
            "sku": self.sku,
            "canonical_name": self.canonical_name,
            "category": self.category,
            "alias_count": str(self.alias_count),
            "alias_examples": self.alias_examples,
            "has_card": str(self.has_card),
            "has_primary_photo": str(self.has_primary_photo),
            "extra_photo_count": str(self.extra_photo_count),
            "video_count": str(self.video_count),
            "cert_pdf_count": str(self.cert_pdf_count),
            "retail_byn": str(self.retail_byn),
            "partner_byn": str(self.partner_byn),
            "pv_present": str(self.pv_present),
            "section_coverage": self.section_coverage,
            "presentation_grade": self.presentation_grade,
            "missing_fields": self.missing_fields,
            "source_refs": self.source_refs,
            "rendered_length": str(self.rendered_length),
            "rendered_sections": str(self.rendered_sections),
            "renderer_defects": self.renderer_defects,
            "rendered_preview": self.rendered_preview,
        }


def build_product_rows(snapshot: dict[str, Any]) -> list[ProductAuditRow]:
    products: dict[str, dict[str, str]] = snapshot["products"]
    cards: dict[str, dict[str, str]] = snapshot["cards"]
    aliases_by_sku = _aliases_by_sku(snapshot["aliases_rows"])
    resources_by_sku = _resources_by_sku(snapshot["resources_rows"])
    snapshot_name = snapshot["snapshot_dir"].name

    rows: list[ProductAuditRow] = []
    for sku in sorted(products):
        product = products[sku]
        card = cards.get(sku)
        aliases = aliases_by_sku.get(sku, [])
        resources = resources_by_sku.get(sku, [])
        counts = _resource_counts(resources)
        primary_url = _clean((card or {}).get("primary_image_url"))
        image_resources = [r for r in resources if _clean(r.get("resource_type")).lower() == "image"]
        has_primary_photo = bool(primary_url or image_resources)
        if primary_url and image_resources:
            extra_photos = max(0, len(image_resources) - 1)
        else:
            extra_photos = len(image_resources)

        cert_pdf_count = counts["pdf"] + counts["certificate"] + counts["docx"]
        alias_examples = "; ".join(_clean(a.get("alias")) for a in aliases[:2])
        section_bits = []
        if card:
            for field in CARD_SECTIONS:
                section_bits.append(f"{field}={'Y' if _clean(card.get(field)) else 'N'}")
        section_coverage = "|".join(section_bits) if section_bits else "no_card"

        rendered = ""
        rendered_sections = 0
        defects: list[str] = []
        if card:
            rendered = render_telegram_product_card(card, product)
            rendered_sections = _count_rendered_sections(rendered)
            defects = detect_renderer_defects(
                rendered,
                product=product,
                card=card,
                has_primary_photo=has_primary_photo,
                image_count=len(image_resources),
            )

        retail_byn = _has_price(product.get("retail_price_byn"))
        partner_byn = _has_price(product.get("partner_price_byn"))
        pv_present = _has_price(product.get(PV_FIELD))
        grade = grade_product(
            product=product,
            card=card,
            alias_count=len(aliases),
            has_primary_photo=has_primary_photo,
            retail_byn=retail_byn,
            partner_byn=partner_byn,
            pv_present=pv_present,
            rendered=rendered,
            rendered_sections=rendered_sections,
            defects=defects,
        )
        missing = _missing_fields(product, card, len(aliases), resources)
        source_refs = f"snapshot={snapshot_name}; products.tsv; " + (
            "product_cards.tsv" if card else "product_cards.tsv:missing"
        )

        rows.append(
            ProductAuditRow(
                sku=sku,
                canonical_name=_clean(product.get("canonical_name")),
                category=_category(product, card),
                alias_count=len(aliases),
                alias_examples=alias_examples,
                has_card=bool(card),
                has_primary_photo=has_primary_photo,
                extra_photo_count=extra_photos,
                video_count=counts["video"],
                cert_pdf_count=cert_pdf_count,
                retail_byn=retail_byn,
                partner_byn=partner_byn,
                pv_present=pv_present,
                section_coverage=section_coverage,
                presentation_grade=grade,
                missing_fields="; ".join(missing),
                source_refs=source_refs,
                rendered_length=len(rendered),
                rendered_sections=rendered_sections,
                renderer_defects="; ".join(defects),
            )
        )
    return rows


def attach_previews(rows: list[ProductAuditRow], snapshot: dict[str, Any]) -> None:
    products = snapshot["products"]
    cards = snapshot["cards"]
    preview_skus = choose_preview_skus(rows, limit=15)
    by_sku = {row.sku: row for row in rows}
    for sku in preview_skus:
        row = by_sku[sku]
        card = cards.get(sku)
        if not card:
            row.rendered_preview = "(no card)"
            continue
        rendered = render_telegram_product_card(card, products[sku])
        row.rendered_preview = _redact_preview(rendered)


def choose_preview_skus(rows: list[ProductAuditRow], limit: int = 15) -> list[str]:
    blocked = [row.sku for row in rows if row.presentation_grade == "blocked"]
    thin = [row.sku for row in rows if row.presentation_grade == "thin"]
    long_cards = [
        row.sku
        for row in sorted(
            [row for row in rows if row.has_card],
            key=lambda row: row.rendered_length,
            reverse=True,
        )[:8]
    ]
    picks: list[str] = []
    for sku in blocked + thin + long_cards:
        if sku not in picks:
            picks.append(sku)
        if len(picks) >= limit:
            return picks
    return picks[:limit]


def build_backlog(rows: list[ProductAuditRow], snapshot_name: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    def add(priority: str, layer: str, sku: str, consequence: str, field: str, evidence: str) -> None:
        items.append(
            {
                "priority": priority,
                "layer": layer,
                "sku": sku,
                "consequence": consequence,
                "source_field": field,
                "evidence": evidence,
            }
        )

    for row in rows:
        if not row.has_card:
            priority = "P0" if row.category in {"device", "home_wellness", "cosmetics"} else "P1"
            add(
                priority,
                "Product_Cards",
                row.sku,
                "Telegram cannot show a structured card; user gets generic or missing-product behavior.",
                "product_cards.tsv row",
                f"{snapshot_name}/products.tsv + missing card row",
            )
            if row.has_primary_photo or row.video_count or row.cert_pdf_count:
                add(
                    "P1",
                    "Resources",
                    row.sku,
                    "Photo/video/PDF assets exist but no card text to present them in Telegram.",
                    "product_cards.tsv + existing resources.tsv",
                    f"images/videos/docs present without card (photo={row.has_primary_photo}, video={row.video_count})",
                )
            continue

        if "aliases:active_alias" in row.missing_fields:
            add(
                "P1",
                "Product_Aliases",
                row.sku,
                "User wording may not resolve to this SKU.",
                "aliases.tsv",
                f"{row.canonical_name}: alias_count=0",
            )

        if "photo_data_missing" in row.renderer_defects:
            add(
                "P0" if row.sku in {"M015-00", "EU-N000031-25", "EU-N000024-24", "M014-00"} else "P1",
                "Resources",
                row.sku,
                "Photo-first Telegram delivery cannot run; card becomes text-only.",
                "product_cards.primary_image_url or resources.tsv image",
                row.renderer_defects,
            )

        if "products:partner_price_byn" in row.missing_fields:
            add(
                "P1",
                "Prices/PV",
                row.sku,
                "Partner price in BYN may be missing in Telegram price answer.",
                "products.tsv partner_price_byn",
                row.missing_fields,
            )

        for defect in [part for part in row.renderer_defects.split("; ") if part]:
            if defect in {"photo_data_missing", "near_telegram_size_limit"}:
                continue
            add(
                "P1",
                "Renderer",
                row.sku,
                "Rendered Telegram card may look broken even though master fields exist.",
                "telegram_card.render_telegram_product_card",
                defect,
            )

    add(
        "P1",
        "Product_Details",
        "*",
        "No extended detail layer in master snapshot; richer cards rely solely on Product_Cards columns.",
        "product_details.tsv",
        f"{snapshot_name}/product_details.tsv has 0 data rows",
    )

    order = {"P0": 0, "P1": 1}
    return sorted(items, key=lambda item: (order.get(item["priority"], 9), item["sku"], item["layer"]))


SHOWCASE_REQUIRED = {
    "M015-00": "активатор",
    "EU-N000031-25": "активатор pro",
    "M014-00": "ба-гуа",
    "EU-N000024-24": "вэнтун",
}


def build_showcase(rows: list[ProductAuditRow]) -> list[dict[str, str]]:
    by_sku = {row.sku: row for row in rows}
    picks: list[dict[str, str]] = []

    def append(row: ProductAuditRow, phrase: str, note: str = "") -> None:
        picks.append(
            {
                "sku": row.sku,
                "name": row.canonical_name,
                "category": row.category,
                "grade": row.presentation_grade,
                "test_phrase": phrase,
                "note": note,
            }
        )

    for sku, phrase in SHOWCASE_REQUIRED.items():
        row = by_sku.get(sku)
        if row:
            append(row, phrase, "required anchor")

    filler_plan = [
        ("F036-00", "спирулина", "consumables anchor"),
        ("D013", "стельки", "home/wellness anchor"),
        ("F071-00", "паста цинфэн", "consumables"),
        ("EU-N000021-24", "magic foherb", "device breadth"),
        ("D003-00", "прокладки", "hygiene/home"),
        ("T015", "палантин", "accessory/home"),
        ("D014", "очки", "accessory"),
        ("F038-00", "соевый пептид", "consumables"),
    ]
    picked_skus = {item["sku"] for item in picks}
    for sku, phrase, note in filler_plan:
        if len(picks) >= 12:
            break
        row = by_sku.get(sku)
        if not row or row.sku in picked_skus or not row.has_card:
            continue
        append(row, phrase, note)
        picked_skus.add(row.sku)

    ranked = sorted(
        [row for row in rows if row.presentation_grade in {"showcase_ready", "usable"} and row.has_card],
        key=lambda row: (-row.alias_count, row.sku),
    )
    for row in ranked:
        if len(picks) >= 12:
            break
        if row.sku in picked_skus:
            continue
        phrase = row.alias_examples.split(";")[0].strip() if row.alias_examples else row.canonical_name.casefold()
        append(row, phrase or row.canonical_name.casefold(), "fill breadth")
        picked_skus.add(row.sku)

    return picks[:12]
