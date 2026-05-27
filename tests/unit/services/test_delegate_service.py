"""
Tests for the delegate workflow:

  * tool_prompts: delegate_system + delegate_contacts round-trip + defaults
  * delegate_service: find_contact, list_contacts
  * delegate_service.build_suggestion:
      - rule-based intro (no MLX engine) — deterministic, mentions sender,
        subject, user note, and target colleague
      - MLX-generated intro (mocked engine) — uses effective_delegate prompt
      - subject normalisation strips Re:/Fwd:/Отв:/Пер: prefixes
  * /tool-prompts API: round-trip with delegate fields + invalid contacts
    are dropped, duplicates by email are deduped
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ----------------------------------------------------------------------
# tool_prompts module-level
# ----------------------------------------------------------------------


def test_default_delegate_system_is_nonempty():
    from personal_assistant.services.tool_prompts import DEFAULT_DELEGATE_SYSTEM
    assert DEFAULT_DELEGATE_SYSTEM.strip()
    # Must position the assistant as the manager's helper.
    assert "руковод" in DEFAULT_DELEGATE_SYSTEM.lower()


def test_all_prompts_prioritise_user_and_last_message():
    """Each prompt must call out the priority on the assistant's user
    and on the **last** message of the thread — the user pinned this as
    the headline rule for all three skills."""
    from personal_assistant.services.tool_prompts import (
        DEFAULT_DELEGATE_SYSTEM,
        DEFAULT_DRAFT_SYSTEM,
        DEFAULT_SUMMARIZE_SYSTEM,
    )
    for name, prompt in [
        ("summarize", DEFAULT_SUMMARIZE_SYSTEM),
        ("draft",     DEFAULT_DRAFT_SYSTEM),
        ("delegate",  DEFAULT_DELEGATE_SYSTEM),
    ]:
        low = prompt.lower()
        assert "приоритет" in low, f"{name}: must declare priority"
        assert "последне"  in low, f"{name}: must mention 'последнее' message"
        assert "пользовател" in low or "руковод" in low, \
            f"{name}: must reference the user / руководитель"


def test_all_prompts_define_extraction_who_to_whom_what_when():
    """All three skills must extract the same four facts:
    кто → кому → что → срок.  The exact prose differs per skill but
    every prompt must hit each marker."""
    from personal_assistant.services.tool_prompts import (
        DEFAULT_DELEGATE_SYSTEM,
        DEFAULT_DRAFT_SYSTEM,
        DEFAULT_SUMMARIZE_SYSTEM,
    )
    for name, prompt in [
        ("summarize", DEFAULT_SUMMARIZE_SYSTEM),
        ("draft",     DEFAULT_DRAFT_SYSTEM),
        ("delegate",  DEFAULT_DELEGATE_SYSTEM),
    ]:
        # "кто → кому → что → к какому сроку" idiom — appears verbatim in
        # the user-supplied spec.  Tolerate small wording shifts via lower-
        # case substring checks rather than exact match.
        low = prompt.lower()
        assert "кто" in low,  f"{name}: must mention 'кто'"
        assert "кому" in low, f"{name}: must mention 'кому'"
        assert "что"  in low, f"{name}: must mention 'что'"
        assert "срок" in low, f"{name}: must mention 'срок'"


def test_summarize_has_required_output_sections():
    """Summarize prompt locks down four section headers — the rendered
    report should be: ПОРУЧЕНИЯ / УЧАСТНИКИ ВСТРЕЧИ / КЛЮЧЕВЫЕ ТЕЗИСЫ /
    ОТВЕТ НА ВОПРОС."""
    from personal_assistant.services.tool_prompts import DEFAULT_SUMMARIZE_SYSTEM
    for section in ("ПОРУЧЕНИЯ", "УЧАСТНИКИ ВСТРЕЧИ",
                    "КЛЮЧЕВЫЕ ТЕЗИСЫ", "ОТВЕТ НА ВОПРОС"):
        assert section in DEFAULT_SUMMARIZE_SYSTEM, \
            f"summarize: missing section '{section}'"


def test_summarize_classifies_urgency_levels():
    """The summarize prompt must teach the four-level urgency scale
    (критичная / высокая / средняя / низкая) so downstream filters work."""
    from personal_assistant.services.tool_prompts import DEFAULT_SUMMARIZE_SYSTEM
    low = DEFAULT_SUMMARIZE_SYSTEM.lower()
    for level in ("критичная", "высокая", "средняя", "низкая"):
        assert level in low, f"summarize: missing urgency level '{level}'"


def test_draft_has_required_output_sections():
    """Draft locks down three sections — АНАЛИЗ ПЕРЕПИСКИ / ДЕЙСТВИЕ ИЛИ
    ДЕЛЕГИРОВАНИЕ / ЧЕРНОВИК ОТВЕТА."""
    from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM
    for section in ("АНАЛИЗ ПЕРЕПИСКИ",
                    "ДЕЙСТВИЕ ИЛИ ДЕЛЕГИРОВАНИЕ",
                    "ЧЕРНОВИК ОТВЕТА"):
        assert section in DEFAULT_DRAFT_SYSTEM, \
            f"draft: missing section '{section}'"


def test_draft_bridges_action_and_delegation():
    """Draft must distinguish the user-acts vs delegate-to-another paths,
    each with a strict opening string the user pinned in the spec."""
    from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM
    assert "Действие:" in DEFAULT_DRAFT_SYSTEM
    assert "Делегировать:" in DEFAULT_DRAFT_SYSTEM


def test_delegate_has_required_output_sections():
    """Delegate prompt locks down four sections — РЕКОМЕНДАЦИЯ / ДЕЙСТВИЕ,
    КОНТЕКСТ ДЛЯ ИСПОЛНИТЕЛЯ, ЧЕРНОВИК ЗАДАЧИ ДЛЯ СОТРУДНИКА, ПРИМЕЧАНИЕ
    ДЛЯ РУКОВОДИТЕЛЯ."""
    from personal_assistant.services.tool_prompts import DEFAULT_DELEGATE_SYSTEM
    for section in ("РЕКОМЕНДАЦИЯ / ДЕЙСТВИЕ",
                    "КОНТЕКСТ ДЛЯ ИСПОЛНИТЕЛЯ",
                    "ЧЕРНОВИК ЗАДАЧИ ДЛЯ СОТРУДНИКА",
                    "ПРИМЕЧАНИЕ ДЛЯ РУКОВОДИТЕЛЯ"):
        assert section in DEFAULT_DELEGATE_SYSTEM, \
            f"delegate: missing section '{section}'"


def test_delegate_classifies_urgency_levels():
    """Delegate prompt uses the day-precise urgency scale: критичная (0-1),
    высокая (2-3), средняя (неделя), низкая."""
    from personal_assistant.services.tool_prompts import DEFAULT_DELEGATE_SYSTEM
    low = DEFAULT_DELEGATE_SYSTEM.lower()
    for level in ("критичная", "высокая", "средняя", "низкая"):
        assert level in low, f"delegate: missing urgency level '{level}'"


def test_tool_prompts_effective_delegate_uses_default_when_empty():
    from personal_assistant.services.tool_prompts import (
        DEFAULT_DELEGATE_SYSTEM,
        ToolPrompts,
    )
    p = ToolPrompts(delegate_system="")
    assert p.effective_delegate() == DEFAULT_DELEGATE_SYSTEM


def test_tool_prompts_effective_delegate_uses_user_override():
    from personal_assistant.services.tool_prompts import ToolPrompts
    custom = "Кастомный промпт для делегирования"
    p = ToolPrompts(delegate_system=custom)
    assert p.effective_delegate() == custom


def test_tool_prompts_default_contacts_empty_list():
    from personal_assistant.services.tool_prompts import ToolPrompts
    p = ToolPrompts()
    # Pydantic-style default — __post_init__ must convert None → []
    assert p.delegate_contacts == []
    assert isinstance(p.delegate_contacts, list)


def test_tool_prompts_roundtrip_contacts(tmp_path, monkeypatch):
    from personal_assistant.config import settings
    from personal_assistant.services.tool_prompts import (
        DelegateContact,
        ToolPrompts,
        invalidate_cache,
        load_tool_prompts,
        save_tool_prompts,
    )
    monkeypatch.setattr(settings, "vault_path", tmp_path)
    invalidate_cache()

    prompts = ToolPrompts(
        delegate_system="custom",
        delegate_contacts=[
            DelegateContact(name="Иван Петров", email="ivan@example.com", role="юрист"),
            DelegateContact(name="Анна",         email="anna@example.com"),
        ],
    )
    save_tool_prompts(prompts)
    invalidate_cache()
    loaded = load_tool_prompts()
    assert loaded.delegate_system == "custom"
    assert len(loaded.delegate_contacts) == 2
    assert loaded.delegate_contacts[0].name == "Иван Петров"
    assert loaded.delegate_contacts[0].role == "юрист"
    assert loaded.delegate_contacts[1].email == "anna@example.com"


def test_normalize_contact_rejects_missing_email():
    from personal_assistant.services.tool_prompts import _normalize_contact
    assert _normalize_contact({"name": "X"}) is None
    assert _normalize_contact({"name": "X", "email": ""}) is None
    assert _normalize_contact({"name": "X", "email": "not-an-email"}) is None
    assert _normalize_contact(None) is None  # type: ignore[arg-type]


def test_normalize_contact_trims_long_fields():
    from personal_assistant.services.tool_prompts import _normalize_contact
    c = _normalize_contact({
        "name":  "A" * 500,
        "email": "x@y.com",
        "role":  "B" * 500,
        "note":  "C" * 500,
    })
    assert c is not None
    assert len(c.name)  <= 120
    assert len(c.role)  <= 120
    assert len(c.note)  <= 300


# ----------------------------------------------------------------------
# delegate_service helpers
# ----------------------------------------------------------------------


def _patch_contacts(monkeypatch, contacts):
    """Override ``get_tool_prompts`` in BOTH modules that read it.

    ``delegate_service`` imports the symbol at module load, so patching
    ``tool_prompts.get_tool_prompts`` after import doesn't affect the bound
    reference inside delegate_service. We patch both locations.
    """
    from personal_assistant.services import delegate_service as ds
    from personal_assistant.services import tool_prompts as tp
    from personal_assistant.services.tool_prompts import ToolPrompts

    def _fake_get(force_reload=False):
        return ToolPrompts(delegate_contacts=list(contacts))

    monkeypatch.setattr(tp, "get_tool_prompts", _fake_get)
    monkeypatch.setattr(ds, "get_tool_prompts", _fake_get)


def test_find_contact_case_insensitive(monkeypatch):
    from personal_assistant.services.delegate_service import find_contact
    from personal_assistant.services.tool_prompts import DelegateContact

    _patch_contacts(monkeypatch, [DelegateContact(name="X", email="MiXed@Case.com")])
    assert find_contact("mixed@case.com") is not None
    assert find_contact("MIXED@CASE.COM") is not None
    assert find_contact("  mixed@case.com  ") is not None


def test_find_contact_returns_none_unknown(monkeypatch):
    from personal_assistant.services.delegate_service import find_contact
    _patch_contacts(monkeypatch, [])
    assert find_contact("nobody@nowhere.com") is None
    assert find_contact("") is None


def test_list_contacts_returns_copy(monkeypatch):
    from personal_assistant.services.delegate_service import list_contacts
    from personal_assistant.services.tool_prompts import DelegateContact

    seed = [DelegateContact(name="X", email="x@example.com")]
    _patch_contacts(monkeypatch, seed)
    out = list_contacts()
    assert len(out) == 1
    assert out[0].email == "x@example.com"
    # Independent copy — mutating result doesn't change the cache
    out.append(DelegateContact(name="Y", email="y@example.com"))
    assert len(list_contacts()) == 1


# ----------------------------------------------------------------------
# build_suggestion — rule-based path
# ----------------------------------------------------------------------


def _item(**overrides):
    base = {
        "id": "msg_1",
        "subject": "Q2 budget review",
        "sender_name": "Иван Петров",
        "sender_email": "ivan@example.com",
        "body_preview": "Просьба согласовать бюджет до пятницы.",
    }
    base.update(overrides)
    return base


def test_build_suggestion_rule_based_no_engine(monkeypatch):
    from personal_assistant.services.delegate_service import build_suggestion
    from personal_assistant.services.tool_prompts import DelegateContact
    _patch_contacts(monkeypatch, [])
    contact = DelegateContact(name="Анна Сидорова", email="anna@example.com", role="финансы")
    sug = build_suggestion(item=_item(), contact=contact, user_note="", mlx_engine=None)
    assert sug.mlx_used is False
    assert "Анна" in sug.intro
    assert "Иван Петров" in sug.intro
    assert "Q2 budget review" in sug.intro
    assert "Спасибо" in sug.intro
    assert sug.contact is contact
    assert sug.source_message_id == "msg_1"


def test_build_suggestion_subject_strips_prefixes(monkeypatch):
    from personal_assistant.services.delegate_service import build_suggestion
    from personal_assistant.services.tool_prompts import DelegateContact
    _patch_contacts(monkeypatch, [])
    contact = DelegateContact(name="X", email="x@example.com")
    for raw, expected in [
        ("Re: Бюджет",         "Поручение: Бюджет"),
        ("RE: Бюджет",         "Поручение: Бюджет"),
        ("Fwd: тема",          "Поручение: тема"),
        ("FW: тема",           "Поручение: тема"),
        ("Отв: тема",          "Поручение: тема"),
        ("Re: Re: вложенная",  "Поручение: вложенная"),
        ("Без префикса",       "Поручение: Без префикса"),
        ("",                   "Поручение: без темы"),
    ]:
        s = build_suggestion(
            item=_item(subject=raw), contact=contact, user_note="", mlx_engine=None,
        )
        assert s.subject == expected, f"input={raw!r} got={s.subject!r}"


def test_build_suggestion_includes_user_note(monkeypatch):
    from personal_assistant.services.delegate_service import build_suggestion
    from personal_assistant.services.tool_prompts import DelegateContact
    _patch_contacts(monkeypatch, [])
    contact = DelegateContact(name="X", email="x@example.com")
    sug = build_suggestion(
        item=_item(),
        contact=contact,
        user_note="Прошу ускорить, ждут к среде.",
        mlx_engine=None,
    )
    assert "Прошу ускорить, ждут к среде." in sug.intro


def test_build_suggestion_handles_missing_sender_gracefully(monkeypatch):
    from personal_assistant.services.delegate_service import build_suggestion
    from personal_assistant.services.tool_prompts import DelegateContact
    _patch_contacts(monkeypatch, [])
    contact = DelegateContact(name="", email="x@example.com")
    sug = build_suggestion(
        item={"id": "m", "subject": "", "sender_name": "", "sender_email": ""},
        contact=contact, user_note="", mlx_engine=None,
    )
    assert sug.intro
    assert sug.subject == "Поручение: без темы"


# ----------------------------------------------------------------------
# build_suggestion — MLX path (engine mocked)
# ----------------------------------------------------------------------


def test_build_suggestion_uses_mlx_when_available(monkeypatch):
    from personal_assistant.services.delegate_service import build_suggestion
    from personal_assistant.services.tool_prompts import DelegateContact
    _patch_contacts(monkeypatch, [])

    engine = MagicMock()
    engine.ask.return_value = "MLX-сгенерированная вводная.\nВторая строка."
    contact = DelegateContact(name="Анна", email="anna@example.com", role="HR")
    sug = build_suggestion(
        item=_item(),
        contact=contact,
        user_note="Срочно",
        mlx_engine=engine,
    )
    assert sug.mlx_used is True
    assert sug.intro.startswith("MLX-сгенерированная")
    # Engine was called with correct keyword args
    call = engine.ask.call_args
    assert "system" in call.kwargs
    assert "Анна" in call.kwargs["question"]
    assert "Срочно" in call.kwargs["question"]
    assert "Q2 budget review" in call.kwargs["question"]


def test_build_suggestion_falls_back_to_rules_on_mlx_error(monkeypatch):
    from personal_assistant.services.delegate_service import build_suggestion
    from personal_assistant.services.tool_prompts import DelegateContact
    _patch_contacts(monkeypatch, [])

    engine = MagicMock()
    engine.ask.side_effect = RuntimeError("MLX OOM")
    contact = DelegateContact(name="Анна", email="anna@example.com")
    sug = build_suggestion(item=_item(), contact=contact, user_note="", mlx_engine=engine)
    assert sug.mlx_used is False
    assert "Анна" in sug.intro
    assert "Иван Петров" in sug.intro


def test_build_suggestion_empty_mlx_output_falls_back(monkeypatch):
    """Engine returns empty/whitespace string → rule-based path kicks in."""
    from personal_assistant.services.delegate_service import build_suggestion
    from personal_assistant.services.tool_prompts import DelegateContact
    _patch_contacts(monkeypatch, [])

    engine = MagicMock()
    engine.ask.return_value = "   "
    contact = DelegateContact(name="Анна", email="anna@example.com")
    sug = build_suggestion(item=_item(), contact=contact, user_note="", mlx_engine=engine)
    assert sug.mlx_used is False


# ----------------------------------------------------------------------
# /tool-prompts API — delegate fields
# ----------------------------------------------------------------------


def _client(tmp_path):
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.server import app
    settings.vault_path = tmp_path
    settings.e2e_test_mode = True
    from personal_assistant.services.tool_prompts import invalidate_cache
    invalidate_cache()
    return TestClient(app)


def test_get_tool_prompts_includes_delegate_fields(tmp_path):
    c = _client(tmp_path)
    r = c.get("/tool-prompts")
    assert r.status_code == 200
    data = r.json()
    assert "delegate_system" in data
    assert "effective_delegate_system" in data
    assert "default_delegate_system" in data
    assert "delegate_is_default" in data
    assert "delegate_contacts" in data
    # No user override yet → effective = default + is_default True + empty contacts
    assert data["delegate_is_default"] is True
    assert data["delegate_contacts"] == []
    assert data["effective_delegate_system"] == data["default_delegate_system"]


def test_post_tool_prompts_saves_delegate_contacts(tmp_path):
    c = _client(tmp_path)
    payload = {
        "draft_system": "",
        "summarize_system": "",
        "delegate_system": "Кастомный",
        "delegate_contacts": [
            {"name": "Анна", "email": "anna@example.com", "role": "HR", "note": ""},
            {"name": "Иван", "email": "ivan@example.com", "role": "юрист", "note": "договоры"},
        ],
    }
    r = c.post("/tool-prompts", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["delegate_contacts_count"] == 2

    # Read back
    r2 = c.get("/tool-prompts")
    assert r2.status_code == 200
    data = r2.json()
    assert data["delegate_system"] == "Кастомный"
    emails = [c["email"] for c in data["delegate_contacts"]]
    assert "anna@example.com" in emails
    assert "ivan@example.com" in emails


def test_post_tool_prompts_drops_invalid_contacts(tmp_path):
    c = _client(tmp_path)
    r = c.post("/tool-prompts", json={
        "draft_system": "",
        "summarize_system": "",
        "delegate_system": "",
        "delegate_contacts": [
            {"name": "Valid",      "email": "v@example.com"},
            {"name": "No email",   "email": ""},
            {"name": "Bad email",  "email": "not-an-email"},
        ],
    })
    assert r.status_code == 200
    assert r.json()["delegate_contacts_count"] == 1


def test_post_tool_prompts_dedupes_contacts_by_email(tmp_path):
    c = _client(tmp_path)
    r = c.post("/tool-prompts", json={
        "draft_system": "",
        "summarize_system": "",
        "delegate_system": "",
        "delegate_contacts": [
            {"name": "First",   "email": "Same@Example.com"},
            {"name": "Dup",     "email": "same@example.com"},  # case-insensitive
            {"name": "Other",   "email": "other@example.com"},
        ],
    })
    assert r.status_code == 200
    assert r.json()["delegate_contacts_count"] == 2

    r2 = c.get("/tool-prompts")
    emails = sorted(x["email"].lower() for x in r2.json()["delegate_contacts"])
    assert emails == ["other@example.com", "same@example.com"]


# ----------------------------------------------------------------------
# /api/v1/inbox/delegate-contacts + /delegate-suggest
# ----------------------------------------------------------------------


def test_inbox_delegate_contacts_endpoint(tmp_path):
    c = _client(tmp_path)
    # Populate via /tool-prompts API
    c.post("/tool-prompts", json={
        "draft_system": "", "summarize_system": "", "delegate_system": "",
        "delegate_contacts": [
            {"name": "Анна", "email": "anna@example.com", "role": "HR", "note": ""},
        ],
    })
    r = c.get("/api/v1/inbox/delegate-contacts")
    assert r.status_code == 200
    assert r.json()["contacts"][0]["email"] == "anna@example.com"


def test_delegate_suggest_unknown_contact_404(tmp_path):
    c = _client(tmp_path)
    r = c.post(
        "/api/v1/inbox/anything/delegate-suggest",
        json={"target_email": "nobody@example.com", "note": ""},
    )
    assert r.status_code == 404
    assert "Сотрудник" in r.json()["detail"]
