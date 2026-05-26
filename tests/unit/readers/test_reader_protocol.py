"""
Tests for DataSourceReader Protocol compatibility.

Verifies that readers satisfy the DataSourceReader Protocol
without requiring a real Calendar / Mail installation.
"""

from __future__ import annotations

from personal_assistant.readers import DataSourceReader


class TestDataSourceReaderProtocol:
    def test_mock_reader_satisfies_protocol(self):
        """A minimal mock object should satisfy the Protocol."""
        from personal_assistant.models import CalendarEvent, MailMessage

        class MockReader:
            def fetch_messages(self, days_back: int = 30) -> list[MailMessage]:
                return []

            def fetch_events(
                self, days_back: int = 30, days_forward: int = 90
            ) -> list[CalendarEvent]:
                return []

        assert isinstance(MockReader(), DataSourceReader)

    def test_incomplete_reader_does_not_satisfy_protocol(self):
        """An object missing fetch_events should not satisfy Protocol."""

        class IncompleteReader:
            def fetch_messages(self, days_back: int = 30):
                return []

        # Protocol check at runtime
        assert not isinstance(IncompleteReader(), DataSourceReader)
