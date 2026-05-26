"""
FastAPI routes for the editable AI-tool settings shown in the "Правила" tab.

These settings are persisted to ``data/config.json`` (see
:mod:`personal_assistant.config`) and applied to the running process
immediately — no server restart required, unlike the legacy ``.env``-based
``/settings`` endpoint.

Endpoints:
  GET   /api/v1/rules/settings  — current values + schema (labels, tooltips, ranges)
  PATCH /api/v1/rules/settings  — validate, persist to config.json, apply immediately
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger

from personal_assistant.config import EDITABLE_FIELDS, settings

router = APIRouter(prefix="/api/v1/rules", tags=["rules-settings"])


@router.get("/settings")
def get_rules_settings() -> dict[str, Any]:
    """Return current editable settings plus the schema that drives the UI."""
    return {
        "settings": settings.editable_dict(),
        "schema": EDITABLE_FIELDS,
        "config_path": str(settings.config_path),
    }


@router.patch("/settings")
def patch_rules_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Validate a partial update, persist it to ``config.json``, apply it now."""
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=400, detail="Empty or invalid settings payload")
    try:
        updated = settings.update(body)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown setting: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(f"[rules] AI-tool settings updated: {sorted(body)}")
    return {"ok": True, "settings": updated}
