"""
Report generation service.

Responsibilities:
  * Build a text prompt from PersonalVault items.
  * Call the MLX engine (or a mock/fallback) to generate the report text.
  * Persist generated reports atomically to ``data/reports.json``.
  * Load and filter the history of stored reports.

Classes:
    MLXAdapter    – thin wrapper that isolates MLX import and provides a mock seam.

Functions:
    build_prompt        – construct a prompt string from vault items + report type.
    generate_report     – end-to-end: filter vault → prompt → MLX → persist.
    load_reports        – read reports.json and return all records.
    get_report_by_id    – look up a single record by id.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Optional

from personal_assistant.personal_vault.models import VaultItem
from personal_assistant.report_schemas import ReportRecord, ReportRequest, ReportType
from personal_assistant.services.vault_filter_service import (
    get_completed_today,
    get_items_for_today,
    get_items_last_7_days,
)

# ---------------------------------------------------------------------------
# Storage path
# ---------------------------------------------------------------------------

_DATA_DIR: Path = Path(__file__).parent.parent.parent.parent / "data"
_REPORTS_FILE: Path = _DATA_DIR / "reports.json"


def _ensure_data_dir() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# MLX Adapter
# ---------------------------------------------------------------------------


class MLXAdapter:
    """Thin wrapper around MLXEngine with a deterministic mock fallback.

    In production the engine module is imported lazily so that tests can
    monkeypatch ``personal_assistant.services.report_service._mlx_adapter``
    before any actual model is loaded.

    :param mock_fn: Optional callable ``(prompt: str) -> str``.  When provided
                    the real MLX engine is never touched.  Used by tests.
    """

    def __init__(self, mock_fn: Optional[object] = None) -> None:
        self._mock_fn = mock_fn

    def generate(self, prompt: str, max_tokens: int = 512) -> str:
        """Generate text for *prompt* and return the result string.

        :param prompt: Full prompt passed to the LM.
        :param max_tokens: Maximum tokens to generate.
        :returns: Generated text string.
        """
        if self._mock_fn is not None:
            return self._mock_fn(prompt)  # type: ignore[operator]

        try:
            from personal_assistant.mlx_server.engine import MLXEngine  # lazy import

            engine: MLXEngine = MLXEngine()
            return engine.ask(question=prompt, max_tokens=max_tokens)
        except Exception as exc:
            return f"[MLX unavailable: {exc}]"


# Module-level singleton – tests may replace this.
_mlx_adapter: MLXAdapter = MLXAdapter()


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_prompt(report_type: ReportType, items: list[VaultItem], target_date: str) -> str:
    """Construct a generation prompt from vault items.

    :param report_type: Which kind of report to generate.
    :param items: Vault items to include as context.
    :param target_date: The date string (YYYY-MM-DD) the report covers.
    :returns: A plain-text prompt string ready for the LM.
    """
    if report_type == ReportType.DAILY_AGENDA:
        intro = f"Составь краткое расписание на {target_date} на основе следующих элементов:"
    elif report_type == ReportType.COMPLETED_REVIEW:
        intro = f"Составь обзор выполненных задач за {target_date} на основе следующих элементов:"
    else:  # WEEKLY_REVIEW
        intro = f"Составь недельный обзор за неделю, заканчивающуюся {target_date}, на основе следующих элементов:"

    lines: list[str] = [intro, ""]
    for i, it in enumerate(items[:30], start=1):  # cap at 30 items
        snippet = (it.subject or it.full_body[:80]).strip()
        lines.append(f"{i}. [{it.item_type}] {it.date_iso[:16]} — {snippet}")

    lines += ["", "Напиши структурированный отчёт на русском языке."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _load_raw() -> list[dict]:
    _ensure_data_dir()
    if not _REPORTS_FILE.exists():
        return []
    try:
        return json.loads(_REPORTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_raw(records: list[dict]) -> None:
    _ensure_data_dir()
    fd, tmp_path = tempfile.mkstemp(dir=str(_DATA_DIR), suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(_REPORTS_FILE))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_reports() -> list[ReportRecord]:
    """Load all stored report records, newest-first.

    :returns: List of :class:`ReportRecord` sorted by ``generated_at`` descending.
    """
    raw = _load_raw()
    records: list[ReportRecord] = []
    for r in raw:
        try:
            records.append(ReportRecord(**r))
        except Exception:
            pass
    records.sort(key=lambda r: r.generated_at, reverse=True)
    return records


def get_report_by_id(report_id: str) -> Optional[ReportRecord]:
    """Look up a single report by *report_id*.

    :param report_id: Short 8-char UUID prefix.
    :returns: :class:`ReportRecord` or ``None`` if not found.
    """
    for rec in load_reports():
        if rec.id == report_id:
            return rec
    return None


def _append_report(record: ReportRecord) -> None:
    raw = _load_raw()
    raw.insert(0, record.model_dump())
    _save_raw(raw)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_report(request: ReportRequest) -> ReportRecord:
    """Generate a report and persist it.

    1. Determine *target_date* (from request or today).
    2. Fetch the relevant vault items via VaultFilterService.
    3. If no items: return a "no data" record without calling MLX.
    4. Build a prompt and call the MLX adapter.
    5. Persist the record atomically and return it.

    :param request: :class:`ReportRequest` describing the report to generate.
    :returns: The newly created :class:`ReportRecord`.
    """
    if request.target_date:
        target = date.fromisoformat(request.target_date)
    else:
        target = date.today()

    target_str = target.isoformat()

    # -- 2. Fetch vault items --------------------------------------------------
    if request.report_type == ReportType.DAILY_AGENDA:
        items = get_items_for_today(target)
    elif request.report_type == ReportType.COMPLETED_REVIEW:
        items = get_completed_today(target)
    else:  # WEEKLY_REVIEW
        items = get_items_last_7_days(target)

    # -- 3. Fallback when no data ---------------------------------------------
    if not items:
        record = ReportRecord(
            type=request.report_type,
            target_date=target_str,
            vault_scope_ids=[],
            content="Нет данных за выбранный период",
        )
        _append_report(record)
        return record

    # -- 4. Generate via MLX --------------------------------------------------
    prompt = build_prompt(request.report_type, items, target_str)
    content = _mlx_adapter.generate(prompt)

    # -- 5. Persist ------------------------------------------------------------
    record = ReportRecord(
        type=request.report_type,
        target_date=target_str,
        vault_scope_ids=[it.id for it in items],
        content=content,
    )
    _append_report(record)
    return record
