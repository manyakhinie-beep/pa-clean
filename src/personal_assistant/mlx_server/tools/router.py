"""
Tool Router — safe execution with timeout, sandbox checks, graceful fallback.

Integrates validator + logging + actual tool modules.
Thread-safe: uses concurrent.futures instead of signals.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from loguru import logger

from personal_assistant.mlx_server.tools.validator import (
    ToolValidationError,
    log_tool_execution,
    validate_tool_call,
)

_TOOL_TIMEOUT_SEC = 10.0
# Dedicated executor for tool calls so that future.result(timeout=...) works
# in any thread (main or FastAPI thread-pool worker).
_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tool_exec")


class ToolExecutionError(Exception):
    """Raised when a tool fails during execution (timeout, exception, sandbox)."""


def _sandbox_check(name: str, args: dict[str, Any]) -> Optional[str]:
    """Prevent dangerous paths / commands. Returns error str or None."""
    for key, val in args.items():
        if isinstance(val, str) and (val.startswith("/") or ".." in val):
            if key in ("path", "file", "filename", "expression"):
                if ".." in val:
                    return f"Sandbox: path argument '{key}' contains forbidden '..': {val}"
    return None


def _run_builtin(name: str, args: dict[str, Any]) -> str:
    """Dispatch to built-in tool modules."""
    if name == "date_calc":
        from personal_assistant.mlx_server.tools.date_calc import run as date_run

        expr = args.get("expression", "")
        result = date_run(expr)
        if "error" in result:
            raise ToolExecutionError(result["error"])
        return f"Результат date_calc: {result['iso']} ({result['human']})"

    # Graceful guidance instead of an exception — the model will receive this
    # as the tool result and should pivot to answering from PERSONALVAULT context.
    return (
        f"[Инструмент '{name}' не существует] "
        "Данные о встречах, письмах и задачах уже содержатся в системном промпте в блоке PERSONALVAULT. "
        "Используй эту информацию напрямую — не вызывай инструменты для получения данных из vault."
    )


def _run_with_timeout(name: str, args: dict[str, Any], timeout: float) -> str:
    """Run a builtin inside a thread-pool so that .result(timeout=...) works everywhere."""
    future = _TOOL_EXECUTOR.submit(_run_builtin, name, args)
    try:
        return future.result(timeout=timeout)
    except Exception:
        # Cancel if still pending; the underlying thread may keep running,
        # but we will not wait for it.
        future.cancel()
        raise


def execute_tool(raw_call: dict[str, Any]) -> dict[str, Any]:
    """
    Validate and execute a tool call.

    Returns {"ok": True, "result": str} on success.
    Returns {"ok": False, "error": str, "status": "failed"} on any failure.
    """
    t0 = time.perf_counter()
    try:
        validated = validate_tool_call(raw_call)
    except ToolValidationError as exc:
        duration = (time.perf_counter() - t0) * 1000
        log_tool_execution(
            name=raw_call.get("name", "<unknown>"),
            args=raw_call.get("arguments", {}),
            status="validation_failed",
            duration_ms=duration,
            result=str(exc),
        )
        return {
            "ok": False,
            "error": str(exc),
            "status": "failed",
            "expected_schema": exc.expected_schema,
        }

    name = validated["name"]
    args = validated["arguments"]

    sandbox_err = _sandbox_check(name, args)
    if sandbox_err:
        duration = (time.perf_counter() - t0) * 1000
        log_tool_execution(name, args, "sandbox_blocked", duration, sandbox_err)
        return {"ok": False, "error": sandbox_err, "status": "failed"}

    try:
        result_text = _run_with_timeout(name, args, _TOOL_TIMEOUT_SEC)
    except TimeoutError:
        duration = (time.perf_counter() - t0) * 1000
        log_tool_execution(name, args, "timeout", duration)
        return {
            "ok": False,
            "error": f"Tool '{name}' exceeded {_TOOL_TIMEOUT_SEC}s timeout.",
            "status": "failed",
        }
    except Exception as exc:
        duration = (time.perf_counter() - t0) * 1000
        log_tool_execution(name, args, "exception", duration, str(exc))
        logger.exception(f"[tools] execution error for {name}")
        return {"ok": False, "error": str(exc), "status": "failed"}

    duration = (time.perf_counter() - t0) * 1000
    log_tool_execution(name, args, "ok", duration, result_text)
    return {"ok": True, "result": result_text, "status": "ok"}
