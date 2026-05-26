"""
PersonalVault v2 — Pydantic models, thread aggregation, AI context builder.
"""

from personal_assistant.personal_vault.db import (
    get_item,
    get_thread,
    insert_item,
    list_items,
    list_threads,
)
from personal_assistant.personal_vault.models import Attachment, VaultItem

__all__ = [
    "Attachment",
    "VaultItem",
    "get_item",
    "get_thread",
    "insert_item",
    "list_items",
    "list_threads",
]
