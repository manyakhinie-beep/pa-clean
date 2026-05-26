"""
Thread Participant Graph Service
=================================
Для заданного thread_id сканирует vault, строит граф участников переписки:
- Кто инициировал тред
- Кто активно участвует (писал письма)
- Кто наблюдатель (только CC)
- Чья сейчас очередь отвечать (my_turn)
- Сколько дней прошло без ответа пользователя
- Хронологический timeline сообщений в треде

Использование:
    from personal_assistant.services.thread_graph_service import build_thread_graph
    graph = build_thread_graph(thread_id="abc123", docs=vault_index.docs)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Participant:
    """Single participant in a thread."""
    email: str
    name: str
    is_me: bool = False
    role: str = "responder"   # "initiator" | "responder" | "observer" (CC-only)
    messages_sent: int = 0
    last_message_date: Optional[str] = None
    # Derived
    initials: str = ""
    avatar_color: str = ""


@dataclass
class TimelineEntry:
    """One message in the thread timeline."""
    date: str               # ISO string
    date_display: str       # human-readable (DD Mon, HH:MM)
    subject: str
    sender_name: str
    sender_email: str
    is_me: bool
    item_id: str            # vault doc id (for navigation)
    path: str


@dataclass
class ThreadGraph:
    """Complete participant graph for a thread."""
    thread_id: str
    subject: str            # canonical (de-prefixed) subject
    message_count: int
    participant_count: int
    participants: list[Participant] = field(default_factory=list)
    initiator: Optional[Participant] = None    # first sender
    last_sender: Optional[Participant] = None  # most recent sender
    my_turn: bool = False           # last message not from me → I should reply
    days_without_reply: int = 0     # days since last external message (if my_turn)
    timeline: list[TimelineEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPLY_PREFIX_RE = re.compile(
    r"^\s*(re|fwd?|aw|tr|sv|rv|отв|пер)\s*(\[\d+\])?\s*:\s*",
    flags=re.IGNORECASE,
)

_AVATAR_COLORS = [
    "#6366f1", "#8b5cf6", "#ec4899", "#f43f5e",
    "#f97316", "#eab308", "#22c55e", "#14b8a6",
    "#0ea5e9", "#3b82f6",
]


def _strip_reply_prefix(subject: str) -> str:
    """Remove Re:/Fwd:/Отв: prefixes repeatedly."""
    s = subject
    prev = None
    while s != prev:
        prev = s
        s = _REPLY_PREFIX_RE.sub("", s)
    return s.strip()


def _normalize_email(addr: str) -> str:
    """Lowercase and strip an email address."""
    _, email = parseaddr(addr)
    return (email or addr).strip().lower()


def _short_name(name: str, email: str) -> str:
    """Return best display name from name or email local-part."""
    if name and name.strip() and name.strip() != email:
        return name.strip()
    local = email.split("@")[0] if "@" in email else email
    return local.replace(".", " ").replace("_", " ").title()


def _initials(name: str) -> str:
    """Return 1-2 initials from a display name."""
    parts = name.split()
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _avatar_color(email: str) -> str:
    idx = sum(ord(c) for c in email) % len(_AVATAR_COLORS)
    return _AVATAR_COLORS[idx]


def _parse_iso(date_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 date string to datetime (UTC-aware)."""
    if not date_str:
        return None
    try:
        s = str(date_str).strip()
        # Handle 'YYYY-MM-DD' without time component
        if len(s) == 10:
            s += "T00:00:00+00:00"
        # Replace space-separated offset like "+03:00" notation issues
        s = s.replace(" ", "T")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _format_date_display(dt: Optional[datetime]) -> str:
    """Human-readable date: 'DD Mon, HH:MM'."""
    if dt is None:
        return ""
    months = ["янв", "фев", "мар", "апр", "май", "июн",
               "июл", "авг", "сен", "окт", "ноя", "дек"]
    return f"{dt.day} {months[dt.month - 1]}, {dt.hour:02d}:{dt.minute:02d}"


