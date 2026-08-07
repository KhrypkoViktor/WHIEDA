from __future__ import annotations

import pytest

from app.errors import advisor_error_response


def test_advisor_error_envelope_has_contract_fields():
    class DummyRequest:
        state = type("S", (), {"trace_id": "trace-123"})()

    payload = advisor_error_response(DummyRequest())
    assert payload["ok"] is False
    assert payload["answer_mode"] == "error"
    assert payload["route"] == "error"
    assert payload["error_id"] == "trace-123"
    assert payload["media"]["videos"] == []
