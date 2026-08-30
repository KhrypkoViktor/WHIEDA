"""Shared parse helpers for the WHIEDA Solution_Bundles regression corpus."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
SHEET_GID = "2001007"
SHEET_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=tsv&gid={SHEET_GID}"
)

HERE = Path(__file__).resolve().parent
TSV_PATH = HERE / "solution_bundles.tsv"
CORPUS_PATH = HERE / "whieda_bundle_regression_cases_v1.jsonl"
REPORT_PATH = (
    HERE.parents[1]
    / "backend"
    / "platform-api"
    / "docs"
    / "WHIEDA_BUNDLE_REGRESSION_CORPUS_LOCAL_REPORT.md"
)

NEGATIVE_PHRASES = [
    "покажи фото товара",
    "какая цена",
    "сколько стоит этот товар",
    "пришли фото",
    "есть ли в наличии",
    "какой артикул",
    "скинь прайс",
    "покажи сертификат",
    "как выглядит упаковка",
    "повтори фото крупным планом",
]


def normalize_text(value: str) -> str:
    text = str(value or "").replace("ё", "е").replace("Ё", "е").casefold()
    return " ".join(text.split())


def is_true(value: object) -> bool:
    return str(value or "").strip().upper() in {"TRUE", "1", "YES", "ДА"}


def is_superseded(row: dict[str, str]) -> bool:
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("version_state", "status", "notes", "bundle_name")
    ).casefold()
    return "superseded" in blob


def split_aliases(raw: str) -> list[str]:
    seen: set[str] = set()
    aliases: list[str] = []
    for part in str(raw or "").replace("\n", ";").split(";"):
        alias = part.strip()
        key = normalize_text(alias)
        if not alias or key in seen:
            continue
        seen.add(key)
        aliases.append(alias)
    return aliases


def ordered_skus(sku_groups: str) -> list[str]:
    skus: list[str] = []
    for group in str(sku_groups or "").split(";"):
        for sku in group.split("|"):
            item = sku.strip()
            if item:
                skus.append(item)
    return skus


def read_tsv(path: Path = TSV_PATH) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def active_bundles(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    live: list[dict[str, str]] = []
    for row in rows:
        if not is_true(row.get("active")):
            continue
        if is_superseded(row):
            continue
        live.append(row)
    return live


def pick_phrases(aliases: list[str], bundle_name: str) -> list[str]:
    if not aliases:
        return []
    by_len = sorted(range(len(aliases)), key=lambda i: (len(aliases[i]), i))
    short = aliases[by_len[0]]
    name_key = normalize_text(bundle_name)
    full = next((alias for alias in aliases if normalize_text(alias) == name_key), None)
    if full is None:
        full = aliases[max(range(len(aliases)), key=lambda i: (len(aliases[i]), -i))]
    chosen = [short]
    if full != short:
        chosen.append(full)
    for alias in aliases:
        if alias not in chosen:
            chosen.append(alias)
        if len(chosen) == 3:
            break
    return chosen


def source_for(bundle_id: str) -> dict[str, str]:
    return {"sheet_id": SHEET_ID, "gid": SHEET_GID, "bundle_id": bundle_id}


def build_positive_cases(row: dict[str, str]) -> list[dict[str, Any]]:
    bundle_id = str(row.get("bundle_id") or "").strip()
    name = str(row.get("bundle_name") or "").strip()
    phrases = pick_phrases(split_aliases(row.get("aliases") or ""), name)
    skus = ordered_skus(row.get("sku_groups") or "")
    cases: list[dict[str, Any]] = []
    for index, phrase in enumerate(phrases, start=1):
        cases.append(
            {
                "case_id": f"BUNDLE-{bundle_id}-{index:02d}",
                "bundle_id": bundle_id,
                "user_text": phrase,
                "expected_mode": "structured_solution_bundle",
                "expected_bundle_name": name,
                "expected_skus": skus,
                "source": source_for(bundle_id),
                "priority": "P0",
            }
        )
    return cases


def build_negative_cases(alias_keys: set[str]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for index, phrase in enumerate(NEGATIVE_PHRASES, start=1):
        if normalize_text(phrase) in alias_keys:
            raise ValueError(f"negative phrase repeats an alias: {phrase}")
        cases.append(
            {
                "case_id": f"BUNDLE-NEG-{index:02d}",
                "bundle_id": None,
                "user_text": phrase,
                "must_not_match_bundle": True,
                "source": {"sheet_id": SHEET_ID, "gid": SHEET_GID, "bundle_id": None},
                "priority": "P0",
            }
        )
    return cases


def build_corpus(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[str]]:
    live = active_bundles(rows)
    alias_keys: set[str] = set()
    broken: list[str] = []
    cases: list[dict[str, Any]] = []
    for row in live:
        aliases = split_aliases(row.get("aliases") or "")
        bundle_id = str(row.get("bundle_id") or "").strip() or "<missing-bundle_id>"
        alias_keys.update(normalize_text(alias) for alias in aliases)
        if not bundle_id or bundle_id == "<missing-bundle_id>":
            broken.append("row without bundle_id")
            continue
        if len(aliases) < 3:
            broken.append(f"{bundle_id}: fewer than 3 aliases ({len(aliases)})")
        cases.extend(build_positive_cases(row))
    cases.extend(build_negative_cases(alias_keys))
    return cases, broken


def write_jsonl(cases: list[dict[str, Any]], path: Path = CORPUS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(case, ensure_ascii=False, sort_keys=True) for case in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_jsonl(path: Path = CORPUS_PATH) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSONL at line {line_no}: {error}") from error
        if not isinstance(item, dict):
            raise ValueError(f"JSON object required at line {line_no}")
        cases.append(item)
    return cases
