#!/usr/bin/env python3
"""Controlled shared-staging tenant canary. Default: refuse and do nothing."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from shared_staging_canary.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
