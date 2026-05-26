"""
Async-safe tool executor wrapper.

Provides ``async_execute_tool`` for async call sites (lifespan, background tasks,
async endpoints) and ``execute_tool_sync`` for sync call sites that need an
explicit timeout in a thread-safe way.

Both wrap the same underlying ``router.execute_tool`` but guarantee that no
``signal`` calls are made in the current thread.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from personal_assistant.mlx_server.tools.router import execute_tool as _raw_execute_tool

_DEFAULT_TIMEOUT = 10.0


async def async_execute_tool(
    raw_call: dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """
    Execute a tool call asynchronously with ``asyncio.wait_for``.

    The actual execution runs in ``asyncio.to_thread`` so that the event loop
    is never blocked by heavy or slow builtin tools.

    On *any* failure (timeout, validation error, exception) a structured dict
    is returned so the caller can inject a fallback message into the prompt
    without breaking the generation pipeline.

    Returns::

        {"ok": True,  "result": str, "status": "ok"}
        {"ok": False, "error": str, "status": "failed"}
    """
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_raw_execute_tool, raw_call),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        duration = (time.perf_counter() - t0) * 1000
        logger.warning(
            f"[tool] async timeout after {duration:.0f}ms for {raw_call.get('name', '?')}"
        )
        return {
            "ok": False,
            "error": f"Tool exceeded {timeout}s timeout.",
            "status": "failed",
        }
    except Exception as exc:
        duration = (time.perf_counter() - t0) * 1000
        logger.exception(f"[tool] async execution error after {duration:.0f}ms")
        return {
            "ok": False,
            "error": str(exc),
            "status": "failed",
        }
    return result


def execute_tool_sync(
    raw_call: dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """
    Synchronous, thread-safe wrapper around the router.

    Uses ``concurrent.futures`` internally (see ``router.py``) so this works
    safely inside FastAPI thread-pool workers or background threads.
    """
    t0 = time.perf_counter()
    try:
        result = _raw_execute_tool(raw_call)
    except Exception as exc:
        duration = (time.perf_counter() - t0) * 1000
        logger.exception(f"[tool] sync execution error after {duration:.0f}ms")
        return {
            "ok": False,
            "error": str(exc),
            "status": "failed",
        }
    return result
