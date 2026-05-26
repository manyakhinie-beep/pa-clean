"""
Unit tests for chat-draft reply behaviour:

  1. Generated AppleScript in the reply branch contains a dedup guard so
     Mail.app's auto-populated To/CC are not duplicated by our explicit
     recipient adds.
  2. ``/api/chat/mail/message-meta`` uses the lenient YAML parser so
     legacy vault files with run-on YAML still pre-fill the reply.
  3. End-to-end: when reply_to_message_id is set, recipients passed by
     the frontend reach the script builder AND the resulting script
     declares the dedup machinery.

The user reported as a bug:
  "что созданный черновик подтягивает предыдущую переписку и ответ её
   продолжает со всеми адресатами в корректных полях, адресаты не
   дублируются" — these tests pin down the contract.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from personal_assistant.mlx_server.chat_routes import _build_save_draft_mail_script


# ----------------------------------------------------------------------
# Script content tests — dedup machinery
# ----------------------------------------------------------------------


def _build(**kwargs):
    defaults = dict(
        subject="Re: Q2",
        body_file_path="/tmp/body.txt",
        to_recipients=["alice@example.com"],
        cc_recipients=[],
        reply_to_message_id="42001",
        save_to_drafts=False,
    )
    defaults.update(kwargs)
    return _build_save_draft_mail_script(**defaults)


def test_reply_branch_snapshots_existing_addresses():
    """Reply branch must read existing to/cc recipients into a list."""
    s = _build()
    assert "existingAddrs" in s
    assert "to recipients of newMsg" in s
    assert "cc recipients of newMsg" in s


def test_reply_branch_uses_ignoring_case():
    """Dedup compare must be case-insensitive — 'Alice@x.com' = 'alice@x.com'."""
    s = _build(to_recipients=["alice@example.com"])
    assert "ignoring case" in s
    assert "end ignoring" in s


def test_reply_branch_guards_each_to_recipient():
    """Each requested To address is wrapped in a 'does not contain' guard."""
    s = _build(to_recipients=["alice@example.com", "carol@example.com"])
    assert s.count("does not contain") >= 2
    assert "alice@example.com" in s
    assert "carol@example.com" in s


def test_reply_branch_guards_cc_recipients():
    s = _build(to_recipients=[], cc_recipients=["bob@example.com"])
    assert "does not contain" in s
    assert "make new cc recipient" in s
    assert "bob@example.com" in s


def test_reply_branch_appends_to_existing_after_add():
    """After adding a new recipient, dedup list grows — prevents intra-call dups."""
    s = _build(to_recipients=["alice@example.com"])
    # The set-end-of-existingAddrs append must appear inside the add block
    assert "set end of existingAddrs to" in s


def test_reply_branch_dedup_only_in_reply_branch_not_fallback():
    """The fallback (new outgoing) branch uses plain 'make new to recipient' calls.

    Mail.app doesn't auto-populate in the new-outgoing path, so no dedup needed.
    """
    s = _build(reply_to_message_id="42001", to_recipients=["alice@example.com"])
    # Two distinct branches present: dedup in reply, plain in fallback
    assert "if origMsg is not missing value then" in s
    # The fallback branch (after "else") should contain the plain add form
    # for compatibility with the existing non-reply path
    assert "make new outgoing message" in s


def test_new_mail_path_no_dedup_machinery():
    """When reply_to_message_id is None, script has no dedup guard at all."""
    s = _build(reply_to_message_id=None)
    assert "existingAddrs" not in s
    assert "does not contain" not in s
    assert "make new outgoing message" in s


def test_save_to_drafts_reply_branch_also_has_dedup():
    """Silent save (save_to_drafts=True) still in reply mode needs dedup."""
    s = _build(save_to_drafts=True)
    assert "existingAddrs" in s
    assert "ignoring case" in s
    # Silent save uses "opening window false"
    assert "opening window false" in s


def test_cc_dedup_includes_make_new_cc_recipient():
    """The cc dedup block must use the cc recipient AppleScript class, not to."""
    s = _build(to_recipients=[], cc_recipients=["x@y.com"])
    # Find the "make new cc recipient" line and verify it's inside an ignoring case block
    cc_idx = s.find("make new cc recipient")
    assert cc_idx > -1
    ig_idx = s.rfind("ignoring case", 0, cc_idx)
    end_idx = s.find("end ignoring", cc_idx)
    assert ig_idx > -1 and end_idx > -1


def test_multiple_addresses_all_dedup_guarded():
    """3 To + 2 CC → 5 guard blocks."""
    s = _build(
        to_recipients=["a@x.com", "b@x.com", "c@x.com"],
        cc_recipients=["d@x.com", "e@x.com"],
    )
    assert s.count("does not contain") == 5
    assert s.count("end ignoring") == 5


def test_addresses_capped_at_ten_each():
    """Defensive cap — script doesn't explode if caller passes 50 addresses."""
    many = [f"u{i}@x.com" for i in range(15)]
    s = _build(to_recipients=many, cc_recipients=many)
    # 10 To + 10 CC max
    assert s.count("does not contain") == 20


