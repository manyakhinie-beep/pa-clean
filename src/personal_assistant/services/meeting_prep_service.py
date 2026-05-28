"""
Smart Meeting Prep Service (Stage 5).

Collects vault context for an upcoming calendar event and builds a
ready-to-use preparation brief for the MLX chat model.

Public API
----------
build_meeting_prep(event_id, vault_path, my_email, mlx_engine=None) -> dict

Output dict keys
----------------
- event_id          : str
- title             : str
- participants      : list[str]          — display names / emails
- participant_emails: list[str]          — bare email addresses
- event_date        : str                — ISO datetime string
- recent_emails     : list[dict]         — emails from participants (last 7 days)
- related_projects  : list[dict]         — vault projects mentioning participants
- previous_meetings : list[dict]         — earlier meetings with same people
- open_action_items : list[str]          — extracted tasks / pending items
- prep_brief        : str                — bullet-point briefing (rule-based or MLX)
- context_prompt    : str                — full prompt ready for /api/chat/send
- event_found       : bool               — False when event_id unknown
- message_count     : int                — total context docs found

Algorithm
---------
1. Find the event .md in vault/calendar/**/*.md by id frontmatter or stem.
2. Parse participants from frontmatter (attendees / participants / contacts fields).
3. Scan vault/mail/**/*.md for messages where sender_email ∈ participants
   and date >= now - RECENT_DAYS.
4. Scan vault/projects/**/*.md for docs mentioning any participant name.
5. Scan vault/calendar/**/*.md for earlier meetings with overlapping attendees.
6. Collect open_action_items from structured extraction cache (extract/*.json)
   or regex-scan mail bodies.
7. Build prep_brief: rule-based summary or MLX-generated brief.
8. Build context_prompt for /api/chat/send.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RECENT_DAYS = 7          # how far back to scan for recent emails
_MAX_RECENT_EMAILS = 8    # cap for context window
_MAX_PROJECTS = 5
_MAX_PREV_MEETINGS = 5
_MAX_ACTION_ITEMS = 10
_BODY_SNIPPET_LEN = 400   # chars per email body in brief


# ---------------------------------------------------------------------------
# Internal helpers (adapted from draft_context_service)
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").strip()


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_str) split from a .md file."""
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


def _extract_email(raw: str) -> str:
    m = re.search(r"<([^>]+)>", raw or "")
    if m:
        return m.group(1).strip().lower()
    if "@" in (raw or ""):
        return raw.strip().lower()
    return ""


