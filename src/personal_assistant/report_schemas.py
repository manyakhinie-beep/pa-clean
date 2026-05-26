"""
Pydantic v2 schemas for the Reports feature.

Classes:
    ReportType       – enumeration of the three supported report kinds.
    ReportRequest    – request body for POST /api/v1/reports/generate.
    ReportRecord     – persisted report record (stored in data/reports.json).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class ReportType(str, Enum):
    """Supported report generation types."""

    DAILY_AGENDA = "daily_agenda"
    COMPLETED_REVIEW = "completed_review"
    WEEKLY_REVIEW = "weekly_review"


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class ReportRequest(BaseModel):
    """Body for POST /api/v1/reports/generate.

    :param report_type: Which report to generate.
    :param target_date: Optional ISO 8601 date string (YYYY-MM-DD).
                        Defaults to today when omitted.
    """

    model_config = ConfigDict(extra="forbid")

    report_type: ReportType
    target_date: Optional[str] = None  # YYYY-MM-DD

    @field_validator("target_date", mode="before")
    @classmethod
    def validate_target_date(cls, v: object) -> Optional[str]:
        """Ensure *target_date* is a valid ISO date string when provided."""
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("target_date must be a string in YYYY-MM-DD format")
        try:
            datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"target_date '{v}' is not a valid YYYY-MM-DD date")
        return v


# ---------------------------------------------------------------------------
# Record schema
# ---------------------------------------------------------------------------


class ReportRecord(BaseModel):
    """A persisted generated report.

    :param id: Short UUID (first 8 hex chars).
    :param type: Which report type was generated.
    :param generated_at: UTC ISO-8601 timestamp of generation.
    :param target_date: The date the report covers (YYYY-MM-DD).
    :param vault_scope_ids: List of vault item IDs used as context.
    :param content: The generated text content.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: ReportType
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    target_date: str = ""
    vault_scope_ids: list[str] = Field(default_factory=list)
    content: str
