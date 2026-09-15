"""Deterministic local master-parity seed compiler for Golden HTTP lab."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SNAPSHOT = ROOT / "n8n" / "live-exports" / "structured-master" / "20260810T083328Z"
DEFAULT_SQL_OUT = Path(__file__).resolve().parents[1] / "fixtures" / "local_master_seed.sql"
DEFAULT_MANIFEST_OUT = Path(__file__).resolve().parents[1] / "fixtures" / "local_master_seed_manifest.json"
GOLDEN_CARDS_MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "snapshot_cards" / "manifest.json"
TENANT = "whieda"

REQUIRED_LAYERS = (
    "products",
    "aliases",
    "product_cards",
    "resources",
    "business_faq",
    "business_objections",
    "capability_responses",
    "clarification_prompts",
    "product_comparisons",
    "starter_basket_templates",
    "promotions",
    "events",
    "community_resources",
)

UNMAPPED_SOURCE_LAYERS = (
    "intent_registry",
    "partners_ref",
    "structure_owners",
    "users_access",
)


class MasterSeedCompileError(RuntimeError):
    pass


@dataclass
class LayerStats:
    source_rows: int = 0
    accepted: int = 0
    deduped: int = 0
    rejected: int = 0
    rejects: list[dict[str, str]] = field(default_factory=list)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_true(value: str | None) -> bool:
    return str(value or "").strip().upper() in {"TRUE", "1", "YES"}


def sql_str(value: str | None) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    if text == "":
        return "NULL"
    return "'" + text.replace("'", "''") + "'"


def sql_num(value: str | None) -> str:
    if value is None:
        return "NULL"
    text = str(value).strip()
    if not text or text == "-":
        return "NULL"
    return text.replace(",", ".")


def sql_bool(value: str | None) -> str:
    return "true" if _is_true(value) else "false"


def verify_snapshot(snapshot_dir: Path) -> dict[str, Any]:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise MasterSeedCompileError(f"missing snapshot manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    layers = manifest.get("layers") or {}
    for layer_name in REQUIRED_LAYERS:
        meta = layers.get(layer_name)
        if not meta:
            raise MasterSeedCompileError(f"manifest missing required layer: {layer_name}")
        rel_file = str(meta.get("file") or "")
        file_path = snapshot_dir / rel_file
        if not file_path.is_file():
            raise MasterSeedCompileError(f"missing required TSV: {rel_file}")
        actual = _sha256(file_path)
        expected = str(meta.get("sha256") or "")
        if actual != expected:
            raise MasterSeedCompileError(
                f"sha256 mismatch for {rel_file}: expected {expected}, got {actual}"
            )
    return manifest


def collect_golden_skus(*, cards_manifest: Path = GOLDEN_CARDS_MANIFEST) -> set[str]:
    if not cards_manifest.is_file():
        raise MasterSeedCompileError(f"missing golden cards manifest: {cards_manifest}")
    data = json.loads(cards_manifest.read_text(encoding="utf-8"))
    return {str(card.get("sku") or "").strip() for card in data.get("cards") or [] if str(card.get("sku") or "").strip()}


def compile_master_seed(
    *,
    snapshot_dir: Path = DEFAULT_SNAPSHOT,
    sql_out: Path = DEFAULT_SQL_OUT,
    manifest_out: Path = DEFAULT_MANIFEST_OUT,
) -> dict[str, Any]:
    snapshot_manifest = verify_snapshot(snapshot_dir)
    layer_stats: dict[str, LayerStats] = {}
    sql_lines: list[str] = [
        "-- NOT PRODUCTION DATA — golden local master-parity fixture",
        f"-- Generated from {snapshot_dir.relative_to(ROOT).as_posix()}",
        "-- Do not edit by hand; run compile_local_master_seed.py",
        "begin;",
        "",
    ]

    products_rows = _read_tsv(snapshot_dir / "products.tsv")
    ps = LayerStats(source_rows=len(products_rows))
    product_skus: set[str] = set()
    product_values: list[str] = []
    for idx, row in enumerate(products_rows, start=2):
        if str(row.get("project_id") or "").strip() != TENANT:
            ps.rejected += 1
            ps.rejects.append({"locator": f"products.tsv:{idx}", "reason": "non-whieda project_id"})
            continue
        sku = str(row.get("sku") or "").strip()
        name = str(row.get("canonical_name") or "").strip()
        if not sku or not name:
            ps.rejected += 1
            ps.rejects.append({"locator": f"products.tsv:{idx}", "reason": "missing sku or canonical_name"})
            continue
        product_skus.add(sku)
        product_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(sku)}, {sql_str(name)}, {sql_str(row.get('category'))}, "
            f"{sql_num(row.get('retail_price_rub'))}, {sql_num(row.get('retail_w'))}, {sql_num(row.get('retail_price_byn'))}, "
            f"{sql_num(row.get('partner_price_rub'))}, {sql_num(row.get('partner_w'))}, {sql_num(row.get('partner_price_byn'))}, "
            f"{sql_num(row.get('partner_points'))}"
            ")"
        )
        ps.accepted += 1
    layer_stats["products"] = ps
    sql_lines.extend(
        [
            "-- products",
            "insert into advisor_structured_products (",
            "  client_id, sku, canonical_name, category, retail_price_rub, retail_w, retail_price_byn,",
            "  partner_price_rub, partner_w, partner_price_byn, partner_points",
            ") values",
            ",\n".join(product_values) if product_values else "-- (none)",
        ]
    )
    if product_values:
        sql_lines.append(
            "on conflict (client_id, sku) do update set "
            "canonical_name=excluded.canonical_name, category=excluded.category, "
            "retail_price_rub=excluded.retail_price_rub, retail_w=excluded.retail_w, "
            "retail_price_byn=excluded.retail_price_byn, partner_price_rub=excluded.partner_price_rub, "
            "partner_w=excluded.partner_w, partner_price_byn=excluded.partner_price_byn, "
            "partner_points=excluded.partner_points;"
        )
    sql_lines.append("")

    alias_rows = _read_tsv(snapshot_dir / "aliases.tsv")
    als = LayerStats(source_rows=len(alias_rows))
    deduped: dict[str, dict[str, str]] = {}
    for idx, row in enumerate(alias_rows, start=2):
        if str(row.get("project_id") or "").strip() != TENANT:
            als.rejected += 1
            als.rejects.append({"locator": f"aliases.tsv:{idx}", "reason": "non-whieda project_id"})
            continue
        alias = str(row.get("alias") or "").strip()
        sku = str(row.get("canonical_sku") or "").strip()
        if not alias or not sku:
            als.rejected += 1
            als.rejects.append({"locator": f"aliases.tsv:{idx}", "reason": "missing alias or canonical_sku"})
            continue
        if not _is_true(row.get("active")):
            als.rejected += 1
            als.rejects.append({"locator": f"aliases.tsv:{idx}", "reason": "inactive alias"})
            continue
        key = alias.casefold()
        priority = int(str(row.get("priority") or "0") or "0")
        existing = deduped.get(key)
        if existing:
            als.deduped += 1
            old_priority = int(str(existing.get("priority") or "0") or "0")
            if priority <= old_priority:
                continue
        deduped[key] = row
    alias_values: list[str] = []
    for row in deduped.values():
        alias_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(row.get('alias'))}, {sql_str(row.get('canonical_sku'))}, "
            f"{sql_str(row.get('canonical_name'))}, {sql_str(row.get('match_type'))}, "
            f"{int(str(row.get('priority') or '0') or '0')}, true, "
            f"{sql_str(row.get('answer_scope'))}, {sql_str(row.get('notes'))}, {sql_str(row.get('updated_at'))}"
            ")"
        )
        als.accepted += 1
    layer_stats["aliases"] = als
    sql_lines.extend(
        [
            "-- aliases",
            "insert into advisor_structured_aliases (",
            "  client_id, alias, canonical_sku, canonical_name, match_type, priority, active, answer_scope, notes, updated_at",
            ") values",
            ",\n".join(alias_values) if alias_values else "-- (none)",
        ]
    )
    if alias_values:
        sql_lines.append(
            "on conflict (client_id, alias) do update set "
            "canonical_sku=excluded.canonical_sku, canonical_name=excluded.canonical_name, "
            "match_type=excluded.match_type, priority=excluded.priority, active=true, "
            "answer_scope=excluded.answer_scope, notes=excluded.notes, updated_at=excluded.updated_at;"
        )
    sql_lines.append("")

    card_rows = _read_tsv(snapshot_dir / "product_cards.tsv")
    cs = LayerStats(source_rows=len(card_rows))
    card_values: list[str] = []
    card_skus: set[str] = set()
    for idx, row in enumerate(card_rows, start=2):
        if str(row.get("project_id") or "").strip() != TENANT:
            cs.rejected += 1
            cs.rejects.append({"locator": f"product_cards.tsv:{idx}", "reason": "non-whieda project_id"})
            continue
        sku = str(row.get("sku") or "").strip()
        if not sku:
            cs.rejected += 1
            cs.rejects.append({"locator": f"product_cards.tsv:{idx}", "reason": "missing sku"})
            continue
        if not str(row.get("what_it_is") or "").strip():
            cs.rejected += 1
            cs.rejects.append({"locator": f"product_cards.tsv:{idx}", "reason": "empty what_it_is"})
            continue
        card_skus.add(sku)
        card_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(sku)}, {sql_str(row.get('canonical_name'))}, {sql_str(row.get('short_name'))}, "
            f"{sql_str(row.get('what_it_is'))}, {sql_str(row.get('who_asks_about_it'))}, "
            f"{sql_str(row.get('common_use_cases'))}, {sql_str(row.get('how_to_use_short'))}, "
            f"{sql_str(row.get('what_to_expect_soft'))}, {sql_str(row.get('contraindications_short'))}, "
            f"{sql_str(row.get('primary_image_url'))}"
            ")"
        )
        cs.accepted += 1
    layer_stats["product_cards"] = cs
    sql_lines.extend(
        [
            "-- product_cards",
            "insert into advisor_structured_product_cards (",
            "  client_id, sku, canonical_name, short_name, what_it_is, who_asks_about_it, common_use_cases,",
            "  how_to_use_short, what_to_expect_soft, contraindications_short, primary_image_url",
            ") values",
            ",\n".join(card_values) if card_values else "-- (none)",
        ]
    )
    if card_values:
        sql_lines.append(
            "on conflict (client_id, sku) do update set "
            "canonical_name=excluded.canonical_name, short_name=excluded.short_name, "
            "what_it_is=excluded.what_it_is, who_asks_about_it=excluded.who_asks_about_it, "
            "common_use_cases=excluded.common_use_cases, how_to_use_short=excluded.how_to_use_short, "
            "what_to_expect_soft=excluded.what_to_expect_soft, "
            "contraindications_short=excluded.contraindications_short, "
            "primary_image_url=excluded.primary_image_url;"
        )
    sql_lines.append("")

    resource_rows = _read_tsv(snapshot_dir / "resources.tsv")
    rs = LayerStats(source_rows=len(resource_rows))
    resource_values: list[str] = []
    for idx, row in enumerate(resource_rows, start=2):
        if str(row.get("project_id") or "").strip() != TENANT:
            rs.rejected += 1
            rs.rejects.append({"locator": f"resources.tsv:{idx}", "reason": "non-whieda project_id"})
            continue
        if not _is_true(row.get("active")):
            rs.rejected += 1
            rs.rejects.append({"locator": f"resources.tsv:{idx}", "reason": "inactive resource"})
            continue
        resource_id = str(row.get("resource_id") or "").strip()
        if not resource_id or not str(row.get("url") or "").strip():
            rs.rejected += 1
            rs.rejects.append({"locator": f"resources.tsv:{idx}", "reason": "missing resource_id or url"})
            continue
        resource_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(resource_id)}, {sql_str(row.get('sku'))}, {sql_str(row.get('canonical_name'))}, "
            f"{sql_str(row.get('alias'))}, {sql_str(row.get('topic'))}, {sql_str(row.get('resource_type') or 'other')}, "
            f"{sql_str(row.get('title'))}, {sql_str(row.get('url'))}, {sql_str(row.get('source_owner'))}, "
            f"{sql_str(row.get('language'))}, {int(str(row.get('priority') or '0') or '0')}, true, "
            f"{sql_str(row.get('audience'))}, {sql_str(row.get('notes'))}, {sql_str(row.get('updated_at'))}"
            ")"
        )
        rs.accepted += 1
    layer_stats["resources"] = rs
    sql_lines.extend(
        [
            "-- resources",
            "insert into advisor_structured_resources (",
            "  client_id, resource_id, sku, canonical_name, alias, topic, resource_type, title, url,",
            "  source_owner, language, priority, active, audience, notes, updated_at",
            ") values",
            ",\n".join(resource_values) if resource_values else "-- (none)",
        ]
    )
    if resource_values:
        sql_lines.append(
            "on conflict (client_id, resource_id) do update set "
            "sku=excluded.sku, canonical_name=excluded.canonical_name, alias=excluded.alias, "
            "topic=excluded.topic, resource_type=excluded.resource_type, title=excluded.title, "
            "url=excluded.url, source_owner=excluded.source_owner, language=excluded.language, "
            "priority=excluded.priority, active=true, audience=excluded.audience, "
            "notes=excluded.notes, updated_at=excluded.updated_at;"
        )
    sql_lines.append("")

    faq_rows = _read_tsv(snapshot_dir / "business_faq.tsv")
    fs = LayerStats(source_rows=len(faq_rows))
    faq_values: list[str] = []
    for idx, row in enumerate(faq_rows, start=2):
        if str(row.get("project_id") or "").strip() != TENANT:
            fs.rejected += 1
            fs.rejects.append({"locator": f"business_faq.tsv:{idx}", "reason": "non-whieda project_id"})
            continue
        if not _is_true(row.get("active")):
            fs.rejected += 1
            fs.rejects.append({"locator": f"business_faq.tsv:{idx}", "reason": "inactive faq"})
            continue
        faq_id = str(row.get("faq_id") or "").strip()
        if not faq_id or not str(row.get("answer_text") or "").strip():
            fs.rejected += 1
            fs.rejects.append({"locator": f"business_faq.tsv:{idx}", "reason": "missing faq_id or answer_text"})
            continue
        faq_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(faq_id)}, {sql_str(row.get('title'))}, {sql_str(row.get('answer_text'))}, "
            f"{sql_str(row.get('aliases'))}, {int(str(row.get('priority') or '0') or '0')}, true"
            ")"
        )
        fs.accepted += 1
    layer_stats["business_faq"] = fs
    _append_simple_upsert(
        sql_lines,
        title="business_faq",
        table="advisor_structured_business_faq",
        columns="client_id, faq_id, title, answer_text, aliases, priority, active",
        values=faq_values,
        conflict="on conflict (client_id, faq_id) do update set title=excluded.title, answer_text=excluded.answer_text, aliases=excluded.aliases, priority=excluded.priority, active=true;",
    )

    obj_rows = _read_tsv(snapshot_dir / "business_objections.tsv")
    os_ = LayerStats(source_rows=len(obj_rows))
    obj_values: list[str] = []
    for idx, row in enumerate(obj_rows, start=2):
        if str(row.get("project_id") or "").strip() != TENANT:
            os_.rejected += 1
            os_.rejects.append({"locator": f"business_objections.tsv:{idx}", "reason": "non-whieda project_id"})
            continue
        if not _is_true(row.get("active")):
            os_.rejected += 1
            os_.rejects.append({"locator": f"business_objections.tsv:{idx}", "reason": "inactive objection"})
            continue
        objection_id = str(row.get("objection_id") or "").strip()
        if not objection_id:
            os_.rejected += 1
            os_.rejects.append({"locator": f"business_objections.tsv:{idx}", "reason": "missing objection_id"})
            continue
        obj_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(objection_id)}, {sql_str(row.get('title'))}, {sql_str(row.get('aliases'))}, "
            f"{sql_str(row.get('first_reply'))}, {sql_str(row.get('clarify'))}, {sql_str(row.get('next_step'))}, "
            f"{sql_str(row.get('do_not_say'))}, {int(str(row.get('priority') or '0') or '0')}, true"
            ")"
        )
        os_.accepted += 1
    layer_stats["business_objections"] = os_
    _append_simple_upsert(
        sql_lines,
        title="business_objections",
        table="advisor_structured_business_objections",
        columns="client_id, objection_id, title, aliases, first_reply, clarify, next_step, do_not_say, priority, active",
        values=obj_values,
        conflict="on conflict (client_id, objection_id) do update set title=excluded.title, aliases=excluded.aliases, first_reply=excluded.first_reply, clarify=excluded.clarify, next_step=excluded.next_step, do_not_say=excluded.do_not_say, priority=excluded.priority, active=true;",
    )

    cap_rows = _read_tsv(snapshot_dir / "capability_responses.tsv")
    caps = LayerStats(source_rows=len(cap_rows))
    cap_values: list[str] = []
    for idx, row in enumerate(cap_rows, start=2):
        if not _is_true(row.get("enabled")):
            caps.rejected += 1
            caps.rejects.append({"locator": f"capability_responses.tsv:{idx}", "reason": "disabled response"})
            continue
        response_id = str(row.get("response_id") or "").strip()
        if not response_id or not str(row.get("answer_text") or "").strip():
            caps.rejected += 1
            caps.rejects.append({"locator": f"capability_responses.tsv:{idx}", "reason": "missing response_id or answer_text"})
            continue
        cap_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(response_id)}, {sql_str(row.get('intent_id'))}, "
            f"{sql_str(row.get('answer_text'))}, true"
            ")"
        )
        caps.accepted += 1
    layer_stats["capability_responses"] = caps
    _append_simple_upsert(
        sql_lines,
        title="capability_responses",
        table="advisor_structured_capability_responses",
        columns="client_id, response_id, intent_id, answer_text, enabled",
        values=cap_values,
        conflict="on conflict (client_id, response_id) do update set intent_id=excluded.intent_id, answer_text=excluded.answer_text, enabled=true;",
    )

    clar_rows = _read_tsv(snapshot_dir / "clarification_prompts.tsv")
    cls = LayerStats(source_rows=len(clar_rows))
    clar_values: list[str] = []
    for idx, row in enumerate(clar_rows, start=2):
        if not _is_true(row.get("enabled")):
            cls.rejected += 1
            cls.rejects.append({"locator": f"clarification_prompts.tsv:{idx}", "reason": "disabled prompt"})
            continue
        key = str(row.get("clarification_key") or "").strip()
        if not key or not str(row.get("prompt_text") or "").strip():
            cls.rejected += 1
            cls.rejects.append({"locator": f"clarification_prompts.tsv:{idx}", "reason": "missing clarification_key or prompt_text"})
            continue
        clar_values.append(f"({sql_str(TENANT)}, {sql_str(key)}, {sql_str(row.get('prompt_text'))}, true)")
        cls.accepted += 1
    layer_stats["clarification_prompts"] = cls
    _append_simple_upsert(
        sql_lines,
        title="clarification_prompts",
        table="advisor_structured_clarification_prompts",
        columns="client_id, clarification_key, prompt_text, enabled",
        values=clar_values,
        conflict="on conflict (client_id, clarification_key) do update set prompt_text=excluded.prompt_text, enabled=true;",
    )

    cmp_rows = _read_tsv(snapshot_dir / "product_comparisons.tsv")
    cms = LayerStats(source_rows=len(cmp_rows))
    cmp_values: list[str] = []
    for idx, row in enumerate(cmp_rows, start=2):
        if str(row.get("project_id") or "").strip() != TENANT:
            cms.rejected += 1
            cms.rejects.append({"locator": f"product_comparisons.tsv:{idx}", "reason": "non-whieda project_id"})
            continue
        if not _is_true(row.get("active")):
            cms.rejected += 1
            cms.rejects.append({"locator": f"product_comparisons.tsv:{idx}", "reason": "inactive comparison"})
            continue
        comparison_id = str(row.get("comparison_id") or "").strip()
        if not comparison_id:
            cms.rejected += 1
            cms.rejects.append({"locator": f"product_comparisons.tsv:{idx}", "reason": "missing comparison_id"})
            continue
        cmp_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(comparison_id)}, {sql_str(row.get('title'))}, {sql_str(row.get('answer_text'))}, "
            f"{sql_str(row.get('left_sku'))}, {sql_str(row.get('right_sku'))}, "
            f"{int(str(row.get('priority') or '0') or '0')}, true"
            ")"
        )
        cms.accepted += 1
    layer_stats["product_comparisons"] = cms
    _append_simple_upsert(
        sql_lines,
        title="product_comparisons",
        table="advisor_structured_product_comparisons",
        columns="client_id, comparison_id, title, answer_text, left_sku, right_sku, priority, active",
        values=cmp_values,
        conflict="on conflict (client_id, comparison_id) do update set title=excluded.title, answer_text=excluded.answer_text, left_sku=excluded.left_sku, right_sku=excluded.right_sku, priority=excluded.priority, active=true;",
    )

    basket_rows = _read_tsv(snapshot_dir / "starter_basket_templates.tsv")
    bs = LayerStats(source_rows=len(basket_rows))
    basket_values: list[str] = []
    for idx, row in enumerate(basket_rows, start=2):
        template_id = str(row.get("template_id") or "").strip()
        if not template_id:
            bs.rejected += 1
            bs.rejects.append({"locator": f"starter_basket_templates.tsv:{idx}", "reason": "missing template_id"})
            continue
        basket_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(template_id)}, {sql_str(row.get('tenant_id') or 'by')}, "
            f"{sql_str(row.get('title'))}, {sql_str(row.get('goal') or 'balanced')}, "
            f"{sql_str(row.get('budget_min'))}, {sql_str(row.get('budget_max'))}, "
            f"{sql_str(row.get('target_pv_min'))}, {sql_str(row.get('target_pv_max'))}, "
            f"{sql_str(row.get('required_product_ids'))}, {sql_str(row.get('preferred_product_ids'))}, "
            f"{sql_str(row.get('excluded_product_ids'))}, {sql_str(row.get('description'))}, "
            f"{int(str(row.get('priority') or '100') or '100')}, {sql_str(row.get('status') or 'draft')}, "
            f"{sql_str(row.get('owner'))}, {sql_str(row.get('updated_at'))}"
            ")"
        )
        bs.accepted += 1
    layer_stats["starter_basket_templates"] = bs
    _append_simple_upsert(
        sql_lines,
        title="starter_basket_templates",
        table="advisor_starter_basket_templates",
        columns="client_id, template_id, tenant_id, title, goal, budget_min, budget_max, target_pv_min, target_pv_max, required_product_ids, preferred_product_ids, excluded_product_ids, description, priority, status, owner, updated_at",
        values=basket_values,
        conflict="on conflict (client_id, template_id) do update set tenant_id=excluded.tenant_id, title=excluded.title, goal=excluded.goal, budget_min=excluded.budget_min, budget_max=excluded.budget_max, target_pv_min=excluded.target_pv_min, target_pv_max=excluded.target_pv_max, required_product_ids=excluded.required_product_ids, preferred_product_ids=excluded.preferred_product_ids, excluded_product_ids=excluded.excluded_product_ids, description=excluded.description, priority=excluded.priority, status=excluded.status, owner=excluded.owner, updated_at=excluded.updated_at;",
    )

    promo_rows = _read_tsv(snapshot_dir / "promotions.tsv")
    prs = LayerStats(source_rows=len(promo_rows))
    promo_values: list[str] = []
    for idx, row in enumerate(promo_rows, start=2):
        promotion_id = str(row.get("promotion_id") or "").strip()
        if not promotion_id:
            prs.rejected += 1
            prs.rejects.append({"locator": f"promotions.tsv:{idx}", "reason": "missing promotion_id"})
            continue
        promo_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(promotion_id)}, {sql_str(row.get('tenant_id') or 'by')}, "
            f"{sql_str(row.get('title'))}, {sql_str(row.get('short_text'))}, {sql_str(row.get('full_text'))}, "
            f"{sql_str(row.get('country'))}, {sql_str(row.get('city'))}, {sql_str(row.get('starts_at'))}, "
            f"{sql_str(row.get('ends_at'))}, {sql_str(row.get('timezone'))}, {sql_str(row.get('promotion_type'))}, "
            f"{sql_str(row.get('product_ids'))}, {sql_str(row.get('min_amount'))}, {sql_str(row.get('min_pv'))}, "
            f"{sql_str(row.get('benefit_text'))}, {sql_str(row.get('source_url'))}, {sql_str(row.get('image_url'))}, "
            f"{int(str(row.get('priority') or '100') or '100')}, {sql_str(row.get('status') or 'draft')}, "
            f"{sql_str(row.get('owner'))}, {sql_str(row.get('updated_at'))}"
            ")"
        )
        prs.accepted += 1
    layer_stats["promotions"] = prs
    _append_simple_upsert(
        sql_lines,
        title="promotions",
        table="advisor_promotions",
        columns="client_id, promotion_id, tenant_id, title, short_text, full_text, country, city, starts_at, ends_at, timezone, promotion_type, product_ids, min_amount, min_pv, benefit_text, source_url, image_url, priority, status, owner, updated_at",
        values=promo_values,
        conflict="on conflict (client_id, promotion_id) do update set tenant_id=excluded.tenant_id, title=excluded.title, short_text=excluded.short_text, full_text=excluded.full_text, country=excluded.country, city=excluded.city, starts_at=excluded.starts_at, ends_at=excluded.ends_at, timezone=excluded.timezone, promotion_type=excluded.promotion_type, product_ids=excluded.product_ids, min_amount=excluded.min_amount, min_pv=excluded.min_pv, benefit_text=excluded.benefit_text, source_url=excluded.source_url, image_url=excluded.image_url, priority=excluded.priority, status=excluded.status, owner=excluded.owner, updated_at=excluded.updated_at;",
    )

    event_rows = _read_tsv(snapshot_dir / "events.tsv")
    es = LayerStats(source_rows=len(event_rows))
    event_values: list[str] = []
    for idx, row in enumerate(event_rows, start=2):
        event_id = str(row.get("event_id") or "").strip()
        if not event_id:
            es.rejected += 1
            es.rejects.append({"locator": f"events.tsv:{idx}", "reason": "missing event_id"})
            continue
        event_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(event_id)}, {sql_str(row.get('tenant_id') or 'by')}, "
            f"{sql_str(row.get('title'))}, {sql_str(row.get('event_type'))}, {sql_str(row.get('description'))}, "
            f"{sql_str(row.get('starts_at'))}, {sql_str(row.get('ends_at'))}, {sql_str(row.get('timezone'))}, "
            f"{sql_str(row.get('country'))}, {sql_str(row.get('city'))}, {sql_str(row.get('address'))}, "
            f"{sql_str(row.get('online_url'))}, {sql_str(row.get('contact'))}, {sql_str(row.get('audience_segment'))}, "
            f"{sql_str(row.get('leader_id'))}, {sql_str(row.get('image_url'))}, {sql_str(row.get('source_url'))}, "
            f"{sql_str(row.get('reminder_offsets'))}, {sql_str(row.get('status') or 'draft')}, "
            f"{sql_str(row.get('owner'))}, {sql_str(row.get('updated_at'))}, {sql_str(row.get('recurrence_rule'))}"
            ")"
        )
        es.accepted += 1
    layer_stats["events"] = es
    _append_simple_upsert(
        sql_lines,
        title="events",
        table="advisor_whieda_events",
        columns="client_id, event_id, tenant_id, title, event_type, description, starts_at, ends_at, timezone, country, city, address, online_url, contact, audience_segment, leader_id, image_url, source_url, reminder_offsets, status, owner, updated_at, recurrence_rule",
        values=event_values,
        conflict="on conflict (client_id, event_id) do update set tenant_id=excluded.tenant_id, title=excluded.title, event_type=excluded.event_type, description=excluded.description, starts_at=excluded.starts_at, ends_at=excluded.ends_at, timezone=excluded.timezone, country=excluded.country, city=excluded.city, address=excluded.address, online_url=excluded.online_url, contact=excluded.contact, audience_segment=excluded.audience_segment, leader_id=excluded.leader_id, image_url=excluded.image_url, source_url=excluded.source_url, reminder_offsets=excluded.reminder_offsets, status=excluded.status, owner=excluded.owner, updated_at=excluded.updated_at, recurrence_rule=excluded.recurrence_rule;",
    )

    comm_rows = _read_tsv(snapshot_dir / "community_resources.tsv")
    crs = LayerStats(source_rows=len(comm_rows))
    comm_values: list[str] = []
    for idx, row in enumerate(comm_rows, start=2):
        resource_id = str(row.get("resource_id") or "").strip()
        if not resource_id:
            crs.rejected += 1
            crs.rejects.append({"locator": f"community_resources.tsv:{idx}", "reason": "missing resource_id"})
            continue
        comm_values.append(
            "("
            f"{sql_str(TENANT)}, {sql_str(resource_id)}, {sql_str(row.get('tenant_id') or 'by')}, "
            f"{sql_str(row.get('leader_id'))}, {sql_str(row.get('title'))}, {sql_str(row.get('category'))}, "
            f"{sql_str(row.get('description'))}, {sql_str(row.get('url'))}, {sql_str(row.get('platform'))}, "
            f"{sql_str(row.get('country'))}, {sql_str(row.get('city'))}, {sql_str(row.get('audience'))}, "
            f"{sql_str(row.get('topic_tags'))}, {sql_str(row.get('access_level') or 'public')}, "
            f"{int(str(row.get('priority') or '100') or '100')}, {sql_bool(row.get('is_official'))}, "
            f"{sql_str(row.get('status') or 'draft')}, {sql_str(row.get('last_checked_at'))}, "
            f"{sql_str(row.get('owner'))}, {sql_str(row.get('updated_at'))}, {sql_str(row.get('notes'))}"
            ")"
        )
        crs.accepted += 1
    layer_stats["community_resources"] = crs
    _append_simple_upsert(
        sql_lines,
        title="community_resources",
        table="advisor_whieda_community_resources",
        columns="client_id, resource_id, tenant_id, leader_id, title, category, description, url, platform, country, city, audience, topic_tags, access_level, priority, is_official, status, last_checked_at, owner, updated_at, notes",
        values=comm_values,
        conflict="on conflict (client_id, resource_id) do update set tenant_id=excluded.tenant_id, leader_id=excluded.leader_id, title=excluded.title, category=excluded.category, description=excluded.description, url=excluded.url, platform=excluded.platform, country=excluded.country, city=excluded.city, audience=excluded.audience, topic_tags=excluded.topic_tags, access_level=excluded.access_level, priority=excluded.priority, is_official=excluded.is_official, status=excluded.status, last_checked_at=excluded.last_checked_at, owner=excluded.owner, updated_at=excluded.updated_at, notes=excluded.notes;",
    )

    sql_lines.append("commit;")
    sql_text = "\n".join(sql_lines) + "\n"
    sql_out.parent.mkdir(parents=True, exist_ok=True)
    sql_out.write_text(sql_text, encoding="utf-8", newline="\n")
    fixture_sql_sha256 = hashlib.sha256(sql_out.read_bytes()).hexdigest()

    golden_skus = collect_golden_skus()
    missing_products = sorted(golden_skus - product_skus)
    missing_cards = sorted(golden_skus - card_skus)

    fixture_manifest: dict[str, Any] = {
        "version": 1,
        "generated_from": snapshot_dir.relative_to(ROOT).as_posix(),
        "snapshot_captured_at": snapshot_manifest.get("captured_at"),
        "snapshot_manifest_sha256": _sha256(snapshot_dir / "manifest.json"),
        "fixture_sql_sha256": fixture_sql_sha256,
        "tenant": TENANT,
        "golden_snapshot_skus": sorted(golden_skus),
        "golden_sku_coverage": {
            "products_present": sorted(golden_skus & product_skus),
            "products_missing": missing_products,
            "cards_present": sorted(golden_skus & card_skus),
            "cards_missing": missing_cards,
        },
        "layer_stats": {
            name: {
                "source_rows": stat.source_rows,
                "accepted": stat.accepted,
                "deduped": stat.deduped,
                "rejected": stat.rejected,
                "rejects": stat.rejects[:20],
            }
            for name, stat in layer_stats.items()
        },
        "source_layer_sha256": {
            name: snapshot_manifest["layers"][name]["sha256"] for name in REQUIRED_LAYERS
        },
        "unmapped_source_layers": list(UNMAPPED_SOURCE_LAYERS),
    }
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(json.dumps(fixture_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if missing_products or missing_cards:
        raise MasterSeedCompileError(
            f"golden SKU coverage incomplete: products_missing={missing_products}, cards_missing={missing_cards}"
        )

    return fixture_manifest


def _append_simple_upsert(
    sql_lines: list[str],
    *,
    title: str,
    table: str,
    columns: str,
    values: list[str],
    conflict: str,
) -> None:
    sql_lines.extend([f"-- {title}", f"insert into {table} ({columns}) values"])
    if values:
        sql_lines.append(",\n".join(values))
        sql_lines.append(conflict)
    else:
        sql_lines.append(f"-- (none)")
    sql_lines.append("")
