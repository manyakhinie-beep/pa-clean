"""
Profile & assistant configuration layer.
"""

from personal_assistant.profile.context_assembler import ProfileAwareAssembler
from personal_assistant.profile.models import AIAssistantConfig, CommunicationTone, UserProfile
from personal_assistant.profile.service import (
    load_config,
    load_legacy_persona,
    load_profile,
    save_config,
    save_legacy_persona,
    save_profile,
)

__all__ = [
    "AIAssistantConfig",
    "CommunicationTone",
    "ProfileAwareAssembler",
    "UserProfile",
    "load_config",
    "load_legacy_persona",
    "load_profile",
    "save_config",
    "save_legacy_persona",
    "save_profile",
]
