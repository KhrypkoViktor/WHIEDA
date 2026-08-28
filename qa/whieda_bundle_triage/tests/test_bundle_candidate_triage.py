"""Offline lint for WHIEDA bundle-candidate triage. No Core/HTTP/publish."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

TRIAGE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TRIAGE_DIR.parents[1]
RUNNER = TRIAGE_DIR / "run_bundle_candidate_triage.py"
FIXTURE_TSV = TRIAGE_DIR / "fixtures" / "09_BUNDLE_CANDIDATES.tsv"
ALLOWED_DECISIONS = {
    "ready_for_owner_review",
    "duplicate",
    "needs_product_mapping",
    "needs_source",
    "blocked_claim",
    "archive",
}
ACTIVE_BUNDLES = (
    "bundle_energy_immunity",
    "bundle_vessels_belly",
    "bundle_shape_recovery",
)
DIAGNOSIS_MARKERS = ("глауком", "онколог", "рак ", "опухол", "абсцесс", "флюс")
RESULT_PROMISE_MARKERS = ("гарантир", "вылеч", "снимет диагноз")


def _run_offline(*extra: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(RUNNER), "--offline", *extra]
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _read_triage() -> list[dict[str, str]]:
    path = TRIAGE_DIR / "bundle_candidate_triage_v1.tsv"
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _read_unknown() -> list[dict[str, str]]:
    path = TRIAGE_DIR / "unknown_item_resolution_v1.tsv"
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _read_regression() -> list[dict]:
    path = TRIAGE_DIR / "active_bundle_regression_cases_v1.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_missing_input_exits_nonzero(tmp_path: Path) -> None:
    missing = tmp_path / "no-such.tsv"
    proc = _run_offline("--input", str(missing), check=False)
    assert proc.returncode != 0
    assert "not found" in (proc.stderr + proc.stdout).lower() or "missing" in (proc.stderr + proc.stdout).lower()


def test_custom_input_path_is_respected(tmp_path: Path) -> None:
    sample = tmp_path / "one.tsv"
    sample.write_text(
        "\t".join(
            [
                "record_id",
                "название_ситуации",
                "основной_товар",
                "source_id",
                "publication_status",
                "source_locator",
                "доп_товары",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "BUNDLE-TEST-1",
                "Схема Реанимация для онкологии",
                "Эликсир Фохоу",
                "RAWCHAT-TEST",
                "blocked_raw",
                "строка 1",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    proc = _run_offline("--input", str(sample), "--out-dir", str(out_dir), check=False)
    assert proc.returncode == 0, proc.stderr
    with (out_dir / "bundle_candidate_triage_v1.tsv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert any(row["record_id"] == "BUNDLE-TEST-1" for row in rows)
    test_row = next(row for row in rows if row["record_id"] == "BUNDLE-TEST-1")
    assert test_row["decision"] == "blocked_claim"


def test_offline_default_fixture_writes_required_artifacts() -> None:
    proc = _run_offline()
    assert proc.returncode == 0, proc.stderr
    assert (TRIAGE_DIR / "bundle_candidate_triage_v1.tsv").is_file()
    assert (TRIAGE_DIR / "unknown_item_resolution_v1.tsv").is_file()
    assert (TRIAGE_DIR / "active_bundle_regression_cases_v1.jsonl").is_file()
    assert (TRIAGE_DIR / "BUNDLE_CANDIDATE_TRIAGE_LOCAL_REPORT.md").is_file()
    report = (TRIAGE_DIR / "BUNDLE_CANDIDATE_TRIAGE_LOCAL_REPORT.md").read_text(encoding="utf-8")
    assert "source_missing" in report
    assert "не опубликован" in report.lower()


def test_decisions_are_from_allowed_enum() -> None:
    _run_offline()
    rows = _read_triage()
    assert rows, "expected triage rows"
    for row in rows:
        assert row["decision"] in ALLOWED_DECISIONS
        assert row["confidence"] in {"high", "medium", "low"}
        assert row.get("owner_approved", "") == ""
        assert "approved" not in (row.get("decision") or "")


def test_rov_duplicates_share_one_canonical() -> None:
    _run_offline()
    by_id = {row["record_id"]: row for row in _read_triage()}
    members = ["BUNDLE-0004", "BUNDLE-0009", "BUNDLE-0014", "BUNDLE-0016", "BUNDLE-0029"]
    present = [rid for rid in members if rid in by_id]
    assert len(present) >= 3
    duplicates = [by_id[rid] for rid in present if by_id[rid]["decision"] == "duplicate"]
    assert duplicates, "expected at least one ROV duplicate"
    canonicals = {row["canonical_candidate_id"] for row in duplicates}
    assert len(canonicals) == 1
    canonical = next(iter(canonicals))
    assert canonical in by_id
    assert by_id[canonical]["decision"] != "duplicate"
    assert by_id[canonical]["canonical_candidate_id"] == ""


def test_unknown_chip_goes_to_resolution_queue() -> None:
    _run_offline()
    unknown = _read_unknown()
    chips = [row for row in unknown if "чип" in (row.get("item_text") or "").lower()]
    assert chips, "chip-from-pad must stay unmapped"
    for row in chips:
        assert "M015" not in (row.get("item_text") or "")
        assert row["record_id"]
        assert row["owner_question"]


def test_safety_blocks_reanimation_glaucoma_and_child_surgery() -> None:
    _run_offline()
    by_id = {row["record_id"]: row for row in _read_triage()}
    for record_id in ("BUNDLE-0008", "BUNDLE-0010", "BUNDLE-0015", "BUNDLE-0018", "BUNDLE-0030"):
        if record_id in by_id:
            assert by_id[record_id]["decision"] == "blocked_claim", record_id


def test_implant_compatibility_is_not_device_duplicate() -> None:
    _run_offline()
    by_id = {row["record_id"]: row for row in _read_triage()}
    row = by_id["BUNDLE-0031"]
    assert row["decision"] != "duplicate"
    assert row["canonical_candidate_id"] != "BUNDLE-0022"


def test_animals_are_archived_not_published() -> None:
    _run_offline()
    by_id = {row["record_id"]: row for row in _read_triage()}
    for record_id in ("BUNDLE-0001", "BUNDLE-0002"):
        if record_id in by_id:
            assert by_id[record_id]["decision"] == "archive"


def test_regression_has_8_to_12_live_phrases_per_active_bundle() -> None:
    _run_offline()
    cases = _read_regression()
    by_bundle: dict[str, list[dict]] = {bundle_id: [] for bundle_id in ACTIVE_BUNDLES}
    for case in cases:
        assert case["case_id"]
        assert case["user_text"]
        assert case["expected_bundle_id"] in ACTIVE_BUNDLES
        assert case["must_contain"]
        assert case["source"]["ref"]
        text = case["user_text"].lower()
        assert not any(marker in text for marker in DIAGNOSIS_MARKERS)
        assert not any(marker in text for marker in RESULT_PROMISE_MARKERS)
        by_bundle[case["expected_bundle_id"]].append(case)
    for bundle_id, group in by_bundle.items():
        assert 8 <= len(group) <= 12, f"{bundle_id} has {len(group)} phrases"


def test_runner_does_not_publish_or_touch_core() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    lowered = source.lower()
    assert "psql" not in lowered
    assert "telegram" not in lowered
    assert "docker" not in lowered
    assert "google" not in lowered
    _run_offline()
    for rel in ("backend/platform-api/app", "postgres", "n8n"):
        proc = subprocess.run(
            ["git", "diff", "--", rel],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.stdout.strip() == "", f"unexpected diff in {rel}"
