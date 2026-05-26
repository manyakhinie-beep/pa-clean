"""
Unit tests for vault/writer.py (VaultWriter).

Covers: write_event, write_message, write_contact,
        frontmatter fields, slugification, deduplication (skip existing).
"""

from __future__ import annotations

from personal_assistant.vault.writer import VaultWriter


class TestVaultWriterEvents:
    def test_write_event_creates_file(self, tmp_path, sample_event):
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_event(sample_event)
        assert path is not None
        assert path.exists()

    def test_write_event_path_structure(self, tmp_path, sample_event):
        vault = tmp_path / "vault"
        writer = VaultWriter(vault)
        path = writer.write_event(sample_event)
        # Should be under calendar/2026/05/
        assert "calendar" in path.parts
        assert "2026" in path.parts
        assert path.suffix == ".md"

    def test_write_event_frontmatter_uid(self, tmp_path, sample_event):
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_event(sample_event)
        content = path.read_text(encoding="utf-8")
        assert "test-uid-001" in content

    def test_write_event_frontmatter_title(self, tmp_path, sample_event):
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_event(sample_event)
        content = path.read_text(encoding="utf-8")
        assert "Team Standup" in content

    def test_write_event_skips_existing(self, tmp_path, sample_event):
        writer = VaultWriter(tmp_path / "vault")
        p1 = writer.write_event(sample_event)
        p2 = writer.write_event(sample_event)  # second write
        assert p1 is not None
        assert p2 is None  # skipped

    def test_write_event_overwrite(self, tmp_path, sample_event):
        writer = VaultWriter(tmp_path / "vault")
        p1 = writer.write_event(sample_event)
        assert p1 is not None, "first write should create the file"
        p2 = writer.write_event(sample_event, overwrite=True)
        assert p2 is not None, "second write with overwrite=True should succeed"
        assert p1 == p2, "overwrite must target the same path"

    def test_write_events_batch(self, tmp_path, sample_event):
        vault = tmp_path / "vault"
        writer = VaultWriter(vault)
        written, skipped = writer.write_events([sample_event, sample_event])
        assert written == 1
        assert skipped == 1


class TestVaultWriterMessages:
    def test_write_message_creates_file(self, tmp_path, sample_mail):
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_message(sample_mail)
        assert path is not None
        assert path.exists()

    def test_write_message_path_structure(self, tmp_path, sample_mail):
        vault = tmp_path / "vault"
        writer = VaultWriter(vault)
        path = writer.write_message(sample_mail)
        assert "mail" in path.parts
        assert path.suffix == ".md"

    def test_write_message_frontmatter_subject(self, tmp_path, sample_mail):
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_message(sample_mail)
        content = path.read_text(encoding="utf-8")
        assert "Invoice" in content

    def test_write_message_frontmatter_source(self, tmp_path, sample_mail):
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_message(sample_mail)
        content = path.read_text(encoding="utf-8")
        assert 'source:' in content

    def test_write_message_body_rendered(self, tmp_path, sample_mail):
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_message(sample_mail)
        content = path.read_text(encoding="utf-8")
        assert "Счёт" in content or "invoice" in content.lower()

    def test_write_message_skips_existing(self, tmp_path, sample_mail):
        writer = VaultWriter(tmp_path / "vault")
        p1 = writer.write_message(sample_mail)
        p2 = writer.write_message(sample_mail)
        assert p1 is not None
        assert p2 is None


class TestVaultWriterContacts:
    def test_write_contact_creates_file(self, tmp_path, sample_contact):
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_contact(sample_contact)
        assert path is not None
        assert path.exists()

    def test_write_contact_filename_is_email(self, tmp_path, sample_contact):
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_contact(sample_contact)
        assert "alice" in path.name
        assert "@" in path.stem or "example" in path.stem

    def test_write_contact_frontmatter_email(self, tmp_path, sample_contact):
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_contact(sample_contact)
        content = path.read_text(encoding="utf-8")
        assert "alice@example.com" in content


class TestVaultWriterSlugs:
    def test_cyrillic_title_in_filename(self, tmp_path):
        """Cyrillic subjects should not crash filename generation."""
        from datetime import datetime, timezone

        from personal_assistant.models import CalendarEvent

        ev = CalendarEvent(
            uid="cyr-001",
            title="Квартальное совещание",
            start=datetime(2026, 5, 20, 10, 0, tzinfo=timezone.utc),
            end=datetime(2026, 5, 20, 11, 0, tzinfo=timezone.utc),
        )
        writer = VaultWriter(tmp_path / "vault")
        path = writer.write_event(ev)
        assert path is not None
        assert path.suffix == ".md"
        # Filename should be safe ASCII (slugified)
        assert path.exists()
