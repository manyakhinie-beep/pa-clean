"""
Shared Pydantic models for all data sources.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Contact(BaseModel):
    """A person derived from Calendar attendees, Mail senders, or Contacts.app."""

    email: str = Field(..., description="Primary email, used as unique key")
    name: Optional[str] = None
    # Enriched full name (Фамилия Имя Отчество) extracted by name_extractor.
    # May differ from `name` which comes directly from the data source.
    full_name: Optional[str] = None
    # Source that provided the current full_name (e.g. "calendar", "mail")
    name_source: Optional[str] = None
    # ISO timestamp of last full_name update
    name_updated_at: Optional[str] = None
    phone: Optional[str] = None
    organization: Optional[str] = None
    notes: Optional[str] = None
    # Sources this contact was seen in
    sources: list[str] = Field(default_factory=list)


class CalendarEvent(BaseModel):
    """A single Calendar event."""

    uid: str
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: Optional[str] = None
    notes: Optional[str] = None
    calendar_name: Optional[str] = None
    attendees: list[str] = Field(default_factory=list)  # email addresses
    organizer: Optional[str] = None
    url: Optional[str] = None
    attachments: list[str] = Field(default_factory=list)  # attachment filenames


class MailMessage(BaseModel):
    """A single Mail message."""

    message_id: str
    subject: str
    sender_name: Optional[str] = None
    sender_email: str
    recipients: list[str] = Field(default_factory=list)  # To: addresses
    cc: list[str] = Field(default_factory=list)           # CC: addresses
    date: datetime
    mailbox: Optional[str] = None
    body: Optional[str] = None  # full plain-text body of the message
    has_attachments: bool = False
    attachments: list[str] = Field(default_factory=list)  # attachment filenames
    thread_id: Optional[str] = (
        None  # stable hash of normalised subject — groups Re:/Fwd: chains
    )
    source: str = (
        "mail"  # "mail" — identifies the reader that produced this
    )
