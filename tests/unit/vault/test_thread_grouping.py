"""
tests/unit/vault/test_thread_grouping.py

Tests for mail thread grouping logic:
  1. VaultWriter._patch_thread_id — updates thread_id in existing frontmatter
  2. VaultWriter.write_message — calls _patch_thread_id when file already exists
  3. Thread ID consistency after ThreadTracker pass
  4. groupByThread semantics replicated in Python (mirrors vault.js logic)
  5. _virtual_thread_from_vault — builds correct Thread from vault docs
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone

import yaml

from personal_assistant.models import MailMessage
from personal_assistant.vault.writer import VaultWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcdt(year=2026, month=5, day=20, hour=10) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _make_msg(
    *,
    message_id: str = "msg1@test",
    subject: str = "Test Subject",
    thread_id: str | None = None,
    sender_email: str = "sender@test.com",
    body: str | None = None,
    date: datetime | None = None,
) -> MailMessage:
    return MailMessage(
        message_id=message_id,
        subject=subject,
        sender_email=sender_email,
        thread_id=thread_id,
        body=body,
        date=date or _utcdt(),
        source="mail",
    )


def _read_fm(path: pathlib.Path) -> dict:
    """Parse YAML frontmatter from a vault .md file."""
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("---"), f"No frontmatter in {path}"
    end = raw.find("\n---", 3)
    assert end != -1, f"Unclosed frontmatter in {path}"
    return yaml.safe_load(raw[3:end]) or {}


# ---------------------------------------------------------------------------
# 1. _patch_thread_id
# ---------------------------------------------------------------------------

class TestPatchThreadId:

    def test_patches_empty_thread_id(self, tmp_path):
        """File with thread_id: '' should be updated with the real ID."""
        writer = VaultWriter(tmp_path)
        msg = _make_msg(thread_id=None)  # will produce thread_id: ""
        path = writer.write_message(msg, overwrite=False)
        assert path is not None

        fm_before = _read_fm(path)
        assert fm_before.get("thread_id") == ""

        # Now patch with a real thread_id
        writer._patch_thread_id(path, "abc123def456")
        fm_after = _read_fm(path)
        assert fm_after.get("thread_id") == "abc123def456"

    def test_no_write_when_same_thread_id(self, tmp_path):
        """If thread_id is already correct, file should NOT be rewritten."""
        writer = VaultWriter(tmp_path)
        msg = _make_msg(thread_id="abc123def456")
        path = writer.write_message(msg)
        assert path is not None

        mtime_before = path.stat().st_mtime_ns
        writer._patch_thread_id(path, "abc123def456")
        mtime_after = path.stat().st_mtime_ns

        assert mtime_before == mtime_after, "File should not be rewritten when thread_id unchanged"

    def test_no_write_when_new_thread_id_empty(self, tmp_path):
        """If new thread_id is empty, nothing should happen."""
        writer = VaultWriter(tmp_path)
        msg = _make_msg(thread_id="abc123")
        path = writer.write_message(msg)
        assert path is not None

        mtime_before = path.stat().st_mtime_ns
        writer._patch_thread_id(path, "")
        writer._patch_thread_id(path, None)
        mtime_after = path.stat().st_mtime_ns

        assert mtime_before == mtime_after

    def test_body_preserved_after_patch(self, tmp_path):
        """The markdown body must be identical before and after patching."""
        writer = VaultWriter(tmp_path)
        msg = _make_msg(thread_id=None, body="Hello, World!")
        path = writer.write_message(msg)

        raw_before = path.read_text(encoding="utf-8")
        body_before = raw_before[raw_before.find("\n---\n", 3) + 5:]

        writer._patch_thread_id(path, "newthreadid1")

        raw_after = path.read_text(encoding="utf-8")
        body_after = raw_after[raw_after.find("\n---\n", 3) + 5:]

        assert body_before == body_after, "Body must be preserved after thread_id patch"

    def test_cyrillic_frontmatter_preserved(self, tmp_path):
        """Cyrillic subject must survive YAML round-trip in patch."""
        writer = VaultWriter(tmp_path)
        msg = _make_msg(subject="Привет мир", thread_id=None)
        path = writer.write_message(msg)
        writer._patch_thread_id(path, "кирилица123")

        fm = _read_fm(path)
        assert fm.get("thread_id") == "кирилица123"
        assert "Привет" in fm.get("title", "")


# ---------------------------------------------------------------------------
# 2. write_message calls _patch_thread_id for existing files
# ---------------------------------------------------------------------------

class TestWriteMessageThreadIdUpdate:

    def test_existing_file_gets_thread_id_patched(self, tmp_path):
        """Second write_message call should update thread_id even when not overwriting."""
        writer = VaultWriter(tmp_path)

        # First sync: thread_id is None (not yet computed)
        msg_v1 = _make_msg(thread_id=None)
        path = writer.write_message(msg_v1, overwrite=False)
        assert path is not None
        assert _read_fm(path).get("thread_id") == ""

        # Second sync: same message, now with computed thread_id
        msg_v2 = _make_msg(thread_id="aaa111bbb222")
        result = writer.write_message(msg_v2, overwrite=False)
        assert result is None, "File should NOT be newly written (already exists)"

        fm = _read_fm(path)
        assert fm.get("thread_id") == "aaa111bbb222", (
            "thread_id must be patched even when overwrite=False"
        )

    def test_second_write_updates_thread_id_but_not_body(self, tmp_path):
        """Patching must not alter the message body text."""
        writer = VaultWriter(tmp_path)
        msg = _make_msg(thread_id=None, body="Original body text")
        path = writer.write_message(msg)

        raw_before = path.read_text(encoding="utf-8")
        body_before = raw_before[raw_before.find("\n---\n", 3) + 5:]

        msg2 = _make_msg(thread_id="updatedthread1")
        writer.write_message(msg2, overwrite=False)

        raw_after = path.read_text(encoding="utf-8")
        # Body section (after second ---) must be unchanged
        body_start = raw_after.find("\n---\n", 3) + 5
        assert raw_after[body_start:] == body_before, \
            "thread_id patch must not touch the body section"
        assert "Original body text" in raw_after[body_start:]


# ---------------------------------------------------------------------------
# 3. Thread-ID consistency across ThreadTracker pass
# ---------------------------------------------------------------------------

class TestThreadTrackerConsistency:

    def test_reply_chain_shares_thread_id(self):
        """Re:/Fwd: variants must produce the same thread_id as the original."""
        from personal_assistant.sync.thread_tracker import ThreadTracker

        msgs = [
            _make_msg(message_id="m1", subject="Project Update"),
            _make_msg(message_id="m2", subject="Re: Project Update"),
            _make_msg(message_id="m3", subject="Fwd: Project Update"),
            _make_msg(message_id="m4", subject="Re: Re: Project Update"),
        ]
        ThreadTracker().group_messages(msgs)
        tids = {m.thread_id for m in msgs}
        assert len(tids) == 1, f"All replies must share one thread_id, got {tids}"

    def test_unrelated_subjects_get_different_thread_ids(self):
        from personal_assistant.sync.thread_tracker import ThreadTracker

        msgs = [
            _make_msg(message_id="a", subject="Alpha topic"),
            _make_msg(message_id="b", subject="Beta topic"),
            _make_msg(message_id="c", subject="Gamma topic"),
        ]
        ThreadTracker().group_messages(msgs)
        tids = [m.thread_id for m in msgs]
        assert len(set(tids)) == 3, "Different subjects must produce different thread_ids"

    def test_thread_tracker_preserves_existing_thread_id(self):
        """If thread_id is already set (e.g. from conversation_id), keep it."""
        from personal_assistant.sync.thread_tracker import ThreadTracker

        msg = _make_msg(thread_id="CONV12345EXISTING")
        ThreadTracker().group_messages([msg])
        assert msg.thread_id == "CONV12345EXISTING"

    def test_cyrillic_subject_grouping(self):
        """Cyrillic reply subjects must be stripped and produce the same hash."""
        from personal_assistant.sync.thread_tracker import ThreadTracker

        msgs = [
            _make_msg(message_id="r1", subject="Отчёт за май"),
            _make_msg(message_id="r2", subject="Отв: Отчёт за май"),
            _make_msg(message_id="r3", subject="Re: Отчёт за май"),
        ]
        ThreadTracker().group_messages(msgs)
        tids = {m.thread_id for m in msgs}
        assert len(tids) == 1, f"Cyrillic reply chain must share one thread_id, got {tids}"


# ---------------------------------------------------------------------------
# 4. Python mirror of JS groupByThread semantics
# ---------------------------------------------------------------------------

def _group_by_thread_py(docs: list[dict]) -> tuple[dict[str, list], list]:
    """Python mirror of vault.js groupByThread() for logic testing."""
    threads: dict[str, list] = {}
    no_thread: list = []
    for doc in docs:
        tid = (doc.get("thread_id") or "").strip()
        if not tid:
            no_thread.append(doc)
        else:
            threads.setdefault(tid, []).append(doc)
    return threads, no_thread


class TestGroupByThreadLogic:

    def test_groups_by_thread_id(self):
        docs = [
            {"thread_id": "tid1", "title": "A"},
            {"thread_id": "tid1", "title": "B"},
            {"thread_id": "tid2", "title": "C"},
        ]
        threads, no_thread = _group_by_thread_py(docs)
        assert len(threads["tid1"]) == 2
        assert len(threads["tid2"]) == 1
        assert no_thread == []

    def test_empty_thread_id_goes_to_no_thread(self):
        docs = [
            {"thread_id": "", "title": "X"},
            {"thread_id": None, "title": "Y"},
        ]
        threads, no_thread = _group_by_thread_py(docs)
        assert threads == {}
        assert len(no_thread) == 2

    def test_single_message_thread_stays_single(self):
        docs = [{"thread_id": "t1", "title": "Only one"}]
        threads, no_thread = _group_by_thread_py(docs)
        assert len(threads["t1"]) == 1
        # JS: renderThreadGroup([doc]) returns renderMailItem(doc) → flat display
        assert len(threads["t1"]) == 1

    def test_mixed_docs_split_correctly(self):
        docs = [
            {"thread_id": "abc", "title": "Reply"},
            {"thread_id": "", "title": "Standalone"},
            {"thread_id": "abc", "title": "Original"},
        ]
        threads, no_thread = _group_by_thread_py(docs)
        assert len(threads["abc"]) == 2
        assert len(no_thread) == 1


# ---------------------------------------------------------------------------
# 5. VaultWriter + ThreadTracker end-to-end: write → resync patches thread_id
# ---------------------------------------------------------------------------

class TestEndToEndThreadPatch:

    def test_resync_patches_empty_thread_id_in_vault_file(self, tmp_path):
        """
        Simulate scenario: first sync writes empty thread_id (e.g. reader bug),
        second sync with ThreadTracker provides the correct thread_id,
        write_message() should patch the existing file.
        """
        from personal_assistant.sync.thread_tracker import ThreadTracker

        writer = VaultWriter(tmp_path)

        # Sync 1: reader accidentally produces no thread_id
        m = _make_msg(message_id="e2e-001", subject="Weekly Report", thread_id=None)
        path = writer.write_message(m)
        assert path is not None
        assert _read_fm(path)["thread_id"] == ""

        # Sync 2: ThreadTracker computes the correct thread_id
        m2 = _make_msg(message_id="e2e-001", subject="Weekly Report", thread_id=None)
        ThreadTracker().group_messages([m2])
        assert m2.thread_id  # must be non-empty after ThreadTracker

        result = writer.write_message(m2, overwrite=False)
        assert result is None  # existing file, not newly written

        fm = _read_fm(path)
        assert fm["thread_id"] == m2.thread_id, (
            f"File must have thread_id={m2.thread_id!r} after resync, got {fm['thread_id']!r}"
        )

    def test_thread_grouped_after_patch(self, tmp_path):
        """
        After patching, two messages with the same thread_id should be
        grouped together by groupByThread logic.
        """
        from personal_assistant.sync.thread_tracker import ThreadTracker

        writer = VaultWriter(tmp_path)

        msgs = [
            _make_msg(message_id="g1", subject="Project Alpha", thread_id=None, date=_utcdt(hour=9)),
            _make_msg(message_id="g2", subject="Re: Project Alpha", thread_id=None, date=_utcdt(hour=10)),
        ]

        # First sync (no thread_ids)
        for m in msgs:
            writer.write_message(m)

        # Read back from vault — simulate what VaultIndex returns
        mail_dir = tmp_path / "mail"
        vault_docs = []
        for md_path in mail_dir.rglob("*.md"):
            fm = _read_fm(md_path)
            vault_docs.append({"thread_id": fm.get("thread_id", ""), "path": str(md_path)})

        # Before resync: all thread_ids should be empty
        assert all(d["thread_id"] == "" for d in vault_docs)
        threads, no_thread = _group_by_thread_py(vault_docs)
        assert len(no_thread) == 2  # both in noThread → shows flat

        # Second sync with ThreadTracker
        ThreadTracker().group_messages(msgs)
        for m in msgs:
            writer.write_message(m, overwrite=False)

        # Read back again
        vault_docs2 = []
        for md_path in mail_dir.rglob("*.md"):
            fm = _read_fm(md_path)
            vault_docs2.append({"thread_id": fm.get("thread_id", ""), "path": str(md_path)})

        threads2, no_thread2 = _group_by_thread_py(vault_docs2)
        assert len(no_thread2) == 0, "After resync, no emails should be in noThread"
        assert len(threads2) == 1, "Both emails must be in the same thread group"
        assert list(threads2.values())[0].__len__() == 2
