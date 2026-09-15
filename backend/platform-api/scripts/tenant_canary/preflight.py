"""Evaluate a tenant canary snapshot without writing or opening a live database."""

from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1]
_PLATFORM_API = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_PLATFORM_API) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_API))

from app.telegram.tenant_media import (  # noqa: E402
    filename_from_local_ref,
    is_safe_path_segment,
    normalize_media_base_url,
)
from tenant_release.package import load_package, validate_package  # noqa: E402
from tenant_release.prices import has_confirmed_retail, normalize_product_prices  # noqa: E402

SECRET_KEY_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "dsn",
    "phone",
    "authorization",
    "webhook",
)
SECRET_VALUE_RE = re.compile(
    r"(postgresql://\S+)|(\bbot\d+:[A-Za-z0-9_-]+)|(\+?\d{10,15})",
    re.IGNORECASE,
)
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
MANIFEST_COLUMNS = (
    "tenant_id",
    "sku",
    "relative_path",
    "filename",
    "source_path",
    "sha256",
    "bytes",
    "mime_type",
    "status",
    "reason",
)
IMAGE_MIMES = frozenset(
    {
        "image/webp",
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
    }
)
READY = "ready_for_shared_staging"
BLOCKED_PACKAGE = "blocked_package"
BLOCKED_BINDING = "blocked_binding"
BLOCKED_RUNTIME = "blocked_runtime"
BLOCKED_MEDIA = "blocked_media"
BLOCKED_MULTIPLE = "blocked_multiple"
CATEGORY_STATES = {
    "package": BLOCKED_PACKAGE,
    "binding": BLOCKED_BINDING,
    "runtime": BLOCKED_RUNTIME,
    "media": BLOCKED_MEDIA,
}


@dataclass
class Finding:
    category: str
    code: str
    message: str
    sku: str | None = None
    row: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"category": self.category, "code": self.code, "message": self.message}
        if self.sku:
            payload["sku"] = self.sku
        if self.row:
            payload["row"] = self.row
        return payload


@dataclass
class PreflightReport:
    state: str
    ok: bool
    tenant_id: str
    mode: str = "plan"
    findings: list[Finding] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)
    package: dict[str, Any] = field(default_factory=dict)
    binding: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    media: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "state": self.state,
            "mode": self.mode,
            "tenant_id": self.tenant_id,
            "counts": self.counts,
            "findings": [item.to_dict() for item in self.findings],
            "package": self.package,
            "binding": self.binding,
            "runtime": self.runtime,
            "media": self.media,
        }


def report_to_json(report: PreflightReport) -> str:
    return json.dumps(redact_payload(report.to_dict()), ensure_ascii=False, indent=2)


def report_to_markdown(report: PreflightReport) -> str:
    lines = [
        f"# Tenant canary preflight",
        "",
        f"- state: `{report.state}`",
        f"- tenant: `{report.tenant_id}`",
        f"- mode: `{report.mode}`",
        f"- ok: `{str(report.ok).lower()}`",
        "",
        "## Counts",
        "",
    ]
    for key, value in report.counts.items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Findings", ""])
    if not report.findings:
        lines.append("No blockers.")
    else:
        for item in report.findings:
            loc = item.sku or item.row or "-"
            lines.append(f"- `{item.category}` / `{item.code}` / {loc}: {item.message}")
    return "\n".join(lines) + "\n"


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in SECRET_KEY_FRAGMENTS):
                out[key] = "[redacted]"
            else:
                out[key] = redact_payload(nested)
        return out
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        if SECRET_VALUE_RE.search(value):
            return SECRET_VALUE_RE.sub("[redacted]", value)
        return value
    return value


def refuse_shared_staging_readonly(*, dsn: str | None = None) -> dict[str, Any]:
    if not str(dsn or "").strip():
        message = "shared-staging-readonly refused: pass a safe staging DSN only as documentation; live DSN is never opened"
    else:
        message = "shared-staging-readonly refused: this slice does not open a DSN; use snapshot files"
    return {
        "ok": False,
        "state": "blocked_runtime",
        "mode": "shared-staging-readonly",
        "code": "shared_staging_readonly_refused",
        "message": message,
        "dsn": "[redacted]",
    }


def _finding(category: str, code: str, message: str, *, sku: str | None = None, row: str | None = None) -> Finding:
    return Finding(category=category, code=code, message=message, sku=sku, row=row)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _photo_required(product: dict[str, Any], media_by_sku: dict[str, list[dict[str, Any]]]) -> bool:
    sku = str(product.get("sku") or "").strip()
    media_state = str(product.get("media_state") or "").strip().lower()
    if media_state in {"present", "required"}:
        return True
    if media_state == "missing":
        return False
    return bool(media_by_sku.get(sku))


