"""
Daily Brief Service (Stage 6).

Builds a structured daily briefing from vault data:
  - Today's calendar events (with meeting prep readiness)
  - Urgent/high-priority inbox items requiring action
  - Open action items / key tasks across threads
  - AI insight (rule-based or MLX-generated summary)

Public API
----------
build_daily_brief(vault_path, my_email, mlx_engine=None, profile_name="") -> dict

Output dict keys
----------------
- generated_at   : str    ISO datetime
- greeting       : str    "Доброе утро, Игорь! Насыщенный день."
- sections       : list[Section]
    Each section: { title, icon, items: list[Item], empty_label }
    Item types:
      calendar: { type:"event", time, title, prep_ready, is_now, is_soon, location }
      inbox:    { type:"inbox", subject, sender, sender_name, deadline_label, tags }
      task:     { type:"task",  text, source_subject, source_date }
- ai_insight     : str    1-2 sentence insight (rule-based or MLX)
- bullets        : list[str]  ≤3 top priorities for the day
- stats          : dict  { events_today, urgent_count, tasks_count }
- cached         : bool
- vault_loaded   : bool

Algorithm
---------
1. Scan vault/calendar/**/*.md for today's events, sort by time.
2. Scan vault/mail/**/*.md for urgent / reply-required items.
3. Scan vault/mail/**/*.md + vault/threads/**/*.md for open action items.
4. Build rule-based insight summarising the day.
5. Optional: use MLX to generate a richer ai_insight.
6. Cache result per-day in vault/daily/<date>_brief_cache.json (TTL 30 min).
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 30 * 60          # 30-minute cache
_MAX_EVENTS        = 10
_MAX_URGENT        = 8
_MAX_TASKS         = 10
_TASK_BODY_LEN     = 300
_BRIEF_MAX_TOKENS  = 400

_TAG_URGENT = {"срочно", "urgency:critical", "urgency:high", "urgency:urgent", "urgent"}
_TAG_IMPORT = {"важно", "important", "urgency:medium", "urgency:important"}
_TAG_REPLY  = {"reply_required", "requires_reply", "reply-required"}

_TASK_RE = re.compile(
    r"(?:прошу|необходимо|нужно|нужен|надо|требуется|сделать|подготовить|"
    r"отправить|согласовать|обсудить|проверить|подтвердить|"
    r"please|need to|action item|todo|must|should)\s+.{5,80}",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", (text or "").strip())


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_str)."""
    if not text.startswith("---"):
        return {}, text.strip()
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text.strip()
    yaml_text = text[3:end]
    try:
        import yaml as _yaml
        fm = _yaml.safe_load(yaml_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
        for line in yaml_text.splitlines():
            if ": " in line:
                k, _, v = line.partition(": ")
                k = k.strip()
                if k and not k.startswith("#"):
                    fm[k] = v.strip().strip('"').strip("'")
    return fm, text[end + 4:].strip()


def _parse_iso(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _fmt_time(dt: datetime) -> str:
    return dt.astimezone().strftime("%H:%M")


def _is_today_dt(dt: datetime) -> bool:
    local_now = datetime.now(timezone.utc).astimezone()
    return dt.astimezone().date() == local_now.date()


def _is_now(dt: datetime, window_min: int = 30) -> bool:
    """True if the event is happening within ±window_min minutes of now."""
    now = datetime.now(timezone.utc)
    return abs((dt - now).total_seconds()) <= window_min * 60


def _is_soon(dt: datetime, minutes: int = 60) -> bool:
    """True if the event starts within the next `minutes` minutes."""
    now = datetime.now(timezone.utc)
    delta = (dt - now).total_seconds()
    return 0 <= delta <= minutes * 60


def _tag_set(fm: dict) -> set[str]:
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {str(t).lower().strip() for t in tags}


def _is_urgent(fm: dict) -> bool:
    return bool(_tag_set(fm) & _TAG_URGENT)


def _is_important(fm: dict) -> bool:
    tags = _tag_set(fm)
    return bool(tags & _TAG_URGENT) or bool(tags & _TAG_IMPORT)


def _requires_reply(fm: dict) -> bool:
    tags = _tag_set(fm)
    if tags & _TAG_REPLY:
        return True
    if str(fm.get("reply_required") or "").lower() in ("true", "1", "yes"):
        return True
    if str(fm.get("intent") or "").lower() in ("request", "question", "поручение"):
        return True
    return False


def _deadline_label(fm: dict) -> str:
    """Return a human-readable deadline label from tags or deadline field."""
    deadline_field = str(fm.get("deadline") or "")
    for tag in _tag_set(fm):
        if tag.startswith("deadline:"):
            val = tag.split(":", 1)[1]
            if val in ("today", "сегодня"):
                return "сегодня"
            if val in ("tomorrow", "завтра"):
                return "завтра"
            if val.startswith("this_week"):
                return "на неделе"
            return val
    if deadline_field:
        if "today" in deadline_field.lower() or "сегодня" in deadline_field.lower():
            return "сегодня"
        return deadline_field[:10]
    return ""


def _sender_name(sender_raw: str) -> str:
    """Extract display name from 'Name <email>' or return email."""
    m = re.match(r"^([^<]+)<", sender_raw or "")
    if m:
        return _norm(m.group(1))
    return _norm(sender_raw).split("@")[0] if "@" in sender_raw else _norm(sender_raw)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(vault_path: Path) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return vault_path / "daily" / f"{today}_brief_cache.json"


def _load_cache(vault_path: Path) -> Optional[dict]:
    try:
        p = _cache_path(vault_path)
        if not p.exists():
            return None
        mtime = p.stat().st_mtime
        if (datetime.now().timestamp() - mtime) > _CACHE_TTL_SECONDS:
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        data["cached"] = True
        return data
    except Exception:
        return None


def _save_cache(vault_path: Path, data: dict) -> None:
    try:
        p = _cache_path(vault_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        save_data = {k: v for k, v in data.items() if k != "cached"}
        p.write_text(json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug(f"[daily_brief] cache save failed: {exc}")


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_calendar_section(vault_path: Path) -> tuple[list[dict], int]:
    """Return (event_items, total_today) from vault/calendar."""
    items: list[dict] = []
    calendar_dir = vault_path / "calendar"
    if not calendar_dir.exists():
        return items, 0

    for md_path in sorted(calendar_dir.rglob("*.md")):
        try:
            raw = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _ = _parse_frontmatter(raw)
        if not isinstance(fm, dict):
            continue

        date_str = str(fm.get("date") or fm.get("start") or "")
        dt = _parse_iso(date_str)
        if dt is None or not _is_today_dt(dt):
            continue

        title = _norm(str(fm.get("title") or fm.get("subject") or md_path.stem))
        items.append({
            "type": "event",
            "time": _fmt_time(dt),
            "title": title,
            "location": _norm(str(fm.get("location") or "")),
            "is_now": _is_now(dt),
            "is_soon": _is_soon(dt),
            "prep_ready": bool(fm.get("prep_brief") or fm.get("brief_ready")),
            "id": str(fm.get("id") or md_path.stem),
            "path": str(md_path),
        })
        if len(items) >= _MAX_EVENTS:
            break

    items.sort(key=lambda x: x["time"])
    return items, len(items)


def _build_inbox_section(vault_path: Path) -> tuple[list[dict], int]:
    """Return (urgent_items, total_urgent) from vault/mail."""
    items: list[dict] = []
    total = 0
    mail_dir = vault_path / "mail"
    if not mail_dir.exists():
        return items, 0

    # Scan last 7 days only
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=7)

    for md_path in sorted(mail_dir.rglob("*.md"), reverse=True):
        try:
            raw = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _ = _parse_frontmatter(raw)
        if not isinstance(fm, dict):
            continue
        doc_type = str(fm.get("type") or "").lower()
        if doc_type not in ("", "email", "mail"):
            continue

        date_str = str(fm.get("date") or "")
        dt = _parse_iso(date_str)
        if dt and dt < cutoff_dt:
            continue

        if not (_is_urgent(fm) or _requires_reply(fm)):
            continue

        total += 1
        if len(items) < _MAX_URGENT:
            items.append({
                "type": "inbox",
                "subject": _norm(str(fm.get("subject") or md_path.stem)),
                "sender": str(fm.get("sender") or fm.get("from") or ""),
                "sender_name": _sender_name(str(fm.get("sender") or fm.get("from") or "")),
                "deadline_label": _deadline_label(fm),
                "tags": list(_tag_set(fm))[:5],
                "date": date_str,
                "id": str(fm.get("id") or md_path.stem),
                "path": str(md_path),
            })

    items.sort(key=lambda x: (0 if _is_urgent_item(x) else 1, x["date"]), reverse=False)
    return items, total


def _is_urgent_item(item: dict) -> bool:
    return any(t in _TAG_URGENT for t in item.get("tags", []))


def _build_tasks_section(vault_path: Path) -> list[dict]:
    """Scan mail + threads for open action items."""
    tasks: list[dict] = []
    for section in ("mail", "threads"):
        sec_path = vault_path / section
        if not sec_path.exists():
            continue
        for md_path in sorted(sec_path.rglob("*.md"), reverse=True):
            if len(tasks) >= _MAX_TASKS:
                break
            try:
                raw = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, body = _parse_frontmatter(raw)
            for m in _TASK_RE.finditer(body):
                sentence = re.sub(r"\s+", " ", m.group(0)).strip().rstrip(".,:;")
                if sentence not in [t["text"] for t in tasks]:
                    tasks.append({
                        "type": "task",
                        "text": sentence[:120],
                        "source_subject": _norm(str(fm.get("subject") or md_path.stem)),
                        "source_date": str(fm.get("date") or "")[:10],
                    })
                if len(tasks) >= _MAX_TASKS:
                    break

    return tasks[:_MAX_TASKS]


# ---------------------------------------------------------------------------
# AI insight
# ---------------------------------------------------------------------------

def _rule_based_insight(
    events: list[dict],
    urgent: list[dict],
    tasks: list[dict],
    profile_name: str,
) -> str:
    parts = []

    n_events = len(events)
    n_urgent = len(urgent)
    n_tasks  = len(tasks)

    if n_events == 0 and n_urgent == 0:
        return f"Свободный день, {profile_name or 'хорошего дня'}!"

    if n_events > 0:
        soon = [e for e in events if e.get("is_soon")]
        if soon:
            parts.append(f"Ближайшая встреча — «{soon[0]['title']}» в {soon[0]['time']}.")
        elif n_events == 1:
            parts.append(f"Одна встреча сегодня — «{events[0]['title']}» в {events[0]['time']}.")
        else:
            parts.append(f"Сегодня {n_events} встреч{'а' if n_events==1 else 'и' if n_events<5 else ''}.")

    if n_urgent > 0:
        senders = list({e["sender_name"] for e in urgent[:3] if e["sender_name"]})
        if senders:
            parts.append(
                f"{'Срочное письмо' if n_urgent==1 else f'{n_urgent} срочных письма/писем'}"
                f" от {', '.join(senders[:2])}."
            )
        else:
            parts.append(f"{n_urgent} срочных писем требуют ответа.")

    if n_tasks > 0:
        parts.append(f"Открытых поручений: {n_tasks}.")

    return " ".join(parts) if parts else "Хорошего дня!"


def _mlx_insight(
    events: list[dict],
    urgent: list[dict],
    tasks: list[dict],
    profile_name: str,
    engine: Any,
) -> str:
    try:
        lines = []
        if events:
            ev_strs = ", ".join(e["time"] + " " + e["title"] for e in events[:4])
            lines.append("Встречи сегодня: " + ev_strs)
        if urgent:
            lines.append(f"Срочные письма: {', '.join(e['subject'] for e in urgent[:3])}")
        if tasks:
            lines.append(f"Поручения: {'; '.join(t['text'][:60] for t in tasks[:3])}")

        if not lines:
            return _rule_based_insight(events, urgent, tasks, profile_name)

        prompt = (
            "\n".join(lines) +
            f"\n\nНапиши одно предложение-инсайт для {profile_name or 'пользователя'} "
            "о его дне: что важнее всего сделать первым делом. "
            "Не больше 25 слов, деловой стиль, без приветствия."
        )
        system = "Ты — деловой ассистент. Дай краткий приоритет на день."
        result = engine.ask(prompt, system=system, max_tokens=_BRIEF_MAX_TOKENS)
        if result and len(result.strip()) > 10:
            return result.strip()
    except Exception as exc:
        logger.warning(f"[daily_brief] MLX insight failed: {exc}")
    return _rule_based_insight(events, urgent, tasks, profile_name)


# ---------------------------------------------------------------------------
# Bullets (top 3 priorities)
# ---------------------------------------------------------------------------

def _build_bullets(
    events: list[dict],
    urgent: list[dict],
    tasks: list[dict],
) -> list[str]:
    bullets: list[str] = []

    # Imminent meetings first
    for e in events:
        if e.get("is_now") or e.get("is_soon"):
            bullets.append(f"🗓️ Сейчас/скоро: <b>{e['title']}</b> в {e['time']}")
            if len(bullets) >= 3:
                return bullets

    # Urgent with deadline today
    for item in urgent:
        if item.get("deadline_label") in ("сегодня", "today"):
            bullets.append(f"📨 Срочно ответить: <b>{item['subject']}</b> — {item['sender_name']}")
            if len(bullets) >= 3:
                return bullets

    # Other urgent inbox
    for item in urgent:
        if item.get("deadline_label") not in ("сегодня", "today"):
            bullets.append(f"📨 Требует ответа: <b>{item['subject']}</b>")
            if len(bullets) >= 3:
                return bullets

    # Today's meetings (not already added)
    for e in events:
        if not e.get("is_now") and not e.get("is_soon"):
            bullets.append(f"🗓️ Встреча: <b>{e['title']}</b> в {e['time']}")
            if len(bullets) >= 3:
                return bullets

    # Tasks
    for t in tasks[:2]:
        bullets.append(f"✅ {t['text'][:80]}")
        if len(bullets) >= 3:
            return bullets

    return bullets[:3]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_daily_brief(
    vault_path: Optional[Path] = None,
    my_email: str = "",
    mlx_engine: Any = None,
    profile_name: str = "",
    force_refresh: bool = False,
) -> dict:
    """
    Build the daily brief.

    Parameters
    ----------
    vault_path    : Path|None  — vault root; auto-resolved from settings if None
    my_email      : str        — user email (excluded from sender counts)
    mlx_engine    : Any|None   — optional MLX engine for AI insight
    profile_name  : str        — user first name for greeting
    force_refresh : bool       — skip cache

    Returns
    -------
    dict with keys:
        generated_at, greeting, sections, ai_insight, bullets, stats,
        cached, vault_loaded
    """
    # Resolve vault path
    if vault_path is None:
        try:
            from personal_assistant.config import settings
            vault_path = settings.vault_path
        except Exception:
            pass

    now_iso = datetime.now(timezone.utc).isoformat()

    if not vault_path or not vault_path.exists():
        return {
            "generated_at": now_iso,
            "greeting": _greeting_str(profile_name),
            "sections": [],
            "ai_insight": "Vault не загружен — запустите синхронизацию.",
            "bullets": [],
            "stats": {"events_today": 0, "urgent_count": 0, "tasks_count": 0},
            "cached": False,
            "vault_loaded": False,
        }

    # Check cache
    if not force_refresh:
        cached = _load_cache(vault_path)
        if cached:
            return cached

    # Build sections
    events, events_total = _build_calendar_section(vault_path)
    urgent, urgent_total = _build_inbox_section(vault_path)
    tasks = _build_tasks_section(vault_path)

    # AI insight
    if mlx_engine is not None:
        ai_insight = _mlx_insight(events, urgent, tasks, profile_name, mlx_engine)
    else:
        ai_insight = _rule_based_insight(events, urgent, tasks, profile_name)

    bullets = _build_bullets(events, urgent, tasks)

    sections = []

    if events:
        sections.append({
            "title": "Сегодня в календаре",
            "icon": "🗓️",
            "items": events,
            "empty_label": "Встреч сегодня нет",
        })
    else:
        sections.append({
            "title": "Сегодня в календаре",
            "icon": "🗓️",
            "items": [],
            "empty_label": "Встреч сегодня нет",
        })

    sections.append({
        "title": "Требуют ответа",
        "icon": "📨",
        "items": urgent,
        "empty_label": "Срочных писем нет",
    })

    if tasks:
        sections.append({
            "title": "Открытые поручения",
            "icon": "✅",
            "items": tasks,
            "empty_label": "Поручений нет",
        })

    result = {
        "generated_at": now_iso,
        "greeting": _greeting_str(profile_name, events, urgent),
        "sections": sections,
        "ai_insight": ai_insight,
        "bullets": bullets,
        "stats": {
            "events_today": events_total,
            "urgent_count": urgent_total,
            "tasks_count": len(tasks),
        },
        "cached": False,
        "vault_loaded": True,
    }

    _save_cache(vault_path, result)
    return result


def _greeting_str(
    name: str,
    events: Optional[list] = None,
    urgent: Optional[list] = None,
) -> str:
    hour = datetime.now().hour
    if hour < 12:
        base = f"Доброе утро{', ' + name if name else ''}!"
    elif hour < 17:
        base = f"Добрый день{', ' + name if name else ''}!"
    else:
        base = f"Добрый вечер{', ' + name if name else ''}!"

    # Mood suffix
    n_events = len(events or [])
    n_urgent = len(urgent or [])

    if n_events >= 4 or n_urgent >= 3:
        suffix = " Насыщенный день."
    elif n_events == 0 and n_urgent == 0:
        suffix = " Спокойный день."
    elif n_events > 0:
        suffix = f" {n_events} встреч{'а' if n_events == 1 else 'и' if n_events < 5 else ''} сегодня."
    else:
        suffix = ""

    return base + suffix
