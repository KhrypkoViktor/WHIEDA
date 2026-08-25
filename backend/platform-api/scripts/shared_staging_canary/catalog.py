"""Tenant-scoped catalog import for shared-staging canary. No other-tenant writes."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tenant_release.package import load_package, validate_package
from tenant_release.prices import has_confirmed_retail, normalize_product_prices


@dataclass
class ImportResult:
    ok: bool
    imported_skus: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


class MemoryCatalog:
    def __init__(self) -> None:
        self.products: dict[tuple[str, str], dict[str, Any]] = {}
        self.aliases: dict[tuple[str, str], dict[str, Any]] = {}
        self.cards: dict[tuple[str, str], dict[str, Any]] = {}
        self.resources: dict[tuple[str, str], dict[str, Any]] = {}
        self.faqs: dict[tuple[str, str], dict[str, Any]] = {}
        self.bindings: dict[str, dict[str, Any]] = {}
        self.webhooks_set: list[str] = []
        self.fail_after: str | None = None
        self._tx: list[dict[str, Any]] = []

    def seed_product(self, tenant_id: str, sku: str, canonical_name: str) -> None:
        self.products[(tenant_id, sku)] = {
            "tenant_id": tenant_id,
            "sku": sku,
            "canonical_name": canonical_name,
        }

    def product_skus(self, tenant_id: str) -> list[str]:
        return sorted(sku for tenant, sku in self.products if tenant == tenant_id)

    def canonical_name(self, tenant_id: str, sku: str) -> str:
        return str(self.products[(tenant_id, sku)]["canonical_name"])

    def binding(self, binding_id: str) -> dict[str, Any]:
        return self.bindings[binding_id]

    def upsert_binding(
        self,
        *,
        binding_id: str,
        tenant_id: str,
        status: str,
        bot_token_ref: str,
        bot_username: str = "lab_bot",
        processing_mode: str = "core",
        webhook_secret_ref: str = "env:LAB_WEBHOOK_SECRET",
    ) -> None:
        if status != "disabled":
            raise ValueError("canary binding must be disabled")
        self.bindings[binding_id] = {
            "binding_id": binding_id,
            "tenant_id": tenant_id,
            "status": status,
            "bot_token_ref": bot_token_ref,
            "bot_username": bot_username,
            "processing_mode": processing_mode,
            "webhook_secret_ref": webhook_secret_ref,
        }

    def begin(self) -> None:
        self._tx.append(
            {
                "products": copy.deepcopy(self.products),
                "aliases": copy.deepcopy(self.aliases),
                "cards": copy.deepcopy(self.cards),
                "resources": copy.deepcopy(self.resources),
                "faqs": copy.deepcopy(self.faqs),
                "bindings": copy.deepcopy(self.bindings),
            }
        )

    def commit(self) -> None:
        if self._tx:
            self._tx.pop()

    def rollback(self) -> None:
        if not self._tx:
            return
        saved = self._tx.pop()
        self.products = saved["products"]
        self.aliases = saved["aliases"]
        self.cards = saved["cards"]
        self.resources = saved["resources"]
        self.faqs = saved["faqs"]
        self.bindings = saved["bindings"]

    def upsert_product(self, tenant_id: str, sku: str, payload: dict[str, Any]) -> None:
        if self.fail_after and self.fail_after == sku:
            raise RuntimeError(f"simulated import error after {sku}")
        self.products[(tenant_id, sku)] = {"tenant_id": tenant_id, "sku": sku, **payload}

    def upsert_alias(self, tenant_id: str, alias: str, payload: dict[str, Any]) -> None:
        self.aliases[(tenant_id, alias)] = {"tenant_id": tenant_id, "alias": alias, **payload}

    def upsert_card(self, tenant_id: str, sku: str, payload: dict[str, Any]) -> None:
        self.cards[(tenant_id, sku)] = {"tenant_id": tenant_id, "sku": sku, **payload}

    def upsert_resource(self, tenant_id: str, resource_id: str, payload: dict[str, Any]) -> None:
        self.resources[(tenant_id, resource_id)] = {
            "tenant_id": tenant_id,
            "resource_id": resource_id,
            **payload,
        }

    def upsert_faq(self, tenant_id: str, faq_id: str, payload: dict[str, Any]) -> None:
        self.faqs[(tenant_id, faq_id)] = {"tenant_id": tenant_id, "faq_id": faq_id, **payload}


def _usd_only_errors(product: dict[str, Any]) -> list[dict[str, Any]]:
    sku = str(product.get("sku") or "")
    entries, errors = normalize_product_prices(product)
    issues = [item for item in errors if item]
    if not has_confirmed_retail(entries):
        issues.append({"code": "retail_usd_missing", "sku": sku, "message": "approved SKU needs USD retail"})
    for entry in entries:
        kind = str(entry.get("kind") or "")
        currency = str(entry.get("currency") or "")
        if kind == "partner":
            issues.append({"code": "usd_only_violation", "sku": sku, "message": "partner price is refused"})
        elif kind == "retail" and currency != "USD":
            issues.append(
                {
                    "code": "usd_only_violation",
                    "sku": sku,
                    "message": f"retail currency {currency!r} is refused; USD only",
                }
            )
    return issues


def select_import_skus(package_dir: Path, *, tenant_id: str) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    try:
        report = validate_package(package_dir)
        loaded = load_package(package_dir)
    except Exception as exc:
        return [], [{"code": "package_invalid", "message": str(exc)}], {}
    errors: list[dict[str, Any]] = list(report.errors)
    package_tenant = str(report.tenant_id or loaded.manifest.get("tenant_id") or "").strip()
    if package_tenant != tenant_id:
        errors.append(
            {
                "code": "tenant_mismatch",
                "message": f"package tenant_id {package_tenant!r} != {tenant_id!r}",
            }
        )
    products = loaded.layers.get("products") or []
    by_sku = {str(item.get("sku") or "").strip(): item for item in products if item.get("sku")}
    importable: list[str] = []
    eligible = set(report.eligible_skus)
    for sku, product in by_sku.items():
        if str(product.get("review_status") or "") != "approved":
            continue
        issues = _usd_only_errors(product)
        if issues:
            errors.extend(issues)
            continue
        if sku in eligible:
            importable.append(sku)
    if tenant_id == "nsp-maxim" and len(importable) != 17:
        errors.append(
            {
                "code": "nsp_approved_count",
                "message": f"nsp-maxim canary requires 17 USD-approved SKU, got {len(importable)}",
            }
        )
    return importable, errors, {"loaded": loaded, "report": report}


def import_tenant_catalog(
    catalog: MemoryCatalog,
    *,
    package_dir: Path,
    tenant_id: str,
) -> ImportResult:
    skus, errors, context = select_import_skus(package_dir, tenant_id=tenant_id)
    if errors:
        return ImportResult(ok=False, imported_skus=[], errors=errors)
    loaded = context["loaded"]
    products = {str(item.get("sku") or ""): item for item in loaded.layers.get("products") or []}
    catalog.begin()
    try:
        for sku in skus:
            product = products[sku]
            catalog.upsert_product(
                tenant_id,
                sku,
                {
                    "canonical_name": product.get("canonical_name"),
                    "retail_prices": product.get("prices") or product.get("retail_prices") or [],
                },
            )
        for row in loaded.layers.get("aliases") or []:
            sku = str(row.get("canonical_sku") or "")
            if sku not in skus:
                continue
            catalog.upsert_alias(tenant_id, str(row.get("alias") or sku), {"canonical_sku": sku})
        for row in loaded.layers.get("cards") or []:
            sku = str(row.get("sku") or "")
            if sku not in skus:
                continue
            catalog.upsert_card(tenant_id, sku, {"what_it_is": row.get("what_it_is")})
        for row in loaded.layers.get("media") or []:
            sku = str(row.get("sku") or "")
            if sku not in skus:
                continue
            resource_id = str(row.get("resource_id") or f"{sku}-photo")
            catalog.upsert_resource(tenant_id, resource_id, {"sku": sku, "url": row.get("url")})
        for row in loaded.layers.get("faq") or []:
            sku = str(row.get("sku") or "")
            if sku and sku not in skus:
                continue
            faq_id = str(row.get("faq_id") or f"{tenant_id}-faq")
            catalog.upsert_faq(tenant_id, faq_id, {"answer_text": row.get("answer_text")})
        catalog.commit()
    except Exception as exc:
        catalog.rollback()
        return ImportResult(ok=False, imported_skus=[], errors=[{"code": "import_rolled_back", "message": str(exc)}])
    return ImportResult(ok=True, imported_skus=skus)


def _sql_lit(value: Any) -> str:
    if value is None:
        return "null"
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def render_disabled_binding_sql(*, tenant_id: str, binding_id: str) -> str:
    return f"""
