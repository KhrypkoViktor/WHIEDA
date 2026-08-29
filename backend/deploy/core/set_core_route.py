#!/usr/bin/env python3
"""Update route switch env vars for Core API deployment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

ROUTES = (
    "CORE_ROUTE_PUBLIC_REF",
    "CORE_ROUTE_LEADS",
    "CORE_ROUTE_ADVISOR",
    "CORE_ROUTE_TELEGRAM",
    "CORE_ROUTE_DEEP",
)

MODES = {
    "CORE_ROUTE_PUBLIC_REF": ("legacy", "shadow", "core"),
    "CORE_ROUTE_LEADS": ("legacy", "shadow", "core"),
    "CORE_ROUTE_ADVISOR": ("legacy", "shadow", "core"),
    "CORE_ROUTE_TELEGRAM": ("legacy", "shadow", "core"),
    "CORE_ROUTE_DEEP": ("off", "shadow", "core"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    for route in ROUTES:
        parser.add_argument(f"--{route.lower()}", choices=MODES[route])
    args = parser.parse_args()

    env_path = Path(args.env_file)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    values = {key: os.environ.get(key) for key in ROUTES}
    for route in ROUTES:
        cli_value = getattr(args, route.lower())
        if cli_value:
            values[route] = cli_value

    updated: dict[str, str] = {}
    for line in lines:
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, val = line.split("=", 1)
        updated[key] = val

    for route in ROUTES:
        if values.get(route):
            updated[route] = values[route]

    env_path.write_text(
        "\n".join(f"{k}={v}" for k, v in updated.items()) + "\n",
        encoding="utf-8",
    )
    print(f"Updated {env_path}")


if __name__ == "__main__":
    main()