# ----------------------------------------------------------------------
# Reply-to-all + quoted history preservation
# ----------------------------------------------------------------------


def test_reply_uses_reply_to_all():
    """``reply with reply to all`` pulls CC straight from Mail.app's message
    database, so reply preserves the full participant list even when our
    vault didn't sync CC (PA_MAIL_FETCH_RECIPIENTS=false)."""
    s = _build()
    assert "with reply to all" in s


def test_reply_preserves_quoted_history():
    """Mail.app pre-fills content with the quoted reply ('On X, Alice wrote:
    > ...'). We must prepend our text above that block, not overwrite it."""
    s = _build()
    assert "set quotedHistory to" in s
    assert "content of newMsg as string" in s
    assert "bodyContent & return & return & quotedHistory" in s


def test_reply_history_preservation_in_silent_save():
    """Silent save (save_to_drafts=True) must also preserve history."""
    s = _build(save_to_drafts=True)
    assert "with reply to all" in s
    assert "bodyContent & return & return & quotedHistory" in s


def test_reply_falls_back_to_plain_body_if_history_empty():
    """Fallback: if quotedHistory is empty, just set content = bodyContent."""
    s = _build()
    assert "if quotedHistory is not \"\"" in s
    assert "else\n            set content of newMsg to bodyContent" in s


def test_new_mail_path_no_history_preservation():
    """When not replying, no quoted history exists — no preservation logic."""
    s = _build(reply_to_message_id=None)
    assert "quotedHistory" not in s
    assert "with reply to all" not in s


# ----------------------------------------------------------------------
# Self-email seed in dedup snapshot
# ----------------------------------------------------------------------


def test_self_email_seeded_into_dedup(monkeypatch):
    """Belt-and-braces: when settings.user_email is set, it seeds
    existingAddrs so the user can never accidentally be cc'd on their own
    reply (e.g. if vault.cc included the user)."""
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "user_email", "igor@example.com")
    s = _build(to_recipients=["alice@example.com"])
    # Seed line must appear
    assert "set end of existingAddrs to \"igor@example.com\"" in s


def test_self_email_seed_absent_when_unset(monkeypatch):
    """No user_email → no seed line."""
    from personal_assistant.config import settings
    monkeypatch.setattr(settings, "user_email", "")
    s = _build(to_recipients=["alice@example.com"])
    # No seed line for "(any email).com" pattern related to a hardcoded self
    # — only the explicit to_recipient should be added.
    assert "alice@example.com" in s
    # The seed mechanism still exists, just has no email to seed
    assert "set existingAddrs to {}" in s


# ----------------------------------------------------------------------
# /mail/message-meta — lenient YAML parser
# ----------------------------------------------------------------------


def _make_client():
    from personal_assistant.config import settings
    from personal_assistant.mlx_server.server import app
    # Match the existing scenario tests: disable test_mode short-circuit
    settings.e2e_test_mode = False
    return TestClient(app)


def _write_mail(tmp_path: Path, filename: str, body: str) -> None:
    mail_dir = tmp_path / "mail"
    mail_dir.mkdir(parents=True, exist_ok=True)
    (mail_dir / filename).write_text(body, encoding="utf-8")


def test_meta_resolves_well_formed_yaml(tmp_path):
    _write_mail(
        tmp_path,
        "msg1.md",
        '---\n'
        'id: msg1\n'
        'message_id: "42001"\n'
        'subject: "Q2 budget"\n'
        'sender: "Alice <alice@example.com>"\n'
        'cc: ["bob@example.com"]\n'
        'thread_id: thr_001\n'
        '---\n\nBody.\n',
    )
    with patch("personal_assistant.config.settings.vault_path", tmp_path):
        client = _make_client()
        r = client.get("/api/chat/mail/message-meta?message_id=42001")
        assert r.status_code == 200
        data = r.json()
        assert data["sender_email"] == "alice@example.com"
        assert data["subject"] == "Q2 budget"
        assert data["cc"] == ["bob@example.com"]
        assert data["thread_id"] == "thr_001"