insert into tenants (tenant_id, display_name, status, default_locale, default_country)
values ({_sql_lit(tenant_id)}, {_sql_lit(tenant_id)}, 'active', 'ru', 'US')
on conflict (tenant_id) do update
set status = 'active', updated_at = now();

insert into tenant_entitlements (tenant_id, feature_key, enabled)
values ({_sql_lit(tenant_id)}, 'structure_basic', true)
on conflict (tenant_id, feature_key) do update set enabled = true;

insert into tenant_advisor_profile (tenant_id, advisor_signature, empty_catalog_guidance)
values ({_sql_lit(tenant_id)}, 'советник', 'Каталог пуст. Товары другого проекта не подставляются.')
on conflict (tenant_id) do update
set advisor_signature = excluded.advisor_signature;

insert into tenant_bot_bindings (
  binding_id, tenant_id, webhook_secret_ref, status,
  bot_token_ref, bot_username, processing_mode
) values (
  {_sql_lit(binding_id)}, {_sql_lit(tenant_id)},
  {_sql_lit('env:' + tenant_id.upper().replace('-', '_') + '_WEBHOOK_SECRET')},
  'disabled',
  {_sql_lit('env:' + tenant_id.upper().replace('-', '_') + '_BOT_TOKEN')},
  {_sql_lit(tenant_id.replace('-', '_') + '_bot')},
  'core'
)
on conflict (binding_id) do update
set tenant_id = excluded.tenant_id,
    status = 'disabled',
    webhook_secret_ref = excluded.webhook_secret_ref,
    bot_token_ref = excluded.bot_token_ref,
    bot_username = excluded.bot_username,
    processing_mode = excluded.processing_mode,
    updated_at = now();
