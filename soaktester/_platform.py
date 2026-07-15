"""Platform helpers.

The ``acouchbase`` SDK requires a ``SelectorEventLoop``. On Windows the default
asyncio loop is the ``ProactorEventLoop``, which the SDK cannot use; its
auto-fallback additionally crashes when the loop is already running. Selecting
the selector policy up front avoids both problems. No-op off Windows.
"""
from __future__ import annotations

import asyncio
import sys


def use_selector_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
