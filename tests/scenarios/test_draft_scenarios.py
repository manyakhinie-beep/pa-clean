"""
Scenario tests for the Mail draft save pipeline.

Covers:
  • _resolve_reply_message_id  — vault item ID → Mail.app message_id lookup
  • get_mail_message_meta      — metadata endpoint (file stem / id / message_id)
  • save_draft_mail endpoint   — HTTP contract + AppleScript payload validation

These tests use a temporary vault directory and mock the AppleScript runner
so they run on any platform without Mail.app or MLX.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    from personal_assistant.mlx_server.server import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary vault/mail directory and patch settings.vault_path."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "mail").mkdir()

    mock_settings = MagicMock()
    mock_settings.vault_path = vault
    # A bare MagicMock makes every attribute truthy, which would make the
    # save-draft endpoint think e2e_test_mode is on and short-circuit before
    # building/calling the (mocked) AppleScript. Pin the flags the endpoint
    # reads to concrete values so the real draft path is exercised.
    mock_settings.e2e_test_mode = False
    mock_settings.mail_auto_draft = False

    with patch("personal_assistant.config.settings", mock_settings):
        yield vault


def _write_mail_doc(
    mail_dir: Path,
    *,
    filename: str,
    message_id: str = "",
    doc_id: str = "",
    subject: str = "Test",
    sender_email: str = "sender@example.com",
    sender_name: str = "Sender",
    recipients: list[str] | None = None,
    cc: list[str] | None = None,
    thread_id: str = "",
) -> Path:
    """Write a vault mail markdown file with given frontmatter."""
    id_line = f'id: "{doc_id}"\n' if doc_id else ""
    path = mail_dir / filename
    body = f"""---
message_id: "{message_id}"
{id_line}thread_id: "{thread_id}"
title: "{subject}"
type: mail-message
source: "mail"
sender: "[[contacts/{sender_email}]]"
sender_name: "{sender_name}"
from: "{sender_email}"
date: 2024-01-15T10:00:00+0000
mailbox: "Inbox"
has_attachments: false
recipients:
{chr(10).join(f'  - "{r}"' for r in (recipients or []))}
cc:
{chr(10).join(f'  - "{c}"' for c in (cc or []))}
tags: [почта]
created: 2024-01-15T10:00:00+0000
---

# {subject}