"""


def render_import_sql(package_dir: Path, *, tenant_id: str) -> tuple[str | None, ImportResult]:
    skus, errors, context = select_import_skus(package_dir, tenant_id=tenant_id)
    if errors:
        return None, ImportResult(ok=False, imported_skus=[], errors=errors)
    loaded = context["loaded"]
    products = {str(item.get("sku") or ""): item for item in loaded.layers.get("products") or []}
    sku_set = set(skus)
    lines = ["begin;", render_disabled_binding_sql(tenant_id=tenant_id, binding_id=f"{tenant_id}-canary-bot")]
    import json

    for sku in skus:
        product = products[sku]
        name = product.get("canonical_name") or sku
        prices = product.get("prices") or product.get("retail_prices") or []
        lines.append(
            "insert into advisor_structured_products "
            "(client_id, sku, canonical_name, retail_price_rub, retail_price_byn, retail_prices) values ("
            f"{_sql_lit(tenant_id)}, {_sql_lit(sku)}, {_sql_lit(name)}, null, null, "
            f"{_sql_lit(json.dumps(prices, ensure_ascii=False))}::jsonb) "
            "on conflict (client_id, sku) do update set "
            "canonical_name = excluded.canonical_name, retail_prices = excluded.retail_prices;"
        )
    for row in loaded.layers.get("aliases") or []:
        sku = str(row.get("canonical_sku") or "")
        if sku not in sku_set:
            continue
        alias = str(row.get("alias") or sku)
        name = (products.get(sku) or {}).get("canonical_name") or sku
        lines.append(
            "insert into advisor_structured_aliases "
            "(client_id, alias, canonical_sku, canonical_name, priority, active) values ("
            f"{_sql_lit(tenant_id)}, {_sql_lit(alias)}, {_sql_lit(sku)}, {_sql_lit(name)}, 100, true) "
            "on conflict (client_id, alias) do update set "
            "canonical_sku = excluded.canonical_sku, canonical_name = excluded.canonical_name, active = true;"
        )
    for row in loaded.layers.get("cards") or []:
        sku = str(row.get("sku") or "")
        if sku not in sku_set:
            continue
        name = row.get("canonical_name") or (products.get(sku) or {}).get("canonical_name") or sku
        what = row.get("what_it_is") or ""
        image = row.get("primary_image_url") or ""
        if not image:
            for media in loaded.layers.get("media") or []:
                if str(media.get("sku") or "") == sku:
                    image = str(media.get("url") or media.get("relative_path") or "")
                    break
        lines.append(
            "insert into advisor_structured_product_cards "
            "(client_id, sku, canonical_name, what_it_is, who_asks_about_it, common_use_cases, "
            "how_to_use_short, primary_image_url) values ("
            f"{_sql_lit(tenant_id)}, {_sql_lit(sku)}, {_sql_lit(name)}, {_sql_lit(what)}, "
            f"'canary', 'canary', 'as directed', {_sql_lit(image)}) "
            "on conflict (client_id, sku) do update set "
            "canonical_name = excluded.canonical_name, what_it_is = excluded.what_it_is, "
            "primary_image_url = excluded.primary_image_url;"
        )
    for row in loaded.layers.get("media") or []:
        sku = str(row.get("sku") or "")
        if sku not in sku_set:
            continue
        resource_id = str(row.get("resource_id") or f"{sku}-photo")
        name = (products.get(sku) or {}).get("canonical_name") or sku
        url = str(row.get("url") or row.get("relative_path") or "")
        title = str(row.get("title") or name)
        lines.append(
            "insert into advisor_structured_resources "
            "(client_id, resource_id, sku, canonical_name, resource_type, title, url, priority, active) values ("
            f"{_sql_lit(tenant_id)}, {_sql_lit(resource_id)}, {_sql_lit(sku)}, {_sql_lit(name)}, "
            f"'image', {_sql_lit(title)}, {_sql_lit(url)}, 100, true) "
            "on conflict (client_id, resource_id) do update set "
            "sku = excluded.sku, url = excluded.url, active = true;"
        )
    for row in loaded.layers.get("faq") or []:
        sku = str(row.get("sku") or "")
        if sku and sku not in sku_set:
            continue
        faq_id = str(row.get("faq_id") or f"{tenant_id}-faq")
        title = str(row.get("title") or sku or "faq")
        answer = str(row.get("answer_text") or "")
        lines.append(
            "insert into advisor_structured_business_faq "
            "(client_id, faq_id, title, answer_text, aliases, priority, active) values ("
            f"{_sql_lit(tenant_id)}, {_sql_lit(faq_id)}, {_sql_lit(title)}, {_sql_lit(answer)}, "
            f"{_sql_lit(title)}, 100, true) "
            "on conflict (client_id, faq_id) do update set "
            "title = excluded.title, answer_text = excluded.answer_text, active = true;"
        )
    lines.append("commit;")
    return "\n".join(lines), ImportResult(ok=True, imported_skus=skus)

