"""
Unit tests for draft_context_service.py and context_builder thread-context injection.

Coverage
--------
D1  build_draft_context — empty vault returns minimal valid dict
D2  build_draft_context — all required keys present
D3  build_draft_context — context_prompt is non-empty string
D4  build_draft_context — thread_messages is a list of dicts
D5  build_draft_context — my_previous_replies identified by my_email
D6  build_draft_context — is_mine=False when my_email does not match
D7  build_draft_context — vault scan finds thread messages by thread_id
D8  build_draft_context — thread_messages sorted chronologically
D9  build_draft_context — key_facts extracted from deadline keyword
D10 build_draft_context — message_count matches len(thread_messages)
D11 build_draft_context — body is truncated at _MSG_BODY_LIMIT
D12 build_draft_context — context_prompt contains subject
D13 build_draft_context — context_prompt contains thread_summary
D14 build_draft_context — draft_hint non-empty when thread has messages
D15 build_draft_context — graceful with non-existent vault_path
D16 _rule_thread_summary — returns string mentioning subject
D17 _rule_thread_summary — counts incoming vs outgoing
D18 _extract_key_facts — finds срок keyword
D19 _extract_key_facts — limits to _KEY_FACTS_LIMIT items
D20 _extract_email — parses Name <email> format
D21 _email_matches — case-insensitive comparison
D22 _build_draft_hint — returns empty-ish string with no messages

CB1  context_builder.build — accepts vault_thread_id without error
CB2  context_builder.build — does not raise when vault_thread_id is None
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_vault(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a temp vault with given {relative_path: content} files."""
    mail_dir = tmp_path / "mail"
    mail_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def md(fm: str, body: str = "") -> str:
    """Build a .md string with frontmatter."""
    return f"---\n{fm.strip()}\n---\n{body.strip()}"


# Shared vault fixture with two messages in the same thread
THREAD_ID = "thread_abc123"
MY_EMAIL = "igor@example.com"
OTHER_EMAIL = "boss@corp.ru"

MSG_INCOMING = md(
    f"""
id: msg_001
thread_id: {THREAD_ID}
subject: Отчёт за май
from: {OTHER_EMAIL}
sender: Иванов <{OTHER_EMAIL}>
date: "2026-05-20T09:00:00"
type: email
""",
    "Добрый день, прошу прислать отчёт. Срок: 25 мая. Дедлайн: 25.05.2026.",
)

MSG_MY_REPLY = md(
    f"""
id: msg_002
thread_id: {THREAD_ID}
subject: "Re: Отчёт за май"
from: {MY_EMAIL}
sender: Игорь <{MY_EMAIL}>
date: "2026-05-21T14:00:00"
type: email
""",
    "Добрый день, отчёт подготовлю к указанному сроку.",
)

MSG_INCOMING_2 = md(
    f"""
id: msg_003
thread_id: {THREAD_ID}
subject: "Re: Re: Отчёт за май"
from: {OTHER_EMAIL}
sender: Иванов <{OTHER_EMAIL}>
date: "2026-05-22T10:00:00"
type: email
""",
    "Отлично, жду отчёт.",
)

UNRELATED_MSG = md(
    """
id: unrelt_001
thread_id: different_thread
subject: Другое письмо
from: other@domain.com
date: "2026-05-19T08:00:00"
type: email
""",
    "Это другое письмо, не связанное с треком.",
)


@pytest.fixture
def vault_with_thread(tmp_path):
    return make_vault(tmp_path, {
        "mail/msg_001.md": MSG_INCOMING,
        "mail/msg_002.md": MSG_MY_REPLY,
        "mail/msg_003.md": MSG_INCOMING_2,
        "mail/unrelt_001.md": UNRELATED_MSG,
    })


# ---------------------------------------------------------------------------
# D1–D15: build_draft_context
# ---------------------------------------------------------------------------

