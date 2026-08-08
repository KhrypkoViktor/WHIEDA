"""Contract tests for WHIEDA offline regression corpus."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
CASES_PATH = ROOT / "qa" / "cases" / "whieda_regression_cases_v1.jsonl"
RUNNER_PATH = ROOT / "qa" / "run_whieda_regression.py"

GROUP_MINIMUMS = {
    "catalog_card": 35,
    "catalog_price": 35,
    "aliases_typo": 35,
    "followup_context": 30,
    "photo_video_certificate": 25,
    "comparison": 20,
    "cart_and_basket": 20,
    "business_faq": 20,
    "promotion_event": 15,
    "safety_and_clarification": 15,
}

DEMO_PRODUCTS_MIN5 = (
    "Активатор клеток",
    "Активатор клеток PRO",
    "Вэнтун",
    "Magic Foherb",
    "Ба-Гуа",
    "Очки",
    "Стельки",
    "Палантин",
    "Магнитный пояс",
    "Линчжи",
    "Лювэй",
    "Соевый пептид",
    "Эликсир Фохоу",
    "Эликсир Саньцин",
    "Эликсир 3 Драгоценности",
    "Роза Фохоу",
)

FORBIDDEN_ARTIFACTS = ("Nordman", "I need human review", "Traceback")
PLACEHOLDER_RE = re.compile(r"\b(TODO|example\s*1|placeholder|fixme|lorem ipsum)\b", re.I)
SECRET_RE = re.compile(
    r"api\.telegram\.org|duckdns\.org|185\.252\.|supabase|PLATFORM_TELEGRAM_BOT_TOKEN\s*=|postgresql://[^@\s]+:[^@\s]+@",
    re.I,
)


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    rows: list[dict] = []
    with CASES_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_corpus_has_minimum_size(cases: list[dict]) -> None:
    assert len(cases) >= 250


def test_case_ids_unique(cases: list[dict]) -> None:
    ids = [row["case_id"] for row in cases]
    assert len(ids) == len(set(ids))


def test_p0_cases_have_must_fields(cases: list[dict]) -> None:
    for row in cases:
        if row["priority"] != "P0":
            continue
        assert row.get("must_contain"), row["case_id"]
        assert row.get("must_not_contain"), row["case_id"]


def test_no_placeholders_or_forbidden_artifacts(cases: list[dict]) -> None:
    for row in cases:
        blob = json.dumps(row, ensure_ascii=False)
        assert not PLACEHOLDER_RE.search(blob), row["case_id"]
        for artifact in FORBIDDEN_ARTIFACTS:
            if artifact in blob and artifact not in row.get("must_not_contain", []):
                # artifact may appear only inside must_not_contain guard strings
                if artifact not in str(row.get("must_not_contain")):
                    pytest.fail(f"{row['case_id']} contains forbidden artifact {artifact}")


def test_no_secrets_or_prod_urls(cases: list[dict]) -> None:
    for row in cases:
        blob = json.dumps(row, ensure_ascii=False)
        assert not SECRET_RE.search(blob), row["case_id"]


def test_group_minimums(cases: list[dict]) -> None:
    counts = Counter(row["group"] for row in cases)
    for group, minimum in GROUP_MINIMUMS.items():
        assert counts[group] >= minimum, f"{group}: {counts[group]} < {minimum}"


def test_demo_products_coverage(cases: list[dict]) -> None:
    product_hits: Counter[str] = Counter()
    for row in cases:
        product = row.get("expected_product")
        if product in DEMO_PRODUCTS_MIN5:
            product_hits[str(product)] += 1
    for product in DEMO_PRODUCTS_MIN5:
        assert product_hits[product] >= 5, f"{product}: {product_hits[product]}"


def test_followup_has_context_before(cases: list[dict]) -> None:
    for row in cases:
        if row["group"] != "followup_context":
            continue
        assert row.get("context_before"), row["case_id"]


def test_safety_cases_do_not_expect_diagnosis(cases: list[dict]) -> None:
    for row in cases:
        if row["group"] != "safety_and_clarification":
            continue
        assert row["expected_mode"] in {"clarification", "knowledge_gap"}, row["case_id"]
        must_not = " ".join(str(x).lower() for x in row.get("must_not_contain", []))
        inp = str(row.get("input", "")).lower()
        if any(word in inp for word in ("леч", "диагноз", "гарант", "излечен", "схема")):
            assert any(word in must_not for word in ("леч", "диагноз", "гарант", "схема", "назнач", "излечен")), row["case_id"]


def test_runner_validate_passes(cases: list[dict]) -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(RUNNER_PATH), "--validate"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_required_mandatory_cases_present(cases: list[dict]) -> None:
    ids = {row["case_id"] for row in cases}
    for required in (
        "ALI-001",
        "ALI-002",
        "FUP-031",
        "SAF-003",
        "SAF-004",
        "CRT-001",
        "MED-001",
    ):
        assert required in ids
