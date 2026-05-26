"""
FastAPI server for the MLX personal assistant.

Endpoints:
  GET  /status              — server & model status
  GET  /vault/stats         — vault document counts
  POST /vault/reload        — перечитать vault (инвалидирует кэш)
  POST /search              — BM25-поиск с LLM-синтезом (блокирующий)
  POST /search/stream       — BM25-поиск со стримингом ответа (SSE/text)
  POST /search/hybrid       — гибридный BM25+вектора поиск с LLM-синтезом [M2]
  POST /api/chat/send       — стриминговый чат (треды, tool calling)
  GET  /index/status        — статус LanceDB векторного индекса [M2]
  POST /index/build         — построить/перестроить векторный индекс [M2]
  POST /summarize/thread    — суммаризация треда по теме
  POST /summarize/contact   — суммаризация переписки с контактом
  POST /draft-reply         — черновик ответа на письмо
  POST /classify            — классификация документов vault
  POST /run-pipeline        — запустить pipeline вручную
  POST /sync                — запустить pa sync-all

Run with:
  uv run pa serve
  # or directly:
  uv run uvicorn personal_assistant.mlx_server.server:app --reload
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator, Optional

from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field

from personal_assistant.config import settings

# WebUI static dir: project_root/webui/dist
_WEBUI_DIST = Path(__file__).parent.parent.parent.parent / "webui" / "dist"
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_SOULS_FILE = _PROJECT_ROOT / "souls.md"
_PERSONA_FILE = _PROJECT_ROOT / "data" / "persona.json"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(..., description="Natural language search query")
    sections: Optional[list[str]] = Field(None, description="Vault sections to search")
    top_k: int = Field(8, ge=1, le=30)
    max_tokens: int = Field(512, ge=64, le=2048)


class SummarizeThreadRequest(BaseModel):
    topic: str = Field(
        ..., description="Email subject or keywords identifying the thread"
    )
    top_k: int = Field(15, ge=1, le=50)
    max_tokens: int = Field(768, ge=64, le=2048)


class SummarizeContactRequest(BaseModel):
    email: str = Field(..., description="Sender email address")
    max_tokens: int = Field(512, ge=64, le=2048)


class DraftReplyRequest(BaseModel):
    topic: str = Field(..., description="Email subject or keywords to find the thread")
    instructions: Optional[str] = Field(
        None, description="What to say / how to respond"
    )
    tone: str = Field("professional", description="professional | friendly | brief")
    max_tokens: int = Field(512, ge=64, le=1024)


class ClassifyRequest(BaseModel):
    sections: Optional[list[str]] = Field(
        None, description="Sections to classify (default: mail, calendar)"
    )
    write_tags: bool = Field(
        True, description="Write classification tags back to .md files"
    )
    use_llm: bool = Field(False, description="Use LLM for semantic classification")


# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------


class HybridSearchRequest(BaseModel):
    query: str = Field(..., description="Natural language search query")
    sections: Optional[list[str]] = Field(None, description="Vault sections to search")
    top_k: int = Field(8, ge=1, le=30)
    max_tokens: int = Field(512, ge=64, le=2048)
    bm25_candidates: int = Field(20, ge=5, le=60, description="BM25 кандидаты для RRF")
    vector_candidates: int = Field(
        20, ge=5, le=60, description="Векторные кандидаты для RRF"
    )


class AppState:
    def __init__(self):
        self.engine = None
        self.index = None
        self._scheduler = None
        self.vector_index = None  # M2: LanceDB

    def load(self):
        from personal_assistant.mlx_server.engine import MLXEngine
        from personal_assistant.mlx_server.scheduler import start_scheduler
        from personal_assistant.mlx_server.vault_index import VaultIndex

        self.engine = MLXEngine()
        self.index = VaultIndex().load()
        self._scheduler = start_scheduler()

    def reload_index(self):
        from personal_assistant.mlx_server.vault_index import VaultIndex

        # Инвалидируем старый кэш перед перезагрузкой
        if self.index is not None:
            self.index.invalidate_cache()
        self.index = VaultIndex().load()

    def load_vector_index(self):
        """Загрузить векторный индекс если построен (не блокирует запуск)."""
        from personal_assistant.mlx_server.vector_index import VectorIndex

        vi = VectorIndex()
        if vi.is_built():
            self.vector_index = vi
            logger.info(f"Векторный индекс загружен: {vi.stats}")


state = AppState()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os

    logger.info("Personal Assistant server starting…")
    loop = asyncio.get_event_loop()

    # Загружаем BM25-индекс
    def _load_index() -> None:
        if state.index is None:
            state.index = (
                __import__(
                    "personal_assistant.mlx_server.vault_index",
                    fromlist=["VaultIndex"],
                )
                .VaultIndex()
                .load()
            )

    await loop.run_in_executor(None, _load_index)
    # Загружаем векторный индекс (если построен)
    await loop.run_in_executor(None, state.load_vector_index)

    # ── MLX model pre-load (PA_PRELOAD_MODEL=1) ───────────────────────────────
    # Load model weights at startup so the first chat request is instant.
    # Skips silently if mlx-lm is not installed or model path is not configured.
    # Also skipped in e2e_test_mode so the test suite never loads multi-GB
    # weights (a TestClient(app) lifespan would otherwise blow up memory).
    if os.getenv("PA_PRELOAD_MODEL") == "1" and not settings.e2e_test_mode:
        def _preload_mlx() -> None:
            from personal_assistant.mlx_server.engine import get_engine

            engine = get_engine()
            # Store engine in server state so routes can reuse the loaded model
            state.engine = engine
            try:
                engine._ensure_loaded()
                logger.info(f"✓ MLX model pre-loaded: {engine.model_name}")
            except RuntimeError as exc:
                # Graceful: log warning, server still starts without LLM
                logger.warning(f"MLX model pre-load skipped: {exc}")

        logger.info("Pre-loading MLX model (PA_PRELOAD_MODEL=1)…")
        await loop.run_in_executor(None, _preload_mlx)
    # ─────────────────────────────────────────────────────────────────────────

    from personal_assistant.mlx_server.scheduler import start_scheduler

    state._scheduler = start_scheduler()
    logger.info(f"Vault: {state.index.stats if state.index else 'not loaded'}")
    vi_info = (
        state.vector_index.stats
        if state.vector_index
        else "не построен (pa build-index)"
    )
    logger.info(f"VectorIndex: {vi_info}")
    yield
    if state._scheduler:
        state._scheduler.shutdown(wait=False)
    logger.info("Server stopped.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Personal Assistant MLX",
    description="Local AI assistant powered by MLX for Apple Silicon",
    version="0.4.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# WebUI — static files + routes (Stage 2)
# ---------------------------------------------------------------------------
# Монтируем статику ПЕРЕД API-роутами, чтобы /static/* не перехватывался роутером
if _WEBUI_DIST.exists():
    app.mount("/static", StaticFiles(directory=str(_WEBUI_DIST)), name="static")
    logger.info(f"WebUI static: {_WEBUI_DIST}")
else:
    logger.warning(
        f"WebUI not built ({_WEBUI_DIST}). Run: cd webui && npm install && npm run build"
    )

# API + index.html маршруты WebUI
from personal_assistant.calendar.routes import router as _calendar_router  # noqa: E402
from personal_assistant.inbox.routes import router as _inbox_router  # noqa: E402
from personal_assistant.mlx_server import chat_routes  # noqa: E402
from personal_assistant.personal_vault.routes import router as _pv_router  # noqa: E402
from personal_assistant.profile.routes import router as _profile_router  # noqa: E402
from personal_assistant.reports.routes import router as _reports_router  # noqa: E402
from personal_assistant.today.brief_routes import router as _brief_router  # noqa: E402
from personal_assistant.today.routes import router as _today_router  # noqa: E402
from personal_assistant.webui.routes import router as _webui_router  # noqa: E402
from personal_assistant.webui.rules_settings import router as _rules_settings_router  # noqa: E402

app.include_router(_webui_router)
app.include_router(_rules_settings_router)
app.include_router(chat_routes.router)
app.include_router(_pv_router)
app.include_router(_profile_router)
app.include_router(_reports_router)
app.include_router(_inbox_router)
app.include_router(_today_router)
app.include_router(_brief_router)
app.include_router(_calendar_router)


def _get_engine():
    if state.engine is None:
        from personal_assistant.mlx_server.engine import MLXEngine

        state.engine = MLXEngine()
    return state.engine


def _get_index():
    if state.index is None:
        from personal_assistant.mlx_server.vault_index import VaultIndex

        state.index = VaultIndex().load()
    return state.index


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/status")
def get_status():
    from personal_assistant.mlx_server.engine import MLXEngine
    engine = _get_engine()
    mlx_ok = MLXEngine._mlx_available
    return {
        "status": "ok",
        "model": engine.model_name,
        "model_loaded": engine.is_loaded,
        "model_path": settings.mlx_model_path or "not configured",
        "mlx_available": mlx_ok,
        "mlx_hint": (
            None if mlx_ok
            else "mlx-lm not installed — run: uv pip install -e '.[mlx]'  (Apple Silicon only)"
        ),
        "vault_path": str(settings.vault_path),
        "schedule_enabled": settings.schedule_enabled,
        "schedule_cron": settings.schedule_cron,
    }


@app.get("/vault/stats")
def vault_stats():
    return _get_index().stats


@app.post("/vault/reload")
def reload_vault():
    state.reload_index()
    return {"status": "ok", "stats": state.index.stats}


@app.post("/search")
def search(req: SearchRequest):
    from personal_assistant.mlx_server.tasks.search import search as do_search

    result = do_search(
        query=req.query,
        engine=_get_engine(),
        index=_get_index(),
        sections=req.sections,
        top_k=req.top_k,
        max_tokens=req.max_tokens,
    )
    return {
        "query": result.query,
        "answer": result.answer,
        "sources": result.source_titles,
        "doc_count": result.doc_count,
    }


@app.post("/summarize/thread")
def summarize_thread(req: SummarizeThreadRequest):
    from personal_assistant.mlx_server.tasks.summarize import (
        summarize_thread as do_summarize,
    )

    result = do_summarize(
        topic=req.topic,
        engine=_get_engine(),
        index=_get_index(),
        top_k=req.top_k,
        max_tokens=req.max_tokens,
    )
    return {
        "topic": result.topic,
        "summary": result.summary,
        "doc_count": result.doc_count,
        "sources": result.source_titles,
    }


@app.post("/summarize/contact")
def summarize_contact(req: SummarizeContactRequest):
    from personal_assistant.mlx_server.tasks.summarize import (
        summarize_contact as do_summarize,
    )

    result = do_summarize(
        email=req.email,
        engine=_get_engine(),
        index=_get_index(),
        max_tokens=req.max_tokens,
    )
    return {
        "email": req.email,
        "summary": result.summary,
        "doc_count": result.doc_count,
        "sources": result.source_titles,
    }


@app.post("/draft-reply")
def draft_reply(req: DraftReplyRequest):
    from personal_assistant.mlx_server.tasks.draft_reply import draft_reply_to_thread

    result = draft_reply_to_thread(
        topic=req.topic,
        engine=_get_engine(),
        index=_get_index(),
        instructions=req.instructions,
        tone=req.tone,
        max_tokens=req.max_tokens,
    )
    return {
        "subject": result.subject,
        "draft": result.draft,
        "based_on": result.based_on,
    }


@app.post("/search/stream")
def search_stream(req: SearchRequest):
    """
    Поиск с потоковым ответом (text/plain).
    Клиент получает токены по мере генерации — без ожидания полного ответа.

    Пример curl:
      curl -N -X POST http://localhost:8000/search/stream \\
        -H 'Content-Type: application/json' \\
        -d '{"query": "встречи на этой неделе"}'
    """
    from personal_assistant.mlx_server.tasks.search import (
        _SEARCH_PROMPT,
        _SEARCH_SYSTEM,
    )

    engine = _get_engine()
    index = _get_index()

    docs = index.search(req.query, sections=req.sections, top_k=req.top_k)
    if not docs:
        return StreamingResponse(
            iter(["Релевантных документов не найдено."]),
            media_type="text/plain; charset=utf-8",
        )

    context = index.build_context(docs)
    prompt_text = _SEARCH_PROMPT.format(query=req.query, context=context)

    def token_stream() -> Iterator[str]:
        try:
            for chunk in engine.stream_ask(
                question=prompt_text,
                system=_SEARCH_SYSTEM,
                max_tokens=req.max_tokens,
            ):
                text = (
                    chunk
                    if isinstance(chunk, str)
                    else (str(chunk) if chunk is not None else "")
                )
                if text:
                    yield text
        except Exception as exc:
            logger.error(f"stream_ask error: {exc}")
            yield f"\n\n[Ошибка генерации: {exc}]"

    return StreamingResponse(token_stream(), media_type="text/plain; charset=utf-8")


@app.post("/classify")
def classify(req: ClassifyRequest, background_tasks: BackgroundTasks):
    from personal_assistant.mlx_server.tasks.classify import (
        classify_vault,
        load_classify_config,
    )

    engine = _get_engine() if req.use_llm else None
    cfg = load_classify_config()
    if req.use_llm:
        cfg.setdefault("llm_classify", {})["enabled"] = True

    result = classify_vault(
        index=_get_index(),
        sections=req.sections,
        config=cfg,
        engine=engine,
        write_tags=req.write_tags,
    )
    # Reload index after tags are written
    if req.write_tags:
        background_tasks.add_task(state.reload_index)

    return {
        "total": result.total,
        "classified": result.classified,
        "label_counts": result.label_counts,
    }


@app.post("/run-pipeline")
def run_pipeline(background_tasks: BackgroundTasks):
    """Trigger the scheduled pipeline manually (runs in background)."""
    from personal_assistant.mlx_server.scheduler import run_pipeline as do_run

    background_tasks.add_task(do_run)
    return {"status": "pipeline started in background"}


@app.get("/index/status")
def index_status():
    """
    Статус LanceDB векторного индекса (Stage M2).

    Возвращает built=false если индекс не построен.
    Для построения: POST /index/build или `pa build-index`.
    """
    from personal_assistant.mlx_server.vector_index import VectorIndex

    vi = state.vector_index or VectorIndex()
    return vi.stats


@app.post("/index/build")
def build_vector_index(background_tasks: BackgroundTasks):
    """
    Построить/перестроить LanceDB векторный индекс (Stage M2).

    Запускается в фоне — возвращает немедленно.
    Время: 1-5 мин для 1000 документов.
    Модель bge-m3 (~570 MB) скачивается при первом запуске.

    После завершения /search/hybrid начнёт использовать векторный поиск.
    """

    def _build():
        from personal_assistant.mlx_server.vector_index import (
            VectorIndex,
            reset_vector_index,
        )

        reset_vector_index()
        vi = VectorIndex()
        count = vi.build(_get_index().docs)
        state.vector_index = vi
        logger.info(f"Векторный индекс готов: {count} docs")

    background_tasks.add_task(_build)
    return {
        "status": "index build started",
        "docs_to_index": len(_get_index().docs),
        "note": "Проверьте прогресс в логах сервера. Займёт 1-5 мин.",
    }


@app.post("/search/hybrid")
def search_hybrid(req: HybridSearchRequest):
    """
    Гибридный поиск BM25 + векторный (Stage M2).

    Алгоритм:
      1. BM25 top-N  — точные ключевые слова
      2. bge-m3 top-N — семантическое сходство (синонимы, перефразировки, RU↔EN)
      3. RRF слияние → top-k документов
      4. LLM-синтез ответа на основе найденных документов

    Требует предварительно построенного индекса (POST /index/build).
    При отсутствии индекса автоматически возвращается к BM25.
    """
    from personal_assistant.mlx_server.tasks.search import (
        _SEARCH_PROMPT,
        _SEARCH_SYSTEM,
    )
    from personal_assistant.mlx_server.vector_index import hybrid_search as do_hybrid

    engine = _get_engine()
    index = _get_index()

    # Выбираем стратегию в зависимости от наличия векторного индекса
    vi = state.vector_index
    if vi is not None and vi.is_built():
        docs = do_hybrid(
            query=req.query,
            bm25_index=index,
            vector_index=vi,
            top_k=req.top_k,
            bm25_candidates=req.bm25_candidates,
            vector_candidates=req.vector_candidates,
            sections=req.sections,
        )
        search_mode = "hybrid (BM25 + bge-m3 + RRF)"
    else:
        # Fallback: чистый BM25
        docs = index.search(req.query, sections=req.sections, top_k=req.top_k)
        search_mode = "bm25 (векторный индекс не построен)"

    if not docs:
        return {
            "query": req.query,
            "answer": "Релевантных документов не найдено.",
            "sources": [],
            "doc_count": 0,
            "search_mode": search_mode,
        }

    context = index.build_context(docs, max_chars=settings.mlx_context_chars)
    prompt_text = _SEARCH_PROMPT.format(query=req.query, context=context)
    answer = engine.ask(prompt_text, system=_SEARCH_SYSTEM, max_tokens=req.max_tokens)

    return {
        "query": req.query,
        "answer": answer,
        "sources": [d.title for d in docs],
        "doc_count": len(docs),
        "search_mode": search_mode,
    }


# ---------------------------------------------------------------------------
# Sync progress state
# ---------------------------------------------------------------------------

_sync_state: dict = {
    "running": False,
    "stage": "idle",  # idle | calendar | mail | contacts | indexing | done | error
    "pct": 0,  # 0-100
    "message": "",
    "error": "",
    "warnings": [],  # per-source warnings collected during sync
    "counts": {},  # per-source item counts  { "mail": 42, "calendar": 7, … }
    "last_sync_at": "",  # ISO datetime (MSK) of last successful sync, e.g. "2026-05-20 14:35:00"
    "last_sync_per_source": {},  # { "calendar": "14:35", "mail": "14:36" }
}

_SYNC_STAGES = [
    ("calendar", "Чтение Календаря…", 20),
    ("mail", "Чтение Почты…", 50),
    ("contacts", "Запись контактов…", 70),
    ("indexing", "Обновление индекса…", 90),
    ("done", "Готово", 100),
]


class _SyncRequest(BaseModel):
    sources: list[str] = Field(
        default_factory=lambda: settings.sync_sources_list or ["calendar", "mail"],
        description=(
            "Sources to sync: calendar, mail. "
            "Defaults to PA_SYNC_SOURCES from .env."
        ),
    )


def _run_sync_calendar(vault_path: Path) -> dict:
    """Sync Apple Calendar directly (no subprocess).

    Returns dict with keys: events, contacts, warning (str | None).
    """
    from personal_assistant.readers.applescript_base import is_app_running
    from personal_assistant.readers.calendar_reader import CalendarReader
    from personal_assistant.vault.writer import VaultWriter

    # Quick sanity-check: Calendar.app must be running for AppleScript to work
    if not is_app_running("Calendar"):
        msg = "Calendar.app не запущена — откройте «Календарь» и повторите"
        logger.warning(f"[sync] calendar: {msg}")
        return {"events": 0, "contacts": 0, "warning": msg}

    reader = CalendarReader()
    reader.PER_CAL_TIMEOUT = settings.calendar_per_cal_timeout
    events = reader.fetch_events(
        days_back=settings.calendar_days_back,
        days_forward=settings.calendar_days_forward,
        calendar_names=settings.calendar_names_list or None,
        fetch_attendees=settings.calendar_fetch_attendees,
        max_events_per_calendar=settings.calendar_max_events,
    )
    contacts = reader.extract_contacts(events)
    writer = VaultWriter(vault_path)
    writer.write_events(events, overwrite=settings.overwrite)
    writer.write_contacts(contacts, overwrite=settings.overwrite)
    logger.info(f"[sync] calendar: {len(events)} events, {len(contacts)} contacts")

    warning = None
    if not events:
        warning = (
            f"0 событий за период "
            f"-{settings.calendar_days_back}…+{settings.calendar_days_forward} дней — "
            "проверьте доступ к Календарю в Системных настройках → Конфиденциальность"
        )
    return {"events": len(events), "contacts": len(contacts), "warning": warning}


def _run_sync_mail(vault_path: Path) -> dict:
    """Sync Apple Mail directly (no subprocess).

    Returns dict with keys: messages, contacts, warning (str | None).
    """
    from personal_assistant.readers.applescript_base import is_app_running
    from personal_assistant.readers.mail_reader import MailReader
    from personal_assistant.vault.writer import VaultWriter

    # Quick sanity-check: Mail.app must be running for AppleScript to work
    if not is_app_running("Mail"):
        msg = (
            "Mail.app не запущена — откройте «Почту» и повторите синхронизацию. "
            "Также проверьте доступ: Системные настройки → Конфиденциальность → Автоматизация"
        )
        logger.warning(f"[sync] mail: {msg}")
        return {"messages": 0, "contacts": 0, "warning": msg}

    from personal_assistant.sync.thread_tracker import ThreadTracker

    reader = MailReader()
    reader.PER_MBOX_TIMEOUT = settings.mail_per_mbox_timeout
    messages = reader.fetch_messages(
        days_back=settings.mail_days_back,
        max_messages_per_mailbox=settings.mail_max_messages,
        fetch_body=settings.mail_fetch_body,
        fetch_recipients=settings.mail_fetch_recipients,
    )
    contacts = reader.extract_contacts(messages)

    # Apply ThreadTracker: upgrades subject-based thread_ids with RFC 2822
    # header-based grouping when PA_MAIL_FETCH_BODY=true, and ensures
    # _patch_thread_id() in write_message() has the latest thread_id.
    ThreadTracker().group_messages(messages)

    writer = VaultWriter(vault_path)
    writer.write_messages(messages, overwrite=settings.overwrite)
    writer.write_contacts(contacts, overwrite=settings.overwrite)
    logger.info(f"[sync] mail: {len(messages)} messages, {len(contacts)} contacts")

    warning = None
    if not messages:
        warning = (
            f"0 сообщений за последние {settings.mail_days_back} дней — "
            "проверьте доступ: Системные настройки → Конфиденциальность → Автоматизация → Mail"
        )
    return {"messages": len(messages), "contacts": len(contacts), "warning": warning}


_SYNC_RUNNERS = {
    "calendar": (_run_sync_calendar, "Чтение Календаря…"),
    "mail": (_run_sync_mail, "Чтение Почты…"),
}


@app.post("/sync")
def sync_data(req: _SyncRequest = None, background_tasks: BackgroundTasks = None):  # type: ignore[assignment]
    """Trigger Apple data sync with progress tracking.

    Pass ``sources`` list to control which sub-commands run.
    Contacts are always written as a by-product of calendar/mail/outlook sync.
    """
    import time as _time

    req = req or _SyncRequest()
    sources = [s for s in (req.sources or ["calendar", "mail"]) if s in _SYNC_RUNNERS]

    if _sync_state["running"]:
        return {"status": "already running", "pct": _sync_state["pct"]}

    vault_path = settings.vault_path

    def _sync():
        global _sync_state
        _sync_state.update(
            {
                "running": True,
                "stage": "starting",
                "pct": 0,
                "message": "Запуск синхронизации…",
                "error": "",
                "warnings": [],
                "counts": {},
            }
        )
        try:
            total = len(sources)
            all_warn: list[str] = []
            all_counts: dict[str, int] = {}

            for idx, src in enumerate(sources):
                runner_fn, label = _SYNC_RUNNERS[src]
                pct_start = int((idx / total) * 80)
                # Announce start
                _sync_state.update(
                    {
                        "stage": src,
                        "pct": pct_start,
                        "message": label,
                    }
                )
                _time.sleep(0.1)

                # Run and capture result dict
                result = runner_fn(vault_path)
                warning = result.get("warning")
                pct_end = int(((idx + 1) / total) * 80)

                # Build human-readable count summary
                parts = []
                if "messages" in result:
                    parts.append(f"{result['messages']} писем")
                if "events" in result:
                    parts.append(f"{result['events']} событий")
                if "contacts" in result:
                    parts.append(f"{result['contacts']} контактов")
                count_str = ", ".join(parts) if parts else "нет данных"

                # Store counts
                for k, v in result.items():
                    if k != "warning" and isinstance(v, int):
                        all_counts[f"{src}.{k}"] = v

                # Build status message
                if warning:
                    msg = f"⚠️ {label.rstrip('…')} — {warning}"
                    all_warn.append(warning)
                else:
                    msg = f"✅ {label.rstrip('…')} — {count_str}"

                from personal_assistant.utils.timezone import get_now_msk as _get_now_msk
                _sync_state["last_sync_per_source"][src] = _get_now_msk().strftime("%H:%M")
                _sync_state.update(
                    {
                        "stage": src,
                        "pct": pct_end,
                        "message": msg,
                        "warnings": list(all_warn),
                        "counts": dict(all_counts),
                    }
                )
                _time.sleep(0.05)

            _sync_state.update(
                {"stage": "indexing", "pct": 85, "message": "Обновление индекса…"}
            )
            state.reload_index()

            # Final message
            warn_suffix = f" — ⚠️ {len(all_warn)} предупреждений" if all_warn else ""
            from personal_assistant.utils.timezone import get_now_msk as _get_now_msk
            _sync_state.update(
                {
                    "stage": "done",
                    "pct": 100,
                    "running": False,
                    "message": f"Синхронизация завершена ({', '.join(sources)}){warn_suffix}",
                    "error": "",
                    "warnings": list(all_warn),
                    "counts": dict(all_counts),
                    "last_sync_at": _get_now_msk().strftime("%Y-%m-%d %H:%M"),
                    "last_sync_per_source": dict(_sync_state.get("last_sync_per_source", {})),
                }
            )
        except Exception as exc:
            logger.exception("[sync] error")
            _sync_state.update(
                {
                    "stage": "error",
                    "pct": 0,
                    "running": False,
                    "message": "Ошибка синхронизации",
                    "error": str(exc),
                }
            )

    background_tasks.add_task(_sync)
    return {"status": "sync started", "sources": sources}


@app.get("/sync/status")
def sync_status():
    """Return current sync progress state."""
    return _sync_state
