from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any


@lru_cache(maxsize=1)
def load_repeat_price_catalog() -> dict[str, Any]:
    resource = files("app.subscriptions").joinpath("repeat_purchase_prices.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("sections"), list):
        raise RuntimeError("invalid repeat price catalog")
    return payload
