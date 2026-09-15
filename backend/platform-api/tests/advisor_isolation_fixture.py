"""In-memory overlapping catalogs for tenant advisor isolation tests."""

from __future__ import annotations

from typing import Any

from app.tenancy import TenantContext

SHARED_ALIAS = "спиралина"
SHARED_SKU = "SP-SHARED"
WHIEDA_ONLY_ALIAS = "активатор клеток"
WHIEDA_ONLY_SKU = "M015-00"

WHIEDA_SPIRULINA = {
    "sku": SHARED_SKU,
    "canonical_name": "Спирулина WHIEDA",
    "retail_price_byn": 45,
    "partner_price_byn": 30,
    "partner_w": 12,
    "retail_price_rub": 1400,
}

NSP_SPIRULINA = {
    "sku": SHARED_SKU,
    "canonical_name": "Спирулина NSP",
    "retail_price_byn": 99,
    "partner_price_byn": 70,
    "partner_w": 20,
    "retail_price_rub": 3100,
}

WHIEDA_ACTIVATOR = {
    "sku": WHIEDA_ONLY_SKU,
    "canonical_name": "Активатор клеток",
    "retail_price_byn": 1750,
    "partner_price_byn": 1050,
    "partner_w": 300,
    "retail_price_rub": 50000,
}


def nsp_tenant() -> TenantContext:
    return TenantContext(
        tenant_id="nsp-maxim",
        status="active",
        display_name="NSP",
        entitlements={"structure_basic": True},
    )


def empty_tenant() -> TenantContext:
    return TenantContext(
        tenant_id="nikita-demo",
        status="active",
        display_name="Nikita",
        entitlements={"structure_basic": True},
    )


