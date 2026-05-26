"""
Dedup engine — prevents duplicates when merging data from multiple sources.

Strategy:
  1. Primary key:   source-specific ID  (message_id / event uid)
  2. Content hash:  SHA-256 of normalised fields (subject + date + sender)
  3. On collision:  keep the richer record (more fields, more recent)

Usage:
    engine = DedupEngine()
    messages = engine.dedup_messages([...list of MailMessage...])
    events   = engine.dedup_events([...list of CalendarEvent...])
    stats    = engine.stats         # {"messages": {"kept": N, "dropped": N}, ...}

The engine is stateless between calls — instantiate fresh for each sync run,
or call reset() to clear internal bookkeeping.
"""

from __future__ import annotations

import hashlib
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from personal_assistant.models import CalendarEvent, MailMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _norm_subject(subject: str) -> str:
    """Normalise email subject for fingerprinting: strip Re/Fwd, lowercase."""
    import re
    s = _nfc(subject).strip()
    # Strip Re: / Fwd: / Отв: / Пер: etc. repeatedly
    pat = re.compile(
        r"^(?:re|fwd?|отв|пер|aw|wg|sv|tr|vs|ref|rv)\s*:\s*",
        re.IGNORECASE,
    )
    prev = None
    while s != prev:
        prev = s
        s = pat.sub("", s)
    return s.strip().lower()


def _dt_str(dt: Optional[datetime]) -> str:
    """ISO date string (UTC) for stable hashing."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _sha8(text: str) -> str:
    """First 8 hex chars of SHA-256."""
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _sha16(text: str) -> str:
    """First 16 hex chars of SHA-256."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Content fingerprints
# ---------------------------------------------------------------------------

def message_fingerprint(msg: MailMessage) -> str:
    """
    Stable hash for a mail message, independent of source.

    Key: subject (normalised) + sender_email (lower) + date (UTC, minute precision)
    """
    key = "|".join([
        _norm_subject(msg.subject),
        (msg.sender_email or "").strip().lower(),
        _dt_str(msg.date),
    ])
    return _sha16(key)


def event_fingerprint(ev: CalendarEvent) -> str:
    """
    Stable hash for a calendar event, independent of source.

    Key: title (normalised lower) + start (UTC, minute precision)
    """
    key = "|".join([
        _nfc(ev.title).strip().lower(),
        _dt_str(ev.start),
    ])
    return _sha16(key)


# ---------------------------------------------------------------------------
# Richness scoring — prefer richer records on collision
# ---------------------------------------------------------------------------

def _message_richness(msg: MailMessage) -> int:
    score = 0
    if msg.body:
        score += len(msg.body)
    if msg.attachments:
        score += len(msg.attachments) * 10
    if msg.recipients:
        score += len(msg.recipients)
    if msg.thread_id:
        score += 5
    return score


def _event_richness(ev: CalendarEvent) -> int:
    score = 0
    if ev.notes:
        score += len(ev.notes)
    if ev.url:
        score += 20
    if ev.location:
        score += 10
    if ev.attendees:
        score += len(ev.attendees) * 5
    if ev.attachments:
        score += len(ev.attachments) * 10
    return score


# ---------------------------------------------------------------------------
# DedupEngine
# ---------------------------------------------------------------------------

