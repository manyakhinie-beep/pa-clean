"""
Readers package — unified data-source interface.

All readers implement DataSourceReader protocol:
  - fetch_messages(days_back) → list[MailMessage]
  - fetch_events(days_back, days_forward) → list[CalendarEvent]

Available implementations:
  - AppleCalendarReader  : Apple Calendar.app via osascript
  - AppleMailReader      : Apple Mail.app via osascript
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from personal_assistant.models import CalendarEvent, MailMessage


@runtime_checkable
class DataSourceReader(Protocol):
    """Minimal interface every reader must satisfy."""

    def fetch_messages(self, days_back: int = 30) -> list[MailMessage]:
        ...

    def fetch_events(
        self,
        days_back: int = 30,
        days_forward: int = 90,
    ) -> list[CalendarEvent]:
        ...


__all__ = [
    "DataSourceReader",
]
