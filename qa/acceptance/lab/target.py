"""Load and validate acceptance target configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REQUIRED_TOP = ("version", "base_url", "health_path", "openapi_path", "advisor")
REQUIRED_ADVISOR = ("path", "method", "request", "response")
REQUIRED_REQUEST_KEYS = ("tenant", "session", "ref", "text", "sku", "country", "language")
REQUIRED_RESPONSE_KEYS = (
    "answer_text",
    "answer_mode",
    "product_name",
    "product_sku",
    "photo",
    "videos",
    "pdf_documents",
    "clarifications",
    "error_id",
)


class TargetConfigError(Exception):
    pass


def load_target(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise TargetConfigError(f"Target config not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TargetConfigError(f"Invalid JSON in target config: {exc}") from exc
    validate_target(data)
    return data


def validate_target(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise TargetConfigError("Target config must be a JSON object")
    for key in REQUIRED_TOP:
        if key not in data:
            raise TargetConfigError(f"Missing required target field: {key}")
    advisor = data.get("advisor")
    if not isinstance(advisor, dict):
        raise TargetConfigError("advisor must be an object")
    for key in REQUIRED_ADVISOR:
        if key not in advisor:
            raise TargetConfigError(f"Missing advisor.{key}")
    req = advisor.get("request")
    if not isinstance(req, dict):
        raise TargetConfigError("advisor.request must be an object")
    for key in REQUIRED_REQUEST_KEYS:
        if key not in req:
            raise TargetConfigError(f"Missing advisor.request.{key}")
    resp = advisor.get("response")
    if not isinstance(resp, dict):
        raise TargetConfigError("advisor.response must be an object")
    for key in REQUIRED_RESPONSE_KEYS:
        if key not in resp or "path" not in resp[key]:
            raise TargetConfigError(f"Missing advisor.response.{key}.path")
    contract = data.get("openapi_contract")
    if contract is not None:
        if not isinstance(contract, dict):
            raise TargetConfigError("openapi_contract must be an object")
        for key in ("required_paths", "advisor_path", "advisor_method"):
            if key not in contract:
                raise TargetConfigError(f"Missing openapi_contract.{key}")
    base = str(data["base_url"]).rstrip("/")
    if not re.match(r"^https?://", base):
        raise TargetConfigError("base_url must start with http:// or https://")


def build_advisor_request(target: dict[str, Any], case: dict[str, Any]) -> tuple[str, str, dict[str, str], dict[str, Any]]:
    advisor = target["advisor"]
    base = str(target["base_url"]).rstrip("/")
    url = f"{base}{advisor['path']}"
    method = str(advisor.get("method", "POST")).upper()
    headers = {str(k): str(v) for k, v in (advisor.get("headers") or {}).items()}
    body: dict[str, Any] = {}
    req_map = advisor["request"]

    session_spec = req_map["session"]
    body[str(session_spec["field"])] = str(session_spec.get("default") or "acceptance-lab")

    ref_spec = req_map["ref"]
    body[str(ref_spec["field"])] = str(ref_spec.get("default") or "")

    text_spec = req_map["text"]
    body[str(text_spec["field"])] = str(case.get(text_spec.get("from_case", "input")) or "")

    country_spec = req_map["country"]
    body[str(country_spec["field"])] = str(case.get("country") or country_spec.get("default") or "RU")

    lang_spec = req_map["language"]
    body[str(lang_spec["field"])] = str(case.get("language") or lang_spec.get("default") or "ru")

    sku_spec = req_map["sku"]
    sku_val = case.get(sku_spec.get("from_case", "sku"))
    if sku_val:
        body[str(sku_spec["field"])] = sku_val

    ctx_spec = req_map.get("context_before") or {}
    if ctx_spec:
        ctx = case.get(ctx_spec.get("from_case", "context_before")) or []
        if ctx:
            body[str(ctx_spec["field"])] = ctx

    return method, url, headers, body


def extract_response_fields(target: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    from lab.paths import get_by_path

    out: dict[str, Any] = {}
    for name, spec in target["advisor"]["response"].items():
        out[name] = get_by_path(payload, str(spec["path"]))
    return out
