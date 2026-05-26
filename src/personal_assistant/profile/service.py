"""
Profile & assistant-config persistence service.

Storage: JSON file at ``<vault_parent>/data/profile.json``.
Atomic writes via temp-file + rename.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from personal_assistant.config import settings
from personal_assistant.profile.models import AIAssistantConfig, UserProfile

_PROFILE_PATH: Path = settings.vault_path.parent / "data" / "profile.json"
_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically to avoid corruption on crash."""
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_profile() -> UserProfile:
    """Load user profile from disk or return defaults."""
    if _PROFILE_PATH.exists():
        try:
            data = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
            return UserProfile.model_validate(data.get("user", {}))
        except Exception:
            pass
    return UserProfile(full_name="")


def save_profile(profile: UserProfile) -> None:
    """Persist user profile atomically."""
    current = _load_all()
    current["user"] = profile.model_dump()
    _atomic_write(_PROFILE_PATH, current)


def load_config() -> AIAssistantConfig:
    """Load assistant config from disk or return defaults."""
    if _PROFILE_PATH.exists():
        try:
            data = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
            return AIAssistantConfig.model_validate(data.get("assistant", {}))
        except Exception:
            pass
    return AIAssistantConfig(name="Ассистент")


def save_config(config: AIAssistantConfig) -> None:
    """Persist assistant config atomically."""
    current = _load_all()
    current["assistant"] = config.model_dump()
    _atomic_write(_PROFILE_PATH, current)


def _load_all() -> dict:
    if _PROFILE_PATH.exists():
        try:
            return json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def load_legacy_persona() -> dict:
    """
    Backward-compatibility bridge for old ``data/persona.json``.

    Returns a dict with keys: user_name, user_role, user_language,
    assistant_name, assistant_style, assistant_focus.
    """
    profile = load_profile()
    config = load_config()
    return {
        "user_name": profile.full_name,
        "user_role": "",
        "user_language": profile.preferred_language,
        "assistant_name": config.name,
        "assistant_style": config.tone_style.value,
        "assistant_focus": config.system_prompt_template,
    }


def save_legacy_persona(payload: dict) -> None:
    """
    Backward-compatibility bridge: write old persona format into new profile.json.
    """
    from personal_assistant.profile.models import CommunicationTone

    profile = load_profile()
    config = load_config()

    if "user_name" in payload:
        profile.full_name = payload["user_name"]
    if "user_language" in payload:
        profile.preferred_language = payload["user_language"]

    if "assistant_name" in payload:
        config.name = payload["assistant_name"]
    if "assistant_style" in payload:
        try:
            config.tone_style = CommunicationTone(payload["assistant_style"])
        except ValueError:
            pass
    if "assistant_focus" in payload:
        config.system_prompt_template = payload["assistant_focus"]

    save_profile(profile)
    save_config(config)
