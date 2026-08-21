"""In-memory and Postgres staging stores. Never writes advisor runtime tables."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from tenant_release.package import LoadedPackage, ValidateReport, load_package, validate_package
from tenant_release.prices import (
    has_confirmed_retail,
    legacy_byn_amount,
    normalize_product_prices,
)

RUNTIME_TABLE_FRAGMENTS = (
    "advisor_structured_",
    "advisor_promotions",
    "advisor_whieda_",
)

FORBIDDEN_STAGE_HOSTS = (
    "supabase",
    "amazonaws.com",
    "azure",
    "neon.tech",
    "render.com",
    "185.252.232.93",
    "wwc.best",
)
LOCAL_VERIFY_DB_PREFIX = "whieda_platform_staging_verify_"


class StageGuardError(RuntimeError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_stage_dsn(dsn: str) -> None:
    if not dsn:
        raise StageGuardError("stage DSN required")
    parsed = urlparse(dsn)
    host = (parsed.hostname or "").lower()
    db = (parsed.path or "").lstrip("/")
    if any(fragment in host for fragment in FORBIDDEN_STAGE_HOSTS):
        raise StageGuardError(f"stage DSN host refused: {host}")
    if host not in {"127.0.0.1", "localhost"}:
        raise StageGuardError("stage is allowed only against local verify DB")
    if not db.startswith(LOCAL_VERIFY_DB_PREFIX):
        raise StageGuardError("stage DSN database is not a local verify DB")


@dataclass
class StageResult:
    ok: bool
    mode: str = "stage"
    reused: bool = False
    run_id: str = ""
    package_id: str = ""
    package_version: str = ""
    tenant_id: str = ""
    package_sha256: str = ""
    staged_products: int = 0
    duplicates: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    validate: dict[str, Any] | None = None

    def to_json(self) -> str:
        payload = {
            "ok": self.ok,
            "mode": self.mode,
            "reused": self.reused,
            "run_id": self.run_id,
            "package_id": self.package_id,
            "package_version": self.package_version,
            "tenant_id": self.tenant_id,
            "package_sha256": self.package_sha256,
            "staged_products": self.staged_products,
            "duplicates": self.duplicates,
            "errors": self.errors,
            "validate": self.validate,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


@dataclass
class CandidateResult:
    ok: bool
    mode: str = "build-release-candidate"
    reused: bool = False
    candidate_id: str = ""
    tenant_id: str = ""
    package_id: str = ""
    package_sha256: str = ""
    status: str = ""
    superseded: list[str] = field(default_factory=list)
    products: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "mode": self.mode,
                "reused": self.reused,
                "candidate_id": self.candidate_id,
                "tenant_id": self.tenant_id,
                "package_id": self.package_id,
                "package_sha256": self.package_sha256,
                "status": self.status,
                "superseded": self.superseded,
                "products": self.products,
                "skipped": self.skipped,
                "errors": self.errors,
            },
            ensure_ascii=False,
            indent=2,
        )


class StagingStore(Protocol):
    def find_run(self, package_id: str, package_sha256: str) -> dict[str, Any] | None: ...

    def insert_run(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def product_count(self, run_id: str) -> int: ...

    def find_current_candidate(self, tenant_id: str, package_id: str) -> dict[str, Any] | None: ...

    def insert_candidate(self, payload: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any]: ...


class MemoryStagingStore:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.products: dict[str, list[dict[str, Any]]] = {}
        self.candidates: dict[str, dict[str, Any]] = {}
        self.candidate_products: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def find_run(self, package_id: str, package_sha256: str) -> dict[str, Any] | None:
        for row in self.runs.values():
            if row["package_id"] == package_id and row["package_sha256"] == package_sha256:
                return dict(row)
        return None

    def insert_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            existing = self.find_run(payload["package_id"], payload["package_sha256"])
            if existing:
                return existing
            run_id = payload.get("run_id") or str(uuid4())
            row = {**payload, "run_id": run_id, "created_at": payload.get("created_at") or utcnow()}
            self.runs[run_id] = row
            self.products[run_id] = list(payload.get("products") or [])
            return dict(row)

    def product_count(self, run_id: str) -> int:
        return len(self.products.get(run_id, []))

    def find_current_candidate(self, tenant_id: str, package_id: str) -> dict[str, Any] | None:
        for row in self.candidates.values():
            if (
                row["tenant_id"] == tenant_id
                and row["package_id"] == package_id
                and row["status"] == "current"
            ):
                return dict(row)
        return None

    def insert_candidate(self, payload: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            current = self.find_current_candidate(payload["tenant_id"], payload["package_id"])
            if current and current.get("package_sha256") == payload["package_sha256"]:
                return {**current, "reused": True, "superseded": []}
            superseded: list[str] = []
            if current:
                current["status"] = "superseded"
                self.candidates[current["candidate_id"]] = current
                superseded.append(current["candidate_id"])
            # One current candidate per tenant+package.
            for row in self.candidates.values():
                if (
                    row["tenant_id"] == payload["tenant_id"]
                    and row["package_id"] == payload["package_id"]
                    and row["status"] == "current"
                    and row.get("package_sha256") != payload["package_sha256"]
                ):
                    raise StageGuardError("parallel current candidate rejected")
            candidate_id = payload.get("candidate_id") or str(uuid4())
            row = {
                **payload,
                "candidate_id": candidate_id,
                "status": "current",
                "created_at": utcnow(),
            }
            self.candidates[candidate_id] = row
            self.candidate_products[candidate_id] = list(products)
            return {**row, "reused": False, "superseded": superseded}


_MEMORY = MemoryStagingStore()


def memory_store() -> MemoryStagingStore:
    return _MEMORY


def reset_memory_store() -> MemoryStagingStore:
    global _MEMORY
    _MEMORY = MemoryStagingStore()
    return _MEMORY


def _eligible_products(loaded: LoadedPackage, report: ValidateReport) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cards = {str(row.get("sku") or ""): row for row in loaded.layers["cards"]}
    media = {}
    for row in loaded.layers["media"]:
        media.setdefault(str(row.get("sku") or ""), []).append(row)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    eligible = set(report.eligible_skus)
    for product in loaded.layers["products"]:
        sku = str(product.get("sku") or "")
        status = str(product.get("review_status") or "")
        if status != "approved" or sku not in eligible:
            skipped.append(
                {
                    "sku": sku,
                    "review_status": status,
                    "reason": "not_approved_or_incomplete",
                }
            )
            continue
        card = cards.get(sku) or {}
        price_entries, _price_errors = normalize_product_prices(product)
        confirmed = has_confirmed_retail(price_entries)
        selected.append(
            {
                "tenant_id": str(loaded.manifest.get("tenant_id") or ""),
                "sku": sku,
                "canonical_name": product.get("canonical_name"),
                "retail_price_byn": legacy_byn_amount(price_entries),
                "partner_price_byn": product.get("partner_price_byn"),
                "partner_w": product.get("partner_w"),
                "price_missing": not confirmed,
                "retail_prices": price_entries,
                "media_state": product.get("media_state")
                or ("present" if media.get(sku) else "missing"),
                "card_present": bool(card),
                "source": product.get("source") or {},
                "review_status": "approved",
            }
        )
    return selected, skipped


def stage_package(package_dir, store: StagingStore | None = None) -> StageResult:
    store = store or memory_store()
    report = validate_package(package_dir)
    if not report.ok:
        return StageResult(
            ok=False,
            errors=list(report.errors),
            validate=json.loads(report.to_json()),
            package_id=report.package_id,
            tenant_id=report.tenant_id,
            package_sha256=report.package_sha256,
        )
    loaded = load_package(package_dir)
    existing = store.find_run(report.package_id, report.package_sha256)
    if existing:
        return StageResult(
            ok=True,
            reused=True,
            run_id=str(existing["run_id"]),
            package_id=report.package_id,
            package_version=report.package_version,
            tenant_id=report.tenant_id,
            package_sha256=report.package_sha256,
            staged_products=store.product_count(str(existing["run_id"])),
            duplicates=0,
            validate=json.loads(report.to_json()),
        )
    products = []
    for row in loaded.layers["products"]:
        entries, _errors = normalize_product_prices(row)
        products.append(
            {
                **row,
                "retail_prices": entries,
                "price_missing": not has_confirmed_retail(entries),
            }
        )
    inserted = store.insert_run(
        {
            "package_id": report.package_id,
            "package_version": report.package_version,
            "tenant_id": report.tenant_id,
            "package_sha256": report.package_sha256,
            "release_status": report.release_status,
            "products": products,
        }
    )
    return StageResult(
        ok=True,
        reused=False,
        run_id=str(inserted["run_id"]),
        package_id=report.package_id,
        package_version=report.package_version,
        tenant_id=report.tenant_id,
        package_sha256=report.package_sha256,
        staged_products=len(products),
        duplicates=0,
        validate=json.loads(report.to_json()),
    )


def build_release_candidate(package_dir, store: StagingStore | None = None) -> CandidateResult:
    store = store or memory_store()
    staged = stage_package(package_dir, store=store)
    if not staged.ok:
        return CandidateResult(ok=False, errors=list(staged.errors), tenant_id=staged.tenant_id)
    loaded = load_package(package_dir)
    report = validate_package(package_dir)
    selected, skipped = _eligible_products(loaded, report)
    inserted = store.insert_candidate(
        {
            "tenant_id": staged.tenant_id,
            "package_id": staged.package_id,
            "package_version": staged.package_version,
            "package_sha256": staged.package_sha256,
            "run_id": staged.run_id,
        },
        selected,
    )
    products = selected
    if inserted.get("reused"):
        products = list(getattr(store, "candidate_products", {}).get(inserted["candidate_id"], selected))
    return CandidateResult(
        ok=True,
        reused=bool(inserted.get("reused")),
        candidate_id=str(inserted["candidate_id"]),
        tenant_id=staged.tenant_id,
        package_id=staged.package_id,
        package_sha256=staged.package_sha256,
        status=str(inserted.get("status") or "current"),
        superseded=list(inserted.get("superseded") or []),
        products=products,
        skipped=skipped,
    )


def refuse_publish() -> dict[str, Any]:
    return {
        "ok": False,
        "mode": "publish",
        "errors": [
            {
                "code": "publish_forbidden",
                "message": "publish is not enabled in Core Gate E; runtime tables stay untouched",
            }
        ],
        "would_write": [],
    }


def asserts_no_runtime_write(sql: str) -> None:
    lowered = sql.lower()
    for fragment in RUNTIME_TABLE_FRAGMENTS:
        if fragment in lowered and ("insert" in lowered or "update" in lowered):
            raise StageGuardError(f"runtime table write refused: {fragment}")
