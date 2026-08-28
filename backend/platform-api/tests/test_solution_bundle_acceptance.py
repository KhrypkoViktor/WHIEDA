from __future__ import annotations

import importlib.util


from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "qa" / "solution_bundles" / "run_solution_bundle_acceptance.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("solution_bundle_acceptance", RUNNER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_active_bundle_regression_corpus_is_complete():
    runner = _load_runner()
    cases = runner.load_cases(runner.DEFAULT_CORPUS)
    assert runner.validate_cases(cases) == []


def test_live_runner_refuses_non_local_core():
    runner = _load_runner()
    for value in ("https://wwc.best", "https://example.com", "http://185.252.232.93:8080"):
        try:
            runner.require_local_url(value)
        except ValueError:
            pass
        else:
            raise AssertionError(value)
