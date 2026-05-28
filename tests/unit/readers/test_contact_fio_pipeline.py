"""
End-to-end verification: AppleScript ``sender`` field with a Russian
ФИО display name → MailReader._convert → extract_contacts → VaultWriter
→ ``vault/contacts/<email>.md`` with ``full_name: Фамилия Имя`` in YAML
frontmatter.

This pins down the pipeline the user just asked about («проверь, что
ФИО подтягивается в контакты vault»), so a future regression in any
link of the chain — sender parsing, name_extractor scoring, vault
template — breaks the build.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml


def _ok(stdout: str):
    r = MagicMock()
    r.stdout = stdout
    r.returncode = 0
    r.stderr = ""
    return r


def _mbox_list(*pairs):
    return "\n".join(f"{a}|||{m}" for a, m in pairs)


def _message_with_sender(sender: str, email: str = "vdmitry@example.ru") -> dict:
    return {
        "id": f"<msg-{email}>",
        "subject": "Тест",
        "sender": sender,
        "recipients": "",
        "cc": "",
        "date": "2026-05-28T10:00:00",
        "mailbox": "Валов Дмитрий",
        "body": "",
        "has_attachments": "false",
        "attachment_names": "",
        "source": "mail",
    }


def _read_contact_frontmatter(vault_root: Path, email: str) -> dict:
    """Parse YAML frontmatter from vault/contacts/<email>.md."""
    safe = email.replace("@", "_").replace(".", "_") if False else email
    # writer slugifies via re.sub(r"[^\w@._-]", "_") — for our test emails
    # only @ + . + alnum are present, so the file name == email + .md
    path = vault_root / "contacts" / f"{email}.md"
    assert path.exists(), f"Contact file not written: {path}"
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("---"), f"No frontmatter in {path}"
    end_idx = raw.find("\n---", 3)
    assert end_idx > 0
    return yaml.safe_load(raw[3:end_idx]) or {}


# ----------------------------------------------------------------------
# Pipeline tests
# ----------------------------------------------------------------------


def test_cyrillic_fio_in_rfc822_format_lands_in_vault(tmp_path: Path):
    """The canonical happy path: «Валов Дмитрий <vdmitry@example.ru>» as
    sender — full_name = "Валов Дмитрий" should appear in YAML."""
    from personal_assistant.readers.mail_reader import MailReader
    from personal_assistant.vault.writer import VaultWriter

    msg = _message_with_sender("Валов Дмитрий <vdmitry@example.ru>")
    side_effects = [
        _ok(_mbox_list(("Sberbank email", "Валов Дмитрий"))),
        _ok(json.dumps([msg])),
    ]
    with patch("sys.platform", "darwin"), patch(
        "subprocess.run", side_effect=side_effects
    ):
        reader = MailReader()
        messages = reader.fetch_messages(days_back=7)
        contacts = reader.extract_contacts(messages)

    assert len(messages) == 1
    assert messages[0].sender_name == "Валов Дмитрий"
    assert messages[0].sender_email == "vdmitry@example.ru"
    assert len(contacts) == 1

    writer = VaultWriter(tmp_path)
    writer.write_contacts(contacts)

    fm = _read_contact_frontmatter(tmp_path, "vdmitry@example.ru")
    assert fm.get("full_name") == "Валов Дмитрий", (
        f"Expected 'Валов Дмитрий' in vault frontmatter, got: {fm.get('full_name')!r}"
    )
    assert fm.get("name_source") == "mail"
    assert "mail" in (fm.get("sources") or [])


def test_full_fio_three_parts_preserved(tmp_path: Path):
    """Full ФИО with patronymic — name_quality should grade as 3 and
    survive the best_name() pick."""
    from personal_assistant.readers.mail_reader import MailReader
    from personal_assistant.vault.writer import VaultWriter

    msg = _message_with_sender(
        "Валов Дмитрий Сергеевич <vds@corp.ru>", email="vds@corp.ru"
    )
    with patch("sys.platform", "darwin"), patch(
        "subprocess.run",
        side_effect=[_ok(_mbox_list(("X", "INBOX"))), _ok(json.dumps([msg]))],
    ):
        reader = MailReader()
        messages = reader.fetch_messages(days_back=1)
        contacts = reader.extract_contacts(messages)

    VaultWriter(tmp_path).write_contacts(contacts)
    fm = _read_contact_frontmatter(tmp_path, "vds@corp.ru")
    assert fm.get("full_name") == "Валов Дмитрий Сергеевич"


def test_quoted_fio_in_sender_unquoted_in_vault(tmp_path: Path):
    """`""Валов, Дмитрий"" <vdmitry@example.ru>` — comma-form + quotes —
    name_extractor should normalize to «Валов Дмитрий»."""
    from personal_assistant.readers.mail_reader import MailReader
    from personal_assistant.vault.writer import VaultWriter

    msg = _message_with_sender(
        '"Валов, Дмитрий" <vdmitry@example.ru>',
    )
    with patch("sys.platform", "darwin"), patch(
        "subprocess.run",
        side_effect=[_ok(_mbox_list(("X", "INBOX"))), _ok(json.dumps([msg]))],
    ):
        reader = MailReader()
        messages = reader.fetch_messages(days_back=1)
        contacts = reader.extract_contacts(messages)

    VaultWriter(tmp_path).write_contacts(contacts)
    fm = _read_contact_frontmatter(tmp_path, "vdmitry@example.ru")
    assert fm.get("full_name") == "Валов Дмитрий"


def test_bare_email_yields_no_full_name(tmp_path: Path):
    """When the sender header has no display part, no ФИО to extract — but
    the contact file should still be written with email-only and no
    spurious full_name guess."""
    from personal_assistant.readers.mail_reader import MailReader
    from personal_assistant.vault.writer import VaultWriter

    msg = _message_with_sender("vdmitry@example.ru")
    with patch("sys.platform", "darwin"), patch(
        "subprocess.run",
        side_effect=[_ok(_mbox_list(("X", "INBOX"))), _ok(json.dumps([msg]))],
    ):
        reader = MailReader()
        messages = reader.fetch_messages(days_back=1)
        contacts = reader.extract_contacts(messages)

    VaultWriter(tmp_path).write_contacts(contacts)
    fm = _read_contact_frontmatter(tmp_path, "vdmitry@example.ru")
    assert not fm.get("full_name")  # None or empty string both acceptable


def test_existing_vault_full_name_not_demoted(tmp_path: Path):
    """If a previous sync wrote a higher-quality ФИО (3 parts), a new
    mail with a 2-part name from same email must NOT overwrite it."""
    from personal_assistant.models import Contact
    from personal_assistant.vault.writer import VaultWriter

    writer = VaultWriter(tmp_path)
    # First write: full ФИО (3 parts → quality 3)
    writer.write_contacts([
        Contact(email="x@y.ru", name="Валов Дмитрий Сергеевич", sources=["mail"])
    ])
    fm1 = _read_contact_frontmatter(tmp_path, "x@y.ru")
    assert fm1.get("full_name") == "Валов Дмитрий Сергеевич"

    # Second write: 2-part name (quality 2) — should NOT demote
    writer.write_contacts([
        Contact(email="x@y.ru", name="Валов Дмитрий", sources=["mail"])
    ])
    fm2 = _read_contact_frontmatter(tmp_path, "x@y.ru")
    assert fm2.get("full_name") == "Валов Дмитрий Сергеевич"