def _days_since(dt: Optional[datetime]) -> int:
    """Return whole days from dt to now (UTC)."""
    if dt is None:
        return 0
    now = datetime.now(tz=timezone.utc)
    delta = now - dt
    return max(0, delta.days)


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_thread_graph(
    thread_id: str,
    docs: list,           # list[VaultDoc] from VaultIndex
    my_email: str = "",   # user's email from profile
    my_name: str = "",    # user's display name
) -> Optional[ThreadGraph]:
    """
    Build a ThreadGraph for *thread_id* from *docs*.

    Args:
        thread_id:  MD5 thread identifier (from vault frontmatter).
        docs:       All VaultDoc objects from VaultIndex.
        my_email:   User's own email (for is_me detection).
        my_name:    User's display name.

    Returns:
        ThreadGraph or None if no messages found for this thread_id.
    """
    # ------------------------------------------------------------------
    # 1. Filter docs belonging to this thread
    # ------------------------------------------------------------------
    thread_docs = [
        d for d in docs
        if d.section == "mail"
        and str(d.frontmatter.get("thread_id", "")).strip() == thread_id
    ]

    if not thread_docs:
        logger.debug(f"[thread_graph] No docs for thread_id={thread_id!r}")
        return None

    my_email_norm = _normalize_email(my_email) if my_email else ""

    # ------------------------------------------------------------------
    # 2. Sort chronologically
    # ------------------------------------------------------------------
    def _sort_key(doc) -> datetime:
        dt = _parse_iso(doc.date)
        return dt if dt else datetime.min.replace(tzinfo=timezone.utc)

    thread_docs = sorted(thread_docs, key=_sort_key)

    # ------------------------------------------------------------------
    # 3. Build participant index
    # ------------------------------------------------------------------
    # email_norm → Participant
    participants: dict[str, Participant] = {}

    # Collect all CC addresses per doc (observers if they never sent)
    cc_only_emails: set[str] = set()

    canonical_subject = _strip_reply_prefix(thread_docs[0].frontmatter.get("subject", "") or thread_docs[0].path.stem)

    for doc in thread_docs:
        fm = doc.frontmatter

        # --- Sender ---
        sender_raw = str(fm.get("sender") or fm.get("from") or fm.get("sender_name") or "").strip()
        sender_email_raw = str(fm.get("sender_email") or doc.sender_email or "").strip()

        # Try to extract email if sender_raw looks like "Name <email>"
        if not sender_email_raw and sender_raw:
            _, extracted = parseaddr(sender_raw)
            if extracted and "@" in extracted:
                sender_email_raw = extracted

        s_email = _normalize_email(sender_email_raw) if sender_email_raw else ""
        s_name  = _short_name(sender_raw, s_email)
        s_is_me = bool(my_email_norm and s_email == my_email_norm)

        doc_date = doc.date
        doc_dt   = _parse_iso(doc_date)

        if s_email:
            if s_email not in participants:
                participants[s_email] = Participant(
                    email=s_email,
                    name=s_name,
                    is_me=s_is_me,
                    role="initiator" if not participants else "responder",
                    messages_sent=0,
                    initials=_initials(s_name),
                    avatar_color=_avatar_color(s_email),
                )
            p = participants[s_email]
            p.messages_sent += 1
            # If this participant was CC-only before, upgrade role to responder
            # (they replied, so they're an active participant now)
            if s_email in cc_only_emails:
                cc_only_emails.discard(s_email)
                if p.role == "observer":
                    p.role = "responder"
            if doc_date:
                if p.last_message_date is None or doc_date > p.last_message_date:
                    p.last_message_date = doc_date
            # Refresh name if it's better (longer, more informative)
            if len(s_name) > len(p.name):
                p.name = s_name
                p.initials = _initials(s_name)

        # --- To recipients ---
        recipients_raw = fm.get("recipients") or fm.get("to") or []
        if isinstance(recipients_raw, str):
            recipients_raw = [r.strip() for r in recipients_raw.split(",") if r.strip()]

        for addr in recipients_raw:
            r_email = _normalize_email(addr)
            _, r_name_raw = parseaddr(addr)
            r_name = _short_name(r_name_raw or "", r_email)
            r_is_me = bool(my_email_norm and r_email == my_email_norm)
            if r_email and r_email not in participants:
                participants[r_email] = Participant(
                    email=r_email,
                    name=r_name,
                    is_me=r_is_me,
                    role="responder",
                    messages_sent=0,
                    initials=_initials(r_name),
                    avatar_color=_avatar_color(r_email),
                )

        # --- CC ---
        cc_raw = fm.get("cc") or []
        if isinstance(cc_raw, str):
            cc_raw = [c.strip() for c in cc_raw.split(",") if c.strip()]

        for addr in cc_raw:
            c_email = _normalize_email(addr)
            _, c_name_raw = parseaddr(addr)
            c_name = _short_name(c_name_raw or "", c_email)
            c_is_me = bool(my_email_norm and c_email == my_email_norm)
            if c_email and c_email not in participants:
                participants[c_email] = Participant(
                    email=c_email,
                    name=c_name,
                    is_me=c_is_me,
                    role="observer",
                    messages_sent=0,
                    initials=_initials(c_name),
                    avatar_color=_avatar_color(c_email),
                )
                cc_only_emails.add(c_email)
            elif c_email in cc_only_emails:
                # Still CC-only
                pass

    # ------------------------------------------------------------------
    # 4. Assign "observer" role to CC-only participants
    # ------------------------------------------------------------------
    for email in cc_only_emails:
        if email in participants and participants[email].messages_sent == 0:
            participants[email].role = "observer"

    # Make sure initiator role is assigned to first actual sender
    # (first doc's sender gets role="initiator" already above)

    # ------------------------------------------------------------------
    # 5. Build timeline
    # ------------------------------------------------------------------
    timeline: list[TimelineEntry] = []
    for doc in thread_docs:
        fm = doc.frontmatter
        sender_raw = str(fm.get("sender") or fm.get("from") or fm.get("sender_name") or "").strip()
        sender_email_raw = str(fm.get("sender_email") or doc.sender_email or "").strip()
        if not sender_email_raw and sender_raw:
            _, extracted = parseaddr(sender_raw)
            if extracted and "@" in extracted:
                sender_email_raw = extracted
        s_email = _normalize_email(sender_email_raw) if sender_email_raw else ""
        s_name  = _short_name(sender_raw, s_email)
        s_is_me = bool(my_email_norm and s_email == my_email_norm)
        doc_dt  = _parse_iso(doc.date)
        item_id = str(fm.get("id") or doc.path.stem)
        subj    = str(fm.get("subject") or fm.get("title") or doc.path.stem).strip()

        timeline.append(TimelineEntry(
            date=doc.date or "",
            date_display=_format_date_display(doc_dt),
            subject=subj,
            sender_name=s_name,
            sender_email=s_email,
            is_me=s_is_me,
            item_id=item_id,
            path=str(doc.path),
        ))

    # ------------------------------------------------------------------
    # 6. Determine my_turn and days_without_reply
    # ------------------------------------------------------------------
    my_turn = False
    days_without_reply = 0

    if timeline and my_email_norm:
        last_entry = timeline[-1]
        if not last_entry.is_me:
            my_turn = True
            last_dt = _parse_iso(last_entry.date)
            days_without_reply = _days_since(last_dt)

    # ------------------------------------------------------------------
    # 7. Initiator / last_sender
    # ------------------------------------------------------------------
    # Initiator = sender of the chronologically first message
    initiator: Optional[Participant] = None
    if timeline:
        first_email = timeline[0].sender_email
        initiator = participants.get(first_email)

    # Last sender = sender of the last message
    last_sender: Optional[Participant] = None
    if timeline:
        last_email = timeline[-1].sender_email
        last_sender = participants.get(last_email)

    # ------------------------------------------------------------------
    # 8. Sort participants: initiator first, then by messages_sent desc,
    #    me always appears (even if messages_sent=0 — added as recipient)
    # ------------------------------------------------------------------
    def _participant_sort_key(p: Participant):
        role_order = {"initiator": 0, "responder": 1, "observer": 2}
        return (role_order.get(p.role, 9), -p.messages_sent)

    sorted_participants = sorted(participants.values(), key=_participant_sort_key)

    # ------------------------------------------------------------------
    # 9. If user's email not in participants but known, add as implicit recipient
    # ------------------------------------------------------------------
    if my_email_norm and my_email_norm not in participants and my_name:
        me = Participant(
            email=my_email_norm,
            name=my_name or my_email_norm.split("@")[0].title(),
            is_me=True,
            role="responder",
            messages_sent=0,
            initials=_initials(my_name or my_email_norm),
            avatar_color=_avatar_color(my_email_norm),
        )
        sorted_participants.append(me)

    graph = ThreadGraph(
        thread_id=thread_id,
        subject=canonical_subject,
        message_count=len(thread_docs),
        participant_count=len(sorted_participants),
        participants=sorted_participants,
        initiator=initiator,
        last_sender=last_sender,
        my_turn=my_turn,
        days_without_reply=days_without_reply,
        timeline=timeline,
    )

    logger.debug(
        f"[thread_graph] thread={thread_id!r} "
        f"msgs={graph.message_count} "
        f"participants={graph.participant_count} "
        f"my_turn={my_turn}"
    )

    return graph


def graph_to_dict(g: ThreadGraph) -> dict:
    """Serialize ThreadGraph to a JSON-serializable dict."""
    def _p(p: Participant) -> dict:
        return {
            "email": p.email,
            "name": p.name,
            "initials": p.initials,
            "avatar_color": p.avatar_color,
            "role": p.role,
            "is_me": p.is_me,
            "messages_sent": p.messages_sent,
            "last_message_date": p.last_message_date,
        }

    def _t(t: TimelineEntry) -> dict:
        return {
            "date": t.date,
            "date_display": t.date_display,
            "subject": t.subject,
            "sender_name": t.sender_name,
            "sender_email": t.sender_email,
            "is_me": t.is_me,
            "item_id": t.item_id,
            "path": t.path,
        }

    return {
        "thread_id": g.thread_id,
        "subject": g.subject,
        "message_count": g.message_count,
        "participant_count": g.participant_count,
        "participants": [_p(p) for p in g.participants],
        "initiator": _p(g.initiator) if g.initiator else None,
        "last_sender": _p(g.last_sender) if g.last_sender else None,
        "my_turn": g.my_turn,
        "days_without_reply": g.days_without_reply,
        "timeline": [_t(t) for t in g.timeline],
    }