def _check_package(
    *,
    tenant_id: str,
    package_dir: Path,
) -> tuple[list[Finding], dict[str, Any], dict[str, Any]]:
    findings: list[Finding] = []
    report = validate_package(package_dir)
    loaded = load_package(package_dir)
    products = loaded.layers["products"]
    media_rows = loaded.layers["media"]
    media_by_sku: dict[str, list[dict[str, Any]]] = {}
    for row in media_rows:
        sku = str(row.get("sku") or "").strip()
        if sku:
            media_by_sku.setdefault(sku, []).append(row)

    package_tenant = str(report.tenant_id or loaded.manifest.get("tenant_id") or "").strip()
    if package_tenant != tenant_id:
        findings.append(
            _finding(
                "package",
                "tenant_mismatch",
                f"package tenant_id {package_tenant!r} != requested {tenant_id!r}",
            )
        )
    for error in report.errors:
        findings.append(
            _finding(
                "package",
                str(error.get("code") or "package_error"),
                str(error.get("message") or "package validation error"),
                sku=str(error.get("sku") or "") or None,
                row=str(error.get("layer") or "") or None,
            )
        )

    products_by_sku = {str(item.get("sku") or "").strip(): item for item in products if item.get("sku")}
    eligible = list(report.eligible_skus)
    approved_skus = [
        sku for sku, item in products_by_sku.items() if str(item.get("review_status") or "") == "approved"
    ]
    for sku in eligible:
        product = products_by_sku.get(sku) or {}
        status = str(product.get("review_status") or "")
        if status != "approved":
            findings.append(
                _finding(
                    "package",
                    "ineligible_in_candidate",
                    f"review_status {status!r} must not enter the candidate set",
                    sku=sku,
                )
            )
        entries, _errors = normalize_product_prices(product)
        if not has_confirmed_retail(entries):
            findings.append(
                _finding(
                    "package",
                    "retail_price_missing",
                    "eligible product has no confirmed retail price; no FX inferred",
                    sku=sku,
                )
            )

    price_covered = 0
    for sku in eligible:
        product = products_by_sku.get(sku) or {}
        entries, _errors = normalize_product_prices(product)
        if has_confirmed_retail(entries):
            price_covered += 1

    summary = {
        "package_id": report.package_id,
        "package_sha256": report.package_sha256,
        "tenant_id": package_tenant,
        "eligible_skus": eligible,
        "validate_ok": report.ok,
    }
    counts = {
        "products": report.counts.get("products", len(products)),
        "approved": report.counts.get("approved", 0),
        "eligible": report.counts.get("eligible", len(eligible)),
        "review_required": report.counts.get("review_required", 0),
        "blocked": report.counts.get("blocked", 0),
        "price_covered": price_covered,
        "approved_rows": len(approved_skus),
    }
    extra = {
        "summary": summary,
        "products_by_sku": products_by_sku,
        "media_by_sku": media_by_sku,
        "eligible_skus": eligible,
        "counts": counts,
        "validate": json.loads(report.to_json()),
    }
    return findings, extra, counts


