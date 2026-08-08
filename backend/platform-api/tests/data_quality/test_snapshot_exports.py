"""Tests for read-only export snapshots."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
DQC = ROOT / "qa" / "data_quality"
SNAPSHOT_CLI = DQC / "snapshot_exports.py"


@pytest.fixture()
def snapshot_env(tmp_path: Path):
    exports = tmp_path / "exports"
    exports.mkdir()
    products = exports / "products_prices.tsv"
    products.write_text("sku\tname\tactive\nSKU1\tOne\ttrue\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "exports_root": str(exports),
        "sources": [
            {
                "layer": "products_prices",
                "file": "products_prices.tsv",
                "format": "tsv",
                "path": str(products),
            },
            {
                "layer": "product_cards",
                "file": "product_cards.tsv",
                "format": "tsv",
                "path": str(exports / "product_cards.tsv"),
            },
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return {"root": tmp_path, "exports": exports, "products": products, "manifest_path": manifest_path, "manifest": manifest}


def _snap_module():
    sys.path.insert(0, str(DQC))
    from dqc import snapshot as snap

    return snap


def test_valid_snapshot(snapshot_env: dict):
    snap = _snap_module()
    result = snap.build_snapshot(
        root=snapshot_env["root"],
        manifest_path=snapshot_env["manifest_path"],
        manifest=snapshot_env["manifest"],
    )
    assert result["layers"]["products_prices"]["status"] == "found"
    assert result["layers"]["products_prices"]["row_count"] == 1
    assert result["layers"]["products_prices"]["sha256"]
    assert result["layers"]["product_cards"]["status"] == "missing"


def test_verify_unchanged(snapshot_env: dict):
    snap = _snap_module()
    built = snap.build_snapshot(
        root=snapshot_env["root"],
        manifest_path=snapshot_env["manifest_path"],
        manifest=snapshot_env["manifest"],
    )
    verify = snap.verify_snapshot(root=snapshot_env["root"], manifest=snapshot_env["manifest"], snapshot=built)
    assert verify["status"] == "PASS"


def test_verify_changed_file(snapshot_env: dict):
    snap = _snap_module()
    built = snap.build_snapshot(
        root=snapshot_env["root"],
        manifest_path=snapshot_env["manifest_path"],
        manifest=snapshot_env["manifest"],
    )
    snapshot_env["products"].write_text("sku\tname\tactive\nSKU1\tChanged\ttrue\nSKU2\tTwo\ttrue\n", encoding="utf-8")
    verify = snap.verify_snapshot(root=snapshot_env["root"], manifest=snapshot_env["manifest"], snapshot=built)
    assert verify["status"] == "FAIL"
    assert verify["changed"]
    assert verify["changed"][0]["layer"] == "products_prices"


def test_verify_removed_file(snapshot_env: dict):
    snap = _snap_module()
    built = snap.build_snapshot(
        root=snapshot_env["root"],
        manifest_path=snapshot_env["manifest_path"],
        manifest=snapshot_env["manifest"],
    )
    snapshot_env["products"].unlink()
    verify = snap.verify_snapshot(root=snapshot_env["root"], manifest=snapshot_env["manifest"], snapshot=built)
    assert verify["status"] == "FAIL"
    assert verify["removed"]
    assert verify["removed"][0]["layer"] == "products_prices"


def test_verify_added_file(snapshot_env: dict):
    snap = _snap_module()
    built = snap.build_snapshot(
        root=snapshot_env["root"],
        manifest_path=snapshot_env["manifest_path"],
        manifest=snapshot_env["manifest"],
    )
    cards = snapshot_env["exports"] / "product_cards.tsv"
    cards.write_text("sku\ttitle\tactive\nSKU1\tCard\ttrue\n", encoding="utf-8")
    verify = snap.verify_snapshot(root=snapshot_env["root"], manifest=snapshot_env["manifest"], snapshot=built)
    assert verify["status"] == "FAIL"
    assert verify["added"]
    assert verify["added"][0]["layer"] == "product_cards"


def test_missing_source_in_snapshot(snapshot_env: dict):
    snap = _snap_module()
    result = snap.build_snapshot(
        root=snapshot_env["root"],
        manifest_path=snapshot_env["manifest_path"],
        manifest=snapshot_env["manifest"],
    )
    missing = result["layers"]["product_cards"]
    assert missing["status"] == "missing"
    assert "sha256" not in missing
    assert "row_count" not in missing


def test_snapshot_cli_creates_file(snapshot_env: dict):
    out = snapshot_env["root"] / "snap.json"
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            str(SNAPSHOT_CLI),
            "--manifest",
            str(snapshot_env["manifest_path"]),
            "--output",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["layers"]["products_prices"]["status"] == "found"
