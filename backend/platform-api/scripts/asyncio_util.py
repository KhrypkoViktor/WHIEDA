"""Windows-safe asyncio runner for psycopg async pool."""

from __future__ import annotations

import asyncio
import selectors
import sys
from typing import TypeVar

T = TypeVar("T")


def run_async(coro) -> T:
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return asyncio.run(coro)
