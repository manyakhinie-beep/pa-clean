"""
Unit tests for MailReader (mail_reader.py).

All AppleScript / osascript calls are mocked via @patch('subprocess.run')
so these tests run on any platform (including Linux CI).

Test strategy:
  - Mock subprocess.run to return pre-baked osascript output
  - Verify MailReader parses JSON correctly into MailMessage models
  - Cover: list mailboxes, fetch messages, noise-folder filtering,
    sender parsing, deduplication across nested mailboxes,
    malformed JSON graceful handling, non-macOS platform
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_result(stdout: str, returncode: int = 0, stderr: str = ""):
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    result.stderr = stderr
    return result


def _mbox_list_output(*pairs: tuple[str, str]) -> str:
    """Build the raw output of _LIST_MAILBOXES_SCRIPT."""
    return "\n".join(f"{acct}|||{mbox}" for acct, mbox in pairs)


def _messages_json(*msgs: dict) -> str:
    return json.dumps(list(msgs))


_MINIMAL_MSG = {
    "id": "MSG-001",
    "subject": "Project Update",
    "sender": "Alice <alice@corp.com>",
    "recipients": "bob@corp.com,charlie@corp.com",
    "cc": "",
    "date": "2026-05-20T10:00:00",
    "mailbox": "INBOX",
    "body": "",
    "has_attachments": "false",
    "attachment_names": "",
    "source": "mail",
}


# ---------------------------------------------------------------------------
# T01: List mailboxes
# ---------------------------------------------------------------------------


class TestMailReaderListMailboxes:
    def test_list_mailboxes_parses_account_and_mailbox(self):
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run",
            return_value=_make_run_result(
                _mbox_list_output(("Work Account", "INBOX"), ("Work Account", "Archive"))
            ),
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            mboxes = reader._list_mailboxes()

        assert len(mboxes) == 2
        assert mboxes[0]["account"] == "Work Account"
        assert mboxes[0]["mailbox"] == "INBOX"

    def test_list_mailboxes_empty_output(self):
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", return_value=_make_run_result("")
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            mboxes = reader._list_mailboxes()

        assert mboxes == []

    def test_list_mailboxes_raises_on_osascript_error(self):
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run",
            return_value=_make_run_result("", returncode=1, stderr="Not authorised"),
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            with pytest.raises(RuntimeError, match="osascript error"):
                reader._list_mailboxes()


# ---------------------------------------------------------------------------
# T02: fetch_messages — happy path
# ---------------------------------------------------------------------------


class TestMailReaderFetchMessages:
    def test_fetch_messages_returns_mail_message_objects(self):
        from personal_assistant.models import MailMessage

        side_effects = [
            _make_run_result(_mbox_list_output(("Work", "INBOX"))),
            _make_run_result(_messages_json(_MINIMAL_MSG)),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages(days_back=7)

        assert len(msgs) == 1
        msg = msgs[0]
        assert isinstance(msg, MailMessage)
        assert msg.message_id == "MSG-001"
        assert msg.subject == "Project Update"
        assert msg.sender_email == "alice@corp.com"

    def test_fetch_messages_parses_sender_name(self):
        side_effects = [
            _make_run_result(_mbox_list_output(("Work", "INBOX"))),
            _make_run_result(_messages_json(_MINIMAL_MSG)),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()

        assert msgs[0].sender_name == "Alice"

    def test_fetch_messages_parses_recipients(self):
        side_effects = [
            _make_run_result(_mbox_list_output(("Work", "INBOX"))),
            _make_run_result(_messages_json(_MINIMAL_MSG)),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()

        recipients = msgs[0].recipients
        assert "bob@corp.com" in recipients
        assert "charlie@corp.com" in recipients

    def test_fetch_messages_parses_attachments(self):
        msg_with_attach = {
            **_MINIMAL_MSG,
            "id": "MSG-ATT",
            "has_attachments": "true",
            "attachment_names": "report.pdf|data.xlsx|",
        }

        side_effects = [
            _make_run_result(_mbox_list_output(("Work", "INBOX"))),
            _make_run_result(_messages_json(msg_with_attach)),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages(fetch_body=True)

        assert msgs[0].has_attachments is True
        assert "report.pdf" in msgs[0].attachments
        assert "data.xlsx" in msgs[0].attachments


# ---------------------------------------------------------------------------
# T03: Noise folder filtering
# ---------------------------------------------------------------------------


class TestMailReaderNoiseFolders:
    @pytest.mark.parametrize(
        "folder_name",
        [
            "Sent Messages",
            "Sent",
            "Trash",
            "Deleted Messages",
            "Junk",
            "Spam",
            "Drafts",
            "Archive",
        ],
    )
    def test_noise_folders_are_skipped(self, folder_name):
        """Standard noise folders must not be fetched (no second subprocess.run call)."""
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run",
            return_value=_make_run_result(
                _mbox_list_output(("Work", folder_name))
            ),
        ) as mock_run:
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()

        # Only one call: list mailboxes (no fetch because folder was skipped)
        assert mock_run.call_count == 1
        assert msgs == []

    def test_inbox_is_not_skipped(self):
        """INBOX must be fetched even though some other folders are noise."""
        side_effects = [
            _make_run_result(
                _mbox_list_output(("Work", "INBOX"), ("Work", "Sent Messages"))
            ),
            _make_run_result(_messages_json(_MINIMAL_MSG)),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ) as mock_run:
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()

        # Two calls: list + one mailbox fetch (Sent is skipped)
        assert mock_run.call_count == 2
        assert len(msgs) == 1

    def test_custom_skip_mailboxes_are_excluded(self):
        """Extra skip_mailboxes argument skips additional folders."""
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run",
            return_value=_make_run_result(
                _mbox_list_output(("Work", "INBOX"), ("Work", "Projects"))
            ),
        ) as mock_run:
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            # Add "Projects" to skip
            msgs = reader.fetch_messages(skip_mailboxes={"Projects", "INBOX"})

        # Both mailboxes skipped → only list call
        assert mock_run.call_count == 1
        assert msgs == []


# ---------------------------------------------------------------------------
# T04: Deduplication across nested mailboxes
# ---------------------------------------------------------------------------


class TestMailReaderDeduplication:
    def test_duplicate_message_ids_are_deduplicated(self):
        """Same message_id appearing in two mailboxes must appear only once."""
        side_effects = [
            _make_run_result(
                _mbox_list_output(("Work", "INBOX"), ("Work", "All Mail"))
            ),
            _make_run_result(_messages_json(_MINIMAL_MSG)),   # INBOX
            _make_run_result(_messages_json(_MINIMAL_MSG)),   # All Mail (duplicate)
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()

        assert len(msgs) == 1
        assert msgs[0].message_id == "MSG-001"

    def test_different_message_ids_are_not_deduplicated(self):
        msg2 = {**_MINIMAL_MSG, "id": "MSG-002", "subject": "Follow-up"}

        side_effects = [
            _make_run_result(
                _mbox_list_output(("Work", "INBOX"), ("Work", "All Mail"))
            ),
            _make_run_result(_messages_json(_MINIMAL_MSG)),
            _make_run_result(_messages_json(msg2)),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()

        assert len(msgs) == 2
        ids = {m.message_id for m in msgs}
        assert ids == {"MSG-001", "MSG-002"}


# ---------------------------------------------------------------------------
# T05: Sender parsing edge cases
# ---------------------------------------------------------------------------


class TestMailReaderSenderParsing:
    def _fetch_with_sender(self, sender_str: str):
        msg = {**_MINIMAL_MSG, "sender": sender_str}
        side_effects = [
            _make_run_result(_mbox_list_output(("Work", "INBOX"))),
            _make_run_result(_messages_json(msg)),
        ]
        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            return reader.fetch_messages()

    def test_sender_with_name_and_angle_brackets(self):
        msgs = self._fetch_with_sender("Alice Smith <alice@example.com>")
        assert msgs[0].sender_email == "alice@example.com"
        assert msgs[0].sender_name == "Alice Smith"

    def test_sender_email_only(self):
        msgs = self._fetch_with_sender("bob@corp.com")
        assert msgs[0].sender_email == "bob@corp.com"

    def test_sender_email_lowercase_normalised(self):
        msgs = self._fetch_with_sender("BOB@CORP.COM")
        assert msgs[0].sender_email == "bob@corp.com"

    def test_sender_with_angle_brackets_no_name(self):
        msgs = self._fetch_with_sender("<carol@example.org>")
        assert msgs[0].sender_email == "carol@example.org"


# ---------------------------------------------------------------------------
# T06: Malformed / edge-case JSON handling
# ---------------------------------------------------------------------------


class TestMailReaderMalformedJSON:
    def test_malformed_json_returns_empty_gracefully(self):
        side_effects = [
            _make_run_result(_mbox_list_output(("Work", "INBOX"))),
            _make_run_result("not valid json {{{"),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()

        assert isinstance(msgs, list)

    def test_empty_json_array_returns_empty(self):
        side_effects = [
            _make_run_result(_mbox_list_output(("Work", "INBOX"))),
            _make_run_result("[]"),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()

        assert msgs == []

    def test_missing_id_field_still_produces_message(self):
        """Messages missing 'id' get empty string message_id — no crash."""
        msg_no_id = {k: v for k, v in _MINIMAL_MSG.items() if k != "id"}

        side_effects = [
            _make_run_result(_mbox_list_output(("Work", "INBOX"))),
            _make_run_result(_messages_json(msg_no_id)),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()

        assert len(msgs) == 1
        assert msgs[0].message_id == ""


# ---------------------------------------------------------------------------
# T07: Non-macOS graceful handling
# ---------------------------------------------------------------------------


class TestMailReaderPlatform:
    def test_fetch_messages_returns_empty_on_linux(self):
        with patch("sys.platform", "linux"):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()

        assert msgs == []

    def test_list_mailboxes_raises_runtime_error_on_linux(self):
        with patch("sys.platform", "linux"):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            with pytest.raises(RuntimeError, match="macOS"):
                reader._list_mailboxes()


# ---------------------------------------------------------------------------
# T08: extract_contacts from messages
# ---------------------------------------------------------------------------


class TestMailReaderExtractContacts:
    def test_extract_contacts_returns_contact_objects(self):
        from personal_assistant.models import Contact

        side_effects = [
            _make_run_result(_mbox_list_output(("Work", "INBOX"))),
            _make_run_result(_messages_json(_MINIMAL_MSG)),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()
            contacts = reader.extract_contacts(msgs)

        assert len(contacts) >= 1
        assert all(isinstance(c, Contact) for c in contacts)
        emails = {c.email for c in contacts}
        assert "alice@corp.com" in emails

    def test_extract_contacts_deduplicates_by_email(self):
        """Same sender appearing in two messages produces one contact."""
        msg2 = {**_MINIMAL_MSG, "id": "MSG-002", "subject": "Another email"}

        side_effects = [
            _make_run_result(
                _mbox_list_output(("Work", "INBOX"), ("Work", "All Mail"))
            ),
            _make_run_result(_messages_json(_MINIMAL_MSG)),
            _make_run_result(_messages_json(msg2)),
        ]

        with patch("sys.platform", "darwin"), patch(
            "subprocess.run", side_effect=side_effects
        ):
            from personal_assistant.readers.mail_reader import MailReader

            reader = MailReader()
            msgs = reader.fetch_messages()
            contacts = reader.extract_contacts(msgs)

        alice_contacts = [c for c in contacts if c.email == "alice@corp.com"]
        assert len(alice_contacts) == 1