class IsolationCatalog:
    """Two tenants share alias/SKU identity but not card, price, media, or FAQ."""

    def __init__(self) -> None:
        self.products: dict[str, dict[str, dict[str, Any]]] = {
            "whieda": {SHARED_SKU: dict(WHIEDA_SPIRULINA), WHIEDA_ONLY_SKU: dict(WHIEDA_ACTIVATOR)},
            "nsp-maxim": {SHARED_SKU: dict(NSP_SPIRULINA)},
        }
        self.aliases: dict[str, dict[str, str]] = {
            "whieda": {SHARED_ALIAS: SHARED_SKU, WHIEDA_ONLY_ALIAS: WHIEDA_ONLY_SKU},
            "nsp-maxim": {SHARED_ALIAS: SHARED_SKU},
        }
        self.cards: dict[str, dict[str, dict[str, Any]]] = {
            "whieda": {
                SHARED_SKU: {
                    "sku": SHARED_SKU,
                    "canonical_name": "Спирулина WHIEDA",
                    "what_it_is": "Карточка WHIEDA",
                    "primary_image_url": "https://cdn.whieda.example/spirulina.jpg",
                }
            },
            "nsp-maxim": {
                SHARED_SKU: {
                    "sku": SHARED_SKU,
                    "canonical_name": "Спирулина NSP",
                    "what_it_is": "Карточка NSP",
                    "primary_image_url": "https://cdn.nsp.example/spirulina.jpg",
                }
            },
        }
        self.resources: dict[str, dict[str, list[dict[str, Any]]]] = {
            "whieda": {
                SHARED_SKU: [
                    {
                        "resource_type": "image",
                        "url": "https://cdn.whieda.example/spirulina.jpg",
                        "title": "фото WHIEDA",
                    },
                    {
                        "resource_type": "pdf",
                        "url": "https://cdn.whieda.example/spirulina.pdf",
                        "title": "PDF WHIEDA",
                    },
                    {
                        "resource_type": "certificate",
                        "url": "https://cdn.whieda.example/cert.pdf",
                        "title": "сертификат WHIEDA",
                    },
                    {
                        "resource_type": "video",
                        "url": "https://cdn.whieda.example/spirulina.mp4",
                        "title": "видео WHIEDA",
                    },
                ]
            },
            "nsp-maxim": {
                SHARED_SKU: [
                    {
                        "resource_type": "image",
                        "url": "https://cdn.nsp.example/spirulina.jpg",
                        "title": "фото NSP",
                    },
                    {
                        "resource_type": "pdf",
                        "url": "https://cdn.nsp.example/spirulina.pdf",
                        "title": "PDF NSP",
                    },
                    {
                        "resource_type": "certificate",
                        "url": "https://cdn.nsp.example/cert.pdf",
                        "title": "сертификат NSP",
                    },
                    {
                        "resource_type": "video",
                        "url": "https://cdn.nsp.example/spirulina.mp4",
                        "title": "видео NSP",
                    },
                ]
            },
        }
        self.faqs: dict[str, dict[str, dict[str, Any]]] = {
            "whieda": {SHARED_ALIAS: {"answer_text": "FAQ спирулины WHIEDA"}},
            "nsp-maxim": {SHARED_ALIAS: {"answer_text": "FAQ спирулины NSP"}},
        }
        self.sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self.comparisons: dict[str, dict[str, dict[str, Any]]] = {
            "whieda": {},
            "nsp-maxim": {},
        }

    def _product(self, tenant_id: str, sku: str) -> dict[str, Any] | None:
        row = self.products.get(tenant_id, {}).get(sku)
        return dict(row) if row else None

    async def resolve_product_by_exact_alias(self, _conn, tenant_id: str, alias: str):
        sku = self.aliases.get(tenant_id, {}).get(alias.lower().strip())
        return self._product(tenant_id, sku) if sku else None

    async def fetch_alias_candidates(self, _conn, tenant_id: str, question: str):
        needle = question.lower()
        rows: list[dict[str, Any]] = []
        for alias, sku in self.aliases.get(tenant_id, {}).items():
            if alias in needle or needle in alias:
                product = self._product(tenant_id, sku)
                if product:
                    rows.append({**product, "alias": alias, "priority": 10, "match_type": "exact"})
        return rows

    async def resolve_product_by_sku(self, _conn, tenant_id: str, sku: str):
        return self._product(tenant_id, sku)

    async def resolve_product_by_partial_alias(self, conn, tenant_id: str, question: str):
        rows = await self.fetch_alias_candidates(conn, tenant_id, question)
        return rows[0] if rows else None

    async def resolve_activator_pro_product(self, _conn, tenant_id: str):
        return None

    async def resolve_product_by_slug(self, _conn, tenant_id: str, slug: str):
        return None

    async def load_product_card(self, _conn, tenant_id: str, sku: str):
        row = self.cards.get(tenant_id, {}).get(sku)
        return dict(row) if row else None

    async def load_product_resources(self, _conn, tenant_id: str, sku: str, resource_type: str | None = None):
        rows = [dict(item) for item in self.resources.get(tenant_id, {}).get(sku, [])]
        if resource_type:
            wanted = resource_type.lower()
            return [item for item in rows if str(item.get("resource_type") or "").lower() == wanted]
        return rows

    async def find_business_faq(self, _conn, tenant_id: str, question: str):
        needle = question.lower()
        for alias, row in self.faqs.get(tenant_id, {}).items():
            if alias in needle:
                return dict(row)
        return None

    async def find_canonical_question(self, *_args, **_kwargs):
        return None

    async def find_business_objection(self, *_args, **_kwargs):
        return None

    async def load_capability_response(self, *_args, **_kwargs):
        return None

    async def load_clarification_prompt(self, *_args, **_kwargs):
        return None

    async def count_catalog_products(self, _conn, tenant_id: str) -> int:
        return len(self.products.get(tenant_id, {}))

    async def list_catalog_products(self, _conn, tenant_id: str, page: int, size: int):
        items = list(self.products.get(tenant_id, {}).values())
        start = max(page - 1, 0) * size
        return items[start : start + size]

    async def load_active_promotions(self, _conn, tenant_id: str, country: str = "BY"):
        _ = country
        if tenant_id == "whieda":
            return [{"title": "Акция WHIEDA", "short_text": "только WHIEDA"}]
        return [{"title": "Акция NSP", "short_text": "только NSP"}] if tenant_id == "nsp-maxim" else []

    async def load_upcoming_events(self, _conn, tenant_id: str, country: str = "BY"):
        _ = country
        if tenant_id == "whieda":
            return [{"title": "Встреча WHIEDA"}]
        return [{"title": "Встреча NSP"}] if tenant_id == "nsp-maxim" else []

    async def load_community_resources(self, *_args, **_kwargs):
        return []

    async def load_starter_basket_templates(self, *_args, **_kwargs):
        return []

    async def load_active_solution_bundles(self, *_args, **_kwargs):
        return []

    async def load_products_by_skus(self, _conn, tenant_id: str, skus, *_args, **_kwargs):
        return [row for sku in skus if (row := self._product(tenant_id, sku))]

    async def load_recommendation_catalog(self, *_args, **_kwargs):
        return []

    async def load_coach_objections(self, *_args, **_kwargs):
        return []

    async def find_product_comparison(self, _conn, tenant_id: str, left_sku: str, right_sku: str):
        return None

    async def load_product_detail(self, *_args, **_kwargs):
        return None

    async def resolve_catalog_product_by_sku(self, conn, tenant_id: str, sku: str):
        return await self.resolve_product_by_sku(conn, tenant_id, sku)

    async def load_session_context(self, _conn, tenant_id: str, session_id: str):
        return dict(self.sessions.get((tenant_id, session_id), {}))

    async def merge_session_context(self, _conn, tenant_id: str, session_id: str, patch: dict[str, Any]):
        current = dict(self.sessions.get((tenant_id, session_id), {}))
        current.update(patch or {})
        self.sessions[(tenant_id, session_id)] = current
        return current
