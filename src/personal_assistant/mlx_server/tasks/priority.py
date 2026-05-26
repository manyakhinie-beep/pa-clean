"""
AI Priority Score — Этап 2 исследования умной обработки почты.

Алгоритм — гибридный, не требует MLX:
  1. Urgency tags      (+40 срочно / +20 важно)
  2. Reply required    (+15)
  3. Sender frequency  (+0..+15 по contact_graph)
  4. Deadline proximity(+25 сегодня..+10 на неделе)
  5. Recency penalty   (−0..−20 за возраст письма)
  6. Unread bonus      (+5)
  ─────────────────────────────────────────────
  Итого: max(0, min(100, sum))

Опционально: MLX boost для «пограничных» писем (30–60):
  «Оцени важность письма от 0 до 10. Только цифру.»
  — конвертируется в ±5 корректировку итогового score.

Graceful degradation:
  • MLX недоступен → rule-based score без коррекции
  • contact_graph пустой → sender_freq = 0
  • Невалидная дата → deadline / recency компоненты = 0

build_contact_graph(vault_path) — сканирует vault .md-файлы,
  считает частоту отправителей → dict[email, {freq, name}].
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Tag constants (mirror inbox/routes.py)
# ---------------------------------------------------------------------------

_TAG_URGENT = {"срочно", "urgency:critical", "urgency:high", "urgency:urgent", "urgent"}
_TAG_IMPORT = {
    "важно", "important", "urgency:medium", "urgency:important",
    "category:finance", "category:legal", "finance", "finances", "финансы",
}

# ---------------------------------------------------------------------------
# Contact graph
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")
_FROM_FRONT_RE = re.compile(
    r"^(?:from|sender|sender_name|от)\s*:\s*(.+)$", re.IGNORECASE
)


def build_contact_graph(vault_path: str | Path) -> dict[str, dict]:
    """
    Scan all .md files under *vault_path* and build a contact frequency map.

    Returns:
        {
          "ivan@corp.ru": {"freq": 12, "name": "Иван Петров"},
          ...
        }

    Only emails from the ``mail/`` section are counted.
    """
    vault_path = Path(vault_path)
    graph: dict[str, dict] = {}

    mail_dir = vault_path / "mail"
    if not mail_dir.exists():
        # Try scanning all markdown files
        mail_dir = vault_path

    for md_file in mail_dir.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # Extract email from frontmatter: from: / sender: / sender_name:
        email_match = None
        name_match = None
        in_frontmatter = False
        for line in text.splitlines():
            if line.strip() == "---":
                if not in_frontmatter:
                    in_frontmatter = True
                    continue
                else:
                    break  # end of frontmatter
            if not in_frontmatter:
                continue
            lower = line.lower()
            if lower.startswith("from:") or lower.startswith("sender:"):
                emails = _EMAIL_RE.findall(line)
                if emails:
                    email_match = emails[0].lower()
                    # Extract name (everything before <email>)
                    raw = re.sub(r"^[^:]+:\s*", "", line).strip()
                    name_part = re.sub(r"<[^>]+>", "", raw).strip().strip('"').strip("'")
                    if name_part and name_part != email_match:
                        name_match = name_part
            elif lower.startswith("sender_name:"):
                raw = re.sub(r"^[^:]+:\s*", "", line).strip().strip('"').strip("'")
                if raw:
                    name_match = raw

        if email_match:
            entry = graph.setdefault(email_match, {"freq": 0, "name": email_match})
            entry["freq"] += 1
            if name_match and entry["name"] == email_match:
                entry["name"] = name_match

    return graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(date_str: str | None) -> Optional[date]:
    """Parse ISO date string → date, returning None on failure."""
    if not date_str:
        return None
    try:
        s = str(date_str).strip()
        # Strip timezone suffix for parsing
        s = re.sub(r"[+-]\d{2}:\d{2}$", "", s).rstrip("Z")
        dt = datetime.fromisoformat(s)
        return dt.date()
    except (ValueError, TypeError):
        return None


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _urgency_score(tags_raw: list[str], is_urgent: bool, is_important: bool) -> int:
    """Urgency component: +40 urgent, +20 important."""
    tags_lower = {t.lower() for t in tags_raw}
    if is_urgent or tags_lower & _TAG_URGENT:
        return 40
    if is_important or tags_lower & _TAG_IMPORT:
        return 20
    return 0


def _deadline_score(item: dict, today: date) -> int:
    """Deadline proximity component: +25/+15/+10 based on days_left."""
    # Check extraction deadline first, then tags
    extraction = item.get("extraction") or {}
    deadline_str = extraction.get("deadline")

    if not deadline_str:
        # Check tags for deadline hints like 'deadline:today', 'deadline:tomorrow'
        for tag in item.get("tags_raw", []):
            t = tag.lower()
            if "deadline:today" in t or "срок:сегодня" in t:
                return 25
            if "deadline:tomorrow" in t or "срок:завтра" in t:
                return 15
            if "deadline:this_week" in t or "this_week" in t:
                return 10
        return 0

    deadline = _parse_date(deadline_str)
    if deadline is None:
        return 0

    days_left = (deadline - today).days
    if days_left <= 0:    # overdue or today
        return 25
    if days_left == 1:
        return 20
    if days_left <= 3:
        return 15
    if days_left <= 7:
        return 10
    return 5


def _sender_score(sender_email: str, contact_graph: dict) -> int:
    """Sender importance: 0–15 based on communication frequency."""
    if not sender_email or not contact_graph:
        return 0
    entry = contact_graph.get(sender_email.lower(), {})
    freq = int(entry.get("freq", 0))
    # Cap at 15: 1 email → 3pts, 5 → 15pts
    return min(15, freq * 3)


def _recency_penalty(item: dict, today: date) -> int:
    """Age penalty: −2 per day, capped at −20."""
    date_str = item.get("date")
    if not date_str:
        return 0
    item_date = _parse_date(str(date_str))
    if item_date is None:
        return 0
    age_days = (today - item_date).days
    if age_days < 0:
        return 0  # future-dated items get no penalty
    return min(20, age_days * 2)


def _reply_score(item: dict) -> int:
    """Reply-required component: +15."""
    extraction = item.get("extraction") or {}
    if extraction.get("reply_required"):
        return 15
    # Also check intent
    intent = extraction.get("intent", "")
    if intent in ("request", "question"):
        return 10
    return 0


def _unread_bonus(item: dict) -> int:
    """Unread bonus: +5."""
    return 5 if not item.get("read", False) else 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_priority(
    item: dict,
    contact_graph: Optional[dict] = None,
    mlx_engine=None,
    today: Optional[date] = None,
) -> int:
    """
    Compute priority score 0–100 for an inbox item.

    Parameters
    ----------
    item:           dict from _doc_to_item (must have tags_raw, is_urgent,
                    is_important, date, read, extraction, sender_email)
    contact_graph:  result of build_contact_graph(); pass None to skip
    mlx_engine:     MLXEngine instance; if provided and score is 30–60 (borderline),
                    a fast one-shot prompt asks the model to adjust ±5
    today:          anchor date (default: UTC today)

    Returns
    -------
    int in [0, 100]
    """
    today = today or _today_utc()
    cg = contact_graph or {}

    score = 0

    # Component 1: urgency tags
    score += _urgency_score(item.get("tags_raw", []), item.get("is_urgent", False), item.get("is_important", False))

    # Component 2: reply required / request intent
    score += _reply_score(item)

    # Component 3: sender frequency
    score += _sender_score(item.get("sender_email", ""), cg)

    # Component 4: deadline proximity
    score += _deadline_score(item, today)

    # Component 5: age penalty
    score -= _recency_penalty(item, today)

    # Component 6: unread bonus
    score += _unread_bonus(item)

    rule_score = max(0, min(100, score))

    # Optional: MLX micro-boost for borderline items (30–60)
    if mlx_engine is not None and 30 <= rule_score <= 60:
        try:
            boost = _mlx_boost(item, mlx_engine)
            rule_score = max(0, min(100, rule_score + boost))
        except Exception as exc:
            logger.debug(f"[priority] MLX boost skipped: {exc}")

    return rule_score


def _mlx_boost(item: dict, mlx_engine) -> int:
    """
    Ask MLX to rate the importance 0–10 → returns ±5 adjustment.

    Keeps the prompt minimal to stay fast (< 512 tokens).
    """
    extraction = item.get("extraction") or {}
    summary = (
        extraction.get("summary_one_line")
        or item.get("subject", "")
        or item.get("body_preview", "")[:120]
    )
    if not summary:
        return 0

    sender = item.get("sender_name", "") or item.get("sender_email", "")
    prompt = (
        f"Оцени важность письма по шкале 0–10. Только одну цифру, без пояснений.\n"
        f"От: {sender}\n"
        f"Суть: {summary}"
    )
    try:
        raw = mlx_engine.generate(prompt, max_tokens=4, temperature=0.0).strip()
        # extract first digit
        m = re.search(r"\d+", raw)
        if not m:
            return 0
        mlx_score = min(10, int(m.group()))
        # map 0–10 → −5..+5 relative to 5 (neutral)
        return (mlx_score - 5)
    except Exception:
        return 0


def priority_label(score: int) -> str:
    """Return human-readable label for a priority score."""
    if score >= 67:
        return "high"
    if score >= 34:
        return "medium"
    return "low"


def priority_color(score: int) -> str:
    """Return CSS class modifier for priority bar (matches SCSS)."""
    if score >= 67:
        return "high"
    if score >= 34:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Batch priority for the full inbox
# ---------------------------------------------------------------------------

def enrich_with_priority(
    items: list[dict],
    vault_path: Optional[str | Path] = None,
    mlx_engine=None,
) -> list[dict]:
    """
    Add ``priority`` and ``priority_label`` fields to each item in-place.

    Builds contact_graph once from vault_path (if provided) and applies
    compute_priority to every item.

    Returns the same list (mutated).
    """
    cg: dict = {}
    if vault_path:
        try:
            cg = build_contact_graph(vault_path)
        except Exception as exc:
            logger.warning(f"[priority] contact_graph build failed: {exc}")

    today = _today_utc()

    for item in items:
        p = compute_priority(item, contact_graph=cg, mlx_engine=mlx_engine, today=today)
        item["priority"] = p
        item["priority_label"] = priority_label(p)

    return items
