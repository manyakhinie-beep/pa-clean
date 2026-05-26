"""
name_extractor.py — Extract and normalise person names from email/calendar strings.

Handles common formats:
  • RFC-822 display name: "Иванов Иван <ivan@corp.ru>"
  • Plain name: "Ivan Ivanov", "ИВАНОВ ИВАН ИВАНОВИЧ"
  • Abbreviated patronymic: "Иванов И.И.", "Ivanov I.I."
  • Comma-separated (LDAP/CSV): "Иванов, Иван Иванович"

Priority when merging multiple candidate names (calendar > email > existing):
  source_priority = {"calendar": 3, "outlook": 2, "mail": 1, "contacts": 0}

Usage::

    from personal_assistant.utils.name_extractor import extract_name, best_name

    name = extract_name("Иванов Иван <ivan@corp.ru>")  # → "Иванов Иван"
    name = best_name([("ivan ivanov", "mail"), ("Иванов Иван", "calendar")])
    # → "Иванов Иван"  (calendar wins on quality AND source priority)
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Words that are never valid names (common noise)
_NOISE_WORDS: frozenset[str] = frozenset(
    {
        # Russian
        "уведомление", "нет", "ответа", "сервис", "info", "support",
        "noreply", "no-reply", "donotreply", "do-not-reply", "mailer",
        "daemon", "postmaster", "admin", "administrator", "system",
        "автоответчик", "robota", "robot", "bot", "notify", "notification",
        "alerts", "helpdesk", "helpdesk",
    }
)

# Source priority for best_name()
_SOURCE_PRIORITY: dict[str, int] = {
    "calendar": 3,
    "outlook":  2,
    "mail":     1,
    "contacts": 0,
}

# Regex: capture display name before angle-bracket email
_RFC822_RE = re.compile(
    r'^"?([^"<@\n]+?)"?\s*<[^@>]+@[^>]+>\s*$',
    re.UNICODE,
)

# Regex: detect it looks like just an email (no display part)
_EMAIL_ONLY_RE = re.compile(r'^[^\s@]+@[^\s@]+$')

# Regex: detect abbreviated initials like "И.И." or "I.I."
_INITIALS_RE = re.compile(r'\b[A-ZА-ЯЁ]\.[A-ZА-ЯЁ]\.', re.UNICODE)

# Cyrillic letter test
_CYRILLIC_RE = re.compile(r'[А-ЯЁа-яё]', re.UNICODE)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def extract_name(raw: str) -> Optional[str]:
    """Extract and normalise a display name from a raw string.

    Handles:
      - ``"Иванов Иван <ivan@corp.ru>"`` → ``"Иванов Иван"``
      - ``"ivan@corp.ru"``               → ``None``
      - ``"ИВАНОВ ИВАН"``               → ``"Иванов Иван"``
      - ``"Иванов, Иван"``              → ``"Иванов Иван"``

    :param raw: Raw display string from email header or calendar attendee field.
    :returns: Normalised name string, or ``None`` if nothing useful was found.
    """
    if not raw:
        return None

    raw = unicodedata.normalize("NFC", raw.strip())

    # Case 1: "Name <email>" format
    m = _RFC822_RE.match(raw)
    if m:
        candidate = m.group(1).strip().strip('"').strip("'")
    else:
        # Case 2: strip any trailing angle-bracket email if not matched by full regex
        candidate = re.sub(r'\s*<[^>]+>\s*$', '', raw).strip()

    # If nothing left or it's a bare email, bail
    if not candidate or _EMAIL_ONLY_RE.match(candidate):
        return None

    # Replace comma-separated "Фамилия, Имя" → "Фамилия Имя"
    if ',' in candidate and candidate.count(',') == 1:
        parts = [p.strip() for p in candidate.split(',')]
        candidate = ' '.join(parts)

    # Strip surrounding quotes
    candidate = candidate.strip('"').strip("'").strip()

    # Noise check (case-insensitive)
    lower = candidate.lower().replace('-', '').replace('.', '')
    if any(noise in lower for noise in _NOISE_WORDS):
        return None

    # Must contain at least 2 characters and at least one letter
    if len(candidate) < 2 or not re.search(r'[A-Za-zА-ЯЁа-яё]', candidate):
        return None

    return normalize_name(candidate)


def normalize_name(name: str) -> str:
    """Normalize whitespace and capitalization for a name string.

    Rules:
      - Collapse internal whitespace
      - Title-case each word (handles both Cyrillic and Latin)
      - Preserve initials like "И.И." (already capitalised)

    :param name: Raw name string.
    :returns: Normalised name.
    """
    # Normalize unicode, collapse whitespace
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[\t\r\n]+', ' ', name)
    name = re.sub(r' {2,}', ' ', name).strip()

    # Split and title-case each token
    tokens = name.split(' ')
    result: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        # Keep initials as-is (e.g. "И.И." or "I.I.")
        if re.match(r'^[A-ZА-ЯЁ]\.[A-ZА-ЯЁ]\.$', tok, re.UNICODE):
            result.append(tok.upper())
        elif _CYRILLIC_RE.search(tok):
            # Cyrillic: capitalize first letter, lowercase rest
            result.append(tok[0].upper() + tok[1:].lower() if len(tok) > 1 else tok.upper())
        else:
            # Latin: standard title case
            result.append(tok.capitalize())
    return ' '.join(result)


def name_quality(name: Optional[str]) -> int:
    """Score the quality of a name for deduplication/priority decisions.

    :returns:
      * 3 — Full ФИО: 3+ meaningful parts, no abbreviated initials
      * 2 — Two-part name (Имя Фамилия) or surname + initials ("Иванов И.И.")
      * 1 — Single word or initials only ("И.И.")
      * 0 — Empty, bare email, or nonsense
    """
    if not name or _EMAIL_ONLY_RE.match(name):
        return 0

    # Count initials groups (e.g. "И.И." counts as 1 token)
    initials_matches = _INITIALS_RE.findall(name)
    initials_count = len(initials_matches)

    # Count real word parts (2+ chars, not pure initials blocks)
    clean = re.sub(r'\b[A-ZА-ЯЁ]\.\s*', '', name, flags=re.UNICODE).strip()
    word_parts = [p for p in clean.split() if len(p) >= 2]

    total_parts = len(word_parts) + initials_count

    if total_parts >= 3 and initials_count == 0:
        return 3  # full ФИО without abbreviations
    if total_parts >= 2:
        return 2  # surname + first name, or surname + initials
    if total_parts == 1 or initials_count > 0:
        return 1  # single token or bare initials
    return 0


def best_name(
    candidates: list[tuple[str, str]],
    source_priority: Optional[dict[str, int]] = None,
) -> Optional[str]:
    """Pick the best display name from a list of ``(name, source)`` pairs.

    Selection criteria (in order):
      1. Highest ``name_quality`` score
      2. Highest source priority when quality is equal
         (default: calendar > outlook > mail > contacts)

    :param candidates: List of ``(raw_name_or_header, source_key)`` tuples.
    :param source_priority: Override the default source priority mapping.
    :returns: The best normalised name, or ``None`` if all candidates are noise.
    """
    sp = source_priority if source_priority is not None else _SOURCE_PRIORITY

    best: Optional[str] = None
    best_q = -1
    best_src_p = -1

    for raw, source in candidates:
        name = extract_name(raw)
        if not name:
            continue
        q = name_quality(name)
        src_p = sp.get(source, 0)

        if q > best_q or (q == best_q and src_p > best_src_p):
            best = name
            best_q = q
            best_src_p = src_p

    return best


def enrich_contact_name(
    existing_name: Optional[str],
    existing_source: Optional[str],
    new_name: Optional[str],
    new_source: str,
) -> tuple[Optional[str], bool]:
    """Decide whether ``new_name`` from ``new_source`` should replace ``existing_name``.

    :returns: ``(chosen_name, was_updated)`` — the winner and whether it changed.
    """
    chosen = best_name(
        [(n, s) for n, s in [
            (existing_name or '', existing_source or ''),
            (new_name or '', new_source),
        ] if n],
    )
    if chosen and chosen != existing_name:
        return chosen, True
    return existing_name, False