def test_meta_resolves_legacy_runon_yaml(tmp_path):
    """Legacy run-on YAML must be parsed (lenient repair) — otherwise the
    reply pre-fill returns 404 and the user sees an empty compose window."""
    _write_mail(
        tmp_path,
        "msg_legacy.md",
        '---\n'
        'id: msg_legacy\n'
        'message_id: "42002"\n'
        'subject: "test"sender: "Ivan <ivan@example.com>"location: "Б.38.13"tags: [почта]\n'
        '---\n\nBody.\n',
    )
    with patch("personal_assistant.config.settings.vault_path", tmp_path):
        client = _make_client()
        r = client.get("/api/chat/mail/message-meta?message_id=42002")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["sender_email"] == "ivan@example.com"
        assert data["subject"] == "test"


def test_meta_404_when_message_unknown(tmp_path):
    with patch("personal_assistant.config.settings.vault_path", tmp_path):
        # Empty vault — must still return 404, not 500
        (tmp_path / "mail").mkdir()
        client = _make_client()
        r = client.get("/api/chat/mail/message-meta?message_id=nope")
        assert r.status_code == 404


def test_meta_matches_by_vault_id_not_just_message_id(tmp_path):
    _write_mail(
        tmp_path,
        "vault_item_stem.md",
        '---\n'
        'id: vault_item_stem\n'
        'message_id: "99001"\n'
        'subject: "x"\n'
        'sender: "a@example.com"\n'
        '---\n',
    )
    with patch("personal_assistant.config.settings.vault_path", tmp_path):
        client = _make_client()
        r = client.get("/api/chat/mail/message-meta?message_id=vault_item_stem")
        assert r.status_code == 200
        assert r.json()["sender_email"] == "a@example.com"


def test_meta_parses_wiki_link_sender(tmp_path):
    """Vault default format: sender as [[contacts/email@x.com]]."""
    _write_mail(
        tmp_path,
        "msg_wiki.md",
        '---\n'
        'id: msg_wiki\n'
        'message_id: "55001"\n'
        'subject: "x"\n'
        'sender: "[[contacts/ivanov@example.com]]"\n'
        '---\n',
    )
    with patch("personal_assistant.config.settings.vault_path", tmp_path):
        client = _make_client()
        r = client.get("/api/chat/mail/message-meta?message_id=55001")
        assert r.status_code == 200
        assert r.json()["sender_email"] == "ivanov@example.com"


# ----------------------------------------------------------------------
# End-to-end: POST /api/chat/save-draft-mail forwards everything
# ----------------------------------------------------------------------


def test_e2e_reply_passes_all_fields_to_builder(tmp_path):
    """Frontend sends reply_to_message_id + To + CC → all reach the script."""
    _write_mail(
        tmp_path,
        "src.md",
        '---\nid: src\nmessage_id: "70001"\nsubject: "topic"\n---\n',
    )
    captured: dict = {}

    def _fake_builder(**kwargs):
        captured.update(kwargs)
        return "tell application \"Mail\"\nend tell"

    with patch("personal_assistant.config.settings.vault_path", tmp_path), \
         patch("personal_assistant.config.settings.e2e_test_mode", False), \
         patch("personal_assistant.mlx_server.chat_routes._build_save_draft_mail_script",
               side_effect=_fake_builder), \
         patch("personal_assistant.readers.applescript_base.run_applescript", return_value=""), \
         patch("platform.system", return_value="Darwin"):
        client = _make_client()
        r = client.post(
            "/api/chat/save-draft-mail",
            json={
                "subject": "Re: topic",
                "body": "Hi, my reply.",
                "to_recipients": ["alice@example.com"],
                "cc_recipients": ["bob@example.com", "carol@example.com"],
                "reply_to_message_id": "src",   # vault id, resolved to 70001
                "save_to_drafts": False,
            },
        )
        assert r.status_code == 200, r.text
        # reply_to_message_id resolved from vault id "src" → "70001"
        assert captured["reply_to_message_id"] == "70001"
        assert captured["to_recipients"] == ["alice@example.com"]
        assert captured["cc_recipients"] == ["bob@example.com", "carol@example.com"]
        assert captured["subject"] == "Re: topic"
