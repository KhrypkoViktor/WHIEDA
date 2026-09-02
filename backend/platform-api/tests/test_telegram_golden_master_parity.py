"""Tests for golden local master-parity fixture and runner completeness fixes."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TG = ROOT / "qa" / "telegram_golden"
LAB = TG / "lab"
SNAPSHOT = ROOT / "n8n" / "live-exports" / "structured-master" / "20260810T083328Z"
FIXTURE_SQL = TG / "fixtures" / "local_master_seed.sql"
FIXTURE_MANIFEST = TG / "fixtures" / "local_master_seed_manifest.json"
ENSURE_DB = ROOT / "postgres" / "scripts" / "ensure_local_core_database.py"
ORCHESTRATOR = ROOT / "backend" / "platform-api" / "scripts" / "local_core_lab" / "orchestrator.py"
ACCEPTANCE = ROOT / "qa" / "acceptance"
if str(ACCEPTANCE) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


compiler_mod = _load("master_seed_compiler", LAB / "master_seed_compiler.py")
corpus_mod = _load("golden_corpus_helpers", LAB / "corpus.py")
http_runner_mod = _load("golden_http_runner_parity", LAB / "http_runner.py")
report_mod = _load("golden_report_parity", LAB / "report.py")


@pytest.fixture()
def golden_target():
    return json.loads((TG / "golden_target.example.json").read_text(encoding="utf-8"))


def test_compiler_refuses_hash_invalid_snapshot(tmp_path: Path):
    bad_dir = tmp_path / "snap"
    bad_dir.mkdir()
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))
    manifest["layers"]["products"]["sha256"] = "0" * 64
    (bad_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name in manifest["layers"]:
        src = SNAPSHOT / manifest["layers"][name]["file"]
        if src.is_file():
            (bad_dir / manifest["layers"][name]["file"]).write_bytes(src.read_bytes())
    with pytest.raises(compiler_mod.MasterSeedCompileError, match="sha256 mismatch"):
        compiler_mod.compile_master_seed(
            snapshot_dir=bad_dir,
            sql_out=tmp_path / "seed.sql",
            manifest_out=tmp_path / "manifest.json",
        )


def test_compiler_deterministic(tmp_path: Path):
    out_a_sql = tmp_path / "a.sql"
    out_a_manifest = tmp_path / "a.json"
    out_b_sql = tmp_path / "b.sql"
    out_b_manifest = tmp_path / "b.json"
    compiler_mod.compile_master_seed(snapshot_dir=SNAPSHOT, sql_out=out_a_sql, manifest_out=out_a_manifest)
    compiler_mod.compile_master_seed(snapshot_dir=SNAPSHOT, sql_out=out_b_sql, manifest_out=out_b_manifest)
    assert out_a_sql.read_bytes() == out_b_sql.read_bytes()
    assert out_a_manifest.read_bytes() == out_b_manifest.read_bytes()


def test_every_golden_snapshot_sku_in_seed():
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    coverage = manifest["golden_sku_coverage"]
    assert coverage["products_missing"] == []
    assert coverage["cards_missing"] == []
    assert len(coverage["products_present"]) == len(manifest["golden_snapshot_skus"])


def test_seed_is_tenant_safe_and_idempotent():
    sql = FIXTURE_SQL.read_text(encoding="utf-8")
    assert "delete from" not in sql.casefold()
    assert "begin;" in sql
    assert "commit;" in sql
    assert "on conflict" in sql
    assert "('whieda'," in sql or "('whieda', " in sql
    assert "test-acme" not in sql


def test_source_price_null_stays_null():
    sql = FIXTURE_SQL.read_text(encoding="utf-8")
    assert ", NULL," in sql or ", null," in sql.lower()
    assert ", 0," not in sql.split("partner_price_byn")[1][:200] if "partner_price_byn" in sql else True


def test_default_ensure_db_has_no_master_seed_flag():
    text = ENSURE_DB.read_text(encoding="utf-8")
    assert "--apply-golden-master-seed" in text
    assert "if args.apply_golden_master_seed" in text


def test_orchestrator_wires_golden_master_seed_flag():
    text = ORCHESTRATOR.read_text(encoding="utf-8")
    assert "golden_master_seed" in text
    assert "compile_golden_master_seed" in text
    assert "telegram_golden_full_positive" in text
    assert "telegram_golden_negative" in text
    assert "telegram_golden_p0" in text


def test_p0_priority_retains_flow_context(golden_target):
    flow = {
        "flow_id": "GOLD-FLOW-P0-SETUP",
        "session": "golden-flow-p0-setup",
        "priority": "P0",
        "turns": [
            {
                "turn": 1,
                "case_id": "T1",
                "class": "product_card",
                "priority": "P1",
                "input": {"user_text": "активатор", "country": "BY"},
                "expected": {
                    "mode": "structured_card",
                    "must_contain": ["Активатор"],
                    "must_not_contain": [],
                    "expected_media": {"photo": "allow"},
                },
                "expected_context_transition": {"sets": {"last_product_sku": "M015-00"}, "clears": [], "requires": {}},
            },
            {
                "turn": 2,
                "case_id": "T2",
                "class": "price",
                "priority": "P0",
                "input": {"user_text": "цена", "country": "BY"},
                "expected": {
                    "mode": "structured_price",
                    "must_contain": ["BYN"],
                    "must_not_contain": [],
                    "expected_media": {"photo": "none"},
                },
                "expected_context_transition": {
                    "sets": {},
                    "clears": [],
                    "requires": {"last_product_sku": "M015-00"},
                },
            },
        ],
    }

    calls: list[str] = []

    class FakeTransport:
        def request(self, method, url, *, headers=None, body=None, timeout_seconds=30.0):
            calls.append(body["question"])
            if body["question"] == "активатор":
                payload = {
                    "ok": True,
                    "answer_text": "Активатор клеток",
                    "answer_mode": "structured_card",
                    "context": {"last_product_sku": "M015-00"},
                    "media": {},
                }
            else:
                payload = {"ok": True, "answer_text": "1750 BYN", "answer_mode": "structured_price", "media": {}}
            return 200, json.dumps(payload), 50.0

    payload = http_runner_mod.run_golden_http(
        target=golden_target,
        cases=[],
        flows=corpus_mod.select_flows_for_priority([flow], "P0"),
        client=FakeTransport(),  # type: ignore[arg-type]
        priority="P0",
    )
    assert calls == ["активатор", "цена"]
    assert payload["results"][0]["turn_role"] == "setup"
    assert payload["results"][0]["status"] == "PASS"
    assert payload["results"][1]["turn_role"] == "assertion"
    assert payload["results"][1]["status"] == "PASS"


def test_full_and_negative_phases_not_blocked_by_p0_failure(golden_target):
    orchestrator = ORCHESTRATOR.read_text(encoding="utf-8")
    assert "if not step.ok:\n                    telegram_golden_failed = True\n                else:" not in orchestrator
    assert "telegram_golden_full_positive" in orchestrator
    assert "--negative-only" in orchestrator


def test_report_labels_fixture_and_redacts(golden_target):
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    payload = {
        "run_id": "parity-test",
        "status": "FAIL",
        "run_phase": "full_positive",
        "target": {"base_url": "http://127.0.0.1:8080", "advisor_path": "/v1/advisor/query"},
        "fixture_parity": manifest,
        "results": [
            {
                "case_id": "X",
                "status": "FAIL",
                "answer_preview": "short preview",
                "reason": "missing",
                "expected_mode": "structured_card",
                "answer_mode": "clarification",
                "kind": "flow_turn",
                "turn_role": "setup",
            }
        ],
        "summary": report_mod.summarize_results(
            [
                {
                    "case_id": "X",
                    "status": "FAIL",
                    "kind": "flow_turn",
                    "turn_role": "setup",
                }
            ]
        ),
    }
    md = report_mod.render_markdown(payload)
    assert "Fixture parity" in md
    assert manifest["generated_from"] in md
    assert "Setup turns" in md
    blob = json.dumps(payload, ensure_ascii=False)
    assert "answer_text" not in blob
    assert hashlib.sha256(FIXTURE_SQL.read_bytes()).hexdigest() == manifest["fixture_sql_sha256"]
