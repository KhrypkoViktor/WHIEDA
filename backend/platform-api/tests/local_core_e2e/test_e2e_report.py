"""Unit tests for E2E report builder."""

from __future__ import annotations

import json
from pathlib import Path

from local_core_lab.e2e_report import E2EReport, render_markdown, utc_now_iso, write_report


def test_utc_now_iso_format():
    value = utc_now_iso()
    assert "T" in value


def test_render_markdown_includes_status():
    report = E2EReport(status="FAIL", started_at=utc_now_iso(), failure_stage="health_ready")
    md = render_markdown(report)
    assert "`FAIL`" in md
    assert "health_ready" in md


def test_write_report_creates_json_and_md(tmp_path):
    report = E2EReport(
        status="PASS",
        started_at="2026-08-08T12:00:00+00:00",
        docker_version="27.0",
        sql_files=["a.sql"],
    )
    json_path, md_path = write_report(report, tmp_path)
    assert json_path.is_file()
    assert md_path.is_file()
    assert (tmp_path / "latest_run.json").is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["status"] == "PASS"


def test_report_includes_acceptance_sections():
    report = E2EReport(
        status="PASS",
        started_at=utc_now_iso(),
        check_target={"status": "PASS"},
        p0_acceptance={"status": "FAIL", "summary": "Total 5 | pass 3 fail 2"},
    )
    md = render_markdown(report)
    assert "check-target" in md
    assert "P0 run" in md


def test_report_to_dict_roundtrip():
    report = E2EReport(status="NOT_RUN", started_at=utc_now_iso(), containers=["pg"])
    data = report.to_dict()
    assert data["status"] == "NOT_RUN"
    assert data["containers"] == ["pg"]
