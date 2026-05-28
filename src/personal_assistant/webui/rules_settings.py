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
    """Validate a partial update, persist it to ``config.json``, apply it now.

    Side effect: when ``mlx_model_path`` changes, the shared MLXEngine is
    asked to drop its cached model so the next chat / draft / delegate
    request loads the new one.  Previously the engine cached the path at
    construction and ignored later updates — the reported bug «UI и .env
    не подхватывают изменение пути к модели».
    """
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=400, detail="Empty or invalid settings payload")
    old_mlx_path = settings.mlx_model_path
    try:
        updated = settings.update(body)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown setting: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    new_mlx_path = settings.mlx_model_path
    model_reloaded = False
    if "mlx_model_path" in body and old_mlx_path != new_mlx_path:
        try:
            from personal_assistant.mlx_server import server as _srv
            engine = getattr(_srv.state, "engine", None)
            if engine is not None and hasattr(engine, "reload"):
                engine.reload()
                model_reloaded = True
        except Exception as exc:  # noqa: BLE001 — never fail settings on engine
            logger.warning(f"[rules] MLX engine reload failed: {exc}")

    logger.info(
        f"[rules] AI-tool settings updated: {sorted(body)} "
        f"(mlx_reload={model_reloaded})"
    )
    return {"ok": True, "settings": updated, "mlx_reloaded": model_reloaded}
