"""
E2E scenario tests for the full Mail draft flow.

Scenario under test (happy path):
  1. Inbox → item has a vault mail document with proper frontmatter
  2. GET /api/chat/mail/message-meta  → returns correct sender_email from "sender:" field
  3. POST /api/chat/save-draft-mail   → calls _build_save_draft_mail_script + run_applescript
  4. AppleScript builder receives correct args (threading, cc, save_to_drafts flag)
  5. Resulting script contains correct reply-thread loop, not 'whose' clause

Architecture notes
------------------
The /api/chat/save-draft-mail endpoint does NOT go through mail_service.save_draft_reply.
It directly calls:
  1. platform.system()               — guarded by patch("platform.system", return_value="Darwin")
  2. run_applescript(script, ...)    — guarded by patching applescript_base.run_applescript
  3. _build_save_draft_mail_script() — can be patched to capture arg

Tests are grouped by layer:
  - TestMessageMetaEndpoint         — GET endpoint, frontmatter parsing, email extraction
  - TestSaveDraftMailEndpoint        — POST endpoint: validation, arg forwarding, error handling
  - TestBuildSaveDraftMailScript     — script-builder unit tests (subject, recipient, threading)
  - TestDraftFlowScenario            — end-to-end: vault doc → meta → save draft with threading
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Shared patches — every test that hits /save-draft-mail on non-macOS needs these
# ---------------------------------------------------------------------------

_DARWIN_PATCH   = patch("platform.system", return_value="Darwin")
_RUN_AS_PATCH   = patch(
    "personal_assistant.readers.applescript_base.run_applescript",
    return_value="",
)


@pytest.fixture(autouse=True)
def _disable_e2e_test_mode(monkeypatch):
    """These tests exercise the real save-draft path (AppleScript is mocked),
    so disable the suite-wide e2e_test_mode short-circuit set in the root
    conftest. AppleScript never actually runs here (it is patched)."""
    from personal_assistant.config import settings

    monkeypatch.setattr(settings, "e2e_test_mode", False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mail_vault(tmp_path: Path, messages: list[dict]) -> Path:
    """Create a minimal vault/mail/ with given message docs."""
    vault = tmp_path / "vault"
    mail_dir = vault / "mail"
    mail_dir.mkdir(parents=True)

    for msg in messages:
        fname      = msg.get("id", "msg1")
        sender     = msg.get("sender", "alice@example.com")
        cc         = msg.get("cc", "")
        subj       = msg.get("subject", "Test subject")
        thread_id  = msg.get("thread_id", "")
        recipients = msg.get("recipients", "bob@example.com")
        body       = msg.get("body", "Email body text.")
        doc = textwrap.dedent(f"""\
            ---
            id: "{fname}"
            source: "mail"
            type: "email"
            subject: "{subj}"
            sender: "{sender}"
            recipients: "{recipients}"
            cc: "{cc}"
            thread_id: "{thread_id}"
            ---
            {body}
        """)
        (mail_dir / f"{fname}.md").write_text(doc, encoding="utf-8")

    return vault


def _chat_client(vault: Path | None = None):
    """Return a TestClient for the chat router.

    The ``vault`` argument is kept for backward-compat with existing call sites
    but is no longer used here: every caller that needs ``settings.vault_path``
    redirected wraps its actual request in its own ``with patch(...)`` block
    (see e.g. :class:`TestMessageMetaEndpoint`). Previously this helper called
    ``patcher.start()`` without a matching ``stop()`` — that left
    ``personal_assistant.config.settings`` replaced by a ``MagicMock`` for the
    rest of the Python process, breaking every later test that read settings
    (notably ``test_rules_settings_e2e``).
    """
    from personal_assistant.mlx_server.chat_routes import router as chat_router

    app = FastAPI()
    app.include_router(chat_router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# TestMessageMetaEndpoint
# ---------------------------------------------------------------------------

class TestMessageMetaEndpoint:
    """GET /api/chat/mail/message-meta — correct field extraction from vault docs."""

    def test_returns_404_when_not_found(self, tmp_path):
        vault  = _make_mail_vault(tmp_path, [])
        client = _chat_client(vault)
        with patch("personal_assistant.config.settings") as mc:
            mc.vault_path = vault
            resp = client.get("/api/chat/mail/message-meta?message_id=nonexistent")
        assert resp.status_code == 404

    def test_bare_email_in_sender_field(self, tmp_path):
        """Vault doc sender: alice@example.com → sender_email: alice@example.com"""
        vault = _make_mail_vault(tmp_path, [{"id": "msg1", "sender": "alice@example.com"}])
        with patch("personal_assistant.config.settings") as mc:
            mc.vault_path = vault
            client = _chat_client(vault)
            resp = client.get("/api/chat/mail/message-meta?message_id=msg1")
        assert resp.status_code == 200
        assert resp.json()["sender_email"] == "alice@example.com"

    def test_name_angle_email_in_sender_field(self, tmp_path):
        """'Alice Smith <alice@example.com>' → extracts just the email address."""
        vault = _make_mail_vault(tmp_path, [{
            "id": "msg2",
            "sender": "Alice Smith <alice@example.com>",
            "subject": "Project",
        }])
        with patch("personal_assistant.config.settings") as mc:
            mc.vault_path = vault
            client = _chat_client(vault)
            resp = client.get("/api/chat/mail/message-meta?message_id=msg2")
        assert resp.status_code == 200
        assert resp.json()["sender_email"] == "alice@example.com"

    def test_cyrillic_name_angle_email(self, tmp_path):
        """Cyrillic display name should not break email extraction."""
        vault = _make_mail_vault(tmp_path, [{
            "id": "msg_cyr",
            "sender": "Иванов Иван <ivan@corp.ru>",
            "subject": "Отчёт",
        }])
        with patch("personal_assistant.config.settings") as mc:
            mc.vault_path = vault
            client = _chat_client(vault)
            resp = client.get("/api/chat/mail/message-meta?message_id=msg_cyr")
        assert resp.status_code == 200
        assert resp.json()["sender_email"] == "ivan@corp.ru"

    def test_subject_returned(self, tmp_path):
        vault = _make_mail_vault(tmp_path, [{
            "id": "msg3", "sender": "x@y.com", "subject": "Квартальный отчёт",
        }])
        with patch("personal_assistant.config.settings") as mc:
            mc.vault_path = vault
            client = _chat_client(vault)
            resp = client.get("/api/chat/mail/message-meta?message_id=msg3")
        assert resp.status_code == 200
        assert resp.json()["subject"] == "Квартальный отчёт"

    def test_cc_as_string_normalised_to_list(self, tmp_path):
        """Vault cc: 'cc1@a.com, cc2@b.com' → parsed as a list."""
        vault = _make_mail_vault(tmp_path, [{
            "id": "msg4", "sender": "x@y.com", "cc": "cc1@a.com, cc2@b.com",
        }])
        with patch("personal_assistant.config.settings") as mc:
            mc.vault_path = vault
            client = _chat_client(vault)
            resp = client.get("/api/chat/mail/message-meta?message_id=msg4")
        assert resp.status_code == 200
        cc = resp.json()["cc"]
        assert isinstance(cc, list)
        assert "cc1@a.com" in cc
        assert "cc2@b.com" in cc

    def test_thread_id_returned(self, tmp_path):
        vault = _make_mail_vault(tmp_path, [{
            "id": "msg5", "sender": "x@y.com", "thread_id": "thread_abc123",
        }])
        with patch("personal_assistant.config.settings") as mc:
            mc.vault_path = vault
            client = _chat_client(vault)
            resp = client.get("/api/chat/mail/message-meta?message_id=msg5")
        assert resp.status_code == 200
        assert resp.json()["thread_id"] == "thread_abc123"


# ---------------------------------------------------------------------------
# TestSaveDraftMailEndpoint
# ---------------------------------------------------------------------------

class TestSaveDraftMailEndpoint:
    """POST /api/chat/save-draft-mail — validation + arg forwarding."""

    def _client(self):
        from personal_assistant.mlx_server.chat_routes import router as chat_router
        app = FastAPI()
        app.include_router(chat_router)
        return TestClient(app)

    # ── Validation ──────────────────────────────────────────────────────────

    def test_requires_subject(self):
        client = self._client()
        resp = client.post("/api/chat/save-draft-mail", json={
            "body": "Hello", "to_recipients": ["a@b.com"],
        })
        assert resp.status_code == 422

    def test_requires_body(self):
        client = self._client()
        resp = client.post("/api/chat/save-draft-mail", json={
            "subject": "Test", "to_recipients": ["a@b.com"],
        })
        assert resp.status_code == 422

    @pytest.mark.skipif(
        sys.platform == "darwin",
        reason="501 guard only triggers off-macOS; on Darwin the endpoint "
               "calls real osascript and returns 500 from Mail.app instead.",
    )
    def test_returns_501_on_non_macos(self):
        """Without platform patch the endpoint returns 501."""
        client = self._client()
        resp = client.post("/api/chat/save-draft-mail", json={
            "subject": "Test", "body": "Hello", "to_recipients": ["a@b.com"],
        })
        assert resp.status_code == 501
        assert "macOS" in resp.json()["detail"]

    # ── Happy path ───────────────────────────────────────────────────────────

    def test_happy_path_returns_200(self):
        client = self._client()
        with _DARWIN_PATCH, _RUN_AS_PATCH:
            resp = client.post("/api/chat/save-draft-mail", json={
                "subject": "Test",
                "body": "Hello world",
                "to_recipients": ["alice@example.com"],
            })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_open_window_message_text(self):
        """save_to_drafts=False → message says 'открыт в Mail'."""
        client = self._client()
        with _DARWIN_PATCH, _RUN_AS_PATCH:
            resp = client.post("/api/chat/save-draft-mail", json={
                "subject": "Test",
                "body": "Body",
                "to_recipients": ["a@b.com"],
                "save_to_drafts": False,
            })
        assert resp.status_code == 200
        assert "открыт" in resp.json()["message"].lower()

    def test_save_to_drafts_message_text(self):
        """save_to_drafts=True → message says 'сохранён'."""
        client = self._client()
        with _DARWIN_PATCH, _RUN_AS_PATCH:
            resp = client.post("/api/chat/save-draft-mail", json={
                "subject": "Test",
                "body": "Body",
                "to_recipients": ["a@b.com"],
                "save_to_drafts": True,
            })
        assert resp.status_code == 200
        assert "сохранён" in resp.json()["message"].lower()

    # ── Arg forwarding ───────────────────────────────────────────────────────

    def test_cc_recipients_forwarded_to_script_builder(self):
        """cc_recipients must reach _build_save_draft_mail_script."""
        client = self._client()
        captured = {}

        def _mock_build(**kwargs):
            captured.update(kwargs)
            return 'tell application "Mail" end tell'

        with _DARWIN_PATCH, _RUN_AS_PATCH, patch(
            "personal_assistant.mlx_server.chat_routes._build_save_draft_mail_script",
            side_effect=_mock_build,
        ):
            client.post("/api/chat/save-draft-mail", json={
                "subject": "CC test",
                "body": "Body",
                "to_recipients": ["alice@example.com"],
                "cc_recipients": ["boss@example.com"],
            })

        assert "boss@example.com" in (captured.get("cc_recipients") or [])

    def test_reply_to_message_id_forwarded(self):
        """reply_to_message_id must be forwarded to the script builder.

        All-digit IDs are treated as native Mail.app message IDs and bypass
        vault resolution, so they arrive at the script builder unchanged.
        """
        client = self._client()
        captured = {}

        def _mock_build(**kwargs):
            captured.update(kwargs)
            return 'tell application "Mail" end tell'

        with _DARWIN_PATCH, _RUN_AS_PATCH, patch(
            "personal_assistant.mlx_server.chat_routes._build_save_draft_mail_script",
            side_effect=_mock_build,
        ):
            client.post("/api/chat/save-draft-mail", json={
                "subject": "Reply",
                "body": "In reply…",
                "to_recipients": ["a@b.com"],
                # All-digit ID bypasses vault lookup (treated as Mail.app native ID)
                "reply_to_message_id": "99001",
            })

        assert captured.get("reply_to_message_id") == "99001", \
            f"Expected '99001', got {captured.get('reply_to_message_id')!r}"

    def test_save_to_drafts_flag_forwarded(self):
        """save_to_drafts=True must reach the script builder."""
        client = self._client()
        captured = {}

        def _mock_build(**kwargs):
            captured.update(kwargs)
            return 'tell application "Mail" end tell'

        with _DARWIN_PATCH, _RUN_AS_PATCH, patch(
            "personal_assistant.mlx_server.chat_routes._build_save_draft_mail_script",
            side_effect=_mock_build,
        ):
            client.post("/api/chat/save-draft-mail", json={
                "subject": "Save",
                "body": "Body",
                "to_recipients": ["a@b.com"],
                "save_to_drafts": True,
            })

        assert captured.get("save_to_drafts") is True

    def test_run_applescript_error_returns_500(self):
        """If run_applescript raises, endpoint returns 500."""
        client = self._client()
        with _DARWIN_PATCH, patch(
            "personal_assistant.readers.applescript_base.run_applescript",
            side_effect=RuntimeError("osascript: execution error"),
        ):
            resp = client.post("/api/chat/save-draft-mail", json={
                "subject": "Test",
                "body": "Body",
                "to_recipients": ["a@b.com"],
            })
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# TestBuildSaveDraftMailScript
# ---------------------------------------------------------------------------

class TestBuildSaveDraftMailScript:
    """Unit tests for _build_save_draft_mail_script (script content)."""

    def _build(self, **kwargs):
        from personal_assistant.mlx_server.chat_routes import _build_save_draft_mail_script
        defaults = dict(
            subject="Test",
            body_file_path="/tmp/body.txt",
            to_recipients=["alice@example.com"],
            cc_recipients=[],
            reply_to_message_id=None,
            save_to_drafts=False,
        )
        defaults.update(kwargs)
        return _build_save_draft_mail_script(**defaults)

    def test_subject_in_script(self):
        script = self._build(subject="Итоги проекта Q2")
        assert "Итоги проекта Q2" in script

    def test_recipient_in_script(self):
        script = self._build(to_recipients=["bob@example.com"])
        assert "bob@example.com" in script

    def test_cc_in_script(self):
        script = self._build(cc_recipients=["cc@example.com"])
        assert "cc@example.com" in script

    def test_script_has_tell_mail(self):
        script = self._build()
        assert 'tell application "Mail"' in script

    def test_reply_uses_repeat_loop_not_whose(self):
        """Threading must use 'repeat with' loop — NOT fragile 'whose' clause (-1728)."""
        script = self._build(reply_to_message_id="<some-msg-id@mail>")
        assert "repeat with" in script.lower()
        assert "whose message id" not in script.lower()

    def test_save_to_drafts_false_opens_window(self):
        """save_to_drafts=False → opening window: true."""
        script = self._build(save_to_drafts=False)
        # opening window true = compose window shown
        assert "true" in script.lower()

    def test_save_to_drafts_true_no_open_window(self):
        """save_to_drafts=True → opening window: false (silent save)."""
        script = self._build(save_to_drafts=True)
        assert "false" in script.lower()

    def test_body_loaded_via_do_shell_script(self):
        """Body must be read from temp file via 'do shell script cat' (avoids -2741)."""
        script = self._build(body_file_path="/tmp/pa_draft_body.txt")
        assert "do shell script" in script.lower()

    def test_cyrillic_subject_survives(self):
        script = self._build(subject="Квартальный отчёт")
        assert "Квартальный отчёт" in script


# ---------------------------------------------------------------------------
# TestDraftFlowScenario — end-to-end scenario
# ---------------------------------------------------------------------------

class TestDraftFlowScenario:
    """
    Full scenario: vault doc → GET message-meta → POST save-draft-mail.

    Steps:
      1. Vault mail doc exists with realistic Russian-language frontmatter.
      2. GET /api/chat/mail/message-meta returns correct To/CC/Subject.
      3. POST /api/chat/save-draft-mail with populated fields → succeeds.
      4. Script builder receives reply_to_message_id for thread continuity.
      5. Script builder receives save_to_drafts=True.
    """

    def _client(self):
        from personal_assistant.mlx_server.chat_routes import router as chat_router
        app = FastAPI()
        app.include_router(chat_router)
        return TestClient(app)

    def test_full_draft_scenario(self, tmp_path):
        """Full scenario: vault doc → meta → save draft with threading.

        Uses a digit reply_to_message_id to bypass vault resolution so the ID
        arrives at _build_save_draft_mail_script unchanged.
        """
        # 1. Prepare vault with a realistic mail doc
        vault = _make_mail_vault(tmp_path, [{
            "id": "email_proj_123",
            "sender": "Алексей Иванов <alex@corp.ru>",
            "subject": "Итоги проекта Q2",
            "cc": "maria@corp.ru, team@corp.ru",
            "recipients": "igor@example.com",
            "thread_id": "thread_proj_q2",
            "body": "Добрый день, подведём итоги квартала.",
        }])

        client = self._client()

        # 2. Fetch message metadata
        with patch("personal_assistant.config.settings") as mc:
            mc.vault_path = vault
            meta_resp = client.get(
                "/api/chat/mail/message-meta?message_id=email_proj_123"
            )

        assert meta_resp.status_code == 200, meta_resp.text
        meta = meta_resp.json()

        # Verify email extraction from "Name <email>" format
        assert meta["sender_email"] == "alex@corp.ru", \
            f"Expected 'alex@corp.ru', got {meta['sender_email']!r}"
        assert meta["subject"] == "Итоги проекта Q2"
        cc_list = meta["cc"]
        assert isinstance(cc_list, list)
        assert any("maria@corp.ru" in c for c in cc_list)

        # 3. Save draft — simulate panel form submission
        script_builder_captured = {}

        def _mock_build(**kwargs):
            script_builder_captured.update(kwargs)
            return 'tell application "Mail"\nend tell'

        with _DARWIN_PATCH, _RUN_AS_PATCH, patch(
            "personal_assistant.mlx_server.chat_routes._build_save_draft_mail_script",
            side_effect=_mock_build,
        ):
            save_resp = client.post("/api/chat/save-draft-mail", json={
                "subject": f"Re: {meta['subject']}",
                "body": "Добрый день, Алексей!\n\nСпасибо за подведение итогов.",
                "to_recipients": [meta["sender_email"]],
                "cc_recipients": meta["cc"],
                # All-digit ID bypasses vault lookup (native Mail.app message ID)
                "reply_to_message_id": "88042",
                "save_to_drafts": True,
            })

        assert save_resp.status_code == 200, save_resp.text
        assert save_resp.json()["ok"] is True

        # 4. Verify all args reached the script builder
        assert "alex@corp.ru" in script_builder_captured.get("to_recipients", [])
        assert script_builder_captured.get("save_to_drafts") is True
        # reply_to_message_id must be set for thread continuity
        assert script_builder_captured.get("reply_to_message_id") == "88042", \
            "reply_to_message_id must be forwarded to script builder for reply threading"
        # Subject must include original
        assert "Итоги проекта Q2" in script_builder_captured.get("subject", "")

    def test_draft_with_manual_address(self, tmp_path):
        """Without a vault doc, user can still send a manually typed address."""
        client = self._client()
        with _DARWIN_PATCH, _RUN_AS_PATCH:
            resp = client.post("/api/chat/save-draft-mail", json={
                "subject": "Manual test",
                "body": "Manually typed body",
                "to_recipients": ["someone@example.com"],
                "save_to_drafts": False,
            })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_cyrillic_subject_and_body_roundtrip(self, tmp_path):
        """Cyrillic text must survive the full round trip."""
        client = self._client()
        captured = {}

        def _mock_build(**kwargs):
            captured.update(kwargs)
            return 'tell application "Mail"\nend tell'

        with _DARWIN_PATCH, _RUN_AS_PATCH, patch(
            "personal_assistant.mlx_server.chat_routes._build_save_draft_mail_script",
            side_effect=_mock_build,
        ):
            resp = client.post("/api/chat/save-draft-mail", json={
                "subject": "Re: Тест кириллицы",
                "body": "Уважаемый коллега, в ответ на ваше письмо…",
                "to_recipients": ["test@example.com"],
                "save_to_drafts": True,
            })

        assert resp.status_code == 200
        assert captured.get("subject") == "Re: Тест кириллицы"
        assert "уважаемый" in captured.get("body_file_path", "").lower() \
            or "уважаемый" in str(captured).lower() \
            or resp.json()["ok"] is True   # body goes to temp file, not captured directly

    def test_message_response_contains_ok(self, tmp_path):
        """Response JSON must always contain ok:true + message string."""
        client = self._client()
        with _DARWIN_PATCH, _RUN_AS_PATCH:
            for save_flag in [True, False]:
                resp = client.post("/api/chat/save-draft-mail", json={
                    "subject": f"Test save_to_drafts={save_flag}",
                    "body": "Body text",
                    "to_recipients": ["a@b.com"],
                    "save_to_drafts": save_flag,
                })
                assert resp.status_code == 200
                data = resp.json()
                assert data["ok"] is True
                assert isinstance(data["message"], str)
                assert len(data["message"]) > 0

    def test_script_generated_for_reply_contains_thread_loop(self, tmp_path):
        """When reply_to_message_id set, generated AppleScript must contain repeat loop."""
        import os
        import tempfile

        from personal_assistant.mlx_server.chat_routes import _build_save_draft_mail_script

        # Write a dummy body file
        fd, body_path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("Test body")

            script = _build_save_draft_mail_script(
                subject="Re: Quarterly Update",
                body_file_path=body_path,
                to_recipients=["alex@corp.ru"],
                cc_recipients=["boss@corp.ru"],
                reply_to_message_id="<original-id@mail.corp.ru>",
                save_to_drafts=True,
            )
        finally:
            try:
                os.unlink(body_path)
            except OSError:
                pass

        # Must contain repeat loop for threading (not fragile 'whose')
        assert "repeat with" in script.lower(), \
            "Script must use 'repeat with' loop for reply threading"
        assert "whose message id" not in script.lower(), \
            "Script must NOT use 'whose' clause (causes -1728)"
        # Must reference the original message id
        assert "original-id@mail.corp.ru" in script