def _clean_body(text: str, max_chars: int = _BODY_SNIPPET_LEN) -> str:
    text = _norm(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[A-Za-z0-9+/]{60,}={0,2}", "[...base64...]", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…[сокращено]"
    return text


def _parse_participants(fm: dict) -> list[str]:
    """Extract participant strings from various frontmatter field names."""
    raw: list = []
    for field in ("attendees", "participants", "contacts", "invitees"):
        val = fm.get(field)
        if isinstance(val, list):
            raw.extend(str(v) for v in val if v)
        elif isinstance(val, str) and val.strip():
            # comma-separated string
            raw.extend(p.strip() for p in val.split(",") if p.strip())
    return [_norm(p) for p in raw if p]


def _emails_from_participants(participants: list[str]) -> list[str]:
    """Extract bare email addresses from display strings."""
    emails = []
    for p in participants:
        e = _extract_email(p)
        if e:
            emails.append(e)
        elif "@" in p:
            emails.append(p.strip().lower())
    return list(dict.fromkeys(emails))  # deduplicate preserving order


def _parse_iso(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _date_is_recent(date_str: str, days: int = _RECENT_DAYS) -> bool:
    dt = _parse_iso(date_str)
    if dt is None:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return dt >= cutoff


def _sender_in_participants(sender_raw: str, participant_emails: list[str]) -> bool:
    if not participant_emails:
        return False
    email = _extract_email(sender_raw) or sender_raw.strip().lower()
    return email in participant_emails


def _name_in_text(participants: list[str], text: str) -> bool:
    """Return True if any participant name/email appears in text."""
    text_lower = text.lower()
    for p in participants:
        # try full string, email part, and name part
        if p.lower() in text_lower:
            return True
        email = _extract_email(p)
        if email and email in text_lower:
            return True
        # name part (before <)
        name = re.sub(r"<.*?>", "", p).strip()
        if name and len(name) > 3 and name.lower() in text_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Vault scanning helpers
# ---------------------------------------------------------------------------

def _find_event_in_vault(event_id: str, vault_path: Path) -> Optional[dict]:
    """Scan vault/calendar/**/*.md for the given event_id."""
    if not vault_path or not vault_path.exists():
        return None
    calendar_dir = vault_path / "calendar"
    search_roots = [calendar_dir] if calendar_dir.exists() else [vault_path]

    for root in search_roots:
        for md_path in sorted(root.rglob("*.md")):
            try:
                raw = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, body = _parse_frontmatter(raw)
            if not isinstance(fm, dict):
                continue
            doc_id = str(fm.get("id") or md_path.stem).strip()
            if doc_id != event_id:
                continue
            participants = _parse_participants(fm)
            return {
                "id": doc_id,
                "title": _norm(str(fm.get("title") or fm.get("subject") or md_path.stem)),
                "date": str(fm.get("date") or fm.get("start") or ""),
                "participants": participants,
                "participant_emails": _emails_from_participants(participants),
                "location": str(fm.get("location") or ""),
                "body": _clean_body(body),
                "path": str(md_path),
            }
    return None


def _scan_recent_emails(
    vault_path: Path,
    participant_emails: list[str],
    recent_days: int = _RECENT_DAYS,
    limit: int = _MAX_RECENT_EMAILS,
) -> list[dict]:
    """Find recent emails from or to participant_emails."""
    results: list[dict] = []
    if not vault_path or not participant_emails:
        return results
    mail_dir = vault_path / "mail"
    search_roots = [mail_dir] if mail_dir.exists() else [vault_path]
    for root in search_roots:
        for md_path in sorted(root.rglob("*.md")):
            try:
                raw = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, body = _parse_frontmatter(raw)
            if not isinstance(fm, dict):
                continue
            doc_type = str(fm.get("type") or "").lower()
            if doc_type not in ("", "email", "mail"):
                continue
            date_str = str(fm.get("date") or "")
            if not _date_is_recent(date_str, recent_days):
                continue
            sender_raw = str(fm.get("sender") or fm.get("from") or "")
            if not _sender_in_participants(sender_raw, participant_emails):
                continue
            results.append({
                "id": str(fm.get("id") or md_path.stem),
                "subject": _norm(str(fm.get("subject") or md_path.stem)),
                "sender": sender_raw,
                "date": date_str,
                "body_snippet": _clean_body(body, max_chars=_BODY_SNIPPET_LEN),
                "path": str(md_path),
            })
            if len(results) >= limit:
                break
    results.sort(key=lambda r: r["date"], reverse=True)
    return results[:limit]


def _scan_related_projects(
    vault_path: Path,
    participants: list[str],
    limit: int = _MAX_PROJECTS,
) -> list[dict]:
    """Find project .md files that mention any participant."""
    results: list[dict] = []
    if not vault_path or not participants:
        return results
    projects_dir = vault_path / "projects"
    if not projects_dir.exists():
        return results
    for md_path in sorted(projects_dir.rglob("*.md")):
        try:
            raw = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _name_in_text(participants, raw):
            continue
        fm, body = _parse_frontmatter(raw)
        results.append({
            "id": str(fm.get("id") or md_path.stem),
            "title": _norm(str(fm.get("title") or fm.get("subject") or md_path.stem)),
            "path": str(md_path),
            "snippet": _clean_body(body, max_chars=200),
        })
        if len(results) >= limit:
            break
    return results


def _scan_previous_meetings(
    vault_path: Path,
    participants: list[str],
    exclude_event_id: str,
    limit: int = _MAX_PREV_MEETINGS,
) -> list[dict]:
    """Find earlier calendar events with overlapping participants."""
    results: list[dict] = []
    if not vault_path or not participants:
        return results
    calendar_dir = vault_path / "calendar"
    if not calendar_dir.exists():
        return results
    now_str = datetime.now(timezone.utc).isoformat()
    for md_path in sorted(calendar_dir.rglob("*.md")):
        try:
            raw = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, body = _parse_frontmatter(raw)
        if not isinstance(fm, dict):
            continue
        doc_id = str(fm.get("id") or md_path.stem).strip()
        if doc_id == exclude_event_id:
            continue
        date_str = str(fm.get("date") or fm.get("start") or "")
        if date_str >= now_str:
            continue  # skip future events
        if not _name_in_text(participants, raw):
            continue
        results.append({
            "id": doc_id,
            "title": _norm(str(fm.get("title") or fm.get("subject") or md_path.stem)),
            "date": date_str,
            "snippet": _clean_body(body, max_chars=200),
        })
        if len(results) >= limit:
            break
    results.sort(key=lambda r: r["date"], reverse=True)
    return results[:limit]


def _scan_open_action_items(
    vault_path: Path,
    participants: list[str],
    limit: int = _MAX_ACTION_ITEMS,
) -> list[str]:
    """Extract open action items from mail/threads mentioning participants."""
    items: list[str] = []
    if not vault_path or not participants:
        return items

    # Regex patterns for task-like sentences
    _TASK_RE = re.compile(
        r"(?:прошу|необходимо|нужно|нужен|надо|требуется|сделать|подготовить|"
        r"отправить|согласовать|обсудить|проверить|подтвердить|"
        r"please|need to|action item|todo|must|should)\s+.{5,80}",
        re.IGNORECASE,
    )

    for section in ("mail", "threads"):
        sec_path = vault_path / section
        if not sec_path.exists():
            continue
        for md_path in sorted(sec_path.rglob("*.md")):
            if len(items) >= limit:
                break
            try:
                raw = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not _name_in_text(participants, raw):
                continue
            _, body = _parse_frontmatter(raw)
            for m in _TASK_RE.finditer(body):
                sentence = m.group(0).strip().rstrip(".,:;")
                sentence = re.sub(r"\s+", " ", sentence)
                if sentence not in items:
                    items.append(sentence)
                if len(items) >= limit:
                    break
    return items[:limit]


# ---------------------------------------------------------------------------
# Brief builders
# ---------------------------------------------------------------------------

def _rule_based_brief(
    event: dict,
    recent_emails: list[dict],
    related_projects: list[dict],
    previous_meetings: list[dict],
    open_action_items: list[str],
) -> str:
    """Build a structured prep brief without MLX."""
    lines: list[str] = []
    title = event.get("title") or "Встреча"
    date_str = event.get("event_date") or event.get("date") or ""
    participants = event.get("participants") or []

    lines.append(f"Подготовка к встрече: «{title}»")
    if date_str:
        lines.append(f"Дата: {date_str[:19].replace('T', ' ')}")
    if participants:
        lines.append(f"Участники: {', '.join(participants[:5])}")

    if recent_emails:
        lines.append(f"\nПоследние письма ({len(recent_emails)} шт.):")
        for e in recent_emails[:3]:
            lines.append(f"  • {e['date'][:10]} от {e['sender']} — {e['subject']}")

    if related_projects:
        lines.append(f"\nСвязанные проекты ({len(related_projects)} шт.):")
        for p in related_projects[:3]:
            lines.append(f"  • {p['title']}")

    if previous_meetings:
        lines.append(f"\nПредыдущие встречи ({len(previous_meetings)} шт.):")
        for m in previous_meetings[:3]:
            lines.append(f"  • {m['date'][:10]} — {m['title']}")

    if open_action_items:
        lines.append(f"\nОткрытые поручения ({len(open_action_items)} шт.):")
        for item in open_action_items[:5]:
            lines.append(f"  • {item}")

    if not recent_emails and not related_projects and not previous_meetings:
        lines.append("\nКонтекст в vault не найден — встреча проводится впервые.")

    return "\n".join(lines)


def _mlx_brief(
    event: dict,
    recent_emails: list[dict],
    previous_meetings: list[dict],
    open_action_items: list[str],
    mlx_engine: Any,
) -> str:
    """Generate an MLX brief; falls back to rule-based on any error."""
    try:
        title = event.get("title") or "Встреча"
        participants_str = ", ".join((event.get("participants") or [])[:5]) or "не указаны"
        date_str = (event.get("event_date") or event.get("date") or "")[:19].replace("T", " ")

        email_ctx = ""
        if recent_emails:
            lines = []
            for e in recent_emails[:4]:
                lines.append(
                    f"  [{e['date'][:10]}] {e['sender']}: {e['subject']}\n"
                    f"  {e['body_snippet'][:200]}"
                )
            email_ctx = "\n".join(lines)

        meeting_ctx = ""
        if previous_meetings:
            lines = [f"  [{m['date'][:10]}] {m['title']}: {m['snippet'][:150]}"
                     for m in previous_meetings[:3]]
            meeting_ctx = "\n".join(lines)

        tasks_ctx = "\n".join(f"  - {t}" for t in open_action_items[:5]) if open_action_items else ""

        prompt_parts = [
            f"Встреча: «{title}» с {participants_str}",
        ]
        if date_str:
            prompt_parts.append(f"Дата: {date_str}")
        if email_ctx:
            prompt_parts.append(f"\nНедавние письма от участников:\n{email_ctx}")
        if meeting_ctx:
            prompt_parts.append(f"\nПредыдущие встречи:\n{meeting_ctx}")
        if tasks_ctx:
            prompt_parts.append(f"\nОткрытые поручения:\n{tasks_ctx}")
        prompt_parts.append(
            "\nСоставь краткий брифинг подготовки к встрече (3–5 пунктов, "
            "на том же языке что переписка). Включи: ключевые темы из писем, "
            "открытые вопросы, на что обратить внимание."
        )

        prompt = "\n".join(prompt_parts)
        system = (
            "Ты — деловой ассистент. Готовишь краткий брифинг к встрече "
            "на основе предоставленных данных. Отвечай структурированно, "
            "без воды, 3–5 конкретных пунктов. Отвечай только на русском языке."
        )

        brief = mlx_engine.ask(prompt, system=system, max_tokens=512)
        if brief and len(brief.strip()) > 20:
            return brief.strip()
    except Exception as exc:
        logger.warning(f"[meeting_prep] MLX brief failed, falling back to rule-based: {exc}")

    return _rule_based_brief(
        event, recent_emails, [], previous_meetings, open_action_items
    )


def _build_context_prompt(
    event: dict,
    recent_emails: list[dict],
    related_projects: list[dict],
    previous_meetings: list[dict],
    open_action_items: list[str],
    prep_brief: str,
) -> str:
    """Build the full context_prompt string for /api/chat/send."""
    title = event.get("title") or "встреча"
    participants_str = ", ".join((event.get("participants") or [])[:5]) or "не указаны"
    date_str = (event.get("event_date") or event.get("date") or "")[:19].replace("T", " ")

    lines = [
        f"Помоги подготовиться к встрече «{title}».",
        f"Участники: {participants_str}.",
    ]
    if date_str:
        lines.append(f"Дата: {date_str}.")

    lines.append("\n═══ БРИФИНГ ═══")
    lines.append(prep_brief)

    if recent_emails:
        lines.append("\n═══ ПОСЛЕДНИЕ ПИСЬМА ОТ УЧАСТНИКОВ ═══")
        for e in recent_emails[:4]:
            lines.append(
                f"[{e['date'][:10]}] {e['sender']} — {e['subject']}\n"
                f"{e['body_snippet'][:300]}"
            )

    if open_action_items:
        lines.append("\n═══ ОТКРЫТЫЕ ПОРУЧЕНИЯ ═══")
        for item in open_action_items[:5]:
            lines.append(f"• {item}")

    lines.append(
        "\n═══ ЗАДАНИЕ ═══\n"
        "Ответь на вопросы по предстоящей встрече или помоги оформить повестку."
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_meeting_prep(
    event_id: str,
    vault_path: Optional[Path] = None,
    my_email: str = "",
    mlx_engine: Any = None,
) -> dict:
    """
    Build full meeting preparation context.

    Parameters
    ----------
    event_id   : str        — Calendar event ID (frontmatter ``id:`` or file stem).
    vault_path : Path|None  — PersonalVault root; if None tries VaultIndex or config.
    my_email   : str        — User's own email (to exclude from participants).
    mlx_engine : Any|None   — Optional MLX engine for AI brief generation.

    Returns
    -------
    dict with keys:
        event_id, title, participants, participant_emails, event_date,
        recent_emails, related_projects, previous_meetings,
        open_action_items, prep_brief, context_prompt,
        event_found, message_count.
    """
    # Resolve vault path
    if vault_path is None:
        try:
            from personal_assistant.config import settings
            vault_path = settings.vault_path
        except Exception:
            pass

    # ── Step 1: find the event ───────────────────────────────────────────────
    event: dict = {}
    event_found = False

    if vault_path:
        found = _find_event_in_vault(event_id, vault_path)
        if found:
            event = found
            event_found = True

    # Fallback: try VaultIndex
    if not event_found:
        try:
            from personal_assistant.mlx_server import server as _srv
            idx = getattr(_srv.state, "index", None)
            if idx is not None:
                for doc in idx.docs:
                    doc_id = str(doc.frontmatter.get("id") or doc.path.stem).strip()
                    if doc_id == event_id:
                        fm = doc.frontmatter
                        participants = _parse_participants(fm)
                        event = {
                            "id": doc_id,
                            "title": _norm(str(fm.get("title") or fm.get("subject") or doc.path.stem)),
                            "date": str(fm.get("date") or fm.get("start") or ""),
                            "participants": participants,
                            "participant_emails": _emails_from_participants(participants),
                            "location": str(fm.get("location") or ""),
                            "body": _clean_body(doc.content),
                            "path": str(doc.path),
                        }
                        event_found = True
                        break
        except Exception as exc:
            logger.debug(f"[meeting_prep] VaultIndex lookup failed: {exc}")

    title = event.get("title") or "Без названия"
    participants = event.get("participants") or []
    participant_emails = event.get("participant_emails") or []
    event_date = event.get("date") or ""

    # Remove my own email from participants
    if my_email:
        my_lower = my_email.strip().lower()
        participant_emails = [e for e in participant_emails if e != my_lower]
        participants = [
            p for p in participants
            if (_extract_email(p) or p.strip().lower()) != my_lower
        ]

    # ── Step 2: gather context ───────────────────────────────────────────────
    recent_emails: list[dict] = []
    related_projects: list[dict] = []
    previous_meetings: list[dict] = []
    open_action_items: list[str] = []

    if vault_path:
        recent_emails = _scan_recent_emails(vault_path, participant_emails)
        related_projects = _scan_related_projects(vault_path, participants)
        previous_meetings = _scan_previous_meetings(
            vault_path, participants, exclude_event_id=event_id
        )
        open_action_items = _scan_open_action_items(vault_path, participants)

    message_count = len(recent_emails) + len(related_projects) + len(previous_meetings)

    # ── Step 3: build brief ──────────────────────────────────────────────────
    event_for_brief = {**event, "event_date": event_date}

    if mlx_engine is not None and (recent_emails or previous_meetings):
        prep_brief = _mlx_brief(
            event_for_brief, recent_emails, previous_meetings,
            open_action_items, mlx_engine
        )
    else:
        prep_brief = _rule_based_brief(
            event_for_brief, recent_emails, related_projects,
            previous_meetings, open_action_items
        )

    # ── Step 4: build context prompt ────────────────────────────────────────
    context_prompt = _build_context_prompt(
        event_for_brief, recent_emails, related_projects,
        previous_meetings, open_action_items, prep_brief
    )

    return {
        "event_id": event_id,
        "title": title,
        "participants": participants,
        "participant_emails": participant_emails,
        "event_date": event_date,
        "location": event.get("location") or "",
        "recent_emails": recent_emails,
        "related_projects": related_projects,
        "previous_meetings": previous_meetings,
        "open_action_items": open_action_items,
        "prep_brief": prep_brief,
        "context_prompt": context_prompt,
        "event_found": event_found,
        "message_count": message_count,
    }
