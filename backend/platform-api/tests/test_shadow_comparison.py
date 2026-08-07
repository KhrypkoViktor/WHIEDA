from __future__ import annotations

from app.advisor.shadow import compare_advisor_responses, log_shadow_comparison


def test_compare_matching_responses():
    core = {
        "ok": True,
        "answer_mode": "structured_business",
        "route": "structured",
        "product": {"sku": "M015-00"},
    }
    legacy = {
        "ok": True,
        "answer_mode": "structured_business",
        "route": "structured",
        "product": {"sku": "M015-00"},
    }
    result = compare_advisor_responses(core, legacy)
    assert result["ok_match"] is True
    assert result["answer_mode_match"] is True
    assert result["sku_match"] is True


def test_compare_mode_mismatch():
    core = {"ok": True, "answer_mode": "fallback", "route": "structured", "product": None}
    legacy = {"ok": True, "answer_mode": "direct_structured_service", "route": "answer", "product": None}
    result = compare_advisor_responses(core, legacy)
    assert result["answer_mode_match"] is False
    assert result["route_match"] is False


def test_log_shadow_comparison_no_raise(monkeypatch):
    events: list[dict] = []

    def capture(event: str, **fields):
        events.append({"event": event, **fields})

    monkeypatch.setattr("app.advisor.shadow.log_event", capture)
    log_shadow_comparison(
        trace_id="trace-1",
        tenant_id="whieda",
        comparison=compare_advisor_responses(
            {"ok": True, "answer_mode": "fallback", "route": "structured"},
            {"ok": True, "answer_mode": "structured_price", "route": "answer"},
        ),
        question_len=12,
    )
    assert events[0]["event"] == "advisor_shadow_comparison"
    assert "answer_mode" in events[0]["mismatch_fields"]
