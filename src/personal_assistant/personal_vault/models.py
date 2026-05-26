"""
Pydantic v2 models for PersonalVault.

All models use strict annotations, field validators and serializers.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_serializer


class Attachment(BaseModel):
    """A single file attachment. Never inlined into body."""

    filename: str = Field(..., min_length=1)
    mime_type: str = Field(default="application/octet-stream")
    size_bytes: int = Field(default=0, ge=0)
    content_id: Optional[str] = Field(default=None)

    @field_validator("filename")
    @classmethod
    def _no_path_traversal(cls, v: str) -> str:
        if ".." in v or "/" in v or "\\" in v:
            raise ValueError("Filename must not contain path separators")
        return v

    @model_serializer
    def serialize(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "content_id": self.content_id,
        }


class VaultItem(BaseModel):
    """A single email or meeting entry."""

    id: str = Field(..., min_length=1)
    item_type: Literal["email", "meeting"]
    thread_id: Optional[str] = Field(default=None)
    parent_message_id: Optional[str] = Field(default=None)
    subject: str = Field(..., min_length=1)
    sender: str = Field(..., min_length=1)
    sender_email: Optional[str] = Field(default=None)
    recipients: list[str] = Field(default_factory=list)
    full_body: str = Field(..., min_length=0)
    body_html: Optional[str] = Field(default=None)
    body_plain: Optional[str] = Field(default=None)
    date_iso: str = Field(..., min_length=1)
    attachments: list[Attachment] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("full_body")
    @classmethod
    def _strip_body(cls, v: str) -> str:
        return v.strip()

    @field_validator("sender_email")
    @classmethod
    def _email_or_none(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and "@" not in v:
            raise ValueError("Invalid email format")
        return v

    @model_serializer
    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "item_type": self.item_type,
            "thread_id": self.thread_id,
            "parent_message_id": self.parent_message_id,
            "subject": self.subject,
            "sender": self.sender,
            "sender_email": self.sender_email,
            "recipients": self.recipients,
            "full_body": self.full_body,
            "body_html": self.body_html,
            "body_plain": self.body_plain,
            "date_iso": self.date_iso,
            "attachments": [a.serialize() for a in self.attachments],
            "metadata": self.metadata,
        }


class Thread(BaseModel):
    """An aggregated conversation thread."""

    id: str = Field(..., min_length=1)
    root_subject: str = Field(..., min_length=1)
    items: list[VaultItem] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)

    @property
    def thread_message_count(self) -> int:
        return len(self.items)

    @model_serializer
    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "root_subject": self.root_subject,
            "thread_message_count": self.thread_message_count,
            "participants": self.participants,
            "items": [i.serialize() for i in self.items],
        }


class ContextRequest(BaseModel):
    """Request body for AI context assembly."""

    thread_id: Optional[str] = Field(default=None, min_length=1)
    query: Optional[str] = Field(default=None)  # fallback: build context from search query
    mode: Literal["draft", "summarize", "chat"] = Field(default="chat")
    max_chars: int = Field(default=12000, ge=1000, le=32000)


class ContextResponse(BaseModel):
    """Response with assembled MLX prompt context."""

    thread_id: str
    system_prompt: str
    messages: list[dict[str, str]]
    total_chars: int