class TestBuildDraftContext:

    def test_d1_empty_vault_returns_valid_dict(self, tmp_path):
        """D1: Empty vault → returns valid dict with all required keys."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("unknown_item", vault_path=tmp_path, my_email=MY_EMAIL)
        assert isinstance(result, dict)

    def test_d2_all_keys_present(self, tmp_path):
        """D2: All required output keys present."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("x", vault_path=tmp_path)
        required = {
            "item_id", "subject", "sender", "sender_email", "thread_id",
            "thread_messages", "thread_summary", "key_facts",
            "my_previous_replies", "draft_hint", "context_prompt", "message_count",
        }
        missing = required - set(result.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_d3_context_prompt_nonempty(self, tmp_path):
        """D3: context_prompt is a non-empty string."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("x", vault_path=tmp_path)
        assert isinstance(result["context_prompt"], str)
        assert len(result["context_prompt"]) > 0

    def test_d4_thread_messages_is_list(self, tmp_path):
        """D4: thread_messages is a list of dicts."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("x", vault_path=tmp_path)
        assert isinstance(result["thread_messages"], list)

    def test_d5_my_previous_replies_identified(self, vault_with_thread):
        """D5: my_previous_replies contains the outgoing message."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context(
            "msg_001",
            vault_path=vault_with_thread,
            my_email=MY_EMAIL,
        )
        # Should find msg_002 as my reply
        my_bodies = [r["body"] for r in result["my_previous_replies"]]
        assert any("отчёт подготовлю" in b.lower() for b in my_bodies), \
            f"My reply body not found: {my_bodies}"

    def test_d6_is_mine_false_for_other_sender(self, vault_with_thread):
        """D6: is_mine=False when sender email doesn't match my_email."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context(
            "msg_001",
            vault_path=vault_with_thread,
            my_email=MY_EMAIL,
        )
        incoming = [m for m in result["thread_messages"] if not m["is_mine"]]
        assert len(incoming) >= 1, "Expected at least one incoming message"
        for m in incoming:
            assert m["is_mine"] is False

    def test_d7_vault_scan_finds_thread_messages(self, vault_with_thread):
        """D7: Vault scan finds all messages with matching thread_id."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context(
            "msg_001",
            vault_path=vault_with_thread,
            my_email=MY_EMAIL,
        )
        # Should find 3 messages from this thread, not the unrelated one
        assert result["message_count"] == 3, \
            f"Expected 3 thread messages, got {result['message_count']}"

    def test_d8_messages_sorted_chronologically(self, vault_with_thread):
        """D8: thread_messages sorted by date ascending."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("msg_001", vault_path=vault_with_thread)
        dates = [m["date"] for m in result["thread_messages"] if m["date"]]
        assert dates == sorted(dates), f"Messages not sorted: {dates}"

    def test_d9_key_facts_from_deadline(self, vault_with_thread):
        """D9: key_facts extracts срок / дедлайн from message body."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("msg_001", vault_path=vault_with_thread)
        assert len(result["key_facts"]) >= 1, "Expected at least one key fact"
        combined = " ".join(result["key_facts"]).lower()
        assert any(kw in combined for kw in ("срок", "дедлайн", "25")), \
            f"No deadline fact found: {result['key_facts']}"

    def test_d10_message_count_matches_list(self, vault_with_thread):
        """D10: message_count == len(thread_messages)."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("msg_001", vault_path=vault_with_thread)
        assert result["message_count"] == len(result["thread_messages"])

    def test_d11_body_truncated(self, tmp_path):
        """D11: Long message bodies are truncated to _MSG_BODY_LIMIT."""
        from personal_assistant.services.draft_context_service import (
            _MSG_BODY_LIMIT,
            build_draft_context,
        )
        long_body = "A" * (_MSG_BODY_LIMIT * 3)
        vault = make_vault(tmp_path, {
            "mail/long_msg.md": md(
                "id: long_msg\nthread_id: t1\nfrom: x@x.com\ndate: 2026-01-01\ntype: email\n",
                long_body,
            )
        })
        result = build_draft_context("long_msg", vault_path=vault)
        for msg in result["thread_messages"]:
            assert len(msg["body"]) <= _MSG_BODY_LIMIT + 30, \
                f"Body not truncated: {len(msg['body'])} chars"

    def test_d12_context_prompt_contains_subject(self, vault_with_thread):
        """D12: context_prompt contains the subject string."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("msg_001", vault_path=vault_with_thread)
        assert "Отчёт за май" in result["context_prompt"] or \
               "отчёт за май" in result["context_prompt"].lower()

    def test_d13_context_prompt_contains_summary(self, vault_with_thread):
        """D13: context_prompt contains thread_summary text."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("msg_001", vault_path=vault_with_thread)
        summary = result["thread_summary"]
        assert summary in result["context_prompt"], \
            "thread_summary not embedded in context_prompt"

    def test_d14_draft_hint_nonempty_with_thread(self, vault_with_thread):
        """D14: draft_hint is non-empty when thread has messages."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context("msg_001", vault_path=vault_with_thread)
        assert result["draft_hint"], "draft_hint should not be empty when thread exists"

    def test_d15_graceful_nonexistent_vault(self):
        """D15: Non-existent vault_path returns valid minimal result."""
        from personal_assistant.services.draft_context_service import build_draft_context
        result = build_draft_context(
            "ghost_item",
            vault_path=Path("/tmp/__nonexistent_vault_xyz__"),
        )
        assert isinstance(result, dict)
        assert result["item_id"] == "ghost_item"
        assert isinstance(result["thread_messages"], list)
        assert isinstance(result["context_prompt"], str)


# ---------------------------------------------------------------------------
# D16–D22: helper functions
# ---------------------------------------------------------------------------

class TestDraftContextHelpers:

    def test_d16_rule_summary_mentions_subject(self):
        """D16: _rule_thread_summary returns string mentioning subject."""
        from personal_assistant.services.draft_context_service import _rule_thread_summary
        msgs = [{"sender_raw": "Иванов", "date": "2026-05-01", "is_mine": False, "body": "..."}]
        result = _rule_thread_summary(msgs, "Важный проект")
        assert "Важный проект" in result

    def test_d17_rule_summary_counts_in_out(self):
        """D17: _rule_thread_summary counts incoming vs outgoing."""
        from personal_assistant.services.draft_context_service import _rule_thread_summary
        msgs = [
            {"sender_raw": "X", "date": "2026-05-01", "is_mine": False, "body": ""},
            {"sender_raw": "Me", "date": "2026-05-02", "is_mine": True,  "body": ""},
            {"sender_raw": "X", "date": "2026-05-03", "is_mine": False, "body": ""},
        ]
        result = _rule_thread_summary(msgs, "Тема")
        assert "2 входящих" in result
        assert "1 исходящих" in result

    def test_d18_extract_key_facts_срок(self):
        """D18: _extract_key_facts finds срок keyword."""
        from personal_assistant.services.draft_context_service import _extract_key_facts
        docs = [{"body": "Прошу отправить до срок: 30 мая."}]
        facts = _extract_key_facts(docs)
        assert any("срок" in f.lower() or "30" in f for f in facts), \
            f"No срок fact found: {facts}"

    def test_d19_key_facts_limit(self):
        """D19: _extract_key_facts returns at most _KEY_FACTS_LIMIT items."""
        from personal_assistant.services.draft_context_service import (
            _KEY_FACTS_LIMIT,
            _extract_key_facts,
        )
        # Body with many matches
        body = " ".join([
            "Дедлайн: 1 мая. Срок: 2 мая. Сумма: 100 руб. "
            "Встреча: завтра. Созвон: в пятницу. "
            "Прошу оплатить. Необходимо завершить. Прошу подтвердить. "
        ] * 4)
        docs = [{"body": body}]
        facts = _extract_key_facts(docs)
        assert len(facts) <= _KEY_FACTS_LIMIT, \
            f"Too many facts: {len(facts)} > {_KEY_FACTS_LIMIT}"

    def test_d20_extract_email_angle_brackets(self):
        """D20: _extract_email parses 'Name <email>' format."""
        from personal_assistant.services.draft_context_service import _extract_email
        assert _extract_email("Иванов <ivan@corp.ru>") == "ivan@corp.ru"
        assert _extract_email("boss@example.com") == "boss@example.com"
        assert _extract_email("no email here") == ""

    def test_d21_email_matches_case_insensitive(self):
        """D21: _email_matches is case-insensitive."""
        from personal_assistant.services.draft_context_service import _email_matches
        assert _email_matches("Igor <IGOR@EXAMPLE.COM>", "igor@example.com") is True
        assert _email_matches("other@example.com", "igor@example.com") is False
        assert _email_matches("anything", "") is False

    def test_d22_draft_hint_empty_no_messages(self):
        """D22: _build_draft_hint returns fallback string with no messages."""
        from personal_assistant.services.draft_context_service import _build_draft_hint
        hint = _build_draft_hint([], [], [])
        assert isinstance(hint, str)


# ---------------------------------------------------------------------------
# CB1–CB2: context_builder integration
# ---------------------------------------------------------------------------

class TestContextBuilderThreadIntegration:

    def test_cb1_build_accepts_vault_thread_id(self):
        """CB1: context_builder.build() accepts vault_thread_id without error."""
        from personal_assistant.mlx_server.context_builder import ContextAssembler
        asm = ContextAssembler()
        result = asm.build(
            user_message="Составь черновик ответа",
            history=[],
            context_paths=[],
            mode="draft",
            vault_thread_id="thread_test_123",
        )
        assert "system_prompt" in result
        assert "messages" in result
        assert isinstance(result["system_prompt"], str)

    def test_cb2_build_no_vault_thread_id(self):
        """CB2: context_builder.build() works fine when vault_thread_id is None."""
        from personal_assistant.mlx_server.context_builder import ContextAssembler
        asm = ContextAssembler()
        result = asm.build(
            user_message="Обычный запрос",
            history=[],
            context_paths=[],
            mode="chat",
            vault_thread_id=None,
        )
        assert isinstance(result["system_prompt"], str)
        # No thread block injected
        assert "ИСТОРИЯ ТРЕДА" not in result["system_prompt"]

    def test_cb3_build_with_empty_thread_id_string(self):
        """CB3: Empty string vault_thread_id treated as None (no block injected)."""
        from personal_assistant.mlx_server.context_builder import ContextAssembler
        asm = ContextAssembler()
        result = asm.build(
            user_message="Тест",
            history=[],
            context_paths=[],
            vault_thread_id="",
        )
        assert "ИСТОРИЯ ТРЕДА" not in result["system_prompt"]

    def test_cb4_make_system_prompt_accepts_thread_block(self):
        """CB4: _make_system_prompt injects thread_context_block when provided."""
        from personal_assistant.mlx_server.context_builder import ContextAssembler
        asm = ContextAssembler()
        block = "--- ИСТОРИЯ ТРЕДА ---\nТест\n--- /ИСТОРИЯ ТРЕДА ---"
        prompt = asm._make_system_prompt([], "draft", thread_context_block=block)
        assert "ИСТОРИЯ ТРЕДА" in prompt

    def test_cb5_thread_block_none_skipped(self):
        """CB5: thread_context_block=None → no thread block in system prompt."""
        from personal_assistant.mlx_server.context_builder import ContextAssembler
        asm = ContextAssembler()
        prompt = asm._make_system_prompt([], "draft", thread_context_block=None)
        assert "ИСТОРИЯ ТРЕДА" not in prompt
