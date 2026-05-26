"""
Tool call validation — Pydantic v2 schemas loaded from tools/registry.json.

- validate_tool_call(raw_dict) → parsed_call or raises ToolValidationError
- log_tool_execution(name, args, status, duration_ms, result)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger
from pydantic import BaseModel, Field, ValidationError, create_model

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
_REGISTRY_FILE = _PROJECT_ROOT / "tools" / "registry.json"
_LOG_FILE = _PROJECT_ROOT / "logs" / "tools.log"

# Configure dedicated tool logger
_tool_logger = logger.bind(subsystem="tools")


def _ensure_log_dir() -> None:
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


class ToolValidationError(Exception):
    """Raised when a tool call does not match the registered schema."""

    def __init__(self, message: str, *, expected_schema: Optional[dict] = None) -> None:
        super().__init__(message)
        self.expected_schema = expected_schema


# ---------------------------------------------------------------------------
# Registry loading
# ---------------------------------------------------------------------------


def _load_registry() -> dict[str, dict]:
    if not _REGISTRY_FILE.exists():
        return {}
    data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
    return {t["id"]: t for t in data.get("tools", []) if t.get("enabled", True)}


_REGISTRY: dict[str, dict] = _load_registry()


def _get_registry() -> dict[str, dict]:
    """Return registry, reloading from disk if the in-memory cache is empty.

    This handles the case where ``registry.json`` did not exist at module
    import time but was created later (e.g. first-run setup).
    """
    global _REGISTRY
    if not _REGISTRY:
        _REGISTRY = _load_registry()
    return _REGISTRY


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class _ToolCallRaw(BaseModel):
    name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


def _build_argument_model(tool_id: str) -> Optional[type[BaseModel]]:
    meta = _get_registry().get(tool_id)
    if not meta:
        return None
    # registry.json currently does not store JSON-Schema properties.
    # We fall back to a permissive dict model, but we can extend
    # registry.json later with "parameters" field.
    params = meta.get("parameters", {})
    props = params.get("properties", {})
    required = set(params.get("required", []))
    if not props:
        return None

    fields: dict[str, Any] = {}
    for key, schema in props.items():
        py_type = _json_schema_to_python(schema)
        default = ... if key in required else None
        fields[key] = (Optional[py_type], Field(default=default))

    return create_model(f"_Args_{tool_id}", __base__=BaseModel, **fields)  # type: ignore[call-overload]


def _json_schema_to_python(schema: dict) -> type:
    t = schema.get("type", "string")
    if t == "string":
        return str
    if t == "integer":
        return int
    if t == "number":
        return float
    if t == "boolean":
        return bool
    if t == "array":
        return list
    if t == "object":
        return dict
    return str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_tool_call(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Validate a raw dict {"name": "...", "arguments": {...}} against the registry.

    Returns the validated dict on success.
    Raises ToolValidationError on failure with expected_schema for retry prompting.
    """
    try:
        parsed = _ToolCallRaw.model_validate(raw)
    except ValidationError as exc:
        raise ToolValidationError(
            f"Invalid tool call structure: {exc.errors(include_url=False)}",
            expected_schema={
                "name": "string (tool id)",
                "arguments": "object",
            },
        ) from exc

    tool_id = parsed.name
    registry = _get_registry()
    if tool_id not in registry:
        raise ToolValidationError(
            f"Tool '{tool_id}' is not registered or disabled.",
            expected_schema={"available_tools": list(registry.keys())},
        )

    arg_model = _build_argument_model(tool_id)
    if arg_model is not None:
        try:
            arg_model.model_validate(parsed.arguments)
        except ValidationError as exc:
            raise ToolValidationError(
                f"Invalid arguments for tool '{tool_id}': {exc.errors(include_url=False)}",
                expected_schema=registry[tool_id].get("parameters", {}),
            ) from exc

    return {"name": tool_id, "arguments": parsed.arguments}


def log_tool_execution(
    name: str,
    args: dict[str, Any],
    status: str,
    duration_ms: float,
    result: Optional[str] = None,
) -> None:
    """Append a structured line to logs/tools.log."""
    _ensure_log_dir()
    from personal_assistant.utils.timezone import format_to_msk_prompt_str

    ts = format_to_msk_prompt_str()
    record = {
        "timestamp": ts,
        "tool": name,
        "status": status,
        "args": args,
        "duration_ms": round(duration_ms, 2),
        "result_preview": (result or "")[:200],
    }
    # Write plain text line for easy tail/grep
    line = (
        f"[{ts}] {name} | status: {status} | args: {json.dumps(args, ensure_ascii=False)} "
        f"| duration: {duration_ms:.1f}ms"
    )
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    _tool_logger.debug(record)
