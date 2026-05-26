"""
Thread tracker — groups messages into email threads and events into meeting series.

Email threading strategy (priority order):
  1. thread_id already set by the reader (e.g. derived from RFC 2822 headers)
  2. In-Reply-To / References headers in message body (RFC 2822)
  3. Normalised subject hash (same as compute_thread_id in applescript_base)

Meeting series strategy:
  1. CalendarEvent.uid prefix before '#' or ':' — iCal recurrence ID pattern
  2. Normalised (title + organizer) within ±3-day windows

Output:
  thread_id stored on each MailMessage (in-place mutation, returns same list)
  series_id stored on each CalendarEvent via .uid suffix-free grouping

Usage:
    tracker = ThreadTracker()
    messages = tracker.group_messages(messages)
    events   = tracker.group_events(events)
    threads  = tracker.thread_index   # {thread_id: [MailMessage, ...]}
    series   = tracker.series_index   # {series_id: [CalendarEvent, ...]}
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from typing import Optional

from loguru import logger

from personal_assistant.models import CalendarEvent, MailMessage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPLY_PREFIX = re.compile(
    r"^(?:re|fwd?|fw|отв|пер|aw|wg|sv|tr|vs|ref|rv)\s*:\s*",
    re.IGNORECASE,
)

_MSG_ID_RE = re.compile(r"<([^>]+)>")


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _norm_subject(subject: str) -> str:
    """Strip reply/forward prefixes, lowercase, NFC-normalize."""
    s = _nfc(subject).strip()
    prev = None
    while s != prev:
        prev = s
        s = _REPLY_PREFIX.sub("", s)
    return s.strip().lower()


def _tid(key: str) -> str:
    """12-char hex thread/series ID from a stable key string."""
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _extract_refs(body: Optional[str]) -> tuple[Optional[str], list[str]]:
    """
    Extract In-Reply-To and References message-IDs from raw body text.

    Returns (in_reply_to_id, [ref_id, ...]).
    Only works when PA_MAIL_FETCH_BODY=true; returns (None, []) otherwise.
    """
    if not body:
        return None, []

    in_reply_to: Optional[str] = None
    refs: list[str] = []

    for line in body.splitlines():
        ll = line.strip()
        if ll.lower().startswith("in-reply-to:"):
            ids = _MSG_ID_RE.findall(ll)
            if ids:
                in_reply_to = ids[0]
        elif ll.lower().startswith("references:"):
            refs = _MSG_ID_RE.findall(ll)

    return in_reply_to, refs


# ---------------------------------------------------------------------------
# ThreadTracker
# ---------------------------------------------------------------------------

class ThreadTracker:
    """
    Groups MailMessage and CalendarEvent objects into threads/series.

    Mutates message.thread_id in-place and groups events into series.
    """

    def __init__(self) -> None:
        self._thread_index: dict[str, list[MailMessage]] = defaultdict(list)
        self._series_index: dict[str, list[CalendarEvent]] = defaultdict(list)

    @property
    def thread_index(self) -> dict[str, list[MailMessage]]:
        return dict(self._thread_index)

    @property
    def series_index(self) -> dict[str, list[CalendarEvent]]:
        return dict(self._series_index)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def _assign_thread_id(self, msg: MailMessage) -> str:
        """Compute thread_id for a single message using the best available signal."""

        # Strategy 1: thread_id already set by the reader (trust it).
        if msg.thread_id:
            return msg.thread_id

        # Strategy 2: In-Reply-To / References headers in body
        in_reply_to, refs = _extract_refs(msg.body)
        if in_reply_to or refs:
            # Use the root message-id as thread anchor
            root = refs[0] if refs else in_reply_to
            return _tid(f"rfc822:{root}")

        # Strategy 3: Normalised subject hash (same algorithm as applescript_base)
        return _tid(_norm_subject(msg.subject))

    def group_messages(self, messages: list[MailMessage]) -> list[MailMessage]:
        """
        Assign thread_id to each message and build thread_index.

        Mutates messages in-place and returns the same list.
        """
        self._thread_index.clear()
        assigned = 0

        for msg in messages:
            tid = self._assign_thread_id(msg)
            if msg.thread_id != tid:
                # Use model_copy to avoid mutating frozen models
                try:
                    msg.thread_id = tid
                except Exception:
                    # Pydantic v2 frozen model — wrap
                    msg = msg.model_copy(update={"thread_id": tid})
            self._thread_index[tid].append(msg)
            assigned += 1

        multi = sum(1 for v in self._thread_index.values() if len(v) > 1)
        logger.info(
            f"[threads] {assigned} messages → {len(self._thread_index)} threads "
            f"({multi} multi-message)"
        )
        return messages

    # ------------------------------------------------------------------
    # Events (meeting series)
    # ------------------------------------------------------------------

    def _series_key(self, ev: CalendarEvent) -> str:
        """
        Stable key that groups recurrence instances of the same event.

        Priority:
          1. uid without recurrence-ID suffix  (e.g. "abc123#20260101T090000Z" → "abc123")
          2. (normalised title + organizer) — catches Outlook-sourced series
        """
        uid = ev.uid or ""

        # iCal recurrence: UID/RECURRENCE-ID pattern  (uid#date or uid:date)
        for sep in ("#", ":"):
            if sep in uid:
                base = uid.split(sep)[0].strip()
                if base:
                    return f"uid:{base}"

        # Outlook global-object-id pattern: sometimes long hex UID shared across instances
        # Use the full uid as-is if it looks like a stable global ID (≥20 chars, no spaces)
        if len(uid) >= 20 and " " not in uid:
            return f"uid:{uid}"

        # Fallback: title + organizer
        title_key = _nfc(ev.title).strip().lower()
        org_key = (ev.organizer or "").strip().lower()
        return f"title:{title_key}|org:{org_key}"

    def _group_by_time_window(
        self,
        events_by_key: dict[str, list[CalendarEvent]],
    ) -> dict[str, list[CalendarEvent]]:
        """
        Secondary pass: merge title-keyed groups that fall within 3-day windows
        (catches weekly/daily recurring series with slight title variations).

        Only applied to 'title:' keys, not 'uid:' keys (those are already exact).
        """
        title_groups: dict[str, list[CalendarEvent]] = {}
        uid_groups: dict[str, list[CalendarEvent]] = {}

        for key, evs in events_by_key.items():
            if key.startswith("uid:"):
                uid_groups[key] = evs
            else:
                title_groups[key] = evs

        # For title groups, sort events by start time and try to merge adjacent groups
        # whose first occurrences are within 3 days of each other
        merged: dict[str, list[CalendarEvent]] = {}
        used: set[str] = set()

        sorted_keys = sorted(
            title_groups.keys(),
            key=lambda k: min(e.start for e in title_groups[k]),
        )

        for key in sorted_keys:
            if key in used:
                continue
            base_evs = list(title_groups[key])
            base_title = key.split("|org:")[0].removeprefix("title:")

            for other_key in sorted_keys:
                if other_key == key or other_key in used:
                    continue
                other_title = other_key.split("|org:")[0].removeprefix("title:")
                # Merge if titles are identical (same title, different organiser)
                if base_title == other_title:
                    base_evs.extend(title_groups[other_key])
                    used.add(other_key)

            sid = _tid(key)
            merged[sid] = base_evs
            used.add(key)

        result = {_tid(k): v for k, v in uid_groups.items()}
        result.update(merged)
        return result

    def group_events(self, events: list[CalendarEvent]) -> list[CalendarEvent]:
        """
        Assign series grouping and build series_index.

        Does NOT mutate CalendarEvent (CalendarEvent.uid is the iCal key).
        series_index maps series_id → list of CalendarEvent instances.
        Returns the same list unchanged.
        """
        self._series_index.clear()
        raw: dict[str, list[CalendarEvent]] = defaultdict(list)

        for ev in events:
            key = self._series_key(ev)
            raw[key].append(ev)

        self._series_index = self._group_by_time_window(dict(raw))  # type: ignore[assignment]

        multi = sum(1 for v in self._series_index.values() if len(v) > 1)
        logger.info(
            f"[series] {len(events)} events → {len(self._series_index)} series "
            f"({multi} with >1 occurrence)"
        )
        return events

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        threads = self._thread_index
        series  = self._series_index
        return {
            "threads": {
                "total": len(threads),
                "multi_message": sum(1 for v in threads.values() if len(v) > 1),
                "longest": max((len(v) for v in threads.values()), default=0),
            },
            "series": {
                "total": len(series),
                "multi_event": sum(1 for v in series.values() if len(v) > 1),
                "longest": max((len(v) for v in series.values()), default=0),
            },
        }