| Поле | Значение |
|------|----------|
| 📨 От | {sender_name} <{sender_email}> |
| 📅 Дата | 15.01.2024 10:00 |
"""
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _resolve_reply_message_id
# ---------------------------------------------------------------------------


class TestResolveReplyMessageId:
    def test_resolve_by_file_stem(self, tmp_vault: Path) -> None:
        from personal_assistant.mlx_server.chat_routes import _resolve_reply_message_id

        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="987654",
            subject="Hello",
        )

        result = _resolve_reply_message_id("2024-01-15_hello_abc123")
        assert result == "987654"

    def test_resolve_by_id_frontmatter(self, tmp_vault: Path) -> None:
        from personal_assistant.mlx_server.chat_routes import _resolve_reply_message_id

        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="555666",
            doc_id="msg_q2_001",
            subject="Hello",
        )

        result = _resolve_reply_message_id("msg_q2_001")
        assert result == "555666"

    def test_resolve_no_match(self, tmp_vault: Path) -> None:
        from personal_assistant.mlx_server.chat_routes import _resolve_reply_message_id

        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="111",
            subject="Hello",
        )

        result = _resolve_reply_message_id("nonexistent")
        assert result is None

    def test_resolve_empty_mail_dir(self, tmp_path: Path) -> None:
        from personal_assistant.mlx_server.chat_routes import _resolve_reply_message_id

        empty_vault = tmp_path / "empty"
        empty_vault.mkdir()
        (empty_vault / "mail").mkdir()

        mock_settings = MagicMock()
        mock_settings.vault_path = empty_vault

        with patch("personal_assistant.config.settings", mock_settings):
            assert _resolve_reply_message_id("anything") is None

    def test_resolve_no_mail_dir(self, tmp_path: Path) -> None:
        from personal_assistant.mlx_server.chat_routes import _resolve_reply_message_id

        no_mail_vault = tmp_path / "no_mail"
        no_mail_vault.mkdir()

        mock_settings = MagicMock()
        mock_settings.vault_path = no_mail_vault

        with patch("personal_assistant.config.settings", mock_settings):
            assert _resolve_reply_message_id("anything") is None


# ---------------------------------------------------------------------------
# get_mail_message_meta
# ---------------------------------------------------------------------------


class TestGetMailMessageMeta:
    def test_lookup_by_message_id(self, client: TestClient, tmp_vault: Path) -> None:
        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="777888",
            subject="Hello",
            sender_email="alice@example.com",
            sender_name="Alice",
            recipients=["bob@example.com"],
            cc=["carol@example.com"],
            thread_id="thread-1",
        )

        resp = client.get("/api/chat/mail/message-meta?message_id=777888")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message_id"] == "777888"
        assert data["subject"] == "Hello"
        assert data["sender_email"] == "alice@example.com"
        assert data["sender_name"] == "Alice"
        assert data["recipients"] == ["bob@example.com"]
        assert data["cc"] == ["carol@example.com"]
        assert data["thread_id"] == "thread-1"

    def test_lookup_by_file_stem(self, client: TestClient, tmp_vault: Path) -> None:
        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="999000",
            subject="By Stem",
        )

        resp = client.get("/api/chat/mail/message-meta?message_id=2024-01-15_hello_abc123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message_id"] == "999000"
        assert data["subject"] == "By Stem"

    def test_lookup_by_id_frontmatter(self, client: TestClient, tmp_vault: Path) -> None:
        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="333444",
            doc_id="msg_special",
            subject="By ID",
        )

        resp = client.get("/api/chat/mail/message-meta?message_id=msg_special")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message_id"] == "333444"
        assert data["subject"] == "By ID"

    def test_lookup_not_found(self, client: TestClient, tmp_vault: Path) -> None:
        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="111",
            subject="Hello",
        )

        resp = client.get("/api/chat/mail/message-meta?message_id=notfound")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower() or "not found" in resp.json()["detail"]

    def test_lookup_no_mail_dir(self, client: TestClient, tmp_path: Path) -> None:
        empty_vault = tmp_path / "empty"
        empty_vault.mkdir()
        # no mail/ subdirectory

        mock_settings = MagicMock()
        mock_settings.vault_path = empty_vault

        with patch("personal_assistant.config.settings", mock_settings):
            resp = client.get("/api/chat/mail/message-meta?message_id=anything")
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# save_draft_mail endpoint
# ---------------------------------------------------------------------------


class TestSaveDraftMailEndpoint:
    """save-draft-mail endpoint tests.

    patch_darwin: the endpoint has ``if platform.system() != "Darwin": raise 501``
    so we patch platform.system for every test in this class.
    """

    @pytest.fixture(autouse=True)
    def patch_darwin(self):
        with patch("platform.system", return_value="Darwin"):
            yield

    @pytest.fixture(autouse=True)
    def _disable_e2e_test_mode(self, monkeypatch):
        """The root conftest forces ``e2e_test_mode=True`` so the suite never
        touches real Mail.app. These endpoint tests instead mock
        ``run_applescript`` and assert it is invoked with the built script, so
        they must run the real (mocked) AppleScript path — disable the
        short-circuit for this class only."""
        from personal_assistant.config import settings

        monkeypatch.setattr(settings, "e2e_test_mode", False)

    def test_save_draft_success(self, client: TestClient, tmp_vault: Path) -> None:
        """POST /save-draft-mail returns 200 when AppleScript succeeds."""
        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="12345",
            subject="Original",
        )

        with patch(
            "personal_assistant.readers.applescript_base.run_applescript",
            return_value="ok",
        ) as mock_run:
            resp = client.post(
                "/api/chat/save-draft-mail",
                json={
                    "subject": "Re: Original",
                    "body": "Hello world",
                    "to_recipients": ["bob@example.com"],
                    "cc_recipients": ["carol@example.com"],
                    "reply_to_message_id": "2024-01-15_hello_abc123",
                    "save_to_drafts": True,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "сохранён" in data["message"].lower()
        mock_run.assert_called_once()
        # Verify the script was built with resolved message_id (12345)
        script = mock_run.call_args[0][0]
        assert "12345" in script
        assert "Hello world" not in script  # body comes from file, not inline

    def test_save_draft_resolves_id(self, client: TestClient, tmp_vault: Path) -> None:
        """When reply_to_message_id is a vault stem, it gets resolved to Mail message_id."""
        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="99999",
            doc_id="msg_q2_001",
            subject="Original",
        )

        with patch(
            "personal_assistant.readers.applescript_base.run_applescript",
            return_value="ok",
        ) as mock_run:
            resp = client.post(
                "/api/chat/save-draft-mail",
                json={
                    "subject": "Re: Original",
                    "body": "Reply text",
                    "to_recipients": [],
                    "cc_recipients": [],
                    "reply_to_message_id": "msg_q2_001",
                    "save_to_drafts": False,
                },
            )

        assert resp.status_code == 200
        script = mock_run.call_args[0][0]
        assert "99999" in script
        # Ensure the stem is NOT in the script (it was resolved)
        assert "msg_q2_001" not in script

    def test_save_draft_numeric_id_passthrough(self, client: TestClient, tmp_vault: Path) -> None:
        """When reply_to_message_id is already numeric, pass it through unchanged."""
        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="55555",
            subject="Original",
        )

        with patch(
            "personal_assistant.readers.applescript_base.run_applescript",
            return_value="ok",
        ) as mock_run:
            resp = client.post(
                "/api/chat/save-draft-mail",
                json={
                    "subject": "Re: Original",
                    "body": "Reply text",
                    "to_recipients": [],
                    "cc_recipients": [],
                    "reply_to_message_id": "55555",
                    "save_to_drafts": True,
                },
            )

        assert resp.status_code == 200
        script = mock_run.call_args[0][0]
        assert "55555" in script

    def test_save_draft_applescript_error(self, client: TestClient, tmp_vault: Path) -> None:
        """When AppleScript fails, endpoint returns 500 with error detail."""
        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="12345",
            subject="Original",
        )

        with patch(
            "personal_assistant.readers.applescript_base.run_applescript",
            side_effect=RuntimeError("error -2741"),
        ):
            resp = client.post(
                "/api/chat/save-draft-mail",
                json={
                    "subject": "Re: Original",
                    "body": "Hello",
                    "to_recipients": [],
                    "cc_recipients": [],
                    "reply_to_message_id": "12345",
                    "save_to_drafts": True,
                },
            )

        assert resp.status_code == 500
        assert "failed" in resp.json()["detail"].lower()

    def test_save_draft_no_reply_id(self, client: TestClient, tmp_vault: Path) -> None:
        """Draft without reply_to_message_id should still work."""
        with patch(
            "personal_assistant.readers.applescript_base.run_applescript",
            return_value="ok",
        ) as mock_run:
            resp = client.post(
                "/api/chat/save-draft-mail",
                json={
                    "subject": "New Draft",
                    "body": "Fresh start",
                    "to_recipients": ["bob@example.com"],
                    "cc_recipients": [],
                    "reply_to_message_id": None,
                    "save_to_drafts": True,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        script = mock_run.call_args[0][0]
        # No threading block when reply_to_message_id is None
        assert "reply to" not in script.lower()

    def test_save_draft_validation_error(self, client: TestClient) -> None:
        """Empty subject should be rejected by Pydantic validation."""
        resp = client.post(
            "/api/chat/save-draft-mail",
            json={
                "subject": "",
                "body": "Hello",
                "to_recipients": [],
                "cc_recipients": [],
                "reply_to_message_id": None,
                "save_to_drafts": True,
            },
        )
        assert resp.status_code == 422

    def test_save_draft_cyrillic_body(self, client: TestClient, tmp_vault: Path) -> None:
        """Cyrillic text in body should be handled correctly via temp file."""
        mail_dir = tmp_vault / "mail"
        _write_mail_doc(
            mail_dir,
            filename="2024-01-15_hello_abc123.md",
            message_id="12345",
            subject="Original",
        )

        with patch(
            "personal_assistant.readers.applescript_base.run_applescript",
            return_value="ok",
        ) as mock_run:
            resp = client.post(
                "/api/chat/save-draft-mail",
                json={
                    "subject": "Re: Original",
                    "body": "Уважаемый коллега, спасибо за письмо.",
                    "to_recipients": ["коллега@пример.рф"],
                    "cc_recipients": [],
                    "reply_to_message_id": "12345",
                    "save_to_drafts": True,
                },
            )

        assert resp.status_code == 200
        script = mock_run.call_args[0][0]
        # The body is read from a temp file, not embedded in script
        assert "Уважаемый" not in script
        # But the subject is in the script
        assert "Re: Original" in script


# ---------------------------------------------------------------------------
# AppleScript payload validation
# ---------------------------------------------------------------------------


class TestAppleScriptPayload:
    """Validate the generated AppleScript structure for all four combinations of
    (reply_to_message_id present/absent) × (save_to_drafts True/False).

    Key invariants (post-fix):
      • content is set AFTER make/reply — never inside 'with properties {content:…}'
      • activate present iff save_to_drafts=False (open-window mode)
      • no 'whose' clause anywhere
    """

    def _build(self, **kwargs):
        from personal_assistant.mlx_server.chat_routes import _build_save_draft_mail_script
        return _build_save_draft_mail_script(**kwargs)

    # ── content must be set separately, not in with-properties ──────────────

    def test_content_set_separately_new_save(self):
        s = self._build(subject="S", body_file_path="/tmp/b.txt",
                        to_recipients=[], cc_recipients=[],
                        reply_to_message_id=None, save_to_drafts=True)
        assert "set content of newMsg to bodyContent" in s
        assert "content:bodyContent" not in s

    def test_content_set_separately_new_open(self):
        s = self._build(subject="S", body_file_path="/tmp/b.txt",
                        to_recipients=[], cc_recipients=[],
                        reply_to_message_id=None, save_to_drafts=False)
        assert "set content of newMsg to bodyContent" in s
        assert "content:bodyContent" not in s

    def test_content_set_separately_reply_save(self):
        s = self._build(subject="Re: S", body_file_path="/tmp/b.txt",
                        to_recipients=[], cc_recipients=[],
                        reply_to_message_id="99001", save_to_drafts=True)
        assert "set content of newMsg to bodyContent" in s
        assert "content:bodyContent" not in s

    def test_content_set_separately_reply_open(self):
        s = self._build(subject="Re: S", body_file_path="/tmp/b.txt",
                        to_recipients=[], cc_recipients=[],
                        reply_to_message_id="99001", save_to_drafts=False)
        assert "set content of newMsg to bodyContent" in s
        assert "content:bodyContent" not in s

    # ── activate present only for open-window mode ───────────────────────────

    def test_activate_present_when_open_window(self):
        s = self._build(subject="S", body_file_path="/tmp/b.txt",
                        to_recipients=[], cc_recipients=[],
                        reply_to_message_id=None, save_to_drafts=False)
        assert "activate" in s

    def test_no_activate_when_save_to_drafts(self):
        s = self._build(subject="S", body_file_path="/tmp/b.txt",
                        to_recipients=[], cc_recipients=[],
                        reply_to_message_id=None, save_to_drafts=True)
        assert "activate" not in s

    def test_activate_present_reply_open_window(self):
        s = self._build(subject="Re: S", body_file_path="/tmp/b.txt",
                        to_recipients=[], cc_recipients=[],
                        reply_to_message_id="88001", save_to_drafts=False)
        assert "activate" in s

    def test_no_activate_reply_save_to_drafts(self):
        s = self._build(subject="Re: S", body_file_path="/tmp/b.txt",
                        to_recipients=[], cc_recipients=[],
                        reply_to_message_id="88001", save_to_drafts=True)
        assert "activate" not in s

    # ── final-action correctness ─────────────────────────────────────────────

    def test_script_contains_subject_and_recipients(self) -> None:
        script = self._build(
            subject="Hello World",
            body_file_path="/tmp/body.txt",
            to_recipients=["a@example.com", "b@example.com"],
            cc_recipients=["c@example.com"],
            reply_to_message_id=None,
            save_to_drafts=True,
        )
        assert "Hello World" in script
        assert "a@example.com" in script
        assert "b@example.com" in script
        assert "c@example.com" in script
        assert "save newMsg" in script

    def test_script_opens_window_when_not_drafts(self) -> None:
        script = self._build(
            subject="Test",
            body_file_path="/tmp/body.txt",
            to_recipients=[],
            cc_recipients=[],
            reply_to_message_id=None,
            save_to_drafts=False,
        )
        assert "open newMsg" in script
        assert "save newMsg" not in script

    def test_script_contains_reply_threading(self) -> None:
        script = self._build(
            subject="Re: Test",
            body_file_path="/tmp/body.txt",
            to_recipients=[],
            cc_recipients=[],
            reply_to_message_id="98765",
            save_to_drafts=True,
        )
        assert "reply origMsg" in script
        assert "98765" in script

    def test_no_whose_in_any_variant(self) -> None:
        """'whose' must never appear — it triggers -2741 in Mail.app."""
        for reply_id in [None, "msg-abc", "99999"]:
            s = self._build(
                subject="Test",
                body_file_path="/tmp/b.txt",
                to_recipients=["a@b.com"],
                cc_recipients=[],
                reply_to_message_id=reply_id,
                save_to_drafts=True,
            )
            assert "whose" not in s, f"'whose' found for reply_id={reply_id!r}"

    def test_reply_open_window_true_uses_opening_window_true(self) -> None:
        """save_to_drafts=False reply path should use opening window true."""
        s = self._build(
            subject="Re: Test",
            body_file_path="/tmp/b.txt",
            to_recipients=[],
            cc_recipients=[],
            reply_to_message_id="12345",
            save_to_drafts=False,
        )
        assert "opening window true" in s
        assert "opening window false" not in s

    def test_reply_save_to_drafts_uses_opening_window_false(self) -> None:
        """save_to_drafts=True reply path should use opening window false + save newMsg."""
        s = self._build(
            subject="Re: Test",
            body_file_path="/tmp/b.txt",
            to_recipients=[],
            cc_recipients=[],
            reply_to_message_id="12345",
            save_to_drafts=True,
        )
        assert "opening window false" in s
        assert "save newMsg" in s
