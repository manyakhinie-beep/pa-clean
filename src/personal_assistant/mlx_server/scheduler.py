"""
Scheduler — runs vault processing tasks on a cron schedule.

Uses APScheduler. The schedule is configured via PA_SCHEDULE_CRON (cron expression)
and PA_SCHEDULE_ENABLED in .env.

Default scheduled pipeline:
  1. Classify all mail and calendar events
  2. Summarize recent mail threads (last 7 days)
  3. Log a daily digest to vault/daily/<date>.md
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler

from personal_assistant.config import settings

# ---------------------------------------------------------------------------
# Scheduled pipeline
# ---------------------------------------------------------------------------


def run_pipeline(vault_path: Optional[Path] = None) -> dict:
    """
    Full scheduled pipeline: classify → summarize recent → write daily digest.
    Returns a summary dict of what was done.
    """
    from personal_assistant.mlx_server.engine import get_engine
    from personal_assistant.mlx_server.tasks.classify import classify_vault
    from personal_assistant.mlx_server.tasks.summarize import summarize_docs
    from personal_assistant.mlx_server.vault_index import VaultIndex

    vault = vault_path or settings.vault_path
    report: dict = {"started_at": datetime.now(tz=timezone.utc).isoformat()}

    logger.info("=== Scheduled pipeline started ===")

    # Load vault index
    index = VaultIndex(vault).load()
    engine = get_engine()

    # 1. Classify mail + calendar
    logger.info("Step 1: Classifying vault docs…")
    classify_result = classify_vault(index, engine=engine, write_tags=True)
    report["classify"] = {
        "total": classify_result.total,
        "classified": classify_result.classified,
        "label_counts": classify_result.label_counts,
    }

    # 2. Summarize mail from the last 7 days
    logger.info("Step 2: Summarizing recent mail…")
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    recent_mails = [d for d in index.get_mails() if (d.date or "") >= cutoff_str]

    thread_summary = None
    if recent_mails:
        thread_summary = summarize_docs(
            docs=recent_mails[:20],
            engine=engine,
            index=index,
            topic="Последние письма (7 дней)",
            max_tokens=600,
        )
    report["summary"] = {
        "recent_mail_count": len(recent_mails),
        "summary": thread_summary.summary if thread_summary else "No recent mail.",
    }

    # 3. Write daily digest
    logger.info("Step 3: Writing daily digest…")
    digest_path = _write_daily_digest(vault, report, thread_summary)
    report["digest_path"] = str(digest_path)
    report["finished_at"] = datetime.now(tz=timezone.utc).isoformat()

    logger.info(f"=== Pipeline done. Digest: {digest_path} ===")
    return report


def _write_daily_digest(vault: Path, report: dict, thread_summary) -> Path:
    today = datetime.now(tz=timezone.utc)
    digest_dir = vault / "daily"
    digest_dir.mkdir(parents=True, exist_ok=True)
    digest_path = digest_dir / f"{today.strftime('%Y-%m-%d')}.md"

    classify = report.get("classify", {})
    label_counts_md = ""
    for classifier, counts in classify.get("label_counts", {}).items():
        label_counts_md += f"\n**{classifier}**: "
        label_counts_md += ", ".join(f"{k}: {v}" for k, v in counts.items())

    summary_text = (
        thread_summary.summary if thread_summary else "_No recent emails to summarize._"
    )

    content = f"""\
---
type: daily-digest
date: {today.strftime("%Y-%m-%d")}
tags: [дайджест, ежедневный]
created: {today.strftime("%Y-%m-%dT%H:%M:%SZ")}
---

# Ежедневный дайджест — {today.strftime("%d.%m.%Y")}

## Итоги классификации

- Обработано документов: {classify.get("total", 0)}
- Классифицировано: {classify.get("classified", 0)}
{label_counts_md}

## Резюме последних писем

{summary_text}

---
_Сгенерировано Personal Assistant MLX pipeline_
"""
    digest_path.write_text(content, encoding="utf-8")
    return digest_path


# ---------------------------------------------------------------------------
# APScheduler setup
# ---------------------------------------------------------------------------


def start_scheduler(
    vault_path: Optional[Path] = None,
) -> Optional["BackgroundScheduler"]:
    """
    Start the background scheduler. Blocks if called standalone,
    or runs in background thread when called from the FastAPI lifespan.
    """
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    if not settings.schedule_enabled:
        logger.info("Scheduler is disabled (PA_SCHEDULE_ENABLED=false)")
        return None

    cron = settings.schedule_cron
    logger.info(f"Starting scheduler with cron: {cron!r}")

    scheduler = BackgroundScheduler(timezone="UTC")

    # Main vault pipeline (sync + classify + summarize)
    scheduler.add_job(
        func=lambda: run_pipeline(vault_path),
        trigger=CronTrigger.from_crontab(cron),
        id="vault_pipeline",
        name="Vault processing pipeline",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Daily Brief generation — every day at 08:00 local time (05:00 UTC by default)
    # Uses a fixed cron expression so it always runs at 8am regardless of main cron setting
    scheduler.add_job(
        func=lambda: _run_daily_brief(vault_path),
        trigger=CronTrigger(hour=5, minute=0, timezone="UTC"),  # 08:00 MSK / adjust as needed
        id="daily_brief",
        name="Daily brief generation",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info("Scheduler started (pipeline + daily brief).")
    return scheduler


def _run_daily_brief(vault_path: Optional[Path] = None) -> None:
    """Generate and cache the daily brief. Called by scheduler at 08:00."""
    try:
        from personal_assistant.mlx_server.engine import get_engine
        from personal_assistant.services.daily_brief_service import build_daily_brief

        vault = vault_path or settings.vault_path
        engine = get_engine()

        try:
            from personal_assistant.profile.service import load_profile
            name = (load_profile().full_name or "").split()[0] or ""
        except Exception:
            name = ""

        try:
            user_email = str(settings.user_email or "")
        except Exception:
            user_email = ""

        result = build_daily_brief(
            vault_path=vault,
            my_email=user_email,
            mlx_engine=engine,
            profile_name=name,
            force_refresh=True,
        )
        stats = result.get("stats", {})
        logger.info(
            f"[brief] Daily brief generated — "
            f"events={stats.get('events_today', 0)}, "
            f"urgent={stats.get('urgent_count', 0)}, "
            f"tasks={stats.get('tasks_count', 0)}"
        )
    except Exception as exc:
        logger.warning(f"[brief] Daily brief generation failed: {exc}")