def _check_binding(
    *,
    tenant_id: str,
    snapshot: dict[str, Any],
) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    snapshot = redact_payload(snapshot) if isinstance(snapshot, dict) else {}
    profile_tenant = str(snapshot.get("tenant_id") or "").strip()
    tenant_status = str(snapshot.get("tenant_status") or "").strip().lower()
    if not profile_tenant:
        findings.append(_finding("binding", "tenant_profile_missing", "tenant profile is missing from binding snapshot"))
    elif profile_tenant != tenant_id:
        findings.append(
            _finding(
                "binding",
                "tenant_profile_mismatch",
                f"binding snapshot tenant_id {profile_tenant!r} != requested {tenant_id!r}",
            )
        )
    if tenant_status != "active":
        findings.append(
            _finding(
                "binding",
                "tenant_inactive",
                f"tenant status {tenant_status or 'missing'!r} is not active",
            )
        )

    bindings = snapshot.get("bindings") if isinstance(snapshot.get("bindings"), list) else []
    claimed_usernames: dict[str, list[str]] = {}
    claimed_bot_ids: dict[str, list[str]] = {}
    active_for_tenant: list[dict[str, Any]] = []
    for index, raw in enumerate(bindings):
        if not isinstance(raw, dict):
            findings.append(_finding("binding", "binding_row_invalid", "binding row must be an object", row=str(index)))
            continue
        row_tenant = str(raw.get("tenant_id") or "").strip()
        status = str(raw.get("status") or "").strip().lower()
        binding_id = str(raw.get("binding_id") or "").strip()
        username = str(raw.get("bot_username") or "").strip()
        bot_id = str(raw.get("bot_id") or "").strip()
        loc = binding_id or f"index:{index}"
        if username:
            claimed_usernames.setdefault(username.lower(), []).append(row_tenant or loc)
        if bot_id:
            claimed_bot_ids.setdefault(bot_id, []).append(row_tenant or loc)
        if row_tenant != tenant_id:
            if status == "active":
                findings.append(
                    _finding(
                        "binding",
                        "foreign_active_binding",
                        f"active binding {loc} belongs to tenant {row_tenant!r}",
                        row=loc,
                    )
                )
            continue
        if status == "active":
            active_for_tenant.append(raw)
            if not username or not bot_id:
                findings.append(
                    _finding(
                        "binding",
                        "binding_identity_missing",
                        "active binding must include bot_username and bot_id",
                        row=loc,
                    )
                )
        elif status in {"", "unknown"}:
            findings.append(
                _finding(
                    "binding",
                    "binding_unknown",
                    "unknown binding is not a canary success",
                    row=loc,
                )
            )

    if not active_for_tenant:
        findings.append(
            _finding(
                "binding",
                "active_binding_missing",
                "exactly one active binding is required; disabled/missing binding is not success",
            )
        )
    elif len(active_for_tenant) > 1:
        ids = [str(item.get("binding_id") or "") for item in active_for_tenant]
        findings.append(
            _finding(
                "binding",
                "multiple_active_bindings",
                f"exactly one active binding required, found {len(active_for_tenant)}: {ids}",
            )
        )

    for username, tenants in claimed_usernames.items():
        unique = {item for item in tenants if item}
        if len(unique) > 1:
            findings.append(
                _finding(
                    "binding",
                    "username_claimed_by_other_tenant",
                    f"bot_username {username!r} is claimed by {sorted(unique)}",
                    row=username,
                )
            )
    for bot_id, tenants in claimed_bot_ids.items():
        unique = {item for item in tenants if item}
        if len(unique) > 1:
            findings.append(
                _finding(
                    "binding",
                    "bot_id_claimed_by_other_tenant",
                    f"bot_id {bot_id!r} is claimed by {sorted(unique)}",
                    row=bot_id,
                )
            )

    summary = {
        "tenant_status": tenant_status,
        "active_bindings": len(active_for_tenant),
        "binding_ids": [str(item.get("binding_id") or "") for item in active_for_tenant],
    }
    return findings, summary


def _check_runtime(
    *,
    tenant_id: str,
    snapshot: dict[str, Any],
    eligible_skus: list[str],
    products_by_sku: dict[str, dict[str, Any]],
) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    runtime_tenant = str(snapshot.get("tenant_id") or "").strip()
    if runtime_tenant and runtime_tenant != tenant_id:
        findings.append(
            _finding(
                "runtime",
                "runtime_tenant_mismatch",
                f"runtime snapshot tenant_id {runtime_tenant!r} != requested {tenant_id!r}",
            )
        )
    candidates = snapshot.get("candidates") if isinstance(snapshot.get("candidates"), list) else []
    seen: list[str] = []
    for index, raw in enumerate(candidates):
        if not isinstance(raw, dict):
            findings.append(_finding("runtime", "candidate_row_invalid", "candidate row must be an object", row=str(index)))
            continue
        row_tenant = str(raw.get("tenant_id") or runtime_tenant or "").strip()
        sku = str(raw.get("sku") or "").strip()
        loc = sku or f"index:{index}"
        if row_tenant != tenant_id:
            findings.append(
                _finding(
                    "runtime",
                    "foreign_candidate",
                    f"runtime candidate belongs to tenant {row_tenant!r}",
                    sku=sku or None,
                    row=loc,
                )
            )
            continue
        status = str(raw.get("review_status") or (products_by_sku.get(sku) or {}).get("review_status") or "")
        if status in {"review_required", "blocked", "candidate"}:
            findings.append(
                _finding(
                    "runtime",
                    "ineligible_runtime_candidate",
                    f"review_status {status!r} must not appear in runtime candidates",
                    sku=sku or None,
                    row=loc,
                )
            )
        if sku and sku not in eligible_skus:
            findings.append(
                _finding(
                    "runtime",
                    "runtime_sku_not_eligible",
                    "runtime candidate is not in the package eligible set",
                    sku=sku,
                )
            )
        if sku:
            seen.append(sku)
    summary = {"candidate_skus": seen, "candidate_count": len(seen)}
    return findings, summary


