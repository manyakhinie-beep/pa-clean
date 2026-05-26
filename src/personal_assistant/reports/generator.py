"""
generator.py — Vault-aware report generation.

Collects relevant vault documents as context, then calls the MLX engine
(if available) or returns a structured plain-text fallback so the endpoint
always produces useful output even without a loaded model.

Supported report types
----------------------
daily_agenda       — Today's meetings + unread / flagged emails
completed_review   — Items completed / resolved on the target date
weekly_review      — Summary of the week: meetings, correspondence, highlights
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from loguru import logger

from personal_assistant.config import settings
from personal_assistant.report_schemas import ReportRecord, ReportRequest, ReportType

if TYPE_CHECKING:
    from personal_assistant.mlx_server.engine import MLXEngine
    from personal_assistant.mlx_server.vault_index import VaultIndex


# ---------------------------------------------------------------------------
# Prompt templates per report type
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "Ты — персональный ИИ-ассистент. "
    "Составляй краткие деловые отчёты на основе предоставленных данных. "
    "Пиши по-русски, если данные на русском. "
    "Используй Markdown: заголовки ## и ###, маркированные списки."
)

_PROMPTS: dict[ReportType, str] = {
    ReportType.DAILY_AGENDA: (
        "Составь повестку дня на {date}.\n\n"
        "Включи:\n"
        "- Встречи и события\n"
        "- Важные письма (срочные, с флагом, требующие ответа)\n"
        "- Задачи к выполнению\n\n"
        "Данные vault:\n{context}"
    ),
    ReportType.COMPLETED_REVIEW: (
        "Составь отчёт о завершённых задачах и переписке за {date}.\n\n"
        "Включи:\n"
        "- Проведённые встречи\n"
        "- Закрытые переписки и решённые вопросы\n"
        "- Ключевые решения дня\n\n"
        "Данные vault:\n{context}"
    ),
    ReportType.WEEKLY_REVIEW: (
        "Составь еженедельный обзор за неделю, содержащую {date}.\n\n"
        "Включи:\n"
        "- Итоги встреч и ключевые решения\n"
        "- Важная переписка\n"
        "- Незавершённые задачи\n"
        "- Приоритеты на следующую неделю\n\n"
        "Данные vault:\n{context}"
    ),
}


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_context(
    index: "VaultIndex",
    target_date: str,
    report_type: ReportType,
    max_chars: int = 6000,
) -> tuple[str, list[str]]:
    """
    Extract relevant vault docs for the report.

    Returns (context_text, list_of_vault_ids).
    """
    if not index.docs:
        return "(vault is empty)", []

    # For daily/completed — filter by target_date prefix; for weekly — broader
    date_prefix = target_date[:7] if report_type == ReportType.WEEKLY_REVIEW else target_date

    relevant = []
    for doc in index.docs:
        doc_date = (
            doc.frontmatter.get("date")
            or doc.frontmatter.get("start")
            or doc.frontmatter.get("created")
            or ""
        )
        if isinstance(doc_date, str) and doc_date.startswith(date_prefix):
            relevant.append(doc)

    # Fallback: include most recent docs when no date match
    if not relevant:
        relevant = sorted(
            index.docs,
            key=lambda d: d.frontmatter.get("date") or d.frontmatter.get("start") or "",
            reverse=True,
        )[:20]
        logger.debug(f"[reports] No docs matched {date_prefix!r}, using {len(relevant)} recent")

    # Build context string (truncated to max_chars)
    parts: list[str] = []
    ids: list[str] = []
    total = 0
    for doc in relevant:
        title = doc.frontmatter.get("title") or doc.frontmatter.get("subject") or doc.path.stem
        kind = doc.frontmatter.get("type", doc.section)
        snippet = doc.content[:800].strip()
        entry = f"### [{kind}] {title}\n{snippet}\n"
        if total + len(entry) > max_chars:
            break
        parts.append(entry)
        ids.append(str(doc.frontmatter.get("id", doc.path.stem)))
        total += len(entry)

    context = "\n".join(parts) if parts else "(нет данных за указанный период)"
    return context, ids


# ---------------------------------------------------------------------------
# Fallback renderer (no MLX)
# ---------------------------------------------------------------------------

def _render_fallback(
    report_type: ReportType,
    target_date: str,
    context: str,
    doc_ids: list[str],
) -> str:
    """Return a minimal structured report without LLM."""
    type_labels = {
        ReportType.DAILY_AGENDA: "Повестка дня",
        ReportType.COMPLETED_REVIEW: "Итоги дня",
        ReportType.WEEKLY_REVIEW: "Еженедельный обзор",
    }
    label = type_labels.get(report_type, str(report_type))
    lines = [
        f"## {label} — {target_date}",
        "",
        "> ⚠️ MLX-модель не загружена. Ниже — необработанные данные vault.",
        "",
        "### Данные из vault",
        "",
        context,
    ]
    if doc_ids:
        lines += ["", f"*Источников: {len(doc_ids)}*"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_report(
    request: ReportRequest,
    engine: "MLXEngine | None" = None,
    index: "VaultIndex | None" = None,
) -> ReportRecord:
    """
    Generate a report, persist it and return the ReportRecord.

    Falls back to structured plain-text when *engine* is None or not loaded.
    If *index* is None, creates a temporary VaultIndex from the configured vault.
    """
    target_date = request.target_date or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    # Load vault index on demand
    if index is None:
        try:
            from personal_assistant.mlx_server.vault_index import VaultIndex
            index = VaultIndex(vault_path=settings.vault_path).load(use_cache=True)
        except Exception as exc:
            logger.warning(f"[reports] Failed to load vault index: {exc}")
            index = None

    context_text = "(vault unavailable)"
    doc_ids: list[str] = []
    if index is not None:
        context_text, doc_ids = _build_context(index, target_date, request.report_type)

    # Try LLM generation
    content: str
    if engine is not None:
        try:
            prompt_tpl = _PROMPTS[request.report_type]
            user_prompt = prompt_tpl.format(date=target_date, context=context_text)
            content = engine.chat(
                messages=[{"role": "user", "content": user_prompt}],
                system=_SYSTEM_PROMPT,
                max_tokens=settings.mlx_max_tokens,
            )
            logger.info(f"[reports] Generated via MLX ({len(content)} chars)")
        except Exception as exc:
            logger.warning(f"[reports] MLX generation failed, using fallback: {exc}")
            content = _render_fallback(request.report_type, target_date, context_text, doc_ids)
    else:
        content = _render_fallback(request.report_type, target_date, context_text, doc_ids)

    record = ReportRecord(
        type=request.report_type,
        target_date=target_date,
        vault_scope_ids=doc_ids,
        content=content,
    )

    from personal_assistant.reports.store import save_report
    save_report(record)

    return record
