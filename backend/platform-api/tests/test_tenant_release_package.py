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
    assert "tenant_release_" not in repo
    assert "tenant_release_" not in engine
    assert "advisor_structured_" not in sql
    assert "nsp-maxim" not in sql.lower()