def _read_manifest(path: Path) -> tuple[list[dict[str, str]], list[Finding]]:
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    if reader.fieldnames is None:
        findings.append(_finding("media", "manifest_header_missing", "media manifest has no header"))
        return [], findings
    missing_cols = [name for name in MANIFEST_COLUMNS if name not in reader.fieldnames]
    if missing_cols:
        findings.append(
            _finding(
                "media",
                "manifest_columns_missing",
                f"media manifest missing columns: {missing_cols}",
            )
        )
    rows: list[dict[str, str]] = []
    for index, raw in enumerate(reader, start=2):
        row = {key: str(raw.get(key) or "").strip() for key in MANIFEST_COLUMNS}
        row["_line"] = str(index)
        rows.append(row)
    return rows, findings


def _check_media(
    *,
    tenant_id: str,
    package_dir: Path,
    media_manifest: Path,
    media_base_url: str,
    eligible_skus: list[str],
    products_by_sku: dict[str, dict[str, Any]],
    media_by_sku: dict[str, list[dict[str, Any]]],
) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    normalized_base = normalize_media_base_url(media_base_url)
    if not normalized_base:
        findings.append(
            _finding(
                "media",
                "media_base_url_invalid",
                "media_base_url must be HTTPS without localhost, Drive, wwc.best or other forbidden hosts",
            )
        )

    for sku, rows in media_by_sku.items():
        for index, row in enumerate(rows):
            refs = [row.get("url"), row.get("relative_path"), row.get("filename"), row.get("photo_url")]
            for raw in refs:
                text = str(raw or "").strip()
                if not text:
                    continue
                if "://" in text or text.lower().startswith(("http:", "https:")):
                    findings.append(
                        _finding(
                            "media",
                            "remote_media_url",
                            "package media must be a local media/{tenant}/{sku}/{file} reference",
                            sku=sku,
                            row=text[:120],
                        )
                    )
                    continue
                parsed = filename_from_local_ref(text, tenant_id=tenant_id, sku=sku)
                if parsed is None and ("/" in text.replace("\\", "/") or text.startswith("media/")):
                    findings.append(
                        _finding(
                            "media",
                            "media_ref_not_aligned",
                            f"package media ref {text!r} is not aligned to media/{tenant_id}/{sku}/{{filename}}",
                            sku=sku,
                            row=text,
                        )
                    )

    manifest_rows, header_findings = _read_manifest(media_manifest)
    findings.extend(header_findings)
    destinations: dict[str, list[str]] = {}
    ready_by_sku: dict[str, list[dict[str, str]]] = {}
    missing_count = 0
    ready_count = 0
    for row in manifest_rows:
        sku = row["sku"]
        loc = row["relative_path"] or f"line:{row['_line']}"
        if row["tenant_id"] != tenant_id:
            findings.append(
                _finding(
                    "media",
                    "manifest_tenant_mismatch",
                    f"manifest tenant_id {row['tenant_id']!r} != requested {tenant_id!r}",
                    sku=sku or None,
                    row=loc,
                )
            )
        dest = row["relative_path"].replace("\\", "/")
        if dest:
            destinations.setdefault(dest, []).append(sku or loc)
        if "://" in dest or dest.lower().startswith(("http:", "https:")) or ".." in dest:
            findings.append(
                _finding(
                    "media",
                    "manifest_path_unsafe",
                    "manifest relative_path must be a local media path without URL or traversal",
                    sku=sku or None,
                    row=dest,
                )
            )
        expected = f"media/{tenant_id}/{sku}/{row['filename']}" if sku and row["filename"] else ""
        aligned = filename_from_local_ref(dest, tenant_id=tenant_id, sku=sku) if sku and dest else None
        if dest and sku and aligned is None:
            findings.append(
                _finding(
                    "media",
                    "manifest_path_not_aligned",
                    f"relative_path {dest!r} must match {expected}",
                    sku=sku,
                    row=dest,
                )
            )
        elif aligned and row["filename"] and aligned != row["filename"]:
            findings.append(
                _finding(
                    "media",
                    "manifest_filename_mismatch",
                    f"filename {row['filename']!r} != path segment {aligned!r}",
                    sku=sku,
                    row=dest,
                )
            )
        status = row["status"].lower()
        if status == "ready":
            ready_count += 1
            ready_by_sku.setdefault(sku, []).append(row)
            if not SHA256_RE.fullmatch(row["sha256"]):
                findings.append(_finding("media", "manifest_sha256_missing", "ready row needs sha256", sku=sku, row=loc))
            try:
                size = int(row["bytes"])
            except ValueError:
                size = 0
            if size <= 0:
                findings.append(_finding("media", "manifest_bytes_missing", "ready row needs nonzero bytes", sku=sku, row=loc))
            mime = row["mime_type"].lower()
            if mime not in IMAGE_MIMES:
                findings.append(
                    _finding(
                        "media",
                        "manifest_mime_missing",
                        "ready row needs an image MIME type",
                        sku=sku,
                        row=loc,
                    )
                )
            if not row["filename"] or not is_safe_path_segment(row["filename"]):
                findings.append(_finding("media", "manifest_filename_invalid", "ready row needs a safe filename", sku=sku, row=loc))
            source = row["source_path"]
            if not source:
                findings.append(_finding("media", "manifest_source_missing", "ready row needs source_path evidence", sku=sku, row=loc))
            else:
                source_path = Path(source)
                if not source_path.is_absolute():
                    source_path = (media_manifest.parent / source).resolve()
                if not source_path.is_file():
                    findings.append(
                        _finding(
                            "media",
                            "manifest_source_unreadable",
                            f"source file is not readable: {source}",
                            sku=sku,
                            row=loc,
                        )
                    )
        elif status == "missing":
            missing_count += 1
        else:
            findings.append(
                _finding(
                    "media",
                    "manifest_status_invalid",
                    f"status {row['status']!r} must be ready or missing",
                    sku=sku or None,
                    row=loc,
                )
            )

    for dest, owners in destinations.items():
        if len(owners) > 1:
            findings.append(
                _finding(
                    "media",
                    "duplicate_destination",
                    f"duplicate media destination {dest}",
                    row=dest,
                )
            )

    required_missing = 0
    for sku in eligible_skus:
        product = products_by_sku.get(sku) or {}
        if not _photo_required(product, media_by_sku):
            continue
        ready_rows = ready_by_sku.get(sku) or []
        if not ready_rows:
            required_missing += 1
            findings.append(
                _finding(
                    "media",
                    "required_photo_missing",
                    "eligible product declares required photo but has no ready manifest row",
                    sku=sku,
                )
            )

    summary = {
        "media_base_url": normalized_base,
        "manifest_rows": len(manifest_rows),
        "ready": ready_count,
        "missing": missing_count,
        "required_photo_gaps": required_missing,
    }
    return findings, summary


