"""
FastAPI routes for the Reports feature.

Endpoints:
  POST   /api/v1/reports/generate  — generate a new report (async-friendly)
  GET    /api/v1/reports            — list persisted reports (newest first)
  GET    /api/v1/reports/{id}       — fetch a single report by short-id
  DELETE /api/v1/reports/{id}       — delete a report

The generator calls the MLX engine (if loaded) or produces a structured
plain-text fallback so the endpoint always succeeds.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from loguru import logger

from personal_assistant.report_schemas import ReportRecord, ReportRequest
from personal_assistant.reports.store import delete_report, get_report, list_reports

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------

@router.post("/reports/generate", response_model=ReportRecord)
def generate(body: ReportRequest):
    """
    Generate a report of the requested type.

    Uses the MLX engine if loaded; otherwise returns a structured vault excerpt
    so the response is always useful.
    """
    # Lazy-import to avoid circular deps and to tolerate missing MLX
    engine = None
    index = None
    try:
        from personal_assistant.mlx_server.server import state  # type: ignore[attr-defined]
        engine = getattr(state, "engine", None)
        index = getattr(state, "index", None)
    except Exception:
        pass  # Server state not available (e.g. unit tests) — fallback is fine

    try:
        from personal_assistant.reports.generator import generate_report
        record = generate_report(request=body, engine=engine, index=index)
    except Exception as exc:
        logger.exception(f"[reports] generation error: {exc}")
        raise HTTPException(status_code=500, detail=f"Report generation failed: {exc}") from exc

    return record


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@router.get("/reports", response_model=list[ReportRecord])
def get_reports(limit: int = 50):
    """Return the most recent *limit* reports, newest first."""
    return list_reports(limit=max(1, min(limit, 200)))


# ---------------------------------------------------------------------------
# Get by id
# ---------------------------------------------------------------------------

@router.get("/reports/{report_id}", response_model=ReportRecord)
def get_report_by_id(report_id: str):
    """Fetch a single report by its short id."""
    record = get_report(report_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")
    return record


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@router.delete("/reports/{report_id}")
def remove_report(report_id: str):
    """Delete a report by its short id."""
    if not delete_report(report_id):
        raise HTTPException(status_code=404, detail=f"Report '{report_id}' not found")
    return {"ok": True, "deleted": report_id}
