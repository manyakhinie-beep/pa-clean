"""Unit tests verifying AI-tool settings are actually applied (calendar + mail).

These complement test_config.py (which proves settings persist) by proving the
stored values change real behaviour. They run without MLX or Apple apps.
"""

from __future__ import annotations

from personal_assistant import config as cfg_mod
from personal_assistant.calendar import calendar_writer
from personal_assistant.calendar.intent_parser import EventDraft, parse_event_intent
from personal_assistant.services import mail_service
from personal_assistant.services.calendar_service import find_conflicts

# --------------------------------------------------------------- default duration

def test_intent_parser_uses_configured_default_duration(monkeypatch):
    monkeypatch.setattr(cfg_mod.settings, "calendar_default_duration", 45)
    draft = parse_event_intent("Встреча с Ивановым завтра в 10:00", mlx_engine=None)
    assert draft.duration_minutes == 45


def test_intent_parser_explicit_duration_overrides_default(monkeypatch):
    monkeypatch.setattr(cfg_mod.settings, "calendar_default_duration", 45)
    draft = parse_event_intent("Созвон завтра в 10:00 на 2 часа", mlx_engine=None)
    assert draft.duration_minutes == 120


# ------------------------------------------------------ calendar e2e_test_mode

def test_create_event_e2e_mode_skips_applescript(monkeypatch):
    monkeypatch.setattr(cfg_mod.settings, "e2e_test_mode", True)

    def _boom(*_args, **_kwargs):
        raise AssertionError("run_applescript must NOT run in e2e_test_mode")

    monkeypatch.setattr(calendar_writer, "run_applescript", _boom)
    draft = EventDraft(
        title="T",
        start_iso="2026-05-26T10:00:00",
        end_iso="2026-05-26T10:30:00",
        calendar_name="Work",
    )
    res = calendar_writer.create_event(draft)
    assert res["success"] is True
    assert res["event_uid"] == "e2e-test-mode"


def test_create_event_dry_run_unaffected(monkeypatch):
    monkeypatch.setattr(cfg_mod.settings, "e2e_test_mode", False)
    draft = EventDraft(
        title="T",
        start_iso="2026-05-26T10:00:00",
        end_iso="2026-05-26T10:30:00",
        calendar_name="Work",
    )
    res = calendar_writer.create_event(draft, dry_run=True)
    assert res["success"] is True
    assert res["event_uid"] == "dry-run"


# ----------------------------------------------------------------- find_conflicts

def test_find_conflicts_detects_overlap():
    events = [
        {"title": "A", "date": "2026-05-26T09:30:00", "end": "2026-05-26T10:15:00"},
        {"title": "B", "date": "2026-05-26T12:00:00", "end": "2026-05-26T13:00:00"},
    ]
    hits = find_conflicts("2026-05-26T10:00:00", "2026-05-26T10:30:00", events)
    assert [h["title"] for h in hits] == ["A"]


def test_find_conflicts_touching_boundary_is_not_conflict():
    events = [{"title": "A", "date": "2026-05-26T10:30:00", "end": "2026-05-26T11:00:00"}]
    assert find_conflicts("2026-05-26T10:00:00", "2026-05-26T10:30:00", events) == []


def test_find_conflicts_skips_events_without_end():
    events = [{"title": "A", "date": "2026-05-26T10:00:00"}]
    assert find_conflicts("2026-05-26T10:00:00", "2026-05-26T10:30:00", events) == []


def test_find_conflicts_invalid_proposed_interval():
    events = [{"title": "A", "date": "2026-05-26T10:00:00", "end": "2026-05-26T11:00:00"}]
    assert find_conflicts("2026-05-26T11:00:00", "2026-05-26T10:00:00", events) == []


# ------------------------------------------------------------ mail e2e_test_mode

def test_save_draft_reply_e2e_mode_no_side_effect(monkeypatch):
    monkeypatch.setattr(cfg_mod.settings, "e2e_test_mode", True)
    res = mail_service.save_draft_reply(
        subject="Re: test", body="hello", to_recipients=["x@example.com"]
    )
    assert res["ok"] is True
    assert res.get("e2e") is True


# ------------------------------------------------------------ mail_auto_draft

def test_resolve_save_to_drafts_explicit_wins(monkeypatch):
    monkeypatch.setattr(cfg_mod.settings, "mail_auto_draft", True)
    assert mail_service.resolve_save_to_drafts(False) is False
    assert mail_service.resolve_save_to_drafts(True) is True


def test_resolve_save_to_drafts_falls_back_to_config(monkeypatch):
    monkeypatch.setattr(cfg_mod.settings, "mail_auto_draft", True)
    assert mail_service.resolve_save_to_drafts(None) is True
    monkeypatch.setattr(cfg_mod.settings, "mail_auto_draft", False)
    assert mail_service.resolve_save_to_drafts(None) is False
