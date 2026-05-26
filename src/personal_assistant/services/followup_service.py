"""
Follow-up Detection — Этап 3 исследования умной обработки почты.

Выявляет письма, ожидающие ответа:
  • intent == "request" или reply_required == True
  • письмо не прочитано ИЛИ старше порогового значения дней
  • нет исходящего письма от текущего пользователя в том же треде

Endpoint: GET /api/v1/inbox/followup-needed
Возвращает: {items: [item_id, ...], count: int, threshold_days: int}

Настройка:
  PA_FOLLOWUP_DAYS_THRESHOLD=2   (из .env, по умолчанию 2)
  PA_USER_EMAIL                  (для определения «моих» исходящих писем)

has_outgoing_in_thread() — сканирует vault .md-файлы в секции mail/,
ищет письма с from: == my_email, у которых thread_id совпадает.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

# Default threshold: letters older than N days without reply are flagged
_DEFAULT_THRESHOLD_DAYS = int(os.environ.get("PA_FOLLOWUP_DAYS_THRESHOLD", "2"))

_EMAIL_CLEAN_RE = re.compile(r"\s*<[^>]+>")


def _parse_date(date_str: str | None) -> Optional[date]:
    if not date_str:
        return None
    try:
        s = str(date_str).strip()
        s = re.sub(r"[+-]\d{2}:\d{2}$", "", s).rstrip("Z")
        return datetime.fromisoformat(s).date()
    except (ValueError, TypeError):
        return None


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _age_days(date_str: str | None) -> int:
    """Return how many days ago the item was dated (0 if today, -1 if future)."""
    d = _parse_date(date_str)
    if d is None:
        return 0
    return (_today() - d).days


def _wants_reply(item: dict) -> bool:
    """True if extraction says reply_required or intent is request/question."""
    extraction = item.get("extraction") or {}
    if extraction.get("reply_required"):
        return True
    if extraction.get("intent") in ("request", "question"):
        return True
    # Fallback: check tags
    tags = {t.lower() for t in item.get("tags_raw", [])}
    if tags & {"urgency:urgent", "urgent", "срочно"}:
        return True
    return False


def has_outgoing_in_thread(
    thread_id: str,
    vault_path: str | Path,
    my_email: str = "",
) -> bool:
    """
    Return True if there is at least one outgoing message in *thread_id*.

    An outgoing message is one where ``from:`` / ``sender:`` in frontmatter
    matches *my_email* (case-insensitive).

    If *my_email* is empty, always returns False (can't determine outgoing).
    """
    if not thread_id or not my_email:
        return False

    my_email_lower = my_email.lower().strip()
    vault_path = Path(vault_path)

    mail_dir = vault_path / "mail"
    if not mail_dir.exists():
        mail_dir = vault_path

    for md_file in mail_dir.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        in_fm = False
        file_thread_id = ""
        file_from_email = ""

        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "---":
                if not in_fm:
                    in_fm = True
                    continue
                else:
                    break
            if not in_fm:
                continue

            lower = stripped.lower()
            if lower.startswith("thread_id:"):
                file_thread_id = re.sub(r"^thread_id\s*:\s*", "", stripped, flags=re.IGNORECASE).strip().strip('"\'')
            elif lower.startswith("from:") or lower.startswith("sender:"):
                emails = re.findall(r"[\w.+-]+@[\w.-]+\.\w{2,}", stripped)
                if emails:
                    file_from_email = emails[0].lower()
                else:
                    # Plain email without <> brackets
                    raw = re.sub(r"^[^:]+:\s*", "", stripped).strip()
                    file_from_email = _EMAIL_CLEAN_RE.sub("", raw).strip().lower()

        if file_thread_id == thread_id and file_from_email == my_email_lower:
            return True

    return False


def detect_followup_needed(
    items: list[dict],
    vault_path: Optional[str | Path] = None,
    my_email: str = "",
    threshold_days: int = _DEFAULT_THRESHOLD_DAYS,
) -> list[str]:
    """
    Return list of item_ids that need a follow-up reply.

    Conditions (ALL must hold):
      1. item is an email (not calendar)
      2. _wants_reply(item) == True  (extraction intent or tags)
      3. age_days >= threshold_days  (letter has been waiting long enough)
      4. has_outgoing_in_thread() == False  (we haven't replied yet)

    Parameters
    ----------
    items:          list of inbox items (from _doc_to_item)
    vault_path:     path to vault root (used to scan for outgoing mail)
    my_email:       user's own email (to identify outgoing messages)
    threshold_days: flag letters older than this many days

    Returns list of item_ids (strings).
    """
    result: list[str] = []

    for item in items:
        # Only flag email items
        if item.get("type") == "meeting":
            continue

        if not _wants_reply(item):
            continue

        age = _age_days(item.get("date"))
        if age < threshold_days:
            continue

        # Check if we already replied in thread
        thread_id = item.get("thread_id", "")
        if vault_path and my_email and thread_id:
            try:
                if has_outgoing_in_thread(thread_id, vault_path, my_email):
                    continue
            except Exception as exc:
                logger.debug(f"[followup] thread check failed for {item.get('id')}: {exc}")

        result.append(str(item.get("id", "")))

    return [r for r in result if r]  # strip empty strings


def enrich_with_followup(
    items: list[dict],
    vault_path: Optional[str | Path] = None,
    my_email: str = "",
    threshold_days: int = _DEFAULT_THRESHOLD_DAYS,
) -> list[dict]:
    """
    Add ``followup_needed`` (bool) field to each item in-place.
    Returns the same list (mutated).
    """
    flagged = set(detect_followup_needed(items, vault_path, my_email, threshold_days))
    for item in items:
        item["followup_needed"] = str(item.get("id", "")) in flagged
    return items
