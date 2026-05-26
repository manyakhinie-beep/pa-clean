"""
Lenient YAML frontmatter parser for vault ``.md`` files.

Some vault entries were written by earlier versions of the templates that had
a Jinja ``trim_blocks`` bug — adjacent fields could collapse onto one line
(e.g. ``location: "Б.38.13"tags: [календарь]``). ``yaml.safe_load`` chokes on
those, returning ``YAMLError``, which silently dropped the affected events
from "Сегодня", chat context, BM25 index and similar consumers.

This module gives a single ``parse_lenient(text)`` entry point that:

1. Tries ``yaml.safe_load`` first (fast path for well-formed files).
2. On ``YAMLError`` repairs known run-on patterns (inserts ``\\n`` before
   well-known field keys when they appear glued to a previous value) and
   tries to parse the repaired text.
3. On repeated failure returns ``{}`` and logs a one-line warning.

Used wherever frontmatter is read from vault ``.md`` files — see
``services.calendar_service``, ``calendar.routes``, ``mlx_server.context_builder``,
``mlx_server.vault_index``.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from loguru import logger

# Known frontmatter field names across calendar/mail/contact templates.
# When yaml.safe_load fails, we look for these keys appearing IMMEDIATELY
# after a closing quote / bracket on the same line as another value and
# insert a newline. Generated from the three templates in
# ``src/personal_assistant/templates/*.j2`` plus a few legacy names.
_KNOWN_KEYS = (
    # calendar
    "uid", "title", "type", "calendar", "start", "end", "all_day",
    "location", "organizer", "attendees", "attachments", "tags",
    "created", "updated",
    # mail
    "message_id", "thread_id", "source", "sender", "sender_name", "from",
    "date", "mailbox", "has_attachments", "recipients", "cc",
    # contact
    "email", "name", "full_name", "name_source", "name_updated_at",
    "organization", "phone", "sources",
    # generic
    "id", "subject", "url", "notes",
)

# Pattern: a closing quote/bracket immediately followed by a known key + colon.
# Captures the closer so we can keep it; inserts a newline before the key.
_RUNON_RE = re.compile(
    r'(["\]}])\s*(' + "|".join(_KNOWN_KEYS) + r')\s*:',
)


def _repair_runon(fm_text: str) -> str:
    """Insert ``\\n`` before known YAML keys that got glued to a previous value.

    Examples this fixes:
        ``location: "Б.38.13"tags: [календарь]``    →    two lines
        ``recipients:\\n  - "a"]cc: ["b"]``         →    two lines (rare)
    """
    return _RUNON_RE.sub(r"\1\n\2:", fm_text)


def parse_lenient(text: str) -> dict[str, Any]:
    """Parse YAML frontmatter from a vault ``.md`` file, tolerantly.

    *text* is the FULL file content (frontmatter delimited by ``---`` lines).
    Returns the frontmatter as a dict, or ``{}`` if nothing parseable.

    Recovers from a known Jinja-trim_blocks bug that produced run-on YAML
    in calendar events; see module docstring.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    if not fm_text:
        return {}

    # Fast path — well-formed YAML.
    try:
        result = yaml.safe_load(fm_text)
        if isinstance(result, dict):
            return result
        # YAML may also parse to None/list/string; treat anything non-dict as empty.
        return {}
    except yaml.YAMLError as exc_strict:
        # Try repair pass for the known run-on pattern.
        try:
            repaired = _repair_runon(fm_text)
            if repaired == fm_text:
                # Nothing to repair, raise original
                raise exc_strict
            result = yaml.safe_load(repaired)
            if isinstance(result, dict):
                logger.debug(
                    "[frontmatter] repaired run-on YAML (consider re-syncing affected vault files)"
                )
                return result
            return {}
        except yaml.YAMLError as exc_repaired:
            logger.warning(
                f"[frontmatter] YAML unparseable even after repair: {exc_repaired}"
            )
            return {}


def parse_lenient_text(fm_text: str) -> dict[str, Any]:
    """Variant that takes ALREADY-EXTRACTED frontmatter text (no ``---`` delims).

    Use this when the caller already split the frontmatter block (e.g. via a
    regex match group). Mirrors :func:`parse_lenient` but skips the delimiter
    detection.
    """
    if not fm_text or not fm_text.strip():
        return {}
    try:
        result = yaml.safe_load(fm_text)
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        try:
            repaired = _repair_runon(fm_text)
            result = yaml.safe_load(repaired)
            if isinstance(result, dict):
                logger.debug("[frontmatter] repaired run-on YAML")
                return result
        except yaml.YAMLError as exc:
            logger.warning(f"[frontmatter] unparseable: {exc}")
        return {}
