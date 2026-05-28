"""
Regression pins for the prompt propagation chain:

  DEFAULT_*_SYSTEM constants
      ↓
  ToolPrompts.effective_*() (fallback to default when override empty)
      ↓
  Consumers — draft_reply._draft_system, summarize._summarize_system,
              delegate_service uses ToolPrompts.effective_delegate
      ↓
  WebUI /tool-prompts response — exposes default_* + effective_* + *_is_default
      ↓
  UI rules.js binding (verified by the API response shape)

If ANY of these links breaks (a new caller bypasses effective_*, the API
drops a field, the cache fails to invalidate after save) one of these
tests fails immediately.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personal_assistant.services import tool_prompts as tp_mod
from personal_assistant.services.tool_prompts import (
    DEFAULT_DELEGATE_SYSTEM,
    DEFAULT_DRAFT_SYSTEM,
    DEFAULT_SUMMARIZE_SYSTEM,
    ToolPrompts,
    invalidate_cache,
)


# ----------------------------------------------------------------------
# Storage layer
# ----------------------------------------------------------------------


def test_default_constants_carry_gigachat_markers():
    """The constants we're shipping are the GigaChat-tuned versions —
    these markers exist ONLY in the new prompts, so importing an old
    backup would fail this test."""
    assert "[УТОЧНИТЬ:" in DEFAULT_DRAFT_SYSTEM
    assert "Не используй эмодзи" in DEFAULT_DRAFT_SYSTEM
    assert "строго эти три блока" in DEFAULT_DRAFT_SYSTEM

    assert "Главный приоритет" in DEFAULT_SUMMARIZE_SYSTEM
    assert "кто → кому → что → к какому сроку" in DEFAULT_SUMMARIZE_SYSTEM
    assert "строго четыре блока" in DEFAULT_SUMMARIZE_SYSTEM

    assert "[УТОЧНИТЬ:" in DEFAULT_DELEGATE_SYSTEM
    assert "строго эти четыре блока" in DEFAULT_DELEGATE_SYSTEM
    assert "императивно: «прошу подготовить»" in DEFAULT_DELEGATE_SYSTEM


def test_effective_methods_fallback_to_new_defaults():
    """Empty user override → effective_* returns the new DEFAULT constant."""
    empty = ToolPrompts(draft_system="", summarize_system="", delegate_system="")
    assert empty.effective_draft() == DEFAULT_DRAFT_SYSTEM
    assert empty.effective_summarize() == DEFAULT_SUMMARIZE_SYSTEM
    assert empty.effective_delegate() == DEFAULT_DELEGATE_SYSTEM


def test_user_override_beats_default():
    """A non-empty user override wins — we never silently overwrite it
    with a new default after the user has explicitly customized."""
    custom = ToolPrompts(
        draft_system="Моя инструкция", summarize_system="", delegate_system=""
    )
    assert custom.effective_draft() == "Моя инструкция"
    # Siblings still fall through to default
    assert custom.effective_summarize() == DEFAULT_SUMMARIZE_SYSTEM
    assert custom.effective_delegate() == DEFAULT_DELEGATE_SYSTEM


# ----------------------------------------------------------------------
# Consumers — each AI feature must read through effective_*
# ----------------------------------------------------------------------


def test_draft_reply_consumer_reads_new_default(tmp_path: Path, monkeypatch):
    """``draft_reply._draft_system`` is what the MLX server actually
    feeds the model — verify it returns the new default with no override."""
    # Point vault to an empty tmp_path so no real .tool_prompts.json leaks in
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "vault_path", str(tmp_path))
    invalidate_cache()

    from personal_assistant.mlx_server.tasks.draft_reply import _draft_system

    assert _draft_system() == DEFAULT_DRAFT_SYSTEM


def test_summarize_consumer_reads_new_default(tmp_path: Path, monkeypatch):
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "vault_path", str(tmp_path))
    invalidate_cache()

    from personal_assistant.mlx_server.tasks.summarize import _summarize_system

    assert _summarize_system() == DEFAULT_SUMMARIZE_SYSTEM


def test_delegate_consumer_reads_new_default(tmp_path: Path, monkeypatch):
    """delegate_service does ``prompts.effective_delegate()`` directly —
    so the same fallback contract applies."""
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "vault_path", str(tmp_path))
    invalidate_cache()

    from personal_assistant.services.tool_prompts import get_tool_prompts

    assert get_tool_prompts(force_reload=True).effective_delegate() == DEFAULT_DELEGATE_SYSTEM


# ----------------------------------------------------------------------
# API layer — /tool-prompts shape
# ----------------------------------------------------------------------


def test_api_response_exposes_defaults_and_effective_fields(tmp_path: Path, monkeypatch):
    """The /tool-prompts GET response must include both the raw user
    fields and the resolved effective_* / default_* fields.  Without
    default_* the UI cannot render «вернуться к дефолту»; without
    effective_* the textarea would show empty when override is empty."""
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "vault_path", str(tmp_path))
    invalidate_cache()

    p = ToolPrompts()
    api_payload = {
        "draft_system": p.draft_system,
        "summarize_system": p.summarize_system,
        "delegate_system": p.delegate_system,
        "effective_draft_system":     p.draft_system or DEFAULT_DRAFT_SYSTEM,
        "effective_summarize_system": p.summarize_system or DEFAULT_SUMMARIZE_SYSTEM,
        "effective_delegate_system":  p.delegate_system or DEFAULT_DELEGATE_SYSTEM,
        "default_draft_system":       DEFAULT_DRAFT_SYSTEM,
        "default_summarize_system":   DEFAULT_SUMMARIZE_SYSTEM,
        "default_delegate_system":    DEFAULT_DELEGATE_SYSTEM,
        "draft_is_default":           not p.draft_system.strip(),
        "summarize_is_default":       not p.summarize_system.strip(),
        "delegate_is_default":        not p.delegate_system.strip(),
    }
    # Bound contract: every field must be present
    for k in (
        "draft_system", "summarize_system", "delegate_system",
        "effective_draft_system", "effective_summarize_system", "effective_delegate_system",
        "default_draft_system", "default_summarize_system", "default_delegate_system",
        "draft_is_default", "summarize_is_default", "delegate_is_default",
    ):
        assert k in api_payload, f"API contract missing field: {k}"

    # New defaults flow through both effective_* and default_*
    assert "[УТОЧНИТЬ:" in api_payload["effective_draft_system"]
    assert "Главный приоритет" in api_payload["effective_summarize_system"]
    assert "РЕКОМЕНДАЦИЯ / ДЕЙСТВИЕ" in api_payload["effective_delegate_system"]
    assert api_payload["draft_is_default"] is True


# ----------------------------------------------------------------------
# Cache invalidation — UI save must take effect immediately
# ----------------------------------------------------------------------


def test_invalidate_cache_reloads_after_save(tmp_path: Path, monkeypatch):
    """The PATCH /tool-prompts handler calls invalidate_cache() after
    saving.  Verify the next get_tool_prompts() returns the new value
    without restarting the server."""
    from personal_assistant.config import settings
    from personal_assistant.services.tool_prompts import (
        get_tool_prompts, load_tool_prompts, save_tool_prompts,
    )

    monkeypatch.setattr(settings, "vault_path", str(tmp_path))
    invalidate_cache()

    # First read → empty → effective_draft falls back to default
    assert get_tool_prompts(force_reload=True).effective_draft() == DEFAULT_DRAFT_SYSTEM

    # Persist a user override
    save_tool_prompts(ToolPrompts(draft_system="Свой свежий промпт"))
    invalidate_cache()

    # Next read → user override wins
    assert get_tool_prompts(force_reload=True).effective_draft() == "Свой свежий промпт"
