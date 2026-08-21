"""Tenant release package firewall — validate, stage, candidate. Never publish."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "tenant-release-package.v1"
LAYER_NAMES = ("products", "aliases", "cards", "media", "faq")
REVIEW_STATUSES = frozenset({"approved", "review_required", "blocked", "candidate"})
RELEASE_STATUSES = frozenset({"candidate", "approved", "review", "blocked"})
CANDIDATE_REVIEW = "approved"

ROOT_MARKERS = ("postgres", "backend", "qa")


def repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        if all((candidate / marker).exists() for marker in ROOT_MARKERS):
            return candidate
    raise RuntimeError("repository root not found")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_json(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(encoded.encode("utf-8"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def seal_package(package_dir: Path) -> dict[str, Any]:
    """Fill manifest file hashes from on-disk JSON. Offline, no DB."""
    package_dir = package_dir.resolve()
    manifest_path = package_dir / "manifest.json"
    manifest = load_json(manifest_path)
    files = dict(manifest.get("files") or {})
    for layer in LAYER_NAMES:
        rel = str((files.get(layer) or {}).get("path") or f"{layer}.json")
        files[layer] = {"path": rel, "sha256": sha256_file(package_dir / rel)}
    manifest["files"] = files
    write_json(manifest_path, manifest)
    return manifest


@dataclass
class PackageIssue:
    code: str
    message: str
    sku: str | None = None
    layer: str | None = None


@dataclass
class ValidateReport:
    ok: bool
    mode: str = "validate"
    schema_version: str = SCHEMA_VERSION
    package_id: str = ""
    package_version: str = ""
    tenant_id: str = ""
    release_status: str = ""
    package_sha256: str = ""
    reused: bool = False
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    eligible_skus: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass
class LoadedPackage:
    directory: Path
    manifest: dict[str, Any]
    layers: dict[str, list[dict[str, Any]]]
    package_sha256: str


def _issue(code: str, message: str, *, sku: str | None = None, layer: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if sku:
        payload["sku"] = sku
    if layer:
        payload["layer"] = layer
    return payload


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def load_package(package_dir: Path) -> LoadedPackage:
    package_dir = package_dir.resolve()
    manifest = load_json(package_dir / "manifest.json")
    layers: dict[str, list[dict[str, Any]]] = {}
    file_hashes: dict[str, str] = {}
    for layer in LAYER_NAMES:
        spec = (manifest.get("files") or {}).get(layer) or {}
        rel = str(spec.get("path") or f"{layer}.json")
        path = package_dir / rel
        layers[layer] = _rows(load_json(path)) if path.is_file() else []
        file_hashes[layer] = sha256_file(path) if path.is_file() else ""
    package_sha256 = sha256_json(
        {
            "manifest": {
                key: manifest.get(key)
                for key in (
                    "schema_version",
                    "package_id",
                    "package_version",
                    "tenant_id",
                    "release_status",
                    "display",
                    "source",
                )
            },
            "files": file_hashes,
        }
    )
    return LoadedPackage(
        directory=package_dir,
        manifest=manifest,
        layers=layers,
        package_sha256=package_sha256,
    )


def validate_package(package_dir: Path) -> ValidateReport:
    """Read-only package report. No network, no database."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    try:
        loaded = load_package(package_dir)
    except (OSError, json.JSONDecodeError) as exc:
        return ValidateReport(ok=False, errors=[_issue("package_unreadable", str(exc))])

    manifest = loaded.manifest
    tenant_id = str(manifest.get("tenant_id") or "").strip()
    package_id = str(manifest.get("package_id") or "").strip()
    package_version = str(manifest.get("package_version") or "").strip()
    release_status = str(manifest.get("release_status") or "").strip()
    schema_version = str(manifest.get("schema_version") or "").strip()

    if schema_version != SCHEMA_VERSION:
        errors.append(_issue("schema_version", f"expected {SCHEMA_VERSION}"))
    if not package_id:
        errors.append(_issue("package_id_missing", "package_id is required"))
    if not package_version:
        errors.append(_issue("package_version_missing", "package_version is required"))
    if not tenant_id:
        errors.append(_issue("tenant_id_missing", "tenant_id is required"))
    if release_status not in RELEASE_STATUSES:
        errors.append(_issue("release_status_invalid", f"invalid release_status {release_status!r}"))
    display = manifest.get("display") if isinstance(manifest.get("display"), dict) else {}
    if not str(display.get("display_name") or "").strip():
        errors.append(_issue("display_name_missing", "display.display_name is required"))
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    if not str(source.get("kind") or "").strip() or not str(source.get("ref") or "").strip():
        errors.append(_issue("source_missing", "manifest source.kind and source.ref are required"))

    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    for layer in LAYER_NAMES:
        spec = files.get(layer) if isinstance(files.get(layer), dict) else {}
        rel = str(spec.get("path") or "")
        expected = str(spec.get("sha256") or "")
        path = loaded.directory / rel if rel else loaded.directory / f"{layer}.json"
        if not rel or not path.is_file():
            errors.append(_issue("file_missing", f"{layer} file missing", layer=layer))
            continue
        actual = sha256_file(path)
        if not expected:
            errors.append(_issue("hash_missing", f"{layer} sha256 missing", layer=layer))
        elif expected != actual:
            errors.append(
                _issue(
                    "hash_mismatch",
                    f"{layer} sha256 mismatch",
                    layer=layer,
                )
            )

    products = loaded.layers["products"]
    aliases = loaded.layers["aliases"]
    cards = loaded.layers["cards"]
    media = loaded.layers["media"]
    faqs = loaded.layers["faq"]

    foreign = False
    for layer, rows in loaded.layers.items():
        for row in rows:
            row_tenant = str(row.get("tenant_id") or "").strip()
            if row_tenant and tenant_id and row_tenant != tenant_id:
                foreign = True
                errors.append(
                    _issue(
                        "foreign_tenant",
                        f"{layer} row tenant_id {row_tenant!r} != manifest {tenant_id!r}",
                        sku=str(row.get("sku") or "") or None,
                        layer=layer,
                    )
                )
    if foreign:
        # Whole package aborts; later staging must insert nothing.
        errors.append(_issue("package_aborted", "foreign tenant row aborts the whole package"))

    sku_seen: dict[str, int] = {}
    for product in products:
        sku = str(product.get("sku") or "").strip()
        if not sku:
            errors.append(_issue("sku_missing", "product sku is required", layer="products"))
            continue
        sku_seen[sku] = sku_seen.get(sku, 0) + 1
        if sku_seen[sku] == 2:
            errors.append(_issue("duplicate_sku", f"duplicate sku {sku}", sku=sku, layer="products"))

    alias_to_skus: dict[str, set[str]] = {}
    for alias_row in aliases:
        alias = str(alias_row.get("alias") or "").strip().lower()
        sku = str(alias_row.get("canonical_sku") or alias_row.get("sku") or "").strip()
        if not alias or not sku:
            errors.append(_issue("alias_incomplete", "alias and canonical_sku required", layer="aliases", sku=sku or None))
            continue
        alias_to_skus.setdefault(alias, set()).add(sku)
    for alias, skus in alias_to_skus.items():
        if len(skus) > 1:
            errors.append(
                _issue(
                    "alias_collision",
                    f"alias {alias!r} maps to {sorted(skus)}",
                    layer="aliases",
                )
            )

    cards_by_sku = {str(row.get("sku") or "").strip(): row for row in cards if row.get("sku")}
    media_by_sku: dict[str, list[dict[str, Any]]] = {}
    for row in media:
        sku = str(row.get("sku") or "").strip()
        if sku:
            media_by_sku.setdefault(sku, []).append(row)

    approved = 0
    review_required = 0
    blocked = 0
    eligible: list[str] = []
    for product in products:
        sku = str(product.get("sku") or "").strip()
        status = str(product.get("review_status") or "").strip()
        if status not in REVIEW_STATUSES:
            errors.append(_issue("review_status_invalid", f"invalid review_status {status!r}", sku=sku or None))
            continue
        if status == "approved":
            approved += 1
        elif status == "review_required":
            review_required += 1
        elif status == "blocked":
            blocked += 1
        if status in {"review_required", "blocked", "candidate"}:
            continue
        card = cards_by_sku.get(sku)
        price_missing = bool(product.get("price_missing"))
        has_price = any(
            product.get(key) not in (None, "", 0, 0.0)
            for key in ("retail_price_byn", "partner_price_byn", "retail_price_rub")
        )
        media_state = str(product.get("media_state") or "").strip() or (
            "present" if media_by_sku.get(sku) else "missing"
        )
        source = product.get("source") if isinstance(product.get("source"), dict) else {}
        source_ok = bool(str(source.get("ref") or "").strip())
        if not str(product.get("canonical_name") or "").strip():
            gaps.append(_issue("canonical_missing", "canonical_name missing", sku=sku))
            continue
        if not card:
            gaps.append(_issue("card_missing", "approved card missing", sku=sku))
            continue
        if not source_ok:
            gaps.append(_issue("source_missing", "product source missing", sku=sku))
            continue
        if not has_price and not price_missing:
            gaps.append(_issue("price_gap", "price missing without price_missing", sku=sku))
            continue
        if has_price and price_missing:
            warnings.append(_issue("price_flag_conflict", "price present with price_missing", sku=sku))
        if media_state == "missing":
            gaps.append(_issue("media_missing", "media_state=missing", sku=sku))
        # media gap is honest and still eligible if other required fields exist
        eligible.append(sku)
        if price_missing:
            gaps.append(_issue("price_missing", "honest price_missing", sku=sku))

    report = ValidateReport(
        ok=not errors,
        package_id=package_id,
        package_version=package_version,
        tenant_id=tenant_id,
        release_status=release_status,
        package_sha256=loaded.package_sha256,
        errors=errors,
        warnings=warnings,
        counts={
            "products": len(products),
            "aliases": len(aliases),
            "cards": len(cards),
            "media": len(media),
            "faq": len(faqs),
            "approved": approved,
            "review_required": review_required,
            "blocked": blocked,
            "eligible": len(eligible),
        },
        gaps=gaps,
        eligible_skus=eligible,
    )
    return report
