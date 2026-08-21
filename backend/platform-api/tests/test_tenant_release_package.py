"""Tenant release package firewall: validate/stage/candidate, never publish."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "backend" / "platform-api" / "scripts"
CLI = SCRIPTS / "run_tenant_release_package.py"
EXAMPLES = ROOT / "qa" / "tenant_release_package" / "examples"
FIXTURES = ROOT / "qa" / "tenant_release_package" / "fixtures"
sys.path.insert(0, str(SCRIPTS))

from tenant_release.package import seal_package, validate_package, write_json  # noqa: E402
from tenant_release.store import (  # noqa: E402
    MemoryStagingStore,
    StageGuardError,
    build_release_candidate,
    memory_store,
    refuse_publish,
    reset_memory_store,
    stage_package,
    validate_stage_dsn,
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _fresh_store():
    reset_memory_store()
    yield
    reset_memory_store()


def test_validate_is_offline_and_accepts_tenant_alpha():
    report = validate_package(EXAMPLES / "tenant-alpha")
    assert report.ok
    assert report.tenant_id == "tenant-alpha"
    assert report.counts["approved"] == 2
    assert report.counts["review_required"] == 1
    assert report.counts["blocked"] == 1
    assert "A-001" in report.eligible_skus
    assert "A-002" in report.eligible_skus
    assert "A-003" not in report.eligible_skus
    proc = _run("--validate", "--package", str(EXAMPLES / "tenant-alpha"))
    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["ok"] is True
    assert payload["mode"] == "validate"


def test_same_package_stage_is_reused_without_duplicates():
    first = stage_package(EXAMPLES / "tenant-alpha")
    second = stage_package(EXAMPLES / "tenant-alpha")
    assert first.ok and second.ok
    assert second.reused is True
    assert second.run_id == first.run_id
    assert second.duplicates == 0
    assert second.staged_products == first.staged_products == 4


def test_changed_card_creates_new_version(tmp_path: Path):
    copy = tmp_path / "alpha-v2"
    shutil.copytree(EXAMPLES / "tenant-alpha", copy)
    cards = json.loads((copy / "cards.json").read_text(encoding="utf-8"))
    cards[0]["what_it_is"] = "Updated Alpha card"
    write_json(copy / "cards.json", cards)
    seal_package(copy)
    first = stage_package(EXAMPLES / "tenant-alpha")
    second = stage_package(copy)
    assert second.ok
    assert second.reused is False
    assert second.package_sha256 != first.package_sha256
    first_c = build_release_candidate(EXAMPLES / "tenant-alpha")
    second_c = build_release_candidate(copy)
    assert second_c.ok
    assert first_c.candidate_id in second_c.superseded
    assert second_c.status == "current"
    assert first_c.candidate_id != second_c.candidate_id


def test_mixed_tenant_aborts_without_partial_rows():
    report = validate_package(FIXTURES / "mixed-tenant")
    assert report.ok is False
    assert any(item["code"] == "foreign_tenant" for item in report.errors)
    staged = stage_package(FIXTURES / "mixed-tenant")
    assert staged.ok is False
    assert staged.run_id == ""
    assert staged.staged_products == 0


def test_review_and_blocked_products_stay_out_of_candidate():
    result = build_release_candidate(EXAMPLES / "tenant-alpha")
    skus = {item["sku"] for item in result.products}
    assert skus == {"A-001", "A-002"}
    skipped = {item["sku"]: item["review_status"] for item in result.skipped}
    assert skipped["A-003"] == "review_required"
    assert skipped["A-004"] == "blocked"
    alpha_one = next(item for item in result.products if item["sku"] == "A-001")
    alpha_two = next(item for item in result.products if item["sku"] == "A-002")
    assert alpha_one["retail_price_byn"] == 41
    assert alpha_two["price_missing"] is True
    assert alpha_two["media_state"] == "missing"
    assert "WHIEDA" not in json.dumps(result.products, ensure_ascii=False)
    assert any(item["currency"] == "BYN" for item in alpha_one["retail_prices"])
    assert all(item["currency"] != "USD" for item in alpha_one["retail_prices"])


def test_alias_collision_and_hash_mismatch_require_review():
    collision = validate_package(FIXTURES / "alias-collision")
    assert collision.ok is False
    assert any(item["code"] == "alias_collision" for item in collision.errors)
    mismatch = validate_package(FIXTURES / "hash-mismatch")
    assert mismatch.ok is False
    assert any(item["code"] == "hash_mismatch" for item in mismatch.errors)


def test_shared_alias_is_independent_per_tenant():
    alpha = build_release_candidate(EXAMPLES / "tenant-alpha")
    reset_memory_store()
    beta = build_release_candidate(EXAMPLES / "tenant-beta")
    alpha_name = next(item["canonical_name"] for item in alpha.products if item["sku"] == "A-001")
    beta_name = next(item["canonical_name"] for item in beta.products if item["sku"] == "B-001")
    assert alpha_name == "Alpha Spirulina"
    assert beta_name == "Beta Spirulina"
    assert alpha.tenant_id == "tenant-alpha"
    assert beta.tenant_id == "tenant-beta"


def test_parallel_attempts_keep_one_current_candidate():
    store = MemoryStagingStore()
    barrier = threading.Barrier(2)

    def _attempt():
        barrier.wait()
        return build_release_candidate(EXAMPLES / "tenant-alpha", store=store)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _attempt(), range(2)))
    currents = [item for item in results if item.ok and item.status == "current"]
    ids = {item.candidate_id for item in currents}
    assert len(ids) == 1
    current_rows = [
        row for row in store.candidates.values() if row["status"] == "current"
    ]
    assert len(current_rows) == 1


def test_publish_always_refuses():
    payload = refuse_publish()
    assert payload["ok"] is False
    assert payload["mode"] == "publish"
    proc = _run("--publish")
    body = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert body["ok"] is False
    assert "publish_forbidden" in body["errors"][0]["code"]


def test_stage_dsn_refuses_shared_and_production():
    with pytest.raises(StageGuardError):
        validate_stage_dsn("postgresql://whieda_platform_staging@staging.internal.example:5432/whieda_platform_staging")
    with pytest.raises(StageGuardError):
        validate_stage_dsn("postgresql://postgres@185.252.232.93:5432/whieda_platform")
    with pytest.raises(StageGuardError):
        validate_stage_dsn("postgresql://postgres@127.0.0.1:55432/whieda_platform_staging")
    validate_stage_dsn("postgresql://postgres@127.0.0.1:55432/whieda_platform_staging_verify_abc12def34")


def test_cli_stage_without_verify_dsn_is_refused():
    proc = _run(
        "--stage",
        "--package",
        str(EXAMPLES / "tenant-alpha"),
    )
    payload = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert payload["errors"][0]["code"] == "stage_dsn_refused"
    proc_shared = _run(
        "--stage",
        "--store",
        "postgres",
        "--dsn",
        "postgresql://whieda_platform_staging@staging.internal.example:5432/whieda_platform_staging",
        "--package",
        str(EXAMPLES / "tenant-alpha"),
    )
    shared = json.loads(proc_shared.stdout)
    assert proc_shared.returncode == 2
    assert shared["errors"][0]["code"] == "stage_dsn_refused"


def test_advisor_repository_does_not_read_release_staging():
    repo = (ROOT / "backend/platform-api/app/advisor/sql/repository.py").read_text(encoding="utf-8")
    engine = (ROOT / "backend/platform-api/app/advisor/sql/engine.py").read_text(encoding="utf-8")
    sql = (ROOT / "postgres/sql/platform_tenant_release_package_v1.sql").read_text(encoding="utf-8")
    price_sql = (ROOT / "postgres/sql/platform_tenant_release_price_plane_v1.sql").read_text(encoding="utf-8")
    assert "tenant_release_" not in repo
    assert "tenant_release_" not in engine
    assert "advisor_structured_" not in sql
    assert "advisor_structured_" not in price_sql
    assert "nsp-maxim" not in sql.lower()
    assert "nsp-maxim" not in price_sql.lower()


def _mutate_gamma(dest: Path, product_patch: dict) -> Path:
    shutil.copytree(EXAMPLES / "tenant-gamma", dest)
    products = json.loads((dest / "products.json").read_text(encoding="utf-8"))
    products[0].update(product_patch)
    write_json(dest / "products.json", products)
    seal_package(dest)
    return dest


def test_usd_retail_is_confirmed_without_byn_conversion():
    report = validate_package(EXAMPLES / "tenant-gamma")
    assert report.ok
    assert "G-001" in report.eligible_skus
    assert not any(item["code"] == "price_missing" for item in report.gaps)
    result = build_release_candidate(EXAMPLES / "tenant-gamma")
    gamma = next(item for item in result.products if item["sku"] == "G-001")
    assert gamma["price_missing"] is False
    assert gamma["retail_price_byn"] is None
    usd = next(item for item in gamma["retail_prices"] if item["kind"] == "retail")
    assert usd["amount"] == "37.13"
    assert usd["currency"] == "USD"
    assert usd["source"] == "catalogue_2026"
    assert usd["sha256"]
    dumped = json.dumps(gamma, ensure_ascii=False)
    assert "BYN" not in dumped
    assert "RUB" not in dumped
    staged = stage_package(EXAMPLES / "tenant-gamma")
    stored = memory_store().products[staged.run_id][0]
    assert stored["retail_prices"][0]["amount"] == "37.13"
    assert stored["retail_prices"][0]["currency"] == "USD"
    assert stored["retail_prices"][0]["sha256"] == usd["sha256"]


def test_stale_price_missing_flag_does_not_hide_usd(tmp_path: Path):
    copy = _mutate_gamma(tmp_path / "stale-flag", {"price_missing": True})
    report = validate_package(copy)
    assert report.ok
    assert not any(item["code"] == "price_missing" for item in report.gaps)
    result = build_release_candidate(copy)
    gamma = next(item for item in result.products if item["sku"] == "G-001")
    assert gamma["price_missing"] is False
    assert gamma["retail_prices"][0]["currency"] == "USD"


def test_malformed_prices_are_validation_errors(tmp_path: Path):
    zero = _mutate_gamma(tmp_path / "zero", {"prices": [{"kind": "retail", "amount": "0", "currency": "USD", "source": "catalogue_2026"}]})
    zero_report = validate_package(zero)
    assert zero_report.ok is False
    assert any(item["code"] == "price_amount_invalid" for item in zero_report.errors)

    bad_ccy = _mutate_gamma(tmp_path / "ccy", {"prices": [{"kind": "retail", "amount": "37.13", "currency": "usd", "source": "catalogue_2026"}]})
    ccy_report = validate_package(bad_ccy)
    assert ccy_report.ok is False
    assert any(item["code"] == "price_currency_invalid" for item in ccy_report.errors)

    missing_src = _mutate_gamma(tmp_path / "src", {"prices": [{"kind": "retail", "amount": "37.13", "currency": "USD", "source": ""}]})
    src_report = validate_package(missing_src)
    assert src_report.ok is False
    assert any(item["code"] == "price_source_missing" for item in src_report.errors)


def test_partner_price_alone_is_not_retail(tmp_path: Path):
    copy = tmp_path / "partner-only"
    shutil.copytree(EXAMPLES / "tenant-alpha", copy)
    products = json.loads((copy / "products.json").read_text(encoding="utf-8"))
    products[0].pop("retail_price_byn", None)
    products[0]["prices"] = [{"kind": "partner", "amount": "28.7", "currency": "BYN", "source": "partner-book"}]
    products[0]["price_missing"] = True
    write_json(copy / "products.json", products)
    seal_package(copy)
    report = validate_package(copy)
    assert report.ok
    assert any(item["code"] == "price_missing" and item.get("sku") == "A-001" for item in report.gaps)
    result = build_release_candidate(copy)
    alpha = next(item for item in result.products if item["sku"] == "A-001")
    assert alpha["price_missing"] is True
    assert not any(item["kind"] == "retail" for item in alpha["retail_prices"])


def test_usd_formatter_does_not_invent_byn():
    from app.advisor.sql.formatters import format_price

    product = {
        "retail_prices": [
            {
                "kind": "retail",
                "amount": "37.13",
                "currency": "USD",
                "source": "catalogue_2026",
            }
        ]
    }
    text = format_price(product, "BY", retail_only=True)
    assert "37.13 USD" in text
    assert "BYN" not in text
    assert "~" not in text
