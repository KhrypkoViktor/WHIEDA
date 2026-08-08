"""Data quality engine orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dqc.baseline import build_baseline, load_baseline, save_baseline
from dqc.checks import run_quality_checks
from dqc.diff import diff_baselines
from dqc.loader import load_table
from dqc.modes import baseline_allowed, evaluate_gate
from dqc.report import build_report_payload, write_reports
from dqc.schema import Contract, validate_schema


class DataQualityEngine:
    def __init__(self, root: Path, manifest_path: Path, mode: str = "dev") -> None:
        self.root = root
        self.manifest_path = manifest_path
        self.mode = mode
        self.dqc_root = root / "qa" / "data_quality"
        self.contracts_dir = self.dqc_root / "contracts"
        self.baseline_path = self.dqc_root / "baselines" / "latest.json"
        self.report_md = self.dqc_root / "reports" / "DATA_QUALITY_REPORT.md"
        self.report_json = self.dqc_root / "reports" / "DATA_QUALITY_REPORT.json"
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.contracts = self._load_contracts()

    def _load_contracts(self) -> dict[str, Contract]:
        out: dict[str, Contract] = {}
        for src in self.manifest.get("sources", []):
            contract_file = self.contracts_dir / src["contract"]
            if contract_file.is_file():
                contract = Contract.from_file(contract_file)
                out[contract.layer] = contract
        return out

    def _resolve_path(self, src: dict[str, Any]) -> Path:
        file_path = src.get("path") or str(Path(self.manifest.get("exports_root", "qa/data_quality/exports")) / src["file"])
        path = Path(file_path)
        if not path.is_absolute():
            path = self.root / path
        return path

    def load_sources(self) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], list[dict[str, Any]]]:
        layers: dict[str, list[dict[str, Any]]] = {}
        file_paths: dict[str, str] = {}
        missing: list[dict[str, Any]] = []
        for src in self.manifest.get("sources", []):
            layer = src["layer"]
            path = self._resolve_path(src)
            rel = str(path.relative_to(self.root)) if path.is_relative_to(self.root) else str(path)
            if not path.is_file():
                missing.append({"layer": layer, "expected_path": rel, "required": bool(src.get("required"))})
                continue
            rows = load_table(path, src.get("format", "tsv"))
            layers[layer] = rows
            file_paths[layer] = rel
        return layers, file_paths, missing

    def validate(self) -> dict[str, Any]:
        layers, file_paths, missing = self.load_sources()
        issues: list[dict[str, Any]] = []
        layer_stats = {layer: len(rows) for layer, rows in layers.items()}

        for src in self.manifest.get("sources", []):
            layer = src["layer"]
            if layer not in layers:
                continue
            contract = self.contracts.get(layer)
            if not contract:
                continue
            issues.extend(
                validate_schema(
                    layer=layer,
                    file_path=file_paths[layer],
                    rows=layers[layer],
                    contract=contract,
                )
            )

        issues.extend(run_quality_checks(layers=layers, contracts=self.contracts, file_paths=file_paths))

        validation_payload = build_report_payload(
            issues=issues,
            missing_sources=[],
            layer_stats=layer_stats,
            diff={"status": "pending"},
            manifest_path=str(self.manifest_path.relative_to(self.root)) if self.manifest_path.is_relative_to(self.root) else str(self.manifest_path),
        )
        gate = evaluate_gate(
            mode=self.mode,
            manifest=self.manifest,
            missing=missing,
            found_layers=list(layers.keys()),
            validation_status=validation_payload["validation_status"],
        )

        baseline = build_baseline(layers=layers, contracts=self.contracts)
        prev = load_baseline(self.baseline_path)
        diff = diff_baselines(baseline, prev)

        payload = build_report_payload(
            issues=issues,
            missing_sources=missing,
            layer_stats=layer_stats,
            diff=diff,
            manifest_path=str(self.manifest_path.relative_to(self.root)) if self.manifest_path.is_relative_to(self.root) else str(self.manifest_path),
            gate=gate,
        )
        allowed, baseline_reason = baseline_allowed(gate=gate, layer_stats=layer_stats)
        return {
            "issues": issues,
            "missing": missing,
            "layer_stats": layer_stats,
            "baseline": baseline,
            "diff": diff,
            "payload": payload,
            "gate": gate,
            "baseline_allowed": allowed,
            "baseline_reason": baseline_reason,
        }

    def save_baseline_snapshot(self, baseline: dict[str, Any], *, layer_stats: dict[str, int] | None = None, gate: dict[str, Any] | None = None) -> None:
        stats = layer_stats or {}
        gate_info = gate or {}
        allowed, reason = baseline_allowed(gate=gate_info, layer_stats=stats)
        if not allowed:
            raise RuntimeError(reason)
        save_baseline(self.baseline_path, baseline)

    def write_report(self, payload: dict[str, Any]) -> None:
        write_reports(payload, self.report_md, self.report_json)
