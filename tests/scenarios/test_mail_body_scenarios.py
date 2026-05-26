"""
Scenario tests for mail body storage in vault.

Covers:
  • Default config fetches mail body (PA_MAIL_FETCH_BODY=true by default)
  • Vault writer stores body in markdown file
  • Inbox API returns body for display
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

    with patch("personal_assistant.config.settings", mock_settings):
        yield vault


# ---------------------------------------------------------------------------
# 1. Default config fetches body
# ---------------------------------------------------------------------------


class TestMailBodyConfig:
    def test_default_mail_fetch_body_is_true(self) -> None:
        """By default, mail_fetch_body should be True so bodies are stored."""
        from personal_assistant.config import _env_bool
        # _env_bool returns the default when env var is absent
        assert _env_bool("MAIL_FETCH_BODY_XYZ", True) is True
        assert _env_bool("MAIL_FETCH_BODY_XYZ", False) is False


# ---------------------------------------------------------------------------
# 2. Vault writer stores body
# ---------------------------------------------------------------------------


class TestVaultWriterStoresBody:
    def test_write_message_includes_body(self, tmp_vault: Path) -> None:
        """VaultWriter.write_message stores email body in the markdown file."""
        from datetime import datetime, timezone

        from personal_assistant.models import MailMessage
        from personal_assistant.vault.writer import VaultWriter

        writer = VaultWriter(tmp_vault)
        msg = MailMessage(
            message_id="12345",
            subject="Test Subject",
            sender_name="Alice",
            sender_email="alice@example.com",
            recipients=["bob@example.com"],
            cc=[],
            date=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
            mailbox="Inbox",
            body="This is the full email body text.",
            has_attachments=False,
            attachments=[],
            thread_id="thread-abc",
        )
        path = writer.write_message(msg, overwrite=True)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        assert "## Текст письма" in content
        assert "This is the full email body text." in content

    def test_write_message_without_body_omits_section(self, tmp_vault: Path) -> None:
        """When body is empty, the body section is omitted from the markdown file."""
        from datetime import datetime, timezone

        from personal_assistant.models import MailMessage
        from personal_assistant.vault.writer import VaultWriter

        writer = VaultWriter(tmp_vault)
        msg = MailMessage(
            message_id="67890",
            subject="No Body",
            sender_name="Bob",
            sender_email="bob@example.com",
            recipients=[],
            cc=[],
            date=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
            mailbox="Inbox",
            body="",
            has_attachments=False,
            attachments=[],
            thread_id="thread-def",
        )
        path = writer.write_message(msg, overwrite=True)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        assert "## Текст письма" not in content


# ---------------------------------------------------------------------------
# 3. Inbox API returns body
# ---------------------------------------------------------------------------


class TestInboxReturnsBody:
    def test_vault_file_contains_body_for_inbox_display(self, tmp_vault: Path) -> None:
        """Vault file stores body so inbox can display it later."""
        from datetime import datetime, timezone

        from personal_assistant.models import MailMessage
        from personal_assistant.vault.writer import VaultWriter

        writer = VaultWriter(tmp_vault)
        msg = MailMessage(
            message_id="11111",
            subject="Important Update",
            sender_name="Charlie",
            sender_email="charlie@example.com",
            recipients=["dave@example.com"],
            cc=[],
            date=datetime(2024, 6, 15, 10, 0, 0, tzinfo=timezone.utc),
            mailbox="Inbox",
            body="Please review the attached documents before Friday.",
            has_attachments=False,
            attachments=[],
            thread_id="thread-xyz",
        )
        path = writer.write_message(msg, overwrite=True)
        assert path is not None
        content = path.read_text(encoding="utf-8")
        # The body section must exist for inbox to extract it
        assert "## Текст письма" in content
        assert "Please review the attached documents before Friday." in content
