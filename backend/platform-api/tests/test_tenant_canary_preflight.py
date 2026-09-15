"""Core Gate I: read-only tenant canary preflight."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "backend" / "platform-api" / "scripts"
CLI = SCRIPTS / "run_tenant_canary_preflight.py"
FIXTURES = ROOT / "qa" / "tenant_canary_preflight"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "qa" / "tenant_canary_preflight"))
sys.path.insert(0, str(ROOT / "backend" / "platform-api"))
# qa/master_integrity ships a build_fixtures module too; drop its cached copy
sys.modules.pop("build_fixtures", None)

from build_fixtures import build  # noqa: E402
from tenant_canary.preflight import evaluate_preflight, redact_payload  # noqa: E402
from tenant_release.package import write_json  # noqa: E402

MEDIA_BASE = "https://media.example.org/media"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture(scope="session")
def fixtures() -> Path:
    build(FIXTURES)
    return FIXTURES


def _bundle(fixtures: Path, tenant_id: str) -> dict[str, Path]:
    base = fixtures / "tenants" / tenant_id
    return {
        "package": base / "package",
        "manifest": base / "media-manifest.tsv",
        "binding": base / "binding.json",
        "runtime": base / "runtime.json",
    }


def _evaluate(fixtures: Path, tenant_id: str, *, requested: str | None = None, **overrides):
    paths = _bundle(fixtures, tenant_id)
    return evaluate_preflight(
        tenant_id=requested or tenant_id,
        package_dir=overrides.get("package", paths["package"]),
        media_manifest=overrides.get("manifest", paths["manifest"]),
        binding_snapshot=overrides.get("binding", paths["binding"]),
        runtime_snapshot=overrides.get("runtime", paths["runtime"]),
        media_base_url=overrides.get("media_base_url", MEDIA_BASE),
    )


def test_ready_tenant_north_and_south_are_isolated(fixtures: Path):
    north = _evaluate(fixtures, "tenant-north")
    south = _evaluate(fixtures, "tenant-south")
    assert north.ok and north.state == "ready_for_shared_staging"
    assert south.ok and south.state == "ready_for_shared_staging"
    assert north.package["tenant_id"] == "tenant-north"
    assert south.package["tenant_id"] == "tenant-south"
    assert north.media["media_base_url"] == MEDIA_BASE
    north_media = json.loads((fixtures / "tenants" / "tenant-north" / "package" / "media.json").read_text(encoding="utf-8"))
    south_media = json.loads((fixtures / "tenants" / "tenant-south" / "package" / "media.json").read_text(encoding="utf-8"))
    assert north_media[0]["url"].startswith("media/tenant-north/")
    assert south_media[0]["url"].startswith("media/tenant-south/")
    assert north_media[0]["url"] != south_media[0]["url"]


def test_cli_ready_exit_zero(fixtures: Path, tmp_path: Path):
    paths = _bundle(fixtures, "tenant-north")
    report = tmp_path / "north.json"
    proc = _run(
        "--tenant",
        "tenant-north",
        "--package",
        str(paths["package"]),
        "--media-manifest",
        str(paths["manifest"]),
        "--binding-snapshot",
        str(paths["binding"]),
        "--runtime-snapshot",
        str(paths["runtime"]),
        "--media-base-url",
        MEDIA_BASE,
        "--report-out",
        str(report),
    )
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["state"] == "ready_for_shared_staging"
    assert report.is_file()
    assert report.with_suffix(".md").is_file()


def test_package_tenant_mismatch(fixtures: Path):
    report = _evaluate(fixtures, "tenant-north", requested="tenant-south")
    assert report.state in {"blocked_package", "blocked_multiple"}
    assert any(item.code == "tenant_mismatch" for item in report.findings)


def test_two_active_bindings(fixtures: Path, tmp_path: Path):
    src = _bundle(fixtures, "tenant-north")["binding"]
    copy = tmp_path / "binding.json"
    payload = json.loads(src.read_text(encoding="utf-8"))
    extra = dict(payload["bindings"][0])
    extra["binding_id"] = "north-advisor-bot-2"
    extra["bot_id"] = "91099"
    extra["bot_username"] = "north_canary_bot_2"
    payload["bindings"].append(extra)
    copy.write_text(json.dumps(payload), encoding="utf-8")
    report = _evaluate(fixtures, "tenant-north", binding=copy)
    assert report.state == "blocked_binding"
    assert any(item.code == "multiple_active_bindings" for item in report.findings)


def test_disabled_and_missing_binding(fixtures: Path, tmp_path: Path):
    src = json.loads(_bundle(fixtures, "tenant-north")["binding"].read_text(encoding="utf-8"))
    disabled = tmp_path / "disabled.json"
    src["bindings"][0]["status"] = "disabled"
    disabled.write_text(json.dumps(src), encoding="utf-8")
    report_disabled = _evaluate(fixtures, "tenant-north", binding=disabled)
    assert report_disabled.state == "blocked_binding"
    assert any(item.code == "active_binding_missing" for item in report_disabled.findings)

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"tenant_id": "tenant-north", "tenant_status": "active", "bindings": []}), encoding="utf-8")
    report_missing = _evaluate(fixtures, "tenant-north", binding=empty)
    assert report_missing.state == "blocked_binding"
    assert any(item.code == "active_binding_missing" for item in report_missing.findings)


def test_remote_and_cross_tenant_media_ref(fixtures: Path, tmp_path: Path):
    package = tmp_path / "remote-package"
    shutil.copytree(_bundle(fixtures, "tenant-north")["package"], package)
    media = json.loads((package / "media.json").read_text(encoding="utf-8"))
    media[0]["url"] = "https://drive.google.com/file/d/abc/view"
    write_json(package / "media.json", media)
    from tenant_release.package import seal_package

    seal_package(package)
    remote = _evaluate(fixtures, "tenant-north", package=package)
    assert any(item.code == "remote_media_url" for item in remote.findings)
    assert remote.state in {"blocked_media", "blocked_multiple"}

    media[0]["url"] = "media/tenant-south/SHARE-01/main.webp"
    write_json(package / "media.json", media)
    seal_package(package)
    crossed = _evaluate(fixtures, "tenant-north", package=package)
    assert any(item.code == "media_ref_not_aligned" for item in crossed.findings)


def test_absent_retail_price(fixtures: Path, tmp_path: Path):
    package = tmp_path / "no-price"
    shutil.copytree(_bundle(fixtures, "tenant-north")["package"], package)
    products = json.loads((package / "products.json").read_text(encoding="utf-8"))
    products[0]["prices"] = []
    products[0]["price_missing"] = True
    write_json(package / "products.json", products)
    from tenant_release.package import seal_package

    seal_package(package)
    report = _evaluate(fixtures, "tenant-north", package=package)
    assert any(item.code == "retail_price_missing" for item in report.findings)
    assert report.state in {"blocked_package", "blocked_multiple"}


def test_manifest_hash_bytes_mime_missing(fixtures: Path, tmp_path: Path):
    src = _bundle(fixtures, "tenant-north")["manifest"].read_text(encoding="utf-8")
    header, row = src.strip().splitlines()
    parts = row.split("\t")
    parts[5] = ""
    parts[6] = ""
    parts[7] = ""
    broken = tmp_path / "media-manifest.tsv"
    broken.write_text(header + "\n" + "\t".join(parts) + "\n", encoding="utf-8")
    report = _evaluate(fixtures, "tenant-north", manifest=broken)
    codes = {item.code for item in report.findings}
    assert "manifest_sha256_missing" in codes
    assert "manifest_bytes_missing" in codes
    assert "manifest_mime_missing" in codes
    assert report.state == "blocked_media"


def test_review_required_product_not_eligible(fixtures: Path, tmp_path: Path):
    runtime = tmp_path / "runtime.json"
    payload = json.loads(_bundle(fixtures, "tenant-north")["runtime"].read_text(encoding="utf-8"))
    payload["candidates"].append(
        {"tenant_id": "tenant-north", "sku": "SHARE-01-REVIEW", "review_status": "review_required"}
    )
    runtime.write_text(json.dumps(payload), encoding="utf-8")
    report = _evaluate(fixtures, "tenant-north", runtime=runtime)
    assert report.state == "blocked_runtime"
    assert any(item.code == "ineligible_runtime_candidate" for item in report.findings)


def test_report_redaction():
    leaked = {
        "bot_token": "123:secret-token",
        "nested": {"webhook_secret": "abc", "ok": True},
        "note": "dsn postgresql://user:pass@host/db and phone +375111111111",
    }
    cleaned = redact_payload(leaked)
    blob = json.dumps(cleaned)
    assert "secret-token" not in blob
    assert "postgresql://" not in blob
    assert "+375111111111" not in blob
    assert cleaned["bot_token"] == "[redacted]"
    assert cleaned["nested"]["webhook_secret"] == "[redacted]"
    assert cleaned["nested"]["ok"] is True


def test_default_invocation_makes_no_network_or_database(fixtures: Path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network or db connection attempted")

    monkeypatch.setattr(socket, "socket", boom)
    report = _evaluate(fixtures, "tenant-north")
    assert report.ok

    proc = _run("--shared-staging-readonly")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["code"] == "shared_staging_readonly_refused"
    assert "postgresql" not in proc.stdout.lower() or "[redacted]" in proc.stdout


def test_apply_and_publish_refused():
    assert _run("--apply").returncode == 1
    assert _run("--publish").returncode == 1
    missing = _run()
    assert missing.returncode == 1
