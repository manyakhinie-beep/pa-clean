"""
Daily Brief API — /api/v1/brief

GET  /api/v1/brief/daily           — full daily brief (cached 30 min)
GET  /api/v1/brief/daily?refresh=1 — force regenerate
POST /api/v1/brief/daily/generate  — trigger async generation (for scheduler)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query
from loguru import logger

router = APIRouter(prefix="/api/v1/brief", tags=["brief"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_vault_path() -> Optional[Path]:
    try:
        from personal_assistant.config import settings
        p = settings.vault_path
        return p if p and p.exists() else None
    except Exception:
        return None


def _get_my_email() -> str:
    try:
        from personal_assistant.config import settings
        return str(settings.user_email or "")
    except Exception:
        return ""


def _get_mlx_engine():
    try:
        from personal_assistant.mlx_server.server import state
        return state.engine
    except Exception:
        return None


def _get_profile_name() -> str:
    try:
        from personal_assistant.profile.service import load_profile
        p = load_profile()
        return (p.full_name or "").split()[0] or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/daily")
def get_daily_brief(refresh: bool = Query(False, description="Force cache refresh")):
    """
    Return the daily brief.

    The brief is cached for 30 minutes. Pass ?refresh=true to force rebuild.

    Sections always present:
      - «Сегодня в календаре»  — today's events
      - «Требуют ответа»       — urgent / reply-required inbox items
      - «Открытые поручения»   — action items from mail/threads (when found)

    Returns:
        {
            generated_at, greeting, sections, ai_insight, bullets,
            stats: { events_today, urgent_count, tasks_count },
            cached, vault_loaded
        }
    """
    from personal_assistant.services.daily_brief_service import build_daily_brief

    vault_path = _get_vault_path()
    my_email = _get_my_email()
    engine = _get_mlx_engine()
    name = _get_profile_name()

    try:
        brief = build_daily_brief(
            vault_path=vault_path,
            my_email=my_email,
            mlx_engine=engine,
            profile_name=name,
            force_refresh=refresh,
        )
    except Exception as exc:
        logger.warning(f"[brief] daily brief failed: {exc}")
        from datetime import datetime, timezone
        brief = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "greeting": "Добрый день!",
            "sections": [],
            "ai_insight": "Не удалось загрузить сводку.",
            "bullets": [],
            "stats": {"events_today": 0, "urgent_count": 0, "tasks_count": 0},
            "cached": False,
            "vault_loaded": False,
        }

    return brief


@router.post("/daily/generate")
def trigger_brief_generation(background_tasks: BackgroundTasks):
    """
    Trigger async daily brief generation (used by scheduler or manual refresh).
    Runs in background, invalidates cache.
    Returns immediately with { queued: true }.
    """
    # Test mode: build_daily_brief reads the vault and may hit MLX engine.
    # TestClient awaits BackgroundTasks inline before returning.
    from personal_assistant.config import settings as _cfg
    if _cfg.e2e_test_mode:
        return {"queued": True, "message": "e2e_test_mode: пропущено", "e2e": True}

    from personal_assistant.services.daily_brief_service import build_daily_brief

    vault_path = _get_vault_path()
    my_email = _get_my_email()
    engine = _get_mlx_engine()
    name = _get_profile_name()

    def _generate():
        try:
            build_daily_brief(
                vault_path=vault_path,
                my_email=my_email,
                mlx_engine=engine,
                profile_name=name,
                force_refresh=True,
            )
            logger.info("[brief] background generation complete")
        except Exception as exc:
            logger.warning(f"[brief] background generation failed: {exc}")

    background_tasks.add_task(_generate)
    return {"queued": True, "message": "Daily brief generation queued"}
