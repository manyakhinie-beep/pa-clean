"""
Vault filter service for the Reports feature.

Provides helpers that pull items from PersonalVault filtered by date and status,
without touching the database layer directly from the report service.

Functions:
    get_items_for_today      – all vault items whose date_iso starts with today's date.
    get_completed_today      – items that are marked completed today.
    get_items_last_7_days    – all items from the last 7 days (inclusive today).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from personal_assistant.personal_vault.db import list_items
from personal_assistant.personal_vault.models import VaultItem
from personal_assistant.utils.timezone import get_now_msk

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iso_prefix(d: date) -> str:
    """Return YYYY-MM-DD prefix for filtering date_iso."""
    return d.strftime("%Y-%m-%d")


def _filter_by_date_prefix(items: list[VaultItem], prefix: str) -> list[VaultItem]:
    """Keep only items whose date_iso starts with *prefix*."""
    return [it for it in items if it.date_iso.startswith(prefix)]


def _filter_completed(items: list[VaultItem]) -> list[VaultItem]:
    """Keep items whose metadata['status'] == 'completed' (case-insensitive)."""
    return [
        it
        for it in items
        if str(it.metadata.get("status", "")).lower() == "completed"
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_items_for_today(target_date: Optional[date] = None) -> list[VaultItem]:
    """Return all vault items whose date_iso matches *target_date* (default: today).

    :param target_date: Optional explicit date; defaults to today in local time.
    :returns: List of VaultItem sorted newest-first (DB default).
    """
    d = target_date or get_now_msk().date()
    all_items = list_items(limit=500)
    return _filter_by_date_prefix(all_items, _iso_prefix(d))


def get_completed_today(target_date: Optional[date] = None) -> list[VaultItem]:
    """Return vault items completed on *target_date* (default: today).

    An item is considered completed when ``metadata["status"] == "completed"``.

    :param target_date: Optional explicit date; defaults to today.
    :returns: Filtered list of completed VaultItem objects.
    """
    today_items = get_items_for_today(target_date)
    return _filter_completed(today_items)


def get_items_last_7_days(target_date: Optional[date] = None) -> list[VaultItem]:
    """Return vault items from the last 7 days, inclusive of *target_date*.

    :param target_date: Anchor date (newest boundary); defaults to today.
    :returns: Items from (target_date - 6 days) through target_date, newest-first.
    """
    end = target_date or get_now_msk().date()
    start = end - timedelta(days=6)
    all_items = list_items(limit=1000)
    result: list[VaultItem] = []
    for it in all_items:
        date_str = it.date_iso[:10]  # YYYY-MM-DD portion
        try:
            item_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if start <= item_date <= end:
            result.append(it)
    return result
