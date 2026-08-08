"""Target health and OpenAPI contract checks."""

from __future__ import annotations

import json
from typing import Any, Callable

RequestFn = Callable[[str, str, dict[str, str] | None, bytes | None], tuple[int, str]]


def check_target_contract(target: dict[str, Any], request_fn: RequestFn) -> dict[str, Any]:
    base = str(target["base_url"]).rstrip("/")
    errors: list[str] = []

    health_path = str(target["health_path"])
    status, body = request_fn("GET", f"{base}{health_path}", {"Accept": "application/json"}, None)
    if status == 0:
        errors.append(f"health {health_path} unreachable: {body}")
    elif status != 200:
        errors.append(f"health {health_path} returned HTTP {status}")

    openapi_path = str(target["openapi_path"])
    ostatus, obody = request_fn("GET", f"{base}{openapi_path}", {"Accept": "application/json"}, None)
    if ostatus == 0:
        errors.append(f"openapi {openapi_path} unreachable: {obody}")
        return {"status": "FAIL", "errors": errors, "openapi": None}
    if ostatus != 200:
        errors.append(f"openapi {openapi_path} returned HTTP {ostatus}")
        return {"status": "FAIL", "errors": errors, "openapi": None}

    try:
        openapi = json.loads(obody)
    except json.JSONDecodeError:
        errors.append("openapi response is not valid JSON")
        return {"status": "FAIL", "errors": errors, "openapi": None}

    if "paths" not in openapi:
        errors.append("openapi missing paths")

    contract = target.get("openapi_contract") or {}
    required_paths = list(contract.get("required_paths") or [])
    paths = openapi.get("paths") or {}
    for path in required_paths:
        if path not in paths:
            errors.append(f"openapi missing required path {path}")

    advisor_path = str(contract.get("advisor_path") or target["advisor"]["path"])
    advisor_method = str(contract.get("advisor_method") or target["advisor"]["method"]).lower()
    advisor_entry = paths.get(advisor_path)
    if not advisor_entry:
        errors.append(f"advisor path missing in openapi: {advisor_path}")
    elif advisor_method not in advisor_entry:
        errors.append(f"advisor method {advisor_method.upper()} missing for {advisor_path}")

    ok = not errors
    return {
        "status": "PASS" if ok else "FAIL",
        "errors": errors,
        "health_status": status,
        "openapi_status": ostatus,
        "openapi_paths": sorted(paths.keys()),
    }
