"""Owner-facing sync registry metadata for admin cabinet (P0.4)."""

from __future__ import annotations

import re
from typing import Any

from app.settings import Settings, get_settings

SAFE_SHEET_URL = re.compile(
    r"^https://docs\.google\.com/spreadsheets/d/[a-zA-Z0-9_-]+(/edit)?/?(\?[^#]*)?(#.*)?$"
)

REGISTRY_KEYS = ("markets", "partners", "structured", "service_centers")

OWNER_GAP_MESSAGES = {
    "no_markets_sync_registry": "Реестр ещё не подключён к рабочей синхронизации.",
    "n8n_cron_not_exposed_to_core": "Журнал запусков пока не передаётся в кабинет. Работа сайта и заявок продолжается.",
    "no_service_centers_data": "Реестр пока пуст: данные появятся после первой синхронизации.",
}

REGISTRY_SPECS: dict[str, dict[str, str]] = {
    "markets": {
        "title": "Рынки и цены",
        "impact": "Влияет на отображение цен и рынков на публичном сайте.",
        "cabinet_href": "/cabinet/markets/",
    },
    "partners": {
        "title": "Партнёры и ref",
        "impact": "Определяет ref-ссылки партнёров и персональные страницы на wwc.best.",
        "cabinet_href": "/cabinet/referrals/",
    },
    "structured": {
        "title": "Бот Structured",
        "impact": "Питает структурированные ответы бота и справочники продуктов.",
        "cabinet_href": "/cabinet/sync/",
    },
    "service_centers": {
        "title": "Сервисные центры и покрытия",
        "impact": "Пока не участвует в маршрутизации заявок; нужен для карты поддержки.",
        "cabinet_href": "/cabinet/service-centers/",
    },
}


def safe_source_url(value: str | None) -> str | None:
    url = (value or "").strip()
    if not url or not SAFE_SHEET_URL.match(url):
        return None
    return url


def _settings_source(settings: Settings, url_attr: str, title_attr: str) -> tuple[str | None, str | None]:
    url = safe_source_url(getattr(settings, url_attr, None))
    title = (getattr(settings, title_attr, None) or "").strip() or None
    if url and not title:
        title = "Открыть управляющий реестр"
    return url, title if url else None


def resolve_registry_source(key: str, settings: Settings | None = None) -> tuple[str | None, str | None]:
    cfg = settings or get_settings()
    if key == "markets" and cfg.wwc_markets_sheet_id:
        sheet_id = cfg.wwc_markets_sheet_id.strip()
        return (
            f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit",
            "Google Sheets — рынки и цены",
        )
    mapping = {
        "markets": ("platform_admin_source_url_markets", "platform_admin_source_title_markets"),
        "partners": ("platform_admin_source_url_partners", "platform_admin_source_title_partners"),
        "structured": ("platform_admin_source_url_structured", "platform_admin_source_title_structured"),
        "service_centers": (
            "platform_admin_source_url_service_centers",
            "platform_admin_source_title_service_centers",
        ),
    }
    url_attr, title_attr = mapping[key]
    return _settings_source(cfg, url_attr, title_attr)


def operational_status(block: dict[str, Any] | None) -> str:
    if not block or block.get("field_status") == "gap":
        return "awaiting_first"
    if block.get("last_error"):
        return "needs_review"
    status = str(block.get("status") or "").lower()
    if status and status not in {"ok", "ready", "success"}:
        return "needs_review"
    return "working"


def enrich_registry_block(
    key: str,
    block: dict[str, Any],
    *,
    settings: Settings | None = None,
    runtime_row_count: int | None = None,
) -> dict[str, Any]:
    spec = REGISTRY_SPECS[key]
    source_url, source_title = resolve_registry_source(key, settings)
    enriched = dict(block)
    internal_reason = enriched.pop("reason", None)
    enriched["registry_key"] = key
    enriched["title"] = spec["title"]
    enriched["impact"] = spec["impact"]
    enriched["cabinet_href"] = spec["cabinet_href"]
    enriched["operational_status"] = operational_status(block)
    if internal_reason:
        enriched["owner_message"] = OWNER_GAP_MESSAGES.get(
            str(internal_reason),
            "Сведения появятся после подключения источника.",
        )
    if source_url:
        enriched["source_url"] = source_url
        enriched["source_title"] = source_title
    if runtime_row_count is not None:
        enriched["runtime_row_count"] = runtime_row_count
    return enriched


def build_empty_state_meta(key: str, *, settings: Settings | None = None) -> dict[str, Any]:
    spec = REGISTRY_SPECS[key]
    source_url, source_title = resolve_registry_source(key, settings)
    titles = {
        "markets": "Рынки и цены пока не синхронизированы",
        "service_centers": "Реестр сервисных центров пока пуст",
    }
    bodies = {
        "markets": (
            "После первой синхронизации управляющего реестра здесь появятся "
            "страны, валюты и прайс-листы для публичного сайта."
        ),
        "service_centers": (
            "После первой синхронизации управляющего реестра здесь появятся "
            "города, контакты, часы работы и покрытие."
        ),
    }
    impacts = {
        "markets": "Публичный сайт продолжает работать на текущих данных; заявки принимаются как обычно.",
        "service_centers": "Публичный сайт и заявки продолжают работать: этот раздел пока не участвует в маршрутизации.",
    }
    payload: dict[str, Any] = {
        "title": titles.get(key, spec["title"]),
        "body": bodies.get(key, "Данные появятся после подключения источника."),
        "site_impact": impacts.get(key, spec["impact"]),
    }
    if source_url:
        payload["source_url"] = source_url
        payload["source_title"] = source_title
    return payload


def build_registry_readiness_item(
    key: str,
    *,
    connected: bool,
    summary: str,
    settings: Settings | None = None,
) -> dict[str, Any]:
    spec = REGISTRY_SPECS[key]
    return {
        "registry_key": key,
        "label": spec["title"],
        "status": "connected" if connected else "awaiting",
        "summary": summary,
        "href": spec["cabinet_href"],
        "operational_status": "working" if connected else "awaiting_first",
    }
