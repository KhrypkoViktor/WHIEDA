"""Offline lint tests for Telegram golden corpus."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TG = ROOT / "qa" / "telegram_golden"
LAB = TG / "lab"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


corpus = _load_module("golden_corpus", LAB / "corpus.py")
offline = _load_module("golden_offline", LAB / "offline_runner.py")
importer = _load_module("golden_importer", LAB / "importer.py")


@pytest.fixture(scope="module")
def golden_cases(tmp_path_factory):
    """Rebuild into a temp folder: the corpus in the repo is curated (its
    expectations are the bot's contract) and a test run must not replace it."""
    build = TG / "build_golden_corpus.py"
    if build.is_file():
        import subprocess

        out = tmp_path_factory.mktemp("golden_build")
        subprocess.run(
            [sys.executable, str(build), "--out-dir", str(out)], cwd=str(ROOT), check=True
        )
    return corpus.load_jsonl(TG / "whieda_telegram_golden_cases_v1.jsonl")


@pytest.fixture(scope="module")
def golden_flows():
    path = TG / "whieda_telegram_golden_flows_v1.jsonl"
    return corpus.load_jsonl(path)


@pytest.fixture(scope="module")
def negative_fixtures():
    path = TG / "whieda_telegram_golden_negative_fixtures_v1.jsonl"
    if not path.is_file():
        build = TG / "build_negative_fixtures.py"
        if build.is_file():
            import subprocess

            subprocess.run([sys.executable, str(build)], cwd=str(ROOT), check=True)
    return corpus.load_jsonl(path)


def test_corpus_meets_p0_thresholds(golden_cases):
    errors = corpus.validate_cases(golden_cases)
    assert not errors, "\n".join(errors)


def test_flows_meet_p0_thresholds(golden_flows):
    errors = corpus.validate_flows(golden_flows)
    assert not errors, "\n".join(errors)


def test_all_classes_represented(golden_cases):
    classes = {row["class"] for row in golden_cases}
    assert classes == set(corpus.GOLDEN_CLASSES)


def test_offline_runner_passes(golden_cases, golden_flows, negative_fixtures):
    result = offline.run_offline(
        cases=golden_cases,
        flows=golden_flows,
        corpus_mod=corpus,
        importer_mod=importer,
        negative_fixtures=negative_fixtures,
    )
    assert result["status"] == "PASS", json.dumps(result, ensure_ascii=False, indent=2)


def test_negative_fixtures_internal_only(negative_fixtures):
    assert len(negative_fixtures) == 5
    errors = corpus.validate_negative_fixtures(negative_fixtures, root=ROOT)
    assert not errors, "\n".join(errors)
    assert all(row["review_status"] == "blocked_raw_internal_only" for row in negative_fixtures)


def test_service_intent_fuzz_provenance(golden_cases):
    fuzz = [row for row in golden_cases if row.get("source", {}).get("kind") == "service_intent_fuzz"]
    assert fuzz, "expected service_intent_fuzz cases from INTENT_CASES import"
    for row in fuzz:
        prov = row["source"].get("provenance", "")
        assert "test_telegram_service_intent_fuzz.py::INTENT_CASES" in prov


def test_smoke_p0_004_price_roundtrip():
    root = ROOT
    smoke_path = root / "n8n/current/source_batches/smoke_cases_sheet_v1/smoke_cases_raw.tsv"
    cases, _ = importer.import_smoke_cases(smoke_path)
    match = [c for c in cases if c.get("source", {}).get("ref") == "P0-004"]
    assert match, "P0-004 missing from smoke import"
    row = match[0]
    assert row["class"] == "price"
    assert row["expected"]["mode"] == "structured_price"


def test_snapshot_cards_manifest_and_count():
    errors = offline.validate_snapshot_cards(TG, min_cards=12)
    assert not errors, "\n".join(errors)


def test_snapshot_cards_render_markers():
    from app.advisor.telegram_card import render_telegram_product_card

    manifest = json.loads((TG / "fixtures/snapshot_cards/manifest.json").read_text(encoding="utf-8"))
    for entry in manifest.get("cards") or []:
        payload = json.loads((TG / "fixtures/snapshot_cards" / entry["file"]).read_text(encoding="utf-8"))
        rendered = render_telegram_product_card(payload["card"], payload["product"])
        for marker in entry.get("golden_markers") or []:
            assert marker in rendered, f"{entry['slug']}: missing rendered marker {marker!r}"


def test_snapshot_service_replies_all_intents():
    errors = offline.validate_service_replies(TG)
    assert not errors, "\n".join(errors)
