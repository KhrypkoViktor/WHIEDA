from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT.parent / "qa" / "whieda_bundles"
sys.path.insert(0, str(QA))

from corpus_lib import (  # noqa: E402
    CORPUS_PATH,
    TSV_PATH,
    active_bundles,
    build_corpus,
    load_jsonl,
    read_tsv,
    write_jsonl,
)
from run_bundle_regression import validate  # noqa: E402


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_bundle_regression", QA / "build_bundle_regression.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tsv_export_is_present():
    assert TSV_PATH.is_file()
    rows = read_tsv(TSV_PATH)
    assert active_bundles(rows)


def test_builder_is_stable(tmp_path):
    rows = read_tsv(TSV_PATH)
    first, broken = build_corpus(rows)
    second, _ = build_corpus(rows)
    assert not broken
    assert [case["case_id"] for case in first] == [case["case_id"] for case in second]
    assert [case["user_text"] for case in first] == [case["user_text"] for case in second]
    out = tmp_path / "cases.jsonl"
    write_jsonl(first, out)
    assert load_jsonl(out) == first


def test_offline_runner_accepts_checked_in_corpus():
    result = validate(TSV_PATH, CORPUS_PATH)
    assert result["ok"], result["errors"]
    assert result["bundles"] >= 1
    assert result["positive"] >= result["bundles"] * 3
    assert result["negative"] == 10
    assert result["P0"] == result["positive"] + result["negative"]


def test_builder_module_exports_main():
    assert hasattr(_load_builder(), "main")