class DedupEngine:
    """
    Deduplicates MailMessage and CalendarEvent lists across sources.

    Tracks both primary keys (message_id / uid) and content fingerprints
    so that the same email/event imported multiple times is stored only once.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        # message_id → fingerprint
        self._msg_ids: dict[str, str] = {}
        # fingerprint → chosen MailMessage
        self._msg_fp: dict[str, MailMessage] = {}

        # uid → fingerprint
        self._ev_uids: dict[str, str] = {}
        # fingerprint → chosen CalendarEvent
        self._ev_fp: dict[str, CalendarEvent] = {}

        self._stats: dict[str, dict[str, int]] = {
            "messages": {"kept": 0, "dropped": 0, "upgraded": 0},
            "events":   {"kept": 0, "dropped": 0, "upgraded": 0},
        }

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def add_message(self, msg: MailMessage) -> bool:
        """
        Add a message to the dedup store.

        Returns True if this message will appear in the final output
        (kept or upgraded an existing entry), False if dropped.
        """
        fp = message_fingerprint(msg)
        mid = msg.message_id

        # Check by primary ID first
        if mid in self._msg_ids:
            existing_fp = self._msg_ids[mid]
            existing = self._msg_fp.get(existing_fp)
            if existing is not None and _message_richness(msg) > _message_richness(existing):
                # Upgrade: richer version of same ID
                self._msg_fp[existing_fp] = msg
                self._stats["messages"]["upgraded"] += 1
                logger.debug(f"[dedup] message upgraded: {mid!r} ({msg.source} > {existing.source})")
                return True
            logger.debug(f"[dedup] message dropped (same id): {mid!r}")
            self._stats["messages"]["dropped"] += 1
            return False

        # Check by content fingerprint (same email, different source)
        if fp in self._msg_fp:
            existing = self._msg_fp[fp]
            if _message_richness(msg) > _message_richness(existing):
                # Replace with richer record, keep its ID mapping too
                old_mid = existing.message_id
                self._msg_fp[fp] = msg
                self._msg_ids[old_mid] = fp
                self._msg_ids[mid] = fp
                self._stats["messages"]["upgraded"] += 1
                logger.debug(
                    f"[dedup] message upgraded (fp collision): {mid!r} "
                    f"({msg.source} > {existing.source})"
                )
                return True
            logger.debug(
                f"[dedup] message dropped (content fp): {mid!r} ~ {existing.message_id!r}"
            )
            self._msg_ids[mid] = fp          # register ID → existing fp
            self._stats["messages"]["dropped"] += 1
            return False

        # New unique message
        self._msg_ids[mid] = fp
        self._msg_fp[fp] = msg
        self._stats["messages"]["kept"] += 1
        return True

    def dedup_messages(self, messages: list[MailMessage]) -> list[MailMessage]:
        """Deduplicate a list of messages. Returns unique list (insertion-stable)."""
        self.reset()
        for msg in messages:
            self.add_message(msg)
        result = list(self._msg_fp.values())
        logger.info(
            f"[dedup] messages: {self._stats['messages']['kept']} kept, "
            f"{self._stats['messages']['dropped']} dropped, "
            f"{self._stats['messages']['upgraded']} upgraded"
        )
        return result

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def add_event(self, ev: CalendarEvent) -> bool:
        """
        Add an event to the dedup store.

        Returns True if kept/upgraded, False if dropped.
        """
        fp = event_fingerprint(ev)
        uid = ev.uid

        if uid in self._ev_uids:
            existing_fp = self._ev_uids[uid]
            existing = self._ev_fp.get(existing_fp)
            if existing is not None and _event_richness(ev) > _event_richness(existing):
                self._ev_fp[existing_fp] = ev
                self._stats["events"]["upgraded"] += 1
                logger.debug(f"[dedup] event upgraded: {uid!r}")
                return True
            self._stats["events"]["dropped"] += 1
            return False

        if fp in self._ev_fp:
            existing = self._ev_fp[fp]
            if _event_richness(ev) > _event_richness(existing):
                old_uid = existing.uid
                self._ev_fp[fp] = ev
                self._ev_uids[old_uid] = fp
                self._ev_uids[uid] = fp
                self._stats["events"]["upgraded"] += 1
                logger.debug(f"[dedup] event upgraded (fp): {uid!r}")
                return True
            self._ev_uids[uid] = fp
            self._stats["events"]["dropped"] += 1
            return False

        self._ev_uids[uid] = fp
        self._ev_fp[fp] = ev
        self._stats["events"]["kept"] += 1
        return True

    def dedup_events(self, events: list[CalendarEvent]) -> list[CalendarEvent]:
        """Deduplicate a list of events. Returns unique list."""
        self.reset()
        for ev in events:
            self.add_event(ev)
        result = list(self._ev_fp.values())
        logger.info(
            f"[dedup] events: {self._stats['events']['kept']} kept, "
            f"{self._stats['events']['dropped']} dropped, "
            f"{self._stats['events']['upgraded']} upgraded"
        )
        return result

    def dedup_all(
        self,
        messages: list[MailMessage],
        events: list[CalendarEvent],
    ) -> tuple[list[MailMessage], list[CalendarEvent]]:
        """Convenience: dedup both lists in one call."""
        unique_msgs = self.dedup_messages(messages)
        unique_evs  = self.dedup_events(events)
        return unique_msgs, unique_evs