def evaluate_preflight(
    *,
    tenant_id: str,
    package_dir: Path,
    media_manifest: Path,
    binding_snapshot: Path,
    runtime_snapshot: Path,
    media_base_url: str,
    mode: str = "plan",
) -> PreflightReport:
    findings: list[Finding] = []
    package_findings, package_extra, counts = _check_package(tenant_id=tenant_id, package_dir=package_dir)
    findings.extend(package_findings)
    binding_raw = _load_json(binding_snapshot)
    runtime_raw = _load_json(runtime_snapshot)
    binding_findings, binding_summary = _check_binding(tenant_id=tenant_id, snapshot=binding_raw)
    findings.extend(binding_findings)
    runtime_findings, runtime_summary = _check_runtime(
        tenant_id=tenant_id,
        snapshot=runtime_raw,
        eligible_skus=package_extra["eligible_skus"],
        products_by_sku=package_extra["products_by_sku"],
    )
    findings.extend(runtime_findings)
    media_findings, media_summary = _check_media(
        tenant_id=tenant_id,
        package_dir=package_dir,
        media_manifest=media_manifest,
        media_base_url=media_base_url,
        eligible_skus=package_extra["eligible_skus"],
        products_by_sku=package_extra["products_by_sku"],
        media_by_sku=package_extra["media_by_sku"],
    )
    findings.extend(media_findings)

    categories = {item.category for item in findings}
    if not categories:
        state = READY
    elif len(categories) == 1:
        state = CATEGORY_STATES[next(iter(categories))]
    else:
        state = BLOCKED_MULTIPLE

    return PreflightReport(
        state=state,
        ok=state == READY,
        tenant_id=tenant_id,
        mode=mode,
        findings=findings,
        counts=counts,
        package=redact_payload(package_extra["summary"]),
        binding=redact_payload(binding_summary),
        runtime=redact_payload(runtime_summary),
        media=redact_payload(media_summary),
    )
