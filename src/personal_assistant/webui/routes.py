"""
WebUI API routes — Stage 2.

Эндпоинты:
  GET    /                       — serve index.html
  GET    /vault/list             — список файлов vault с метаданными
  GET    /vault/file             — содержимое .md файла
  PATCH  /vault/file             — сохранить изменения в .md файле
  DELETE /vault/file             — удалить файл из vault
  GET    /vault/tags             — все уникальные теги
  GET    /settings               — текущие настройки (из .env / Settings)
  POST   /settings               — сохранить настройки в .env
  GET    /classify/config        — classify.yaml (raw + parsed)
  PUT    /classify/config        — сохранить classify.yaml
  GET    /classify/labels        — плоский список меток из classify.yaml
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
import re as _re
import shutil as _shutil
import threading as _threading
import uuid as _uuid
import zipfile as _zipfile
from datetime import date as _date
from datetime import datetime as _dt
from datetime import timedelta as _td
from datetime import timezone as _tz
from pathlib import Path
from typing import Any, Optional
from typing import List as _List
from typing import Optional as _Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from loguru import logger
from pydantic import BaseModel
from pydantic import BaseModel as _BaseModel
from pydantic import Field as _Field

router = APIRouter()

# Пути — routes.py → webui → personal_assistant → src → project root
_PKG_DIR = Path(__file__).parent  # src/personal_assistant/webui
_PROJECT_ROOT = _PKG_DIR.parent.parent.parent  # project root
_WEBUI_DIR = _PROJECT_ROOT / "webui"
_WEBUI_INDEX = _WEBUI_DIR / "index.html"
_ENV_FILE = _PROJECT_ROOT / ".env"


# ---------------------------------------------------------------------------
# Serve WebUI
# ---------------------------------------------------------------------------


@router.get("/", include_in_schema=False)
async def serve_index():
    """Отдаёт index.html WebUI. Если не собран — показывает инструкцию."""
    if not _WEBUI_INDEX.exists():
        return HTMLResponse(
            """<!doctype html><html><head><meta charset="utf-8">
            <title>Personal Assistant — WebUI not built</title>
            <style>body{font-family:system-ui;max-width:600px;margin:80px auto;color:#333}
            code{background:#f3f4f6;padding:2px 6px;border-radius:4px}
            pre{background:#1f2937;color:#f9fafb;padding:16px;border-radius:8px}</style>
            </head><body>
            <h2>⚠️ WebUI не собран</h2>
            <p>Выполните в корне проекта:</p>
            <pre>cd webui
npm install
npm run build</pre>
            <p>Затем перезапустите: <code>uv run pa serve</code></p>
            </body></html>""",
            status_code=503,
        )
    return FileResponse(str(_WEBUI_INDEX))


# ---------------------------------------------------------------------------
# Vault API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Search (doc-level, no LLM) — used by WebUI Search tab
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Date parsing helpers for free-form date search
# ---------------------------------------------------------------------------

_MONTHS_RU: dict[str, int] = {
    # genitive (e.g. "15 мая")
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    # nominative (e.g. "май 2026")
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

_MONTHS_EN: dict[str, int] = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ALL_MONTHS: dict[str, int] = {**_MONTHS_RU, **_MONTHS_EN}



def _parse_date_from_query(query: str) -> list[str]:
    """Extract YYYY-MM-DD (or YYYY-MM prefix) tokens from a free-form query.

    Supports:
    - ISO: 2026-05-15
    - RU numeric: 15.05.2026
    - RU text: "15 мая", "15 мая 2026", "май 2026"
    - EN text: "May 15", "15 May", "May 15 2026", "may 2026"

    Returns list of date-prefix strings to match against ``VaultDoc.date``.
    """
    q = query.lower().strip()
    found: set[str] = set()
    current_year = _date.today().year

    # 1. ISO YYYY-MM-DD
    for m in _re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", q):
        found.add(f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")

    # 2. DD.MM.YYYY (Russian numeric)
    for m in _re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", q):
        found.add(f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}")

    # 3. Month-name patterns (longest match first to avoid "май" inside "январь")
    month_alt = "|".join(sorted(_ALL_MONTHS.keys(), key=len, reverse=True))

    # "15 мая 2026", "15 мая", "15 May 2026", "15 May"
    for m in _re.finditer(r"\b(\d{1,2})\s+(" + month_alt + r")(?:\s+(\d{4}))?\b", q):
        day, mn, yr = int(m.group(1)), m.group(2), m.group(3)
        mnum = _ALL_MONTHS.get(mn)
        if mnum and 1 <= day <= 31:
            found.add(f"{int(yr) if yr else current_year}-{mnum:02d}-{day:02d}")

    # "мая 15 2026", "мая 15", "May 15 2026", "May 15"
    for m in _re.finditer(r"\b(" + month_alt + r")\s+(\d{1,2})(?:\s+(\d{4}))?\b", q):
        mn, day, yr = m.group(1), int(m.group(2)), m.group(3)
        mnum = _ALL_MONTHS.get(mn)
        if mnum and 1 <= day <= 31:
            found.add(f"{int(yr) if yr else current_year}-{mnum:02d}-{day:02d}")

    # 4. Month+year only → YYYY-MM prefix: "май 2026", "May 2026"
    for m in _re.finditer(r"\b(" + month_alt + r")\s+(\d{4})\b", q):
        mnum = _ALL_MONTHS.get(m.group(1))
        if mnum:
            found.add(f"{m.group(2)}-{mnum:02d}")

    return list(found)


class _SearchDocsRequest(_BaseModel):
    query: str = ""
    sections: _Optional[_List[str]] = None  # ["mail","calendar","contacts"]
    tags: _Optional[_List[str]] = None       # tag filter (OR-match)
    top_k: int = _Field(20, ge=1, le=100)
    mode: str = _Field("bm25", pattern="^(bm25|hybrid)$")  # "bm25" | "hybrid"


@router.post("/search/docs")
async def search_docs(req: _SearchDocsRequest):
    """
    Поиск документов в vault (BM25 или Гибридный режим) без LLM-синтеза.

    Режимы (``mode``):
    - ``bm25``   — чистый BM25 (TF-IDF bag-of-words по заголовку, тегам,
                   именам вложений и тексту).
    - ``hybrid`` — BM25 + keyword-fallback: документы с нулевым BM25-скором,
                   но содержащие все токены запроса в заголовке / вложениях /
                   тегах / дате, добавляются в конец списка.

    Дополнительно:
    - Дата в свободной форме — если запрос содержит дату (ISO, DD.MM.YYYY,
      «15 мая», «May 15», «май 2026» и т.п.), соответствующие документы
      поднимаются наверх результатов; при нулевом BM25-скоре выполняется
      самостоятельный date-only поиск по всему индексу.
    - Поиск по вложениям — имена файлов-вложений индексируются в BM25 и
      возвращаются в ответе как ``attachments``.
    - Фильтр по тегам — OR-match: документ содержит хотя бы один из указанных
      тегов.
    """
    from personal_assistant.mlx_server.server import state

    index = state.index
    if index is None:
        # Graceful degradation: vault not yet loaded — return empty result
        # instead of 503 so the client can handle it without error handling.
        return {
            "results": [],
            "total": 0,
            "sections": {},
            "note": "Vault не загружен. Выполните /vault/reload или /index/build для инициализации.",
        }

    q = req.query.strip()

    # --- Date tokens extraction -----------------------------------------
    date_prefixes: list[str] = _parse_date_from_query(q) if q else []

    def _date_match(doc) -> bool:
        d = doc.date or ""
        return any(d.startswith(p) for p in date_prefixes)

    # --- Core search ----------------------------------------------------
    if q:
        # BM25 search
        pool: list = index.search(q, sections=req.sections, top_k=req.top_k)
        pool = sorted(pool, key=lambda d: d.date or "", reverse=True)

        if req.mode == "hybrid":
            # Keyword fallback: add zero-score docs that match all query tokens
            bm25_paths = {str(d.path) for d in pool}
            candidates = [d for d in index.docs if str(d.path) not in bm25_paths]
            if req.sections:
                candidates = [d for d in candidates if d.section in req.sections]
            q_tokens = q.lower().split()

            def _kw_match(doc) -> bool:
                haystack = (
                    doc.title.lower()
                    + " "
                    + " ".join(doc.attachments).lower()
                    + " "
                    + " ".join(doc.tags).lower()
                    + " "
                    + (doc.date or "")
                )
                return all(tok in haystack for tok in q_tokens)

            fallback = sorted(
                [d for d in candidates if _kw_match(d)],
                key=lambda d: d.date or "",
                reverse=True,
            )
            slots = max(0, req.top_k - len(pool))
            pool = pool + fallback[:slots]

        # --- Date boost -------------------------------------------------
        if date_prefixes:
            matched = [d for d in pool if _date_match(d)]
            unmatched = [d for d in pool if not _date_match(d)]
            pool = matched + unmatched

            # If BM25 produced nothing date-relevant, fall back to date-only scan
            if not matched:
                all_docs = index.docs
                if req.sections:
                    all_docs = [d for d in all_docs if d.section in req.sections]
                date_only = sorted(
                    [d for d in all_docs if _date_match(d)],
                    key=lambda d: d.date or "",
                    reverse=True,
                )
                pool = date_only[:req.top_k] + [d for d in pool if str(d.path) not in {str(x.path) for x in date_only}]

    else:
        # No text query — list + filter
        pool = list(index.docs)
        if req.sections:
            pool = [d for d in pool if d.section in req.sections]

        # Date filter when only date tokens present (no text)
        if date_prefixes:
            pool = [d for d in pool if _date_match(d)]

        pool = sorted(pool, key=lambda d: d.date or "", reverse=True)
        pool = pool[: req.top_k]

    # --- Tag filter (OR-match) ------------------------------------------
    if req.tags:
        tag_set = set(req.tags)
        pool = [d for d in pool if tag_set & set(d.tags or [])]

    # --- Deduplicate & cap ---------------------------------------------
    seen_paths: set[str] = set()
    deduped: list = []
    for d in pool:
        key = str(d.path)
        if key not in seen_paths:
            seen_paths.add(key)
            deduped.append(d)
    pool = deduped[: req.top_k]

    def _doc_to_dict(d) -> dict:
        # Identity / threading fields — needed so that frontend actions
        # ("draft reply", "summarize") can open the chat with the correct
        # reply_message_id / vault_thread_id without a second round-trip.
        fm = d.frontmatter or {}
        doc_id = str(fm.get("id") or d.path.stem).strip()
        thread_id = str(fm.get("thread_id") or "").strip() or None
        message_id = str(fm.get("message_id") or "").strip() or None
        subject = str(fm.get("subject") or d.title or d.path.stem).strip()
        sender_name = str(fm.get("sender_name") or fm.get("sender") or "").strip() or None
        sender_email = d.sender_email
        return {
            "id": doc_id,
            "path": str(d.path),
            "section": d.section or "",
            "title": d.title or d.path.name,
            "date": str(d.date)
            if d.date
            else str(fm.get("date") or fm.get("start") or ""),
            "tags": d.tags or [],
            "snippet": d.short_summary(200),
            "attachments": d.attachments,
            "thread_id": thread_id,
            "message_id": message_id,
            "subject": subject,
            "sender_name": sender_name,
            "sender_email": sender_email,
        }

    return {
        "docs": [_doc_to_dict(d) for d in pool],
        "total": len(pool),
        "mode": req.mode,
        "date_prefixes": date_prefixes,
    }


@router.get("/vault/list")
async def vault_list(section: str = "", limit: int = 500):
    """Список документов vault с метаданными."""
    from personal_assistant.mlx_server.server import state  # noqa: E402

    index = state.index
    if index is None:
        return {
            "docs": [], "sections": [], "total": 0, "total_all": 0,
            "section_counts": {}, "urgency_counts": {}, "category_counts": {},
        }

    docs = index.docs
    if section:
        docs = [d for d in docs if d.section == section]
    # Sort by date descending (newest first).
    # d.date may be a datetime, date, or plain str — convert to str for safe
    # lexicographic comparison (ISO format sorts correctly as a string).
    def _date_key(d) -> str:
        v = d.date
        if v is None:
            return ""
        return str(v)

    docs = sorted(docs, key=_date_key, reverse=True)
    docs = docs[:limit]

    all_docs = index.docs  # full list for counts

    # Count per section
    section_counts: dict[str, int] = {}
    urgency_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for d in all_docs:
        sec = d.section or ""
        section_counts[sec] = section_counts.get(sec, 0) + 1
        for tag in (d.tags or []):
            if tag.startswith("urgency:"):
                val = tag.split(":", 1)[1]
                urgency_counts[val] = urgency_counts.get(val, 0) + 1
            elif tag.startswith("category:"):
                val = tag.split(":", 1)[1]
                category_counts[val] = category_counts.get(val, 0) + 1

    total_all = len(all_docs)

    return {
        "docs": [
            {
                "path": str(d.path),
                "section": d.section or "",
                "title": d.title or d.path.name,
                "date": str(d.date) if d.date else "",
                "tags": d.tags,
                "snippet": d.ui_preview(150),
                # Mail-specific: thread grouping + display
                "thread_id": str(d.frontmatter.get("thread_id") or "")
                if d.section == "mail"
                else "",
                "subject": str(
                    d.frontmatter.get("title") or d.frontmatter.get("subject") or ""
                )
                if d.section == "mail"
                else "",
                "sender": str(d.frontmatter.get("sender") or "")
                if d.section == "mail"
                else "",
                "sender_name": str(d.frontmatter.get("sender_name") or "")
                if d.section == "mail"
                else "",
                "from_email": str(d.frontmatter.get("from") or d.sender_email or "")
                if d.section == "mail"
                else "",
                "source": str(d.frontmatter.get("source") or "")
                if d.section == "mail"
                else "",
                "mailbox": str(d.frontmatter.get("mailbox") or "")
                if d.section == "mail"
                else "",
            }
            for d in docs
        ],
        "total": len(docs),
        "total_all": total_all,
        "sections": sorted({d.section for d in all_docs if d.section}),
        "section_counts": section_counts,
        "urgency_counts": urgency_counts,
        "category_counts": category_counts,
    }


@router.get("/vault/mentioned-in")
async def vault_mentioned_in(path: str):
    """
    Вернуть объекты, которые «упоминают» данный документ vault:
    - Проекты с этим path в vault_items
    - События календаря с похожим subject
    - Почтовые треды с тем же thread_id
    - Eisenhower-записи, ссылающиеся на файл
    Также возвращает теги и мета документа для отображения в правой панели.
    """
    from personal_assistant.mlx_server.server import state  # noqa

    index = state.index
    results: list[dict] = []

    # ── Target doc meta ──────────────────────────────────────────────────────
    target_title = ""
    target_tags: list[str] = []
    target_thread_id = ""

    if index:
        for d in index.docs:
            if str(d.path) == path or d.path.name == Path(path).name:
                target_title = d.title or d.path.stem
                target_tags  = d.tags or []
                target_thread_id = str(d.frontmatter.get("thread_id") or "")
                break

    # ── Projects linking this file ────────────────────────────────────────────
    projects = _load_projects()
    for p in projects:
        vault_items = p.get("vault_items", [])
        if path in vault_items or any(path.endswith(vi) for vi in vault_items):
            goals = p.get("goals", [])
            done  = sum(1 for g in goals if g.get("done"))
            pct   = round(done / len(goals) * 100) if goals else 0
            deadline = _deadline_label(p.get("deadline") or "")
            results.append({
                "type":     "project",
                "id":       p["id"],
                "title":    p.get("name", "Проект"),
                "subtitle": f"проект · {len(goals)} {'цель' if len(goals)==1 else 'цели' if len(goals)<5 else 'целей'}",
                "meta":     f"прогресс {pct}%{(' · deadline ' + deadline) if deadline else ''}",
                "icon":     "project",
            })

    # ── Calendar events with matching subject or keywords ────────────────────
    if index and target_title:
        kw = target_title.lower().split()[:3]
        for d in index.docs:
            if d.section != "calendar":
                continue
            title_lower = (d.title or "").lower()
            if any(k in title_lower for k in kw if len(k) > 3):
                start = str(d.frontmatter.get("start") or d.frontmatter.get("date") or "")
                time_label = _start_label(start)
                results.append({
                    "type":     "calendar",
                    "id":       str(d.path),
                    "title":    d.title or d.path.stem,
                    "subtitle": time_label,
                    "meta":     "",
                    "icon":     "calendar",
                })
                if len([r for r in results if r["type"] == "calendar"]) >= 3:
                    break

    # ── Mail thread (same thread_id) ─────────────────────────────────────────
    if index and target_thread_id:
        thread_docs = [d for d in index.docs
                       if d.section == "mail"
                       and str(d.frontmatter.get("thread_id") or "") == target_thread_id]
        if thread_docs:
            count = len(thread_docs)
            senders = list({
                str(d.frontmatter.get("sender_name") or d.frontmatter.get("sender") or "")
                for d in thread_docs if d.frontmatter.get("sender_name") or d.frontmatter.get("sender")
            })
            dates = sorted(str(d.date) for d in thread_docs if d.date)
            since = dates[0][:10] if dates else ""
            results.append({
                "type":     "mail_thread",
                "id":       target_thread_id,
                "title":    f"Тред «{target_title[:30]}»",
                "subtitle": f"+{count} {'письмо' if count==1 else 'письма' if count<5 else 'писем'}",
                "meta":     (f"с {since} · {len(senders)} {'контакт' if len(senders)==1 else 'контакта' if len(senders)<5 else 'контактов'}")
                            if since else "",
                "icon":     "mail",
            })

    # ── Eisenhower matrix ─────────────────────────────────────────────────────
    eisenhower_file = _PROJECT_ROOT / "data" / "eisenhower.json"
    if eisenhower_file.exists():
        try:
            tasks_data = _json.loads(eisenhower_file.read_text(encoding="utf-8"))
            tasks = tasks_data if isinstance(tasks_data, list) else tasks_data.get("tasks", [])
            for t in tasks:
                text = (t.get("title") or t.get("text") or "").lower()
                if target_title.lower() in text or any(k in text for k in target_title.lower().split()[:2] if len(k) > 3):
                    q = t.get("quadrant", "")
                    quad_label = {
                        "q1": "срочно & важно",
                        "q2": "важно, не срочно",
                        "q3": "срочно, не важно",
                        "q4": "не срочно & не важно",
                    }.get(q, q)
                    results.append({
                        "type":     "eisenhower",
                        "id":       t.get("id", ""),
                        "title":    t.get("title") or t.get("text") or "Задача",
                        "subtitle": quad_label,
                        "meta":     "",
                        "icon":     "eisenhower",
                    })
        except Exception:
            pass

    return {
        "path":     path,
        "title":    target_title,
        "tags":     target_tags,
        "items":    results,
        "count":    len(results),
    }


def _deadline_label(d: str) -> str:
    """Преобразовать ISO-дату в читаемую метку дедлайна (пт, сб, 23 мая)."""
    if not d:
        return ""
    import datetime as _dt  # noqa
    try:
        date = _dt.date.fromisoformat(d[:10])
        today = _dt.date.today()
        diff = (date - today).days
        if diff < 0:
            return "просрочен"
        if diff == 0:
            return "сегодня"
        if diff == 1:
            return "завтра"
        days = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"]
        if diff <= 7:
            return days[date.weekday() + 1 if date.weekday() < 6 else 0]
        months = ["янв","фев","мар","апр","май","июн","июл","авг","сен","окт","ноя","дек"]
        return f"{date.day} {months[date.month - 1]}"
    except (ValueError, TypeError):
        return d


def _start_label(start: str) -> str:
    """Преобразовать ISO datetime в метку 'сегодня 11:00' / 'пт 15:30'."""
    if not start:
        return ""
    import datetime as _dt  # noqa
    try:
        dt = _dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        today = _dt.date.today()
        diff = (dt.date() - today).days
        time_str = dt.strftime("%H:%M")
        if diff == 0:
            return f"сегодня {time_str}"
        if diff == 1:
            return f"завтра {time_str}"
        if diff == -1:
            return f"вчера {time_str}"
        days = ["пн","вт","ср","чт","пт","сб","вс"]
        if abs(diff) <= 7:
            return f"{days[dt.weekday()]} {time_str}"
        months = ["янв","фев","мар","апр","май","июн","июл","авг","сен","окт","ноя","дек"]
        return f"{dt.day} {months[dt.month-1]} {time_str}"
    except (ValueError, TypeError):
        return start


@router.get("/vault/mention")
async def vault_mention(q: str = "", limit: int = 8):
    """
    Быстрый поиск для автодополнения @mention в чате.
    Возвращает топ-N документов по BM25 или первые N если запрос пустой.
    """
    from personal_assistant.mlx_server.server import state  # noqa

    index = state.index
    if index is None:
        return {"docs": []}

    SECTION_ICON = {"calendar": "📅", "mail": "📧", "contacts": "👤"}

    if len(q) >= 2:
        docs = index.search(q, top_k=limit)
    else:
        # Show the most recent mails + events when query is empty
        recent = sorted(
            [d for d in index.docs if d.section in ("mail", "calendar")],
            key=lambda d: str(
                d.frontmatter.get("date") or d.frontmatter.get("start") or ""
            ),
            reverse=True,
        )
        docs = recent[:limit]

    return {
        "docs": [
            {
                "path": str(d.path),
                "title": d.title,
                "section": d.section,
                "type": str(d.frontmatter.get("type", d.section or "")),
                "date": str(
                    d.frontmatter.get("date") or d.frontmatter.get("start") or ""
                ),
                "from": str(
                    d.frontmatter.get("from") or d.frontmatter.get("sender_email") or ""
                ),
                "icon": SECTION_ICON.get(d.section or "", "📄"),
                "snippet": d.short_summary(80),
            }
            for d in docs
        ]
    }


@router.get("/vault/file")
async def vault_file(path: str):
    """Читать содержимое .md файла из vault."""
    from personal_assistant.config import settings as cfg

    file_path = Path(path)
    vault_root = cfg.vault_path.resolve()

    try:
        file_path.resolve().relative_to(vault_root)
    except ValueError:
        raise HTTPException(403, "Доступ запрещён: путь вне vault")

    if not file_path.exists():
        raise HTTPException(404, "Файл не найден")

    return {
        "path": str(file_path),
        "name": file_path.name,
        "content": file_path.read_text(encoding="utf-8", errors="replace"),
    }


class VaultFilePatch(BaseModel):
    content: str


@router.patch("/vault/file")
async def vault_patch(path: str, body: VaultFilePatch):
    """Перезаписать содержимое .md файла (сохранение из редактора)."""
    from personal_assistant.config import settings as cfg

    file_path = Path(path)
    vault_root = cfg.vault_path.resolve()

    try:
        file_path.resolve().relative_to(vault_root)
    except ValueError:
        raise HTTPException(403, "Доступ запрещён: путь вне vault")

    if not file_path.exists():
        raise HTTPException(404, "Файл не найден")

    file_path.write_text(body.content, encoding="utf-8")
    return {"status": "ok", "path": str(file_path)}


@router.delete("/vault/file")
async def vault_delete(path: str):
    """Удалить .md файл из vault."""
    from personal_assistant.config import settings as cfg

    file_path = Path(path)
    vault_root = cfg.vault_path.resolve()

    try:
        file_path.resolve().relative_to(vault_root)
    except ValueError:
        raise HTTPException(403, "Доступ запрещён: путь вне vault")

    if not file_path.exists():
        raise HTTPException(404, "Файл не найден")

    file_path.unlink()
    return {"status": "deleted", "path": str(file_path)}


@router.get("/vault/mail-thread/{thread_id}")
async def vault_mail_thread(thread_id: str):
    """
    Возвращает тред писем из VaultIndex (.md-файлы) по thread_id (12-символьный hex-хэш).

    Отличается от /api/v1/vault/threads/{tid} — тот читает из PersonalVault SQLite.
    Этот читает из VaultIndex, который заполняет sync pipeline.

    Response shape (совместим с vault.js openDetail rendering):
      {
        thread_id, root_subject, thread_message_count, participants,
        last_message_id,   ← internet message-id последнего письма (для Reply)
        items: [{date_iso, sender, full_body, attachments: [{filename}], message_id}]
      }
    """
    from personal_assistant.mlx_server.server import state  # noqa

    index = state.index
    if index is None:
        raise HTTPException(503, "Vault не загружен")

    tid = thread_id.strip()
    if not tid:
        raise HTTPException(400, "thread_id не может быть пустым")

    mail_docs = [
        d for d in index.docs
        if d.section == "mail" and str(d.frontmatter.get("thread_id", "")).strip() == tid
    ]
    if not mail_docs:
        raise HTTPException(404, f"Тред {tid} не найден в VaultIndex")

    # Sort chronologically
    mail_docs.sort(key=lambda d: str(d.date or ""))

    # Participants (unique, non-empty)
    seen_p: set[str] = set()
    participants: list[str] = []
    for d in mail_docs:
        p = str(d.frontmatter.get("sender_name") or d.frontmatter.get("from") or "").strip()
        if p and p not in seen_p:
            seen_p.add(p)
            participants.append(p)

    root_subject = str(mail_docs[0].frontmatter.get("title") or mail_docs[0].title or "")

    items = []
    for d in mail_docs:
        atts = d.frontmatter.get("attachments") or []
        if isinstance(atts, str):
            atts = [atts]
        items.append({
            "date_iso": str(d.date or ""),
            "sender": str(d.frontmatter.get("sender_name") or d.frontmatter.get("from") or ""),
            "full_body": d.content or "",
            "attachments": [{"filename": str(a)} for a in atts],
            "message_id": str(d.frontmatter.get("message_id") or ""),
        })

    last_message_id = items[-1]["message_id"] if items else ""

    return {
        "thread_id": tid,
        "root_subject": root_subject,
        "thread_message_count": len(items),
        "participants": participants,
        "last_message_id": last_message_id,
        "items": items,
    }


@router.get("/vault/tags")
async def vault_tags():
    """Все уникальные теги из vault."""
    from personal_assistant.mlx_server.server import state  # noqa

    index = state.index
    if index is None:
        return {"tags": []}

    tags: set[str] = set()
    for doc in index.docs:
        tags.update(doc.tags)

    return {"tags": sorted(tags)}


# ---------------------------------------------------------------------------
# Vault diagnostics
# ---------------------------------------------------------------------------


@router.get("/vault/diagnostics")
async def vault_diagnostics():
    """
    Диагностика vault: путь, существование, количество файлов, статус индекса.
    Используется в WebUI для отображения состояния vault пользователю.
    """
    from personal_assistant.config import settings as cfg
    from personal_assistant.mlx_server.server import state  # noqa

    vault = cfg.vault_path
    vault_exists = vault.exists()
    md_count = 0
    sections: dict[str, int] = {}

    if vault_exists:
        for f in vault.rglob("*.md"):
            md_count += 1
            rel = f.relative_to(vault)
            section = rel.parts[0] if len(rel.parts) > 1 else "root"
            sections[section] = sections.get(section, 0) + 1

    index = state.index
    index_loaded = index is not None
    index_doc_count = len(index.docs) if index_loaded else 0

    return {
        "vault_path": str(vault),
        "vault_exists": vault_exists,
        "md_count": md_count,
        "sections": sections,
        "index_loaded": index_loaded,
        "index_doc_count": index_doc_count,
        "ok": vault_exists and index_loaded and md_count > 0,
    }


# ---------------------------------------------------------------------------
# Settings API
# ---------------------------------------------------------------------------


@router.get("/settings")
async def get_settings():
    """Текущие настройки из конфига."""
    from personal_assistant.config import settings as cfg

    return {
        "mlx_model_path": cfg.mlx_model_path,
        "mlx_max_tokens": cfg.mlx_max_tokens,
        "mlx_temperature": cfg.mlx_temperature,
        "mlx_context_chars": cfg.mlx_context_chars,
        "embedding_model": cfg.embedding_model,
        "embedding_model_path": cfg.embedding_model_path,
        "embedding_ssl_verify": cfg.embedding_ssl_verify,
        "hf_token": cfg.hf_token,
        "vault_path": str(cfg.vault_path),
        "calendar_days_back": cfg.calendar_days_back,
        "calendar_days_forward": cfg.calendar_days_forward,
        "mail_days_back": cfg.mail_days_back,
        "schedule_enabled": cfg.schedule_enabled,
        "schedule_cron": cfg.schedule_cron,
        "log_level": cfg.log_level,
    }


class SettingsUpdate(BaseModel):
    mlx_model_path: Optional[str] = None
    mlx_max_tokens: Optional[int] = None
    mlx_temperature: Optional[float] = None
    mlx_context_chars: Optional[int] = None
    embedding_model: Optional[str] = None
    embedding_model_path: Optional[str] = None
    embedding_ssl_verify: Optional[bool] = None
    hf_token: Optional[str] = None
    vault_path: Optional[str] = None
    calendar_days_back: Optional[int] = None
    calendar_days_forward: Optional[int] = None
    mail_days_back: Optional[int] = None
    schedule_enabled: Optional[bool] = None
    schedule_cron: Optional[str] = None
    log_level: Optional[str] = None


_ENV_KEY_MAP = {
    "mlx_model_path": "PA_MLX_MODEL_PATH",
    "mlx_max_tokens": "PA_MLX_MAX_TOKENS",
    "mlx_temperature": "PA_MLX_TEMPERATURE",
    "mlx_context_chars": "PA_MLX_CONTEXT_CHARS",
    "embedding_model": "PA_EMBEDDING_MODEL",
    "embedding_model_path": "PA_EMBEDDING_MODEL_PATH",
    "embedding_ssl_verify": "PA_EMBEDDING_SSL_VERIFY",
    "hf_token": "PA_HF_TOKEN",
    "vault_path": "PA_VAULT_PATH",
    "calendar_days_back": "PA_CALENDAR_DAYS_BACK",
    "calendar_days_forward": "PA_CALENDAR_DAYS_FORWARD",
    "mail_days_back": "PA_MAIL_DAYS_BACK",
    "schedule_enabled": "PA_SCHEDULE_ENABLED",
    "schedule_cron": "PA_SCHEDULE_CRON",
    "log_level": "PA_LOG_LEVEL",
}


@router.post("/settings")
async def save_settings(update: SettingsUpdate):
    """Сохранить настройки в ``.env`` И применить к живому объекту settings.

    Ранее ручка только писала в ``.env`` через ``set_key`` и сообщала
    «применятся после перезапуска» — это ломало UI-сценарий смены
    ``mlx_model_path``: пользователь меняет путь, видит «saved», а
    инференс продолжает грузиться со старого. Теперь:

      1. Пишем в ``.env`` (сохраняется на следующий запуск).
      2. Применяем к ``settings`` через ``setattr`` (живой процесс
         видит новое значение немедленно).
      3. Если поменялся ``mlx_model_path`` — сбрасываем MLXEngine,
         чтобы следующий /chat использовал новую модель.
    """
    from dotenv import set_key
    from personal_assistant.config import settings as live_cfg

    env_file = _ENV_FILE
    if not env_file.exists():
        env_file.touch()

    old_mlx = live_cfg.mlx_model_path
    saved: dict = {}
    for field, env_key in _ENV_KEY_MAP.items():
        value = getattr(update, field)
        if value is None:
            continue
        # Persist to .env so the value survives a restart
        set_key(str(env_file), env_key, str(value))
        saved[field] = value
        # Apply to the live settings instance.  We bypass settings.update()
        # because not every key in _ENV_KEY_MAP is in EDITABLE_FIELDS
        # (e.g. vault_path, log_level) — direct setattr keeps it simple.
        if hasattr(live_cfg, field):
            try:
                # Cast obvious numeric / bool types
                current = getattr(live_cfg, field)
                if isinstance(current, bool):
                    coerced: Any = value if isinstance(value, bool) else (str(value).lower() in ("1","true","yes","on"))
                elif isinstance(current, int) and not isinstance(current, bool):
                    coerced = int(value)
                elif isinstance(current, float):
                    coerced = float(value)
                else:
                    coerced = value
                setattr(live_cfg, field, coerced)
            except Exception:
                pass  # malformed input — .env still gets it for restart

    # If mlx_model_path actually changed — drop the engine's cached model.
    mlx_reloaded = False
    if "mlx_model_path" in saved and saved["mlx_model_path"] != old_mlx:
        try:
            from personal_assistant.mlx_server import server as _srv
            engine = getattr(_srv.state, "engine", None)
            if engine is not None and hasattr(engine, "reload"):
                engine.reload()
                mlx_reloaded = True
        except Exception:  # noqa: BLE001 — never fail settings on engine
            pass

    return {
        "status": "ok",
        "saved": saved,
        "mlx_reloaded": mlx_reloaded,
        "note": (
            "Настройки сохранены в .env и применены к текущему процессу. "
            "vault_path и log_level всё ещё требуют перезапуска для полного эффекта."
        ),
    }


# ---------------------------------------------------------------------------
# Schedule status API
# ---------------------------------------------------------------------------


@router.get("/schedule/status")
async def schedule_status():
    """Вернуть статус планировщика и время следующего запуска."""
    from personal_assistant.config import settings as cfg

    enabled = cfg.schedule_enabled
    cron = cfg.schedule_cron
    next_run: _Optional[str] = None

    if enabled:
        try:
            from datetime import datetime, timezone

            from apscheduler.triggers.cron import CronTrigger

            trigger = CronTrigger.from_crontab(cron, timezone="UTC")
            fire_time = trigger.get_next_fire_time(None, datetime.now(timezone.utc))
            next_run = fire_time.isoformat() if fire_time else None
        except Exception:
            next_run = None

    return {
        "enabled": enabled,
        "cron": cron,
        "next_run": next_run,
    }


# ---------------------------------------------------------------------------
# Tool Prompts API  (GET /tool-prompts, POST /tool-prompts)
# ---------------------------------------------------------------------------


class DelegateContactBody(BaseModel):
    name: str = ""
    email: str
    role: str = ""
    note: str = ""


class ToolPromptsBody(BaseModel):
    draft_system: str = ""
    summarize_system: str = ""
    delegate_system: str = ""
    delegate_contacts: list[DelegateContactBody] = []


@router.get("/tool-prompts")
async def get_tool_prompts_api():
    """Вернуть текущие промпты тулов (пользовательские + дефолты для UI).

    Plus the delegate-tool config: system prompt and the list of colleagues
    the user can forward / delegate emails to.  The list is rendered both in
    Rules → Инструменты (editor) and in the Inbox assistant panel (picker).
    """
    from personal_assistant.config import settings as _cfg
    from personal_assistant.services.tool_prompts import (
        _PROMPTS_FILENAME,
        DEFAULT_DELEGATE_SYSTEM,
        DEFAULT_DRAFT_SYSTEM,
        DEFAULT_SUMMARIZE_SYSTEM,
        get_tool_prompts,
    )

    p = get_tool_prompts()
    prompts_file = str(Path(_cfg.vault_path) / _PROMPTS_FILENAME)
    return {
        # Saved user overrides (empty string when no custom is set). This
        # contract is consumed by older tests; keep it stable.
        "draft_system":               p.draft_system,
        "summarize_system":           p.summarize_system,
        "delegate_system":            p.delegate_system,
        "delegate_contacts":          [
            {"name": c.name, "email": c.email, "role": c.role, "note": c.note}
            for c in p.delegate_contacts
        ],
        # Effective text shown in the textarea: user override if set, else
        # the built-in default. The UI displays the actual content so the
        # user can SEE and edit the default in place.
        "effective_draft_system":     p.draft_system or DEFAULT_DRAFT_SYSTEM,
        "effective_summarize_system": p.summarize_system or DEFAULT_SUMMARIZE_SYSTEM,
        "effective_delegate_system":  p.delegate_system or DEFAULT_DELEGATE_SYSTEM,
        # Whether the effective text is the built-in default (badge in UI).
        "draft_is_default":           not p.draft_system.strip(),
        "summarize_is_default":       not p.summarize_system.strip(),
        "delegate_is_default":        not p.delegate_system.strip(),
        # Defaults exposed verbatim so the UI can compute "Reset to default"
        # and detect "user edited content".
        "default_draft_system":       DEFAULT_DRAFT_SYSTEM,
        "default_summarize_system":   DEFAULT_SUMMARIZE_SYSTEM,
        "default_delegate_system":    DEFAULT_DELEGATE_SYSTEM,
        # File-path alias (frontend reads ``file_path``; keep
        # ``prompts_file_path`` for backwards-compat).
        "file_path":                  prompts_file,
        "prompts_file_path":          prompts_file,
        "max_prompt_len":             8_000,
    }


@router.post("/tool-prompts")
async def save_tool_prompts_api(body: ToolPromptsBody):
    """Сохранить пользовательские промпты тулов (с валидацией)."""
    from personal_assistant.services.tool_prompts import (
        DelegateContact,
        PromptValidationError,
        ToolPrompts,
        invalidate_cache,
        save_tool_prompts,
        validate_prompt,
    )

    try:
        draft_clean     = validate_prompt(body.draft_system,     "draft_system")
        summarize_clean = validate_prompt(body.summarize_system, "summarize_system")
        delegate_clean  = validate_prompt(body.delegate_system,  "delegate_system")
    except PromptValidationError as exc:
        raise HTTPException(422, str(exc))

    # Validate / clean each delegate contact.  Reject duplicates by email
    # (case-insensitive) — the UI picker must show each colleague once.
    contacts: list[DelegateContact] = []
    seen_emails: set[str] = set()
    for c in body.delegate_contacts:
        email = (c.email or "").strip()
        if not email or "@" not in email:
            continue
        key = email.lower()
        if key in seen_emails:
            continue
        seen_emails.add(key)
        contacts.append(DelegateContact(
            name=(c.name or email.split("@")[0]).strip()[:120],
            email=email[:200],
            role=(c.role or "").strip()[:120],
            note=(c.note or "").strip()[:300],
        ))

    prompts = ToolPrompts(
        draft_system=draft_clean,
        summarize_system=summarize_clean,
        delegate_system=delegate_clean,
        delegate_contacts=contacts,
    )
    save_tool_prompts(prompts)
    invalidate_cache()
    return {"status": "ok", "delegate_contacts_count": len(contacts)}


# ---------------------------------------------------------------------------
# Classify config API
# ---------------------------------------------------------------------------


def _classify_yaml_path() -> Path:
    from personal_assistant.config import settings as cfg

    return cfg.classify_config_file


class ClassifyConfigBody(BaseModel):
    yaml_text: str


@router.get("/classify/config")
async def get_classify_config():
    """Читать classify.yaml — возвращает raw YAML + разобранную структуру."""
    import yaml

    path = _classify_yaml_path()
    if path.exists():
        raw = path.read_text(encoding="utf-8")
        try:
            parsed = yaml.safe_load(raw) or {}
        except Exception as e:
            parsed = {}
            raw = f"# Ошибка парсинга YAML: {e}\n" + raw
    else:
        # Возвращаем дефолтный шаблон
        import yaml as _yaml

        from personal_assistant.mlx_server.tasks.classify import DEFAULT_CONFIG

        raw = _yaml.dump(DEFAULT_CONFIG, allow_unicode=True, default_flow_style=False)
        parsed = DEFAULT_CONFIG

    return {
        "yaml_text": raw,
        "parsed": parsed,
        "path": str(path),
        "exists": path.exists(),
    }


@router.put("/classify/config")
async def save_classify_config(body: ClassifyConfigBody):
    """Сохранить classify.yaml (валидируем YAML перед записью)."""
    import yaml

    # Validate
    try:
        yaml.safe_load(body.yaml_text)
    except Exception as e:
        raise HTTPException(400, f"Некорректный YAML: {e}")

    path = _classify_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.yaml_text, encoding="utf-8")
    return {"status": "ok", "path": str(path)}


@router.get("/classify/labels")
async def get_classify_labels():
    """Плоский список меток из classify.yaml (для фильтров Vault и Search)."""
    import yaml

    path = _classify_yaml_path()
    if not path.exists():
        from personal_assistant.mlx_server.tasks.classify import DEFAULT_CONFIG

        cfg = DEFAULT_CONFIG
    else:
        try:
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            cfg = {}

    labels: list[str] = []
    for classifier_name, labels_dict in (cfg.get("classifiers") or {}).items():
        if isinstance(labels_dict, dict):
            for label_name in labels_dict:
                labels.append(f"{classifier_name}:{label_name}")

    return {"labels": labels}


@router.post("/classify/apply")
async def classify_apply():
    """
    Применить classify.yaml к текущему vault (без MLX, только keyword-matching).
    Записывает теги в frontmatter каждого документа mail/calendar.
    """
    import yaml

    from personal_assistant.mlx_server.server import state  # noqa
    from personal_assistant.mlx_server.tasks.classify import (
        DEFAULT_CONFIG,
        classify_vault,
    )

    index = state.index
    if index is None:
        return {"status": "error", "message": "Vault index not loaded"}

    path = _classify_yaml_path()
    if not path.exists():
        config = DEFAULT_CONFIG
    else:
        try:
            config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return {"status": "error", "message": f"Ошибка чтения classify.yaml: {exc}"}

    try:
        result = classify_vault(
            index=index, config=config, engine=None, write_tags=True
        )
        # Reload vault index so updated tags are immediately visible in UI and search
        state.reload_index()
        skipped = result.total - result.classified
        return {
            "status": "ok",
            "total": result.total,
            "classified": result.classified,
            "skipped": skipped,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.delete("/classify/tags")
async def classify_reset_tags():
    """
    Сбросить все теги-классификаторы из frontmatter vault-документов.
    Удаляет только теги вида «classifier:label» (содержат двоеточие).
    Возвращает количество изменённых файлов.
    """
    import yaml as _yaml

    from personal_assistant.config import settings as cfg
    from personal_assistant.mlx_server.server import state  # noqa

    vault = cfg.vault_path
    if not vault.exists():
        raise HTTPException(503, "Vault не найден")

    changed = 0
    errors: list[str] = []

    for md_file in vault.rglob("*.md"):
        try:
            raw = md_file.read_text(encoding="utf-8", errors="replace")
            # Parse YAML frontmatter
            if not raw.startswith("---"):
                continue
            end = raw.find("\n---", 3)
            if end == -1:
                continue
            fm_text = raw[3:end]
            body = raw[end + 4 :]

            try:
                fm = _yaml.safe_load(fm_text) or {}
            except Exception:
                continue

            tags: list = fm.get("tags", []) or []
            if not isinstance(tags, list):
                tags = [tags]

            # Remove classifier tags (contain ":")
            new_tags = [t for t in tags if ":" not in str(t)]
            if new_tags == tags:
                continue  # nothing to remove

            fm["tags"] = new_tags
            new_fm = _yaml.dump(
                fm, allow_unicode=True, default_flow_style=False
            ).rstrip()
            new_raw = f"---\n{new_fm}\n---{body}"
            md_file.write_text(new_raw, encoding="utf-8")
            changed += 1
        except Exception as exc:
            errors.append(f"{md_file.name}: {exc}")

    # Reload index so changes are visible immediately
    try:
        state.reload_index()
    except Exception:
        pass

    return {
        "status": "ok",
        "changed": changed,
        "errors": errors,
    }


@router.post("/classify/llm-batch")
async def classify_llm_batch(background_tasks: BackgroundTasks):
    """Stage 8: Run LLM-assisted semantic classification on vault docs with low rule confidence.

    Scans all mail/outlook/calendar .md files, computes rule confidence,
    and invokes the MLX engine for docs below the configured threshold.
    Results are cached in data/llm_classify_cache.json.

    Returns immediately; the batch runs in the background.
    """
    import yaml as _yaml

    from personal_assistant.config import settings as cfg
    from personal_assistant.mlx_server.server import state  # noqa
    from personal_assistant.mlx_server.tasks.llm_classify_service import (
        LLMClassifyCache,
        batch_llm_classify_vault,
    )

    # Test mode: batch_llm_classify_vault iterates the entire vault and calls
    # the MLX engine — TestClient awaits BackgroundTasks inline.
    if cfg.e2e_test_mode:
        return {
            "status": "started",
            "threshold": 0.0,
            "batch_size": 0,
            "engine_ready": False,
            "message": "e2e_test_mode: пропущено",
            "e2e": True,
        }

    vault = cfg.vault_path
    if not vault.exists():
        raise HTTPException(503, "Vault не найден")

    path = _classify_yaml_path()
    if path.exists():
        try:
            config = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            return {"status": "error", "message": f"Ошибка чтения classify.yaml: {exc}"}
    else:
        from personal_assistant.mlx_server.tasks.classify import DEFAULT_CONFIG
        config = DEFAULT_CONFIG

    llm_cfg = config.get("llm_classify", {})
    if not llm_cfg.get("enabled", False):
        return {
            "status": "disabled",
            "message": "llm_classify.enabled = false в classify.yaml",
        }

    engine = state.engine  # None if MLX not loaded — batch will skip LLM calls
    threshold = float(llm_cfg.get("threshold", 0.4))
    batch_size = int(llm_cfg.get("batch_size", 5))
    cache = LLMClassifyCache()

    def _run():
        try:
            result = batch_llm_classify_vault(
                vault_path=vault,
                engine=engine,
                config=config,
                threshold=threshold,
                batch_size=batch_size,
                cache=cache,
            )
            # Reload index so new ai_classified tags are visible
            try:
                state.reload_index()
            except Exception:
                pass
            logger.info(f"LLM batch classify done: {result.to_dict()}")
        except Exception as exc:
            logger.error(f"LLM batch classify error: {exc}")

    background_tasks.add_task(_run)

    return {
        "status": "started",
        "threshold": threshold,
        "batch_size": batch_size,
        "engine_ready": engine is not None,
        "message": "LLM классификация запущена в фоне",
    }


@router.get("/classify/stats")
async def classify_stats():
    """Stage 8: Return classification statistics for the vault.

    Reports total doc count, AI-classified count, and category distribution.
    """
    from personal_assistant.config import settings as cfg
    from personal_assistant.mlx_server.tasks.llm_classify_service import (
        LLMClassifyCache,
        get_classify_stats,
    )

    vault = cfg.vault_path
    if not vault.exists():
        return {"status": "error", "message": "Vault not found", "total_docs": 0}

    cache = LLMClassifyCache()
    stats = get_classify_stats(vault, cache)
    return {"status": "ok", **stats}


@router.get("/vault/contacts")
async def vault_contacts(limit: int = 100):
    """Список контактов для пикера в редакторе classify.yaml."""
    from personal_assistant.mlx_server.server import state  # noqa

    index = state.index
    if index is None:
        return {"contacts": []}

    contacts = index.get_contacts()[:limit]
    return {
        "contacts": [
            {
                "email": str(d.frontmatter.get("email", "") or d.path.stem),
                "name": d.title,
            }
            for d in contacts
        ]
    }


# ---------------------------------------------------------------------------
# Projects API (stored in data/projects.json)
# ---------------------------------------------------------------------------

_PROJECTS_FILE = _PROJECT_ROOT / "data" / "projects.json"


def _load_projects() -> list:
    if _PROJECTS_FILE.exists():
        try:
            return _json.loads(_PROJECTS_FILE.read_text(encoding="utf-8")) or []
        except Exception:
            return []
    return []


def _save_projects(projects: list) -> None:
    _PROJECTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROJECTS_FILE.write_text(
        _json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class ProjectBody(BaseModel):
    name: str
    description: str = ""
    status: str = "active"  # active | done | paused
    deadline: Optional[str] = None
    goals: list = []


@router.get("/projects")
async def get_projects():
    return {"projects": _load_projects()}


@router.post("/projects")
async def create_project(body: ProjectBody):
    projects = _load_projects()
    p = {
        "id": str(_uuid.uuid4())[:8],
        **body.model_dump(),
        "progress": 0,
        "created_at": str(_date.today()),
    }
    projects.append(p)
    _save_projects(projects)
    return p


@router.put("/projects/{project_id}")
async def update_project(project_id: str, body: ProjectBody):
    projects = _load_projects()
    for i, p in enumerate(projects):
        if p["id"] == project_id:
            goals = body.goals or p.get("goals", [])
            done = sum(1 for g in goals if g.get("done"))
            pct = round(done / len(goals) * 100) if goals else 0
            projects[i] = {**p, **body.model_dump(), "progress": pct}
            _save_projects(projects)
            return {"ok": True}
    raise HTTPException(status_code=404, detail="Project not found")


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    projects = [p for p in _load_projects() if p["id"] != project_id]
    _save_projects(projects)
    return {"ok": True}


@router.get("/projects/{project_id}/goals")
async def get_project_goals(project_id: str):
    projects = _load_projects()
    p = next((p for p in projects if p["id"] == project_id), None)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"goals": p.get("goals", [])}


class GoalBody(BaseModel):
    title: str
    done: bool = False
    quadrant: str = "q2"
    deadline: Optional[str] = None  # ISO date or label: today/tomorrow/this_week


@router.post("/projects/{project_id}/goals")
async def add_project_goal(project_id: str, body: GoalBody):
    projects = _load_projects()
    for i, p in enumerate(projects):
        if p["id"] == project_id:
            goal = {"id": str(_uuid.uuid4())[:8], **body.model_dump()}
            projects[i].setdefault("goals", []).append(goal)
            _save_projects(projects)
            return goal
    raise HTTPException(status_code=404, detail="Project not found")


@router.put("/projects/{project_id}/goals/{goal_id}")
async def update_project_goal(project_id: str, goal_id: str, body: GoalBody):
    projects = _load_projects()
    for i, p in enumerate(projects):
        if p["id"] == project_id:
            goals = p.get("goals", [])
            for j, g in enumerate(goals):
                if g["id"] == goal_id:
                    goals[j] = {**g, **body.model_dump()}
                    projects[i]["goals"] = goals
                    _save_projects(projects)
                    return {"ok": True}
    raise HTTPException(status_code=404, detail="Goal not found")


@router.delete("/projects/{project_id}/goals/{goal_id}")
async def delete_project_goal(project_id: str, goal_id: str):
    """Удалить цель из проекта."""
    projects = _load_projects()
    for i, p in enumerate(projects):
        if p["id"] == project_id:
            goals = [g for g in p.get("goals", []) if g["id"] != goal_id]
            done = sum(1 for g in goals if g.get("done"))
            pct = round(done / len(goals) * 100) if goals else 0
            projects[i]["goals"] = goals
            projects[i]["progress"] = pct
            _save_projects(projects)
            return {"ok": True, "goals": goals, "progress": pct}
    raise HTTPException(status_code=404, detail="Goal not found")


@router.get("/projects/{project_id}/related")
async def get_project_related(project_id: str):
    """Вернуть связанные данные: письма, встречи, контакты, чат-треды из vault."""
    projects = _load_projects()
    p = next((p for p in projects if p["id"] == project_id), None)
    if not p:
        raise HTTPException(404, "Project not found")

    vault_items = p.get("vault_items", [])
    contacts = p.get("contacts", [])
    name_lower = (p.get("name") or "").lower()

    # Categorise vault items
    mails: list = []
    meetings: list = []
    for vpath in vault_items:
        base = vpath.lower()
        if "outlook" in base or "mail" in base or "email" in base:
            mails.append(vpath)
        elif "calendar" in base or "meeting" in base or "event" in base:
            meetings.append(vpath)
        else:
            mails.append(vpath)  # treat unknown as mail

    # Try to enrich from vault index if available
    try:
        from personal_assistant.mlx_server.server import state  # noqa: PLC0415
        idx = state.index
        if idx:
            from personal_assistant.mlx_server.vault_index import VaultIndex  # noqa: PLC0415
            if isinstance(idx, VaultIndex):
                # Search vault for items matching project name
                hits = idx.search(name_lower, sections=[], top_k=20) if name_lower else []
                for h in hits:
                    fm = h.frontmatter
                    src = str(fm.get("source", ""))
                    hpath = str(h.path)
                    if hpath not in vault_items:
                        if src in ("outlook", "mail") or "mail" in hpath.lower():
                            if len(mails) < 20:
                                mails.append(hpath)
                        elif src in ("calendar",) or "calendar" in hpath.lower():
                            if len(meetings) < 20:
                                meetings.append(hpath)
    except Exception:
        pass

    # Chat threads that reference the project
    chat_threads: list = []
    try:
        from personal_assistant.mlx_server.chat_db import (  # noqa: PLC0415
            get_messages,
            list_threads,
        )
        for th in list_threads():
            for m in get_messages(th.id):
                content = m.content or ""
                if name_lower and name_lower in content.lower():
                    chat_threads.append({"id": th.id, "preview": content[:80]})
                    break
    except Exception:
        pass

    # Summarise contact focus
    contact_names = []
    for c in contacts[:5]:
        n = c.get("name") or c.get("email") or str(c) if isinstance(c, dict) else str(c)
        contact_names.append(n)

    return {
        "mails": mails[:12],
        "meetings": meetings[:4],
        "contacts": contacts[:10],
        "contact_names": contact_names,
        "chat_threads": chat_threads[:5],
        "mail_count": len(mails),
        "meeting_count": len(meetings),
        "contact_count": len(contacts),
        "thread_count": len(chat_threads),
    }


@router.post("/projects/{project_id}/suggest-goal")
async def suggest_project_goal(project_id: str):
    """AI предлагает следующую цель на основе текущих целей и названия проекта."""
    projects = _load_projects()
    p = next((p for p in projects if p["id"] == project_id), None)
    if not p:
        raise HTTPException(404, "Project not found")

    goals_done = [g["title"] for g in p.get("goals", []) if g.get("done")]
    goals_todo = [g["title"] for g in p.get("goals", []) if not g.get("done")]

    prompt = (
        f"Проект: {p.get('name', '')}\n"
        f"Описание: {p.get('description', '')}\n"
        f"Завершённые цели: {', '.join(goals_done) or 'нет'}\n"
        f"Активные цели: {', '.join(goals_todo) or 'нет'}\n"
        f"Предложи одну следующую конкретную цель (1–2 предложения) для этого проекта. "
        f"Ответь только текстом цели, без пояснений."
    )

    suggested_title = ""
    try:
        from personal_assistant.mlx_server.server import state  # noqa: PLC0415
        engine = state.engine
        if engine and engine.is_loaded:
            for chunk in engine.stream(messages=[{"role": "user", "content": prompt}],
                                       max_tokens=80):
                suggested_title += chunk
        else:
            # Fallback: deterministic suggestion based on existing goals
            suggested_title = _fallback_goal_suggestion(p)
    except Exception:
        suggested_title = _fallback_goal_suggestion(p)

    suggested_title = suggested_title.strip()
    return {"title": suggested_title or "Подготовить итоговый отчёт"}


def _fallback_goal_suggestion(p: dict) -> str:
    """Детерминированная подсказка цели без LLM."""
    goals = p.get("goals", [])
    done_count = sum(1 for g in goals if g.get("done"))
    total = len(goals)
    name = p.get("name", "проекта")
    if total == 0:
        return f"Определить основные задачи {name}"
    if done_count == 0:
        return f"Составить план действий по {name}"
    if done_count == total:
        return f"Подготовить итоговый отчёт по {name}"
    pending = [g["title"] for g in goals if not g.get("done")]
    return f"Завершить: {pending[0]}" if pending else f"Проверить результаты {name}"


@router.get("/projects/{project_id}/assistant-suggests")
async def project_assistant_suggests(project_id: str):
    """Проактивная AI-подсказка: что сделать прямо сейчас по проекту."""
    projects = _load_projects()
    p = next((p for p in projects if p["id"] == project_id), None)
    if not p:
        raise HTTPException(404, "Project not found")

    import datetime as _dt  # noqa: PLC0415
    today = _dt.date.today()
    deadline_str = p.get("deadline") or ""
    goals_todo = [g for g in p.get("goals", []) if not g.get("done")]

    suggestion = ""
    action = ""

    if deadline_str:
        try:
            deadline = _dt.date.fromisoformat(deadline_str[:10])
            days_left = (deadline - today).days
            if 0 <= days_left <= 1 and goals_todo:
                next_goal = goals_todo[0]["title"]
                suggestion = f"Дедлайн завтра! Начните с: «{next_goal}» прямо сейчас."
                action = "open_chat"
            elif 1 < days_left <= 3 and goals_todo:
                next_goal = goals_todo[0]["title"]
                deadline_fmt = deadline.strftime("%-d %b")
                suggestion = (
                    f"Забронируйте слот до {deadline_fmt} для работы над "
                    f"«{next_goal}»."
                )
                action = "book_slot"
        except ValueError:
            pass

    if not suggestion and goals_todo:
        next_goal = goals_todo[0]["title"]
        suggestion = f"Следующий шаг: завершите «{next_goal}»."
        action = "open_chat"

    if not suggestion:
        suggestion = f"Проект «{p.get('name', '')}» завершён. Подготовьте итоговый отчёт."
        action = "summarize"

    return {
        "suggestion": suggestion,
        "action": action,
        "project_id": project_id,
    }


# ---------------------------------------------------------------------------
# Souls.md API
# ---------------------------------------------------------------------------
_SOULS_FILE = _PROJECT_ROOT / "souls.md"


@router.get("/souls")
async def get_souls():
    content = _SOULS_FILE.read_text(encoding="utf-8") if _SOULS_FILE.exists() else ""
    return {"content": content}


class SoulsBody(BaseModel):
    content: str


@router.put("/souls")
async def save_souls(body: SoulsBody):
    _SOULS_FILE.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": str(_SOULS_FILE)}


# ---------------------------------------------------------------------------
# Persona API  (structured user + assistant identity, stored in data/persona.json)
# ---------------------------------------------------------------------------
_PERSONA_FILE = _PROJECT_ROOT / "data" / "persona.json"

_DEFAULT_PERSONA = {
    "user_name": "",
    "user_role": "",
    "user_language": "ru",
    "assistant_name": "Ассистент",
    "assistant_style": "профессиональный и краткий",
    "assistant_focus": "",
}


def _load_persona() -> dict:
    # Backward-compat: read from new profile.json if available
    from personal_assistant.profile.service import load_legacy_persona
    return load_legacy_persona()


@router.get("/persona")
async def get_persona():
    return _load_persona()


class PersonaBody(BaseModel):
    user_name: Optional[str] = None
    user_role: Optional[str] = None
    user_language: Optional[str] = None
    assistant_name: Optional[str] = None
    assistant_style: Optional[str] = None
    assistant_focus: Optional[str] = None


@router.put("/persona")
async def save_persona(body: PersonaBody):
    from personal_assistant.profile.service import save_legacy_persona
    save_legacy_persona(body.model_dump(exclude_none=True))
    return {"ok": True, "persona": _load_persona()}


# ---------------------------------------------------------------------------
# Tools registry API
# ---------------------------------------------------------------------------
_TOOLS_FILE = _PROJECT_ROOT / "tools" / "registry.json"


@router.get("/tools")
async def get_tools():
    if _TOOLS_FILE.exists():
        return _json.loads(_TOOLS_FILE.read_text(encoding="utf-8"))
    return {"tools": []}


class ToolToggle(BaseModel):
    enabled: bool


@router.put("/tools/{tool_id}")
async def toggle_tool(tool_id: str, body: ToolToggle):
    if not _TOOLS_FILE.exists():
        raise HTTPException(status_code=404, detail="registry.json not found")
    data = _json.loads(_TOOLS_FILE.read_text(encoding="utf-8"))
    for t in data.get("tools", []):
        if t["id"] == tool_id:
            t["enabled"] = body.enabled
    _TOOLS_FILE.write_text(
        _json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# GTD Rules API
# ---------------------------------------------------------------------------
_GTD_FILE = _PROJECT_ROOT / "data" / "gtd_rules.json"


@router.get("/gtd-rules")
async def get_gtd_rules():
    if _GTD_FILE.exists():
        return _json.loads(_GTD_FILE.read_text(encoding="utf-8"))
    return {"rules": []}


class GtdRulesBody(BaseModel):
    rules: list


@router.put("/gtd-rules")
async def save_gtd_rules(body: GtdRulesBody):
    _GTD_FILE.parent.mkdir(parents=True, exist_ok=True)
    _GTD_FILE.write_text(
        _json.dumps(body.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Eisenhower matrix API
# ---------------------------------------------------------------------------
_EISENHOWER_FILE = _PROJECT_ROOT / "data" / "eisenhower.json"


@router.get("/eisenhower")
async def get_eisenhower():
    if _EISENHOWER_FILE.exists():
        return _json.loads(_EISENHOWER_FILE.read_text(encoding="utf-8"))
    return {"tasks": []}


class EisenhowerBody(BaseModel):
    tasks: list


@router.put("/eisenhower")
async def save_eisenhower(body: EisenhowerBody):
    _EISENHOWER_FILE.parent.mkdir(parents=True, exist_ok=True)
    _EISENHOWER_FILE.write_text(
        _json.dumps(body.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True}


# =============================================================================
# Test-data generator + rollback
# =============================================================================

_SNAPSHOTS_DIR = _PROJECT_ROOT / "data" / "snapshots"
_TESTDATA_MARKER = "testdata_generated"  # frontmatter tag added to every generated file


# ── helpers ──────────────────────────────────────────────────────────────────


def _vault_root() -> Path:
    from personal_assistant.config import settings as _cfg

    return _cfg.vault_path


def _snap_id(label: str) -> str:
    ts = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M%S")
    return f"{ts}_{label}"


def _create_snapshot(snap_id: str) -> Path:
    """Zip vault + data/projects.json into a snapshot archive."""
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    archive = _SNAPSHOTS_DIR / f"{snap_id}.zip"
    vault = _vault_root()
    with _zipfile.ZipFile(archive, "w", _zipfile.ZIP_DEFLATED) as zf:
        if vault.exists():
            for f in vault.rglob("*.md"):
                zf.write(f, f.relative_to(vault.parent))
        if _PROJECTS_FILE.exists():
            zf.write(_PROJECTS_FILE, _PROJECTS_FILE.relative_to(_PROJECT_ROOT))
    return archive


def _restore_snapshot(archive: Path) -> None:
    """Remove generated files then restore vault + projects from archive."""
    vault = _vault_root()
    # Delete only files tagged as generated
    if vault.exists():
        for f in list(vault.rglob("*.md")):
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
                if _TESTDATA_MARKER in txt:
                    f.unlink()
            except Exception:
                pass
    # Delete generated projects
    projects = _load_projects()
    projects = [p for p in projects if not p.get("_testdata")]
    _save_projects(projects)
    # Restore from archive
    with _zipfile.ZipFile(archive, "r") as zf:
        zf.extractall(_PROJECT_ROOT)


# ── sample data templates ─────────────────────────────────────────────────────

_SAMPLE_EVENTS = [
    {
        "uid": "test-evt-001",
        "title": "Синк с командой по Q3",
        "start_offset_days": -2,
        "duration_h": 1,
        "location": "Zoom",
        "calendar": "Работа",
        "attendees": ["ivan.petrov@company.ru", "anna.sidorova@company.ru"],
        "notes": "Обсуждение OKR на третий квартал, распределение задач.",
    },
    {
        "uid": "test-evt-002",
        "title": "Демо продукта клиенту",
        "start_offset_days": 1,
        "duration_h": 2,
        "location": "Офис, переговорка 3",
        "calendar": "Работа",
        "attendees": ["client@bigcorp.com"],
        "notes": "Показать новый UI, ответить на вопросы по API.",
    },
    {
        "uid": "test-evt-003",
        "title": "1:1 с руководителем",
        "start_offset_days": 3,
        "duration_h": 1,
        "location": None,
        "calendar": "Личное",
        "attendees": [],
        "notes": "Обсудить карьерный план и результаты квартала.",
    },
    {
        "uid": "test-evt-004",
        "title": "Ретроспектива спринта",
        "start_offset_days": -5,
        "duration_h": 2,
        "location": "Teams",
        "calendar": "Работа",
        "attendees": ["dev1@company.ru", "dev2@company.ru", "pm@company.ru"],
        "notes": "Что прошло хорошо, что нет. Action items на следующий спринт.",
    },
    {
        "uid": "test-evt-005",
        "title": "Медицинский осмотр",
        "start_offset_days": 7,
        "duration_h": 1,
        "location": "Клиника Здоровье, каб. 12",
        "calendar": "Личное",
        "attendees": [],
        "notes": "",
    },
]

_SAMPLE_MAILS: list[dict[str, Any]] = [
    {
        "mid": "test-mail-001@example.com",
        "subject": "Предложение о партнёрстве",
        "sender_name": "Алексей Громов",
        "sender_email": "alexey.gromov@partner.ru",
        "mailbox": "Входящие",
        "days_ago": 1,
        "body": (
            "Добрый день! Хотел бы обсудить возможное партнёрство наших компаний. "
            "Мы занимаемся разработкой ML-решений и видим синергию с вашими продуктами. "
            "Буду рад созвониться на этой неделе."
        ),
    },
    # ── Thread: «Отчёт за май» — 3 сообщения ─────────────────────────────────
    {
        "mid": "test-mail-006@example.com",
        "subject": "Отчёт за май",
        "sender_name": "Игорь Манякин",
        "sender_email": "igor@company.ru",
        "mailbox": "Отправленные",
        "days_ago": 5,
        "body": (
            "Коллеги, направляю финальную версию отчёта за май. "
            "Прошу проверить раздел 3 по конверсии и подтвердить цифры. "
            "Жду комментарии до пятницы."
        ),
    },
    {
        "mid": "test-mail-002@example.com",
        "subject": "Re: Отчёт за май",
        "sender_name": "Мария Козлова",
        "sender_email": "m.kozlova@company.ru",
        "mailbox": "Входящие",
        "days_ago": 3,
        "body": (
            "Игорь, добавила комментарии к разделу 3. Цифры по конверсии нужно "
            "перепроверить с аналитиками — вижу расхождение с дашбордом. "
            "Дедлайн по отчёту — пятница."
        ),
    },
    {
        "mid": "test-mail-007@example.com",
        "subject": "Отв: Отчёт за май",
        "sender_name": "Дмитрий Орлов",
        "sender_email": "d.orlov@company.ru",
        "mailbox": "Входящие",
        "days_ago": 2,
        "body": (
            "Согласен с Марией, цифры по конверсии расходятся. "
            "Сверился с аналитиками — правильные данные: 4.2%. "
            "Обновите раздел 3 и пришлите финальную версию."
        ),
    },
    # ── Отдельные письма ──────────────────────────────────────────────────────
    {
        "mid": "test-mail-003@example.com",
        "subject": "Счёт №2847 от 12.05.2026",
        "sender_name": "Бухгалтерия ООО Сервис",
        "sender_email": "billing@ooo-servis.ru",
        "mailbox": "Входящие",
        "days_ago": 6,
        "body": (
            "Направляем счёт на оплату услуг за май 2026 г. Сумма: 45 000 руб. "
            "Срок оплаты — до 25.05.2026. Реквизиты во вложении."
        ),
    },
    {
        "mid": "test-mail-004@example.com",
        "subject": "Результаты A/B теста лендинга",
        "sender_name": "Аналитика",
        "sender_email": "analytics@company.ru",
        "mailbox": "Входящие",
        "days_ago": 2,
        "body": (
            "Тест завершён. Вариант B показал +18% к конверсии (p<0.05). "
            "Рекомендуем выкатить B как основной. Полный отчёт по ссылке."
        ),
    },
    {
        "mid": "test-mail-005@example.com",
        "subject": "Запрос на расширение команды",
        "sender_name": "HR-отдел",
        "sender_email": "hr@company.ru",
        "mailbox": "Отправленные",
        "days_ago": 4,
        "body": (
            "Прошу рассмотреть найм двух backend-разработчиков в команду. "
            "Обоснование и JD прилагаю. Планируемый онбординг — июль 2026."
        ),
    },
]

_SAMPLE_PROJECTS: list[dict[str, Any]] = [
    {
        "name": "Запуск нового продукта",
        "description": "Вывод ML-платформы на рынок: маркетинг, продажи, onboarding.",
        "status": "active",
        "deadline": (_dt.now() + _td(days=45)).strftime("%Y-%m-%d"),
        "goals": [
            {
                "id": "g1",
                "title": "Подготовить landing page",
                "done": True,
                "quadrant": "Q1",
            },
            {
                "id": "g2",
                "title": "Согласовать бюджет на рекламу",
                "done": False,
                "quadrant": "Q1",
            },
            {
                "id": "g3",
                "title": "Написать документацию API",
                "done": False,
                "quadrant": "Q2",
            },
            {
                "id": "g4",
                "title": "Настроить CRM-интеграцию",
                "done": False,
                "quadrant": "Q2",
            },
        ],
    },
    {
        "name": "Оптимизация инфраструктуры",
        "description": "Сократить расходы на облако на 30%, улучшить latency.",
        "status": "active",
        "deadline": (_dt.now() + _td(days=20)).strftime("%Y-%m-%d"),
        "goals": [
            {
                "id": "g5",
                "title": "Аудит текущих сервисов",
                "done": True,
                "quadrant": "Q1",
            },
            {
                "id": "g6",
                "title": "Перенести staging на spot-инстансы",
                "done": True,
                "quadrant": "Q2",
            },
            {
                "id": "g7",
                "title": "Настроить auto-scaling",
                "done": False,
                "quadrant": "Q2",
            },
        ],
    },
    {
        "name": "Обучение команды",
        "description": "Серия воркшопов по ML и продуктовым метрикам.",
        "status": "paused",
        "deadline": (_dt.now() + _td(days=90)).strftime("%Y-%m-%d"),
        "goals": [
            {
                "id": "g8",
                "title": "Составить учебный план",
                "done": True,
                "quadrant": "Q2",
            },
            {
                "id": "g9",
                "title": "Провести воркшоп #1",
                "done": False,
                "quadrant": "Q2",
            },
        ],
    },
]


def _make_event_md(evt: dict, now: _dt) -> str:
    start = now + _td(days=evt["start_offset_days"])
    start = start.replace(hour=10, minute=0, second=0, microsecond=0)
    end = start + _td(hours=evt["duration_h"])
    atts = (
        "\n".join(f'  - "[[contacts/{a}]]"' for a in evt["attendees"])
        if evt["attendees"]
        else ""
    )
    loc = f'location: "{evt["location"]}"' if evt["location"] else ""
    return f"""---
uid: {evt["uid"]}
title: "{evt["title"]}"
type: calendar-event
calendar: {evt["calendar"]}
start: {start.strftime("%Y-%m-%dT%H:%M:%S+03:00")}
end: {end.strftime("%Y-%m-%dT%H:%M:%S+03:00")}
all_day: false
{loc}
{"attendees:" if atts else ""}
{atts}
tags: [календарь, {_TESTDATA_MARKER}]
created: {now.strftime("%Y-%m-%dT%H:%M:%S+03:00")}
---

# {evt["title"]}

| Поле | Значение |
|------|----------|
| 📅 Начало | {start.strftime("%d.%m.%Y %H:%M")} |
| ⏰ Конец | {end.strftime("%d.%m.%Y %H:%M")} |
| 📆 Календарь | {evt["calendar"]} |
{"| 📍 Место | " + evt["location"] + " |" if evt["location"] else ""}

{"## Заметки" if evt["notes"] else ""}
{evt["notes"]}
"""


_TESTDATA_REPLY_RE = _re.compile(
    r"^\s*(re|fwd?|aw|tr|sv|rv|vl|rép|rep|ref|отв|пер)\s*(\[\d+\])?\s*:\s*",
    flags=_re.IGNORECASE,
)


def _testdata_thread_id(subject: str) -> str:
    """Replicate compute_thread_id() logic for test data generation.

    Strips Re:/Fwd:/Отв: prefixes, lowercases, MD5[:12] — identical
    algorithm to readers.applescript_base.compute_thread_id so test-data
    threads are grouped the same way as real synced mail.
    """
    s = subject
    prev = None
    while s != prev:
        prev = s
        s = _TESTDATA_REPLY_RE.sub("", s)
    return _hashlib.md5(s.strip().lower().encode("utf-8", errors="replace"), usedforsecurity=False).hexdigest()[:12]


def _make_mail_md(mail: dict, now: _dt) -> str:
    date = now - _td(days=mail["days_ago"])
    thread_id = _testdata_thread_id(mail["subject"])
    sender_name = mail.get("sender_name", "")
    sender_email = mail.get("sender_email", "")
    return f"""---
message_id: "<{mail["mid"]}>"
thread_id: "{thread_id}"
title: "{mail["subject"]}"
type: mail-message
sender_name: "{sender_name}"
sender: "[[contacts/{sender_email}]]"
from: "{sender_name} <{sender_email}>"
date: {date.strftime("%Y-%m-%dT%H:%M:%S+03:00")}
mailbox: {mail["mailbox"]}
has_attachments: false
tags: [почта, {_TESTDATA_MARKER}]
created: {now.strftime("%Y-%m-%dT%H:%M:%S+03:00")}
---

# {mail["subject"]}

| Поле | Значение |
|------|----------|
| 📨 От | {sender_name} <[[contacts/{sender_email}]]> |
| 📅 Дата | {date.strftime("%d.%m.%Y %H:%M")} |
| 📁 Ящик | {mail["mailbox"]} |

## Текст письма

{mail["body"]}
"""


# ── API endpoints ─────────────────────────────────────────────────────────────


@router.get("/testdata/snapshots")
async def list_snapshots():
    """Список доступных снапшотов для отката."""
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snaps = sorted(_SNAPSHOTS_DIR.glob("*.zip"), reverse=True)
    result = []
    for s in snaps:
        stat = s.stat()
        result.append(
            {
                "id": s.stem,
                "filename": s.name,
                "size_kb": round(stat.st_size / 1024, 1),
                "created": _dt.fromtimestamp(stat.st_mtime, tz=_tz.utc).isoformat(),
            }
        )
    return {"snapshots": result}


class GenerateBody(BaseModel):
    events: bool = True
    mail: bool = True
    projects: bool = True
    snapshot: bool = True  # make a rollback snapshot before writing


@router.post("/testdata/generate")
async def generate_testdata(body: GenerateBody):
    """Генерирует тестовые данные в vault и projects.json."""
    vault = _vault_root()
    now = _dt.now(_tz.utc).astimezone()
    snap_id = None

    # 1. Snapshot before touching anything
    if body.snapshot:
        snap_id = _snap_id("before_testdata")
        _create_snapshot(snap_id)

    created_files: list[str] = []
    errors: list[str] = []

    # 2. Calendar events
    if body.events:
        cal_dir = vault / "calendar"
        cal_dir.mkdir(parents=True, exist_ok=True)
        for evt in _SAMPLE_EVENTS:
            try:
                md = _make_event_md(evt, now)
                path = cal_dir / f"{evt['uid']}.md"
                path.write_text(md, encoding="utf-8")
                created_files.append(str(path.relative_to(vault)))
            except Exception as e:
                errors.append(f"event {evt['uid']}: {e}")

    # 3. Mail messages
    if body.mail:
        mail_dir = vault / "mail"
        mail_dir.mkdir(parents=True, exist_ok=True)
        for mail in _SAMPLE_MAILS:
            try:
                md = _make_mail_md(mail, now)
                slug = mail["mid"].replace("@", "_at_").replace(".", "_")
                path = mail_dir / f"{slug}.md"
                path.write_text(md, encoding="utf-8")
                created_files.append(str(path.relative_to(vault)))
            except Exception as e:
                errors.append(f"mail {mail['mid']}: {e}")

    # 4. Projects
    if body.projects:
        try:
            existing = _load_projects()
            # Remove previously generated test projects first
            existing = [p for p in existing if not p.get("_testdata")]
            for tmpl in _SAMPLE_PROJECTS:
                p = {
                    "id": str(_uuid.uuid4())[:8],
                    "_testdata": True,
                    "progress": 0,
                    "created_at": now.strftime("%Y-%m-%d"),
                    **tmpl,
                }
                # Compute progress from goals
                goals = tmpl.get("goals", [])
                if goals:
                    p["progress"] = round(
                        sum(1 for g in goals if g["done"]) / len(goals) * 100
                    )
                existing.append(p)
            _save_projects(existing)
            created_files.append("data/projects.json")
        except Exception as e:
            errors.append(f"projects: {e}")

    # 5. Reload vault index so new files appear immediately
    try:
        from personal_assistant.mlx_server.server import state

        state.reload_index()
    except Exception:
        pass

    return {
        "ok": not errors,
        "snap_id": snap_id,
        "created": created_files,
        "errors": errors,
        "events_count": len(_SAMPLE_EVENTS) if body.events else 0,
        "mail_count": len(_SAMPLE_MAILS) if body.mail else 0,
        "proj_count": len(_SAMPLE_PROJECTS) if body.projects else 0,
    }


class RollbackBody(BaseModel):
    snap_id: str


@router.post("/testdata/rollback")
async def rollback_testdata(body: RollbackBody):
    """Откатить к снапшоту: удалить сгенерированные файлы, восстановить архив."""
    archive = _SNAPSHOTS_DIR / f"{body.snap_id}.zip"
    if not archive.exists():
        raise HTTPException(404, f"Снапшот не найден: {body.snap_id}")
    try:
        _restore_snapshot(archive)
    except Exception as e:
        raise HTTPException(500, f"Ошибка восстановления: {e}")

    # Reload vault
    try:
        from personal_assistant.mlx_server.server import state

        state.reload_index()
    except Exception:
        pass

    return {"ok": True, "restored_from": body.snap_id}


@router.delete("/testdata/snapshots/{snap_id}")
async def delete_snapshot(snap_id: str):
    """Удалить снапшот."""
    archive = _SNAPSHOTS_DIR / f"{snap_id}.zip"
    if not archive.exists():
        raise HTTPException(404, "Снапшот не найден")
    archive.unlink()
    return {"ok": True}


@router.delete("/testdata/generated")
async def delete_generated():
    """Удалить только сгенерированные файлы (без восстановления из снапшота)."""
    vault = _vault_root()
    removed: list[str] = []
    if vault.exists():
        for f in list(vault.rglob("*.md")):
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
                if _TESTDATA_MARKER in txt:
                    f.unlink()
                    removed.append(str(f.name))
            except Exception:
                pass
    projects = _load_projects()
    had = len(projects)
    projects = [p for p in projects if not p.get("_testdata")]
    _save_projects(projects)
    try:
        from personal_assistant.mlx_server.server import state

        state.reload_index()
    except Exception:
        pass
    return {
        "ok": True,
        "removed_files": removed,
        "removed_projects": had - len(projects),
    }


# =============================================================================
# MLX Model downloader — Qwen and other mlx-community presets
# =============================================================================

# Catalogue of recommended Qwen models available on mlx-community HuggingFace
QWEN_MODELS: list[dict] = [
    {
        "id": "qwen2.5-7b-4bit",
        "repo": "mlx-community/Qwen2.5-7B-Instruct-4bit",
        "name": "Qwen 2.5 7B Instruct (4-bit)",
        "size_gb": 4.5,
        "description": "Основная рабочая модель. Хорошее качество, умеренная скорость.",
        "tags": ["recommended", "balanced"],
    },
    {
        "id": "qwen2.5-3b-4bit",
        "repo": "mlx-community/Qwen2.5-3B-Instruct-4bit",
        "name": "Qwen 2.5 3B Instruct (4-bit)",
        "size_gb": 2.0,
        "description": "Быстрая модель для тестирования. Подходит для MacBook Air M1/M2.",
        "tags": ["fast", "testing"],
    },
    {
        "id": "qwen2.5-1.5b-4bit",
        "repo": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
        "name": "Qwen 2.5 1.5B Instruct (4-bit)",
        "size_gb": 1.0,
        "description": "Минимальная модель. Очень быстро, качество ограничено.",
        "tags": ["minimal", "testing"],
    },
    {
        "id": "qwen2.5-14b-4bit",
        "repo": "mlx-community/Qwen2.5-14B-Instruct-4bit",
        "name": "Qwen 2.5 14B Instruct (4-bit)",
        "size_gb": 8.5,
        "description": "Высокое качество. Требует Mac с 16+ ГБ RAM.",
        "tags": ["high-quality"],
    },
    {
        "id": "qwen2.5-coder-7b-4bit",
        "repo": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
        "name": "Qwen 2.5 Coder 7B (4-bit)",
        "size_gb": 4.5,
        "description": "Специализирована на коде. Хороша для технических задач.",
        "tags": ["code"],
    },
]

# Per-repo pull state: { repo: { status, progress, error, local_path } }
_pull_state: dict[str, dict] = {}
_pull_lock = _threading.Lock()


def _default_models_dir() -> Path:
    """~/.cache/personal-assistant/models  — no sudo, writable by user."""
    return Path.home() / ".cache" / "personal-assistant" / "models"


def _local_path_for(repo: str) -> Path:
    slug = repo.replace("/", "--")
    return _default_models_dir() / slug


def _do_pull(repo: str) -> None:
    """Background thread: download model via huggingface_hub."""
    with _pull_lock:
        _pull_state[repo] = {
            "status": "downloading",
            "progress": 0,
            "error": None,
            "local_path": None,
        }

    local = _local_path_for(repo)
    try:
        from huggingface_hub import snapshot_download

        local.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"[model-pull] Downloading {repo} → {local}")

        from personal_assistant.config import settings as _cfg

        _token = _cfg.hf_token or None

        # Handle self-signed corporate SSL certificates
        if not _cfg.embedding_ssl_verify:
            import os as _os

            _os.environ.setdefault("CURL_CA_BUNDLE", "")
            _os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

        path = snapshot_download(
            repo_id=repo,
            local_dir=str(local),
            token=_token,
            ignore_patterns=["*.pt", "*.bin", "original/*"],  # skip non-MLX weights
        )
        with _pull_lock:
            _pull_state[repo] = {
                "status": "done",
                "progress": 100,
                "error": None,
                "local_path": path,
            }
        logger.info(f"[model-pull] Done: {path}")

    except ImportError:
        msg = "huggingface_hub не установлен. Добавьте в зависимости: uv add huggingface_hub"
        logger.error(f"[model-pull] {msg}")
        with _pull_lock:
            _pull_state[repo] = {
                "status": "error",
                "progress": 0,
                "error": msg,
                "local_path": None,
            }

    except Exception as exc:
        msg = str(exc)
        logger.error(f"[model-pull] Error downloading {repo}: {msg}")
        with _pull_lock:
            _pull_state[repo] = {
                "status": "error",
                "progress": 0,
                "error": msg,
                "local_path": None,
            }


@router.get("/model/catalogue")
async def model_catalogue():
    """Список рекомендованных Qwen MLX-моделей с локальным статусом."""
    result = []
    for m in QWEN_MODELS:
        local = _local_path_for(m["repo"])
        pull = _pull_state.get(m["repo"], {})
        result.append(
            {
                **m,
                "installed": local.exists() and any(local.iterdir()),
                "local_path": str(local) if local.exists() else None,
                "pull_status": pull.get("status"),
                "pull_progress": pull.get("progress", 0),
                "pull_error": pull.get("error"),
            }
        )
    return {"models": result}


class ModelPullBody(BaseModel):
    repo: str


@router.post("/model/pull")
async def model_pull(body: ModelPullBody, background_tasks: BackgroundTasks):
    """Начать загрузку модели в фоне."""
    # Test mode: refuse to download multi-GB model weights from HuggingFace.
    # TestClient would otherwise await the background pull inline.
    from personal_assistant.config import settings as _cfg
    if _cfg.e2e_test_mode:
        return {"ok": False, "message": "e2e_test_mode: загрузка пропущена", "e2e": True}

    repo = body.repo
    known_repos = {m["repo"] for m in QWEN_MODELS}
    if repo not in known_repos:
        raise HTTPException(400, f"Неизвестный repo: {repo}")

    with _pull_lock:
        state_now = _pull_state.get(repo, {})
    if state_now.get("status") == "downloading":
        return {"ok": False, "message": "Загрузка уже идёт"}

    background_tasks.add_task(_do_pull, repo)
    return {"ok": True, "message": f"Загрузка {repo} начата"}


@router.get("/model/pull-status")
async def model_pull_status(repo: str):
    """Статус загрузки модели."""
    with _pull_lock:
        st = _pull_state.get(repo, {"status": "idle"})
    return st


class ModelActivateBody(BaseModel):
    repo: str


@router.post("/model/activate")
async def model_activate(body: ModelActivateBody):
    """
    Установить загруженную модель как активную:
    сохранить путь в .env и перезагрузить engine.
    """
    from dotenv import set_key

    local = _local_path_for(body.repo)
    if not local.exists():
        raise HTTPException(404, "Модель не загружена")

    model_path = str(local)
    if _ENV_FILE.exists():
        set_key(str(_ENV_FILE), "PA_MLX_MODEL_PATH", model_path)

    # Hot-reload engine
    try:
        from personal_assistant.mlx_server.engine import MLXEngine
        from personal_assistant.mlx_server.server import state as srv_state

        srv_state.engine = MLXEngine(model_path=model_path)
        logger.info(f"[model-activate] Switched to {model_path}")
    except Exception as exc:
        logger.warning(f"[model-activate] Could not hot-reload engine: {exc}")

    return {"ok": True, "model_path": model_path}


@router.delete("/model/local")
async def model_delete_local(repo: str):
    """Удалить загруженную модель из диска."""
    local = _local_path_for(repo)
    if not local.exists():
        raise HTTPException(404, "Модель не найдена")
    _shutil.rmtree(local, ignore_errors=True)
    with _pull_lock:
        _pull_state.pop(repo, None)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Projects: vault item linking + contact assignment
# ---------------------------------------------------------------------------


class VaultLinkBody(BaseModel):
    vault_path: str


class ContactLinkBody(BaseModel):
    email: str
    name: Optional[str] = None


@router.post("/projects/{project_id}/link-vault")
async def project_link_vault(project_id: str, body: VaultLinkBody):
    """Привязать vault-документ к проекту."""
    projects = _load_projects()
    for i, p in enumerate(projects):
        if p["id"] == project_id:
            links: list = p.setdefault("vault_items", [])
            if body.vault_path not in links:
                links.append(body.vault_path)
                _save_projects(projects)
            return {"ok": True, "vault_items": projects[i]["vault_items"]}
    raise HTTPException(404, "Project not found")


@router.delete("/projects/{project_id}/link-vault")
async def project_unlink_vault(project_id: str, vault_path: str):
    """Отвязать vault-документ от проекта (vault_path передаётся как query param)."""
    projects = _load_projects()
    for i, p in enumerate(projects):
        if p["id"] == project_id:
            links: list = p.get("vault_items", [])
            if vault_path in links:
                links.remove(vault_path)
                projects[i]["vault_items"] = links
                _save_projects(projects)
            return {"ok": True, "vault_items": projects[i].get("vault_items", [])}
    raise HTTPException(404, "Project not found")


@router.post("/projects/{project_id}/link-contact")
async def project_link_contact(project_id: str, body: ContactLinkBody):
    """Назначить контакт на проект."""
    projects = _load_projects()
    for i, p in enumerate(projects):
        if p["id"] == project_id:
            contacts: list = p.setdefault("contacts", [])
            emails = [c["email"] if isinstance(c, dict) else c for c in contacts]
            if body.email not in emails:
                contacts.append({"email": body.email, "name": body.name or ""})
                _save_projects(projects)
            return {"ok": True, "contacts": projects[i]["contacts"]}
    raise HTTPException(404, "Project not found")


@router.delete("/projects/{project_id}/link-contact")
async def project_unlink_contact(project_id: str, email: str):
    """Удалить контакт из проекта (email передаётся как query param)."""
    projects = _load_projects()
    for i, p in enumerate(projects):
        if p["id"] == project_id:
            contacts: list = p.get("contacts", [])
            contacts = [
                c for c in contacts
                if (c["email"] if isinstance(c, dict) else c) != email
            ]
            projects[i]["contacts"] = contacts
            _save_projects(projects)
            return {"ok": True, "contacts": projects[i]["contacts"]}
    raise HTTPException(404, "Project not found")


# ---------------------------------------------------------------------------
# Structured Rules API  (replaces / supplements GTD rules)
# ---------------------------------------------------------------------------

_RULES_FILE_WEBUI = _PROJECT_ROOT / "data" / "rules.json"


def _load_rules_raw() -> list:
    if _RULES_FILE_WEBUI.exists():
        try:
            data = _json.loads(_RULES_FILE_WEBUI.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _save_rules_raw(rules: list) -> None:
    _RULES_FILE_WEBUI.parent.mkdir(parents=True, exist_ok=True)
    _RULES_FILE_WEBUI.write_text(
        _json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class RuleBody(BaseModel):
    name: str = ""
    keywords: list = []
    contacts: list = []
    eisenhower_quadrant: str = "q2"
    action_type: str = "info"
    priority: int = 100
    tags: list = []
    enabled: bool = True


@router.get("/rules")
async def get_rules():
    """Список структурированных правил классификации."""
    return {"rules": _load_rules_raw()}


@router.post("/rules")
async def create_rule(body: RuleBody):
    """Создать новое правило."""
    import uuid as _uuid_mod
    rules = _load_rules_raw()
    rule = {"id": str(_uuid_mod.uuid4())[:8], **body.model_dump()}
    rules.append(rule)
    _save_rules_raw(rules)
    return rule


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: str, body: RuleBody):
    """Обновить правило по ID."""
    rules = _load_rules_raw()
    for i, r in enumerate(rules):
        if r["id"] == rule_id:
            rules[i] = {**r, **body.model_dump(), "id": rule_id}
            _save_rules_raw(rules)
            return {"ok": True, "rule": rules[i]}
    raise HTTPException(404, "Rule not found")


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    """Удалить правило. Возвращает 404 если правило не найдено."""
    rules = _load_rules_raw()
    ids_before = {r["id"] for r in rules}
    if rule_id not in ids_before:
        raise HTTPException(404, f"Rule '{rule_id}' not found")
    rules = [r for r in rules if r["id"] != rule_id]
    _save_rules_raw(rules)
    return {"ok": True}


class ClassifyTextBody(BaseModel):
    text: str
    contacts: list = []


@router.post("/rules/classify")
async def classify_text(body: ClassifyTextBody):
    """Classify text against active rules.

    ``contacts`` accepts both plain email strings and ``{email, name, ...}``
    dicts — email addresses are extracted from either format.
    """
    from personal_assistant.services.rule_engine import Rule, classify_item
    rules_raw = _load_rules_raw()
    rules = [Rule.model_validate(r) for r in rules_raw]

    # Normalise contacts: accept both plain strings and {email, ...} dicts
    emails: list[str] = []
    for c in body.contacts:
        if isinstance(c, str):
            emails.append(c)
        elif isinstance(c, dict):
            email = c.get("email") or c.get("address") or ""
            if email:
                emails.append(str(email))

    result = classify_item(body.text, emails or None, rules)
    return {
        "matched_rule_id": result.matched_rule_id,
        "matched_rule_name": result.matched_rule_name,
        "eisenhower_quadrant": result.eisenhower_quadrant.value,
        "action_type": result.action_type.value,
        "tags": result.tags,
        "matched_keywords": result.matched_keywords,
        "score": result.score,
    }


# ---------------------------------------------------------------------------
# Tag history API
# ---------------------------------------------------------------------------


class TagChangeBody(BaseModel):
    item_id: str
    old_value: str = ""
    new_value: str = ""
    section: str = ""
    changed_by: str = "user"
    note: str = ""


@router.get("/tag-history")
async def get_tag_history(
    item_id: Optional[str] = None,
    section: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 200,
):
    """История изменений тегов. Поддерживает фильтрацию по item_id, section, дате."""
    from personal_assistant.services.tag_history_service import list_changes
    changes = list_changes(
        item_id=item_id,
        section=section,
        since=since,
        until=until,
        limit=limit,
    )
    return {"changes": [c.model_dump() for c in changes]}


@router.post("/tag-history")
async def record_tag_change(body: TagChangeBody):
    """Записать изменение тега."""
    from personal_assistant.services.tag_history_service import record_change
    change = record_change(
        item_id=body.item_id,
        old_value=body.old_value,
        new_value=body.new_value,
        section=body.section,
        changed_by=body.changed_by,
        note=body.note,
    )
    return change.model_dump()


@router.delete("/tag-history/{change_id}")
async def delete_tag_change(change_id: str):
    """Удалить запись из истории тегов."""
    from personal_assistant.services.tag_history_service import delete_change
    deleted = delete_change(change_id)
    if not deleted:
        raise HTTPException(404, "Change not found")
    return {"ok": True}


@router.delete("/tag-history")
async def clear_tag_history(item_id: Optional[str] = None):
    """Очистить историю тегов (для конкретного item_id или полностью)."""
    from personal_assistant.services.tag_history_service import clear_history
    deleted = clear_history(item_id=item_id)
    return {"ok": True, "deleted": deleted}
