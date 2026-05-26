"""
Pydantic v2 models for UserProfile and AIAssistantConfig.

Strict validation, enum constraints, XSS sanitization.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_serializer

#: Simple RFC-5322 subset for user_email validation.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


class CommunicationTone(str, Enum):
    """Allowed communication tones."""

    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    BRIEF = "brief"
    FORMAL = "formal"
    TECHNICAL = "technical"


class UserProfile(BaseModel):
    """User identity and preferences.

    :param full_name: Display name of the user.
    :param user_email: Primary email — used by the classifier to detect action items
        addressed to this user (``has_action_for_user`` flag).
    :param preferred_language: ISO 639-1 code (e.g. ``"ru"``).
    :param communication_tone: Preferred tone for AI responses.
    :param timezone: IANA timezone string (default ``Europe/Moscow``).
    :param context_notes: Free-form personal context injected into the system prompt.
    """

    full_name: str = Field(default="", max_length=120)
    user_email: Optional[str] = Field(
        default=None,
        max_length=254,
        description="Primary email for has_action_for_user detection",
    )
    preferred_language: str = Field(default="ru", min_length=2, max_length=2)
    communication_tone: CommunicationTone = Field(default=CommunicationTone.PROFESSIONAL)
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=50)
    context_notes: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("user_email")
    @classmethod
    def _valid_email(cls, v: Optional[str]) -> Optional[str]:
        """Validate user_email as a well-formed email address (RFC-5322 subset)."""
        if v is None:
            return None
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError(f"user_email is not a valid email address: {v!r}")
        return v

    @field_validator("preferred_language")
    @classmethod
    def _iso639_1(cls, v: str) -> str:
        v = v.lower().strip()
        if len(v) != 2 or not v.isalpha():
            raise ValueError("preferred_language must be a valid ISO 639-1 code (2 letters)")
        return v

    @field_validator("context_notes")
    @classmethod
    def _no_xss(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        # Block script tags, javascript: URIs, and inline event handlers
        if re.search(r"<script|javascript:|on\w+\s*=", v, re.IGNORECASE):
            raise ValueError("context_notes contains potentially unsafe content")
        return v

    @model_serializer
    def serialize(self) -> dict[str, object]:
        return {
            "full_name": self.full_name,
            "user_email": self.user_email,
            "preferred_language": self.preferred_language,
            "communication_tone": self.communication_tone.value,
            "timezone": self.timezone,
            "context_notes": self.context_notes,
        }


class AIAssistantConfig(BaseModel):
    """AI assistant behaviour configuration."""

    name: str = Field(default="Ассистент", min_length=1, max_length=60)
    response_language: str = Field(default="ru", min_length=2, max_length=2)
    tone_style: CommunicationTone = Field(default=CommunicationTone.PROFESSIONAL)
    system_prompt_template: str = Field(default="", max_length=4000)
    max_context_tokens: int = Field(default=12000, ge=1000, le=32000)

    @field_validator("response_language")
    @classmethod
    def _iso639_1(cls, v: str) -> str:
        v = v.lower().strip()
        if len(v) != 2 or not v.isalpha():
            raise ValueError("response_language must be a valid ISO 639-1 code (2 letters)")
        return v

    @model_serializer
    def serialize(self) -> dict[str, object]:
        return {
            "name": self.name,
            "response_language": self.response_language,
            "tone_style": self.tone_style.value,
            "system_prompt_template": self.system_prompt_template,
            "max_context_tokens": self.max_context_tokens,
        }
