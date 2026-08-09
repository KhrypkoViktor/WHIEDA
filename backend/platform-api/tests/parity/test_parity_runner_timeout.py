"""Parity runner timeout and not_run reporting."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PARITY = ROOT / "qa" / "parity"
ACCEPTANCE = ROOT / "qa" / "acceptance"


def _load_runner():
    spec = importlib.util.spec_from_file_location("parity_runner_timeout", PARITY / "lab" / "runner.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class SlowTransport:
    def request(self, method, url, *, headers=None, body=None, timeout_seconds=5.0):
        time.sleep(0.05)
        payload = json.dumps(
            {
                "ok": True,
                "answer_text": "ok",
                "answer_mode": "structured_card",
                "product": {"canonical_name": "Test"},
                "media": {"photo_url": None, "videos": [], "documents": []},
                "context": {},
            }
        )
        return 200, payload, 50.0


class InstantTransport:
    def __init__(self, responses: list[tuple[int, str]]):
        self.responses = responses
        self.calls = 0

    def request(self, method, url, *, headers=None, body=None, timeout_seconds=5.0):
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        status, text = self.responses[idx]
        return status, text, 10.0


def test_parity_run_timeout_marks_not_run(tmp_path):
    mod = _load_runner()
    corpus = tmp_path / "cases.jsonl"
    corpus.write_text(
        "\n".join(
            json.dumps(
                {
                    "case_id": f"PARITY-T-{i}",
                    "priority": "P2",
                    "input": f"test {i}",
                    "expected_mode": "structured_card",
                    "must_contain": ["ok"],
                    "expected_media": {"photo": "none", "video_count_min": 0, "document_count_min": 0},
                    "max_latency_ms": 3000,
                    "capability_id": "product_card",
                }
            )
            for i in range(5)
        ),
        encoding="utf-8",
    )
    payload = mod.run_parity_cases(
        target_path=ACCEPTANCE / "acceptance_target.example.json",
        corpus_path=corpus,
        transport=SlowTransport(),
        run_timeout_seconds=0.08,
        timeout_seconds=5.0,
    )
    assert payload["timed_out"] is True
    assert payload["summary"]["not_run"] > 0
    assert payload["not_run_case_ids"]
    assert payload["live_status"] == "FAIL"


def test_orchestrator_continues_parity_on_p0_fail(tmp_path, monkeypatch):
    from local_core_lab.orchestrator import OrchestratorConfig, run_lab
    from local_core_lab.subprocess_util import StepResult

    monkeypatch.setattr("local_core_lab.orchestrator.shutil.which", lambda name: "/docker")
    monkeypatch.setattr("local_core_lab.orchestrator.E2E_REPORTS_DIR", tmp_path)
    monkeypatch.setattr("local_core_lab.orchestrator.capture_versions", lambda r: None)
    monkeypatch.setattr("local_core_lab.orchestrator.docker_logs", lambda c, tail=200: "")
    monkeypatch.setattr("local_core_lab.orchestrator.stop_core_only", lambda: StepResult(name="stop", command=[], returncode=0))
    monkeypatch.setattr("local_core_lab.orchestrator.ensure_acceptance_target", lambda: None)
    monkeypatch.setattr(
        "local_core_lab.orchestrator.run_verify",
        lambda *a, **k: {"status": "PASS", "checks": []},
    )

    calls: list[str] = []

    def fake_capture(cmd, **kwargs):
        name = kwargs.get("name", "step")
        calls.append(name)
        if name == "acceptance_p0_run":
            return StepResult(
                name=name,
                command=cmd,
                returncode=1,
                stdout="Total 8 | pass 6 fail 2 skip 0 unasserted 0\n",
            )
        if name == "core_local_parity_run":
            return StepResult(
                name=name,
                command=cmd,
                returncode=0,
                stdout="Total 85 | pass 85 fail 0 unasserted 0 | P0 fail 0\nnot_run: 0\ntimeout: False\n",
            )
        return StepResult(name=name, command=cmd, returncode=0)

    code, state = run_lab(
        OrchestratorConfig(e2e_mode=True, parity_mode=True),
        run_capture_fn=fake_capture,
        wait_health_fn=lambda t: {"status": "PASS"},
    )
    assert "core_local_parity_run" in calls
    assert state.report.parity_run.get("status") == "PASS"
    assert state.report.preflight_smoke.get("status") == "FAIL"
    assert code == 1
