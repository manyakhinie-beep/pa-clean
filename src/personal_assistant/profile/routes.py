"""
FastAPI routes for profile and assistant configuration.

Endpoints:
  GET  /api/v1/profile
  PUT  /api/v1/profile
  GET  /api/v1/assistant-config
  PUT  /api/v1/assistant-config
"""

from __future__ import annotations

from fastapi import APIRouter
from loguru import logger

from personal_assistant.profile.models import AIAssistantConfig, UserProfile
from personal_assistant.profile.service import load_config, load_profile, save_config, save_profile

router = APIRouter(prefix="/api/v1")


@router.get("/profile")
def get_profile():
    """Return current user profile."""
    return load_profile().serialize()


@router.put("/profile")
def put_profile(body: UserProfile):
    """Update user profile."""
    save_profile(body)
    logger.info(f"[profile] updated: {body.full_name}, lang={body.preferred_language}")
    return {"ok": True, "profile": body.serialize()}


@router.get("/assistant-config")
def get_assistant_config():
    """Return current AI assistant configuration."""
    return load_config().serialize()


@router.put("/assistant-config")
def put_assistant_config(body: AIAssistantConfig):
    """Update AI assistant configuration."""
    save_config(body)
    logger.info(f"[assistant] updated: {body.name}, tokens={body.max_context_tokens}")
    return {"ok": True, "config": body.serialize()}
