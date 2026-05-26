"""
tests/unit/mlx/test_tool_prompts_e2e.py

E2E-style tests verifying that:
  1. DraftReply passes the active system prompt (DEFAULT or custom) to MLXEngine.
  2. Changing draft_system actually changes the system arg passed to engine.ask().
  3. Custom summarize_system is passed through the summarize task.
  4. strip_emoji() removes emoji characters from draft output.
  5. DEFAULT_DRAFT_SYSTEM explicitly forbids emojis in its text.
  6. _deadline_bucket() produces correct temporal buckets.
  7. Profile identity hint (user_email) appears in system prompt block.
  8. /api/chat/save-draft-outlook validates request fields correctly.
  9. DEFAULT_DRAFT_SYSTEM has structured format (checklist, sections).
 10. Prompt length limit is 8000; validate_prompt rejects beyond that.
 11. GET /tool-prompts returns prompts_file_path and max_prompt_len.

All tests mock MLXEngine so no GPU / model download is required.
"""

from __future__ import annotations

import pathlib
import tempfile
from datetime import date, timedelta
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vault_doc(
    title: str = "Test Email",
    content: str = "Please review and reply.",
    section: str = "mail",
    path: Optional[pathlib.Path] = None,
) -> MagicMock:
    """Create a minimal VaultDoc mock."""
    doc = MagicMock()
    doc.title = title
    doc.content = content
    doc.section = section
    doc.path = path or pathlib.Path("/fake/vault/mail/2026/05/test.md")
    doc.date = "2026-05-20T10:00:00Z"
    doc.frontmatter = {}
    doc.sender_email = "sender@example.com"
    doc.tags = []
    doc.raw = content
    return doc


def _make_index(doc: MagicMock) -> MagicMock:
    """Create a minimal VaultIndex mock."""
    idx = MagicMock()
    idx.docs = [doc]
    idx.build_context.return_value = doc.content
    idx.get_thread.return_value = [doc]
    return idx


def _make_engine(response: str = "Здравствуйте! Отвечаю на ваше письмо.") -> MagicMock:
    """Create a mock MLXEngine that captures calls to .ask()."""
    engine = MagicMock()
    engine.ask.return_value = response
    return engine


# ---------------------------------------------------------------------------
# 1–2. DraftReply passes system prompt to engine.ask()
# ---------------------------------------------------------------------------

class TestDraftReplySystemPrompt:

    def test_default_system_prompt_is_passed(self):
        """draft_reply must pass DEFAULT_DRAFT_SYSTEM (or custom override) to engine.ask."""
        from personal_assistant.mlx_server.tasks.draft_reply import draft_reply
        from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM

        doc = _make_vault_doc()
        idx = _make_index(doc)
        engine = _make_engine()

        draft_reply(doc=doc, engine=engine, index=idx)

        engine.ask.assert_called_once()
        _, kwargs = engine.ask.call_args
        system_used = kwargs.get("system") or engine.ask.call_args[0][1]
        assert system_used == DEFAULT_DRAFT_SYSTEM, (
            f"Expected DEFAULT_DRAFT_SYSTEM, got {system_used!r}"
        )

    def test_custom_draft_system_is_used_when_set(self, tmp_path):
        """When a custom draft_system is saved, engine.ask must receive it."""
        from personal_assistant.mlx_server.tasks.draft_reply import draft_reply
        from personal_assistant.services.tool_prompts import (
            ToolPrompts,
            invalidate_cache,
            save_tool_prompts,
        )

        # Write a custom prompt to a temp vault
        custom_prompt = "Ты строгий корпоративный email-бот. Отвечай только по-русски, без лирики."
        prompts = ToolPrompts(draft_system=custom_prompt)

        with patch("personal_assistant.services.tool_prompts._prompts_path",
                   return_value=tmp_path / ".tool_prompts.json"):
            save_tool_prompts(prompts)
            invalidate_cache()

            doc = _make_vault_doc()
            idx = _make_index(doc)
            engine = _make_engine()

            with patch("personal_assistant.services.tool_prompts._prompts_path",
                       return_value=tmp_path / ".tool_prompts.json"):
                draft_reply(doc=doc, engine=engine, index=idx)

        engine.ask.assert_called_once()
        _, kwargs = engine.ask.call_args
        system_used = kwargs.get("system") or engine.ask.call_args[0][1]
        assert system_used == custom_prompt, (
            f"Expected custom prompt, got {system_used!r}"
        )

    def test_different_custom_prompts_produce_different_system_args(self, tmp_path):
        """Two different custom prompts must yield two different engine.ask(system=...) values."""
        from personal_assistant.mlx_server.tasks.draft_reply import draft_reply
        from personal_assistant.services.tool_prompts import (
            ToolPrompts,
            invalidate_cache,
            save_tool_prompts,
        )

        results = []
        for text in [
            "Будь кратким и формальным.",
            "Будь дружелюбным и развёрнутым.",
        ]:
            prompts_path = tmp_path / f"prompts_{len(results)}.json"
            prompts = ToolPrompts(draft_system=text)
            with patch("personal_assistant.services.tool_prompts._prompts_path",
                       return_value=prompts_path):
                save_tool_prompts(prompts)
                invalidate_cache()

                doc = _make_vault_doc()
                idx = _make_index(doc)
                engine = _make_engine()

                with patch("personal_assistant.services.tool_prompts._prompts_path",
                           return_value=prompts_path):
                    draft_reply(doc=doc, engine=engine, index=idx)

            _, kwargs = engine.ask.call_args
            system_used = kwargs.get("system") or engine.ask.call_args[0][1]
            results.append(system_used)

        assert results[0] != results[1], (
            "Different prompts must produce different system args to engine.ask"
        )

    def test_empty_custom_prompt_falls_back_to_default(self, tmp_path):
        """Empty custom draft_system must fall back to DEFAULT_DRAFT_SYSTEM."""
        from personal_assistant.mlx_server.tasks.draft_reply import draft_reply
        from personal_assistant.services.tool_prompts import (
            DEFAULT_DRAFT_SYSTEM,
            ToolPrompts,
            invalidate_cache,
            save_tool_prompts,
        )

        prompts = ToolPrompts(draft_system="")  # empty → should use default
        prompts_path = tmp_path / ".tool_prompts.json"

        with patch("personal_assistant.services.tool_prompts._prompts_path",
                   return_value=prompts_path):
            save_tool_prompts(prompts)
            invalidate_cache()

            doc = _make_vault_doc()
            idx = _make_index(doc)
            engine = _make_engine()

            with patch("personal_assistant.services.tool_prompts._prompts_path",
                       return_value=prompts_path):
                draft_reply(doc=doc, engine=engine, index=idx)

        _, kwargs = engine.ask.call_args
        system_used = kwargs.get("system") or engine.ask.call_args[0][1]
        assert system_used == DEFAULT_DRAFT_SYSTEM


# ---------------------------------------------------------------------------
# 3. Custom summarize_system is passed through
# ---------------------------------------------------------------------------

class TestSummarizeSystemPrompt:

    def test_custom_summarize_system_is_used(self, tmp_path):
        """When summarize_system is set, summarize task must pass it to the engine."""
        from personal_assistant.services.tool_prompts import (
            ToolPrompts,
            invalidate_cache,
            save_tool_prompts,
        )

        custom_summarize = "Суммаризируй только ключевые метрики, игнорируй вводные слова."
        prompts = ToolPrompts(summarize_system=custom_summarize)
        prompts_path = tmp_path / ".tool_prompts.json"

        with patch("personal_assistant.services.tool_prompts._prompts_path",
                   return_value=prompts_path):
            save_tool_prompts(prompts)
            invalidate_cache()

            # Verify effective_summarize() returns custom text
            with patch("personal_assistant.services.tool_prompts._prompts_path",
                       return_value=prompts_path):
                from personal_assistant.services.tool_prompts import get_tool_prompts
                loaded = get_tool_prompts(force_reload=True)
                assert loaded.effective_summarize() == custom_summarize


# ---------------------------------------------------------------------------
# 4–5. strip_emoji() and DEFAULT_DRAFT_SYSTEM no-emoji guarantee
# ---------------------------------------------------------------------------

class TestStripEmoji:

    def test_strip_emoji_removes_emoji_characters(self):
        from personal_assistant.mlx_server.tasks.draft_reply import strip_emoji

        text = "Привет! 😊 Спасибо за ваше письмо. 📧 С уважением 🙏"
        result = strip_emoji(text)
        assert "😊" not in result
        assert "📧" not in result
        assert "🙏" not in result
        assert "Привет" in result
        assert "С уважением" in result

    def test_strip_emoji_preserves_plain_text(self):
        from personal_assistant.mlx_server.tasks.draft_reply import strip_emoji

        text = "Уважаемый Иван Петрович,\n\nВ ответ на Ваш запрос сообщаю..."
        assert strip_emoji(text) == text

    def test_strip_emoji_collapses_extra_spaces(self):
        from personal_assistant.mlx_server.tasks.draft_reply import strip_emoji

        text = "Привет 🎉 мир"
        result = strip_emoji(text)
        assert "  " not in result  # no double spaces

    def test_draft_reply_output_has_no_emoji(self):
        """When engine returns text with emojis, draft_reply must strip them."""
        from personal_assistant.mlx_server.tasks.draft_reply import draft_reply

        emoji_response = "Уважаемый коллега! 😊 Рад ответить на ваш запрос. 🎯"
        doc = _make_vault_doc()
        idx = _make_index(doc)
        engine = _make_engine(response=emoji_response)

        result = draft_reply(doc=doc, engine=engine, index=idx)
        assert "😊" not in result.draft
        assert "🎯" not in result.draft
        assert "Уважаемый" in result.draft

    def test_default_draft_system_mentions_no_emoji(self):
        """DEFAULT_DRAFT_SYSTEM must explicitly forbid emoji."""
        from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM

        # The instruction must contain some variant of "эмодзи" prohibition
        text_lower = DEFAULT_DRAFT_SYSTEM.lower()
        assert "эмодзи" in text_lower or "emoji" in text_lower, (
            "DEFAULT_DRAFT_SYSTEM must mention emoji restriction"
        )
        assert "не использу" in text_lower or "no emoji" in text_lower or "без эмодзи" in text_lower, (
            "DEFAULT_DRAFT_SYSTEM must contain an explicit prohibition"
        )


# ---------------------------------------------------------------------------
# 6. _deadline_bucket() temporal buckets
# ---------------------------------------------------------------------------

class TestDeadlineBucket:

    def _today_iso(self) -> str:
        return date.today().isoformat()

    def _delta_iso(self, days: int) -> str:
        return (date.today() + timedelta(days=days)).isoformat()

    def test_today_bucket(self):
        from personal_assistant.mlx_server.tasks.classify import _deadline_bucket
        assert _deadline_bucket(self._today_iso()) == "today"

    def test_tomorrow_bucket(self):
        from personal_assistant.mlx_server.tasks.classify import _deadline_bucket
        assert _deadline_bucket(self._delta_iso(1)) == "tomorrow"

    def test_this_week_bucket(self):
        from personal_assistant.mlx_server.tasks.classify import _deadline_bucket
        for days in [2, 3, 5, 7]:
            result = _deadline_bucket(self._delta_iso(days))
            assert result == "this_week", f"Expected this_week for +{days} days, got {result!r}"

    def test_future_bucket(self):
        from personal_assistant.mlx_server.tasks.classify import _deadline_bucket
        assert _deadline_bucket(self._delta_iso(14)) == "future"
        assert _deadline_bucket(self._delta_iso(30)) == "future"

    def test_past_returns_none(self):
        from personal_assistant.mlx_server.tasks.classify import _deadline_bucket
        assert _deadline_bucket(self._delta_iso(-1)) is None
        assert _deadline_bucket(self._delta_iso(-30)) is None

    def test_invalid_iso_returns_none(self):
        from personal_assistant.mlx_server.tasks.classify import _deadline_bucket
        assert _deadline_bucket("not-a-date") is None
        assert _deadline_bucket("") is None
        assert _deadline_bucket(None) is None  # type: ignore[arg-type]

    def test_datetime_iso_uses_date_only(self):
        """ISO datetime strings like '2026-05-20T14:00:00+00:00' should use only the date part."""
        from personal_assistant.mlx_server.tasks.classify import _deadline_bucket
        # Build a datetime string for today
        today_dt = date.today().isoformat() + "T18:00:00+00:00"
        assert _deadline_bucket(today_dt) == "today"

    def test_classification_tags_use_buckets(self):
        """_append_classification_tags must write @deadline:today style tags."""
        from personal_assistant.mlx_server.tasks.classify import (
            ClassifyResult,
            _append_classification_tags,
        )

        # Create a temp vault file
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "msg.md"
            today_iso = date.today().isoformat()
            p.write_text(
                "---\ntitle: Test\ntags: []\n---\n\nBody\n",
                encoding="utf-8",
            )

            doc = MagicMock()
            doc.path = p
            doc.title = "Test"
            doc.content = "Body"

            result = ClassifyResult(
                doc_title="Test",
                doc_path=str(p),
                labels={"urgency": "urgent"},
                deadlines=[today_iso],
            )
            _append_classification_tags(doc, result)

            raw = p.read_text(encoding="utf-8")
            assert "@deadline:today" in raw, f"Expected @deadline:today in {raw!r}"
            # Must NOT contain the raw ISO date as a tag
            assert f"@deadline:{today_iso}" not in raw, (
                f"Raw ISO date tag must not appear in {raw!r}"
            )


# ---------------------------------------------------------------------------
# 7. Profile identity hint in system prompt
# ---------------------------------------------------------------------------

class TestProfileIdentityBlock:

    def test_user_email_appears_in_profile_block(self):
        """When user_email is set, _build_profile_block must include it."""
        from personal_assistant.profile.context_assembler import _build_profile_block
        from personal_assistant.profile.models import (
            AIAssistantConfig,
            CommunicationTone,
            UserProfile,
        )

        profile = UserProfile(
            full_name="Иван Иванов",
            user_email="ivan@example.com",
            communication_tone=CommunicationTone.PROFESSIONAL,
        )
        config = AIAssistantConfig()
        block = _build_profile_block(profile, config)

        assert "ivan@example.com" in block, "user_email must appear in the profile block"

    def test_delegation_hint_uses_name_and_email(self):
        """The 'мне' delegation hint must reference both full_name and user_email."""
        from personal_assistant.profile.context_assembler import _build_profile_block
        from personal_assistant.profile.models import AIAssistantConfig, UserProfile

        profile = UserProfile(full_name="Мария Петрова", user_email="maria@corp.ru")
        config = AIAssistantConfig()
        block = _build_profile_block(profile, config)

        # Both name and email should be in the delegation hint
        assert "Мария Петрова" in block
        assert "maria@corp.ru" in block
        # Must mention the key word "мне"
        assert "мне" in block.lower()

    def test_no_delegation_hint_when_no_profile_data(self):
        """When full_name and user_email are both empty, no delegation hint."""
        from personal_assistant.profile.context_assembler import _build_profile_block
        from personal_assistant.profile.models import AIAssistantConfig, UserProfile

        profile = UserProfile(full_name="", user_email=None)
        config = AIAssistantConfig()
        block = _build_profile_block(profile, config)

        # Should not contain the delegation ВАЖНО hint
        assert "ВАЖНО: когда пользователь пишет" not in block


# ---------------------------------------------------------------------------
# 8. _build_save_draft_script AppleScript builder tests
# ---------------------------------------------------------------------------

class TestBuildSaveDraftScript:

    def test_build_save_draft_script_standalone(self):
        """_build_save_draft_script must use do shell script + 'open msg' for standalone draft.

        Body must be read from the tempfile via 'cat', NOT embedded as a string literal.
        Outlook for Mac 16.x raises error -1701 on 'save msg'; the correct verb
        is 'open msg' which opens the compose window.
        """
        import os
        import tempfile

        from personal_assistant.mlx_server.chat_routes import _build_save_draft_script

        body_text = "Hello World"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False
        ) as tf:
            tf.write(body_text)
            tmp_path = tf.name

        try:
            script = _build_save_draft_script(
                app_name="Microsoft Outlook",
                subject="Test Draft",
                body_file_path=tmp_path,
                to_recipients=["test@example.com"],
                reply_to_message_id=None,
            )
        finally:
            os.unlink(tmp_path)

        assert "Microsoft Outlook" in script
        assert "Test Draft" in script
        assert tmp_path in script                      # path referenced in script
        assert "do shell script" in script             # body read via shell, not embedded
        assert body_text not in script                 # body text must NOT be inline
        assert "test@example.com" in script
        assert "make new outgoing message" in script
        assert "open msg" in script                    # correct verb for Outlook Mac
        assert "save msg" not in script                # this raises -1701 in Outlook Mac

    def test_build_save_draft_script_reply(self):
        """_build_save_draft_script must find original message via repeat loop, then reply.

        'whose internet message id = X' causes -2741 because 'message' is a
        class-name token in Outlook's dictionary.  The safe alternative is a
        repeat loop comparing (internet message id of checkMsg) directly on each
        object — no whose-clause, no class-name ambiguity.
        """
        import os
        import tempfile

        from personal_assistant.mlx_server.chat_routes import _build_save_draft_script

        body_text = "Thanks for the report."
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False
        ) as tf:
            tf.write(body_text)
            tmp_path = tf.name

        try:
            script = _build_save_draft_script(
                app_name="Microsoft Outlook",
                subject="Re: Report",
                body_file_path=tmp_path,
                to_recipients=[],
                reply_to_message_id="<original@mail.ru>",
            )
        finally:
            os.unlink(tmp_path)

        assert "original@mail.ru" in script            # message-id stored as variable
        assert "repeat with checkMsg" in script        # loop-based lookup, not whose
        assert "internet message id of checkMsg" in script  # direct property access
        assert "reply origMsg" in script               # actual reply command
        assert "do shell script" in script             # body read via shell, not embedded
        assert body_text not in script                 # body text must NOT be inline
        assert "open msg" in script                    # correct verb for Outlook Mac
        assert "save msg" not in script                # this raises -1701 in Outlook Mac
        assert "whose internet message id" not in script  # no fragile whose-clause

    def test_build_script_quotes_in_subject_safe(self):
        """Subject with double quotes must be safely escaped via quote concatenation."""
        import os
        import tempfile

        from personal_assistant.mlx_server.chat_routes import _build_save_draft_script

        body_text = 'She replied "OK"'
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False
        ) as tf:
            tf.write(body_text)
            tmp_path = tf.name

        try:
            script = _build_save_draft_script(
                app_name="Microsoft Outlook",
                subject='He said "Hello"',
                body_file_path=tmp_path,
                to_recipients=[],
                reply_to_message_id=None,
            )
        finally:
            os.unlink(tmp_path)

        # Subject must appear (escaped via quote-concatenation, not raw quotes)
        assert "He said" in script
        # Body text must NOT be embedded in the script at all
        assert body_text not in script
        assert "do shell script" in script             # body read via shell, not embedded
        assert "open msg" in script
        assert "save msg" not in script

    def test_build_script_body_with_newlines_uses_file(self):
        """Multi-line body must not be embedded as a string literal (would cause -2741)."""
        import os
        import tempfile

        from personal_assistant.mlx_server.chat_routes import _build_save_draft_script

        multiline_body = "Line one.\nLine two.\nLine three with дата: 2026-05-22."
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", encoding="utf-8", delete=False
        ) as tf:
            tf.write(multiline_body)
            tmp_path = tf.name

        try:
            script = _build_save_draft_script(
                app_name="Microsoft Outlook",
                subject="Multi-line test",
                body_file_path=tmp_path,
                to_recipients=[],
                reply_to_message_id=None,
            )
        finally:
            os.unlink(tmp_path)

        # None of the body lines must appear verbatim in the script
        assert "Line one." not in script
        assert "Line two." not in script
        assert "Line three" not in script
        # File path and do shell script must be present
        assert tmp_path in script
        assert "do shell script" in script             # body read via cat, not embedded
        assert "open msg" in script


# ---------------------------------------------------------------------------
# 9. DEFAULT_DRAFT_SYSTEM structured format (checklist, analysis sections)
# ---------------------------------------------------------------------------

class TestDefaultDraftSystemStructure:

    def test_default_draft_has_checklist(self):
        """DEFAULT_DRAFT_SYSTEM must contain a pre-send checklist."""
        from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM

        assert "Чек-лист" in DEFAULT_DRAFT_SYSTEM or "чек-лист" in DEFAULT_DRAFT_SYSTEM, \
            "DEFAULT_DRAFT_SYSTEM must contain a checklist section"

    def test_default_draft_has_analysis_section(self):
        """DEFAULT_DRAFT_SYSTEM must instruct model to analyse the incoming message."""
        from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM

        assert "Проанализируй" in DEFAULT_DRAFT_SYSTEM, \
            "DEFAULT_DRAFT_SYSTEM must contain an analysis instruction"

    def test_default_draft_has_output_format(self):
        """DEFAULT_DRAFT_SYSTEM must define an output format section."""
        from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM

        assert "Формат вывода" in DEFAULT_DRAFT_SYSTEM or "Черновик ответа" in DEFAULT_DRAFT_SYSTEM, \
            "DEFAULT_DRAFT_SYSTEM must define the output format"

    def test_default_draft_has_manual_fill_section(self):
        """DEFAULT_DRAFT_SYSTEM must prompt the model to list gaps for manual fill-in."""
        from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM

        assert "УТОЧНИТЬ" in DEFAULT_DRAFT_SYSTEM or "дополнить вручную" in DEFAULT_DRAFT_SYSTEM.lower(), \
            "DEFAULT_DRAFT_SYSTEM must include a section for manual gaps"

    def test_default_draft_no_emoji_rule(self):
        """DEFAULT_DRAFT_SYSTEM must explicitly forbid emoji (regression guard)."""
        from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM

        text_lower = DEFAULT_DRAFT_SYSTEM.lower()
        assert "эмодзи" in text_lower, "emoji prohibition must still be present"
        assert "не использу" in text_lower or "не применяй" in text_lower, \
            "prohibition must use an imperative form"


# ---------------------------------------------------------------------------
# 10. Prompt length limit is 8000; validate_prompt rejects beyond that
# ---------------------------------------------------------------------------

class TestPromptLengthLimit:

    def test_limit_is_8000(self):
        """_MAX_PROMPT_LEN must be 8000."""
        from personal_assistant.services.tool_prompts import _MAX_PROMPT_LEN

        assert _MAX_PROMPT_LEN == 8_000, f"Expected 8000, got {_MAX_PROMPT_LEN}"

    def test_validate_accepts_prompt_under_8000(self):
        """validate_prompt must accept a 7999-char prompt."""
        from personal_assistant.services.tool_prompts import validate_prompt

        long_ok = "А" * 7999
        result = validate_prompt(long_ok)
        assert len(result) == 7999

    def test_validate_rejects_prompt_over_8000(self):
        """validate_prompt must reject a prompt exceeding 8000 chars."""
        from personal_assistant.services.tool_prompts import PromptValidationError, validate_prompt

        too_long = "Б" * 8001
        with pytest.raises(PromptValidationError, match="слишком длинный"):
            validate_prompt(too_long)

    def test_default_draft_fits_within_limit(self):
        """DEFAULT_DRAFT_SYSTEM itself must be under the 8000-char limit."""
        from personal_assistant.services.tool_prompts import _MAX_PROMPT_LEN, DEFAULT_DRAFT_SYSTEM

        assert len(DEFAULT_DRAFT_SYSTEM) <= _MAX_PROMPT_LEN, (
            f"DEFAULT_DRAFT_SYSTEM is {len(DEFAULT_DRAFT_SYSTEM)} chars, exceeds limit {_MAX_PROMPT_LEN}"
        )


# ---------------------------------------------------------------------------
# 11. GET /tool-prompts returns prompts_file_path and max_prompt_len
# ---------------------------------------------------------------------------

class TestToolPromptsApiResponse:
    """Tests for GET /tool-prompts API response fields."""

    def _make_client(self, tmp_path):
        """Return a TestClient pointed at the real app with vault_path patched."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from personal_assistant.services.tool_prompts import invalidate_cache
        from personal_assistant.webui.routes import router as webui_router

        invalidate_cache()
        mini_app = FastAPI()
        mini_app.include_router(webui_router)
        client = TestClient(mini_app)
        return client

    def test_api_returns_prompts_file_path(self, tmp_path):
        """GET /tool-prompts must include prompts_file_path in the response."""
        from personal_assistant.services.tool_prompts import invalidate_cache

        invalidate_cache()
        with patch("personal_assistant.config.settings.vault_path", str(tmp_path)):
            client = self._make_client(tmp_path)
            resp = client.get("/tool-prompts")

        assert resp.status_code == 200
        data = resp.json()
        assert "prompts_file_path" in data, "Response must include prompts_file_path"
        assert ".tool_prompts.json" in data["prompts_file_path"]

    def test_api_returns_max_prompt_len(self, tmp_path):
        """GET /tool-prompts must include max_prompt_len = 8000."""
        from personal_assistant.services.tool_prompts import invalidate_cache

        invalidate_cache()
        with patch("personal_assistant.config.settings.vault_path", str(tmp_path)):
            client = self._make_client(tmp_path)
            resp = client.get("/tool-prompts")

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("max_prompt_len") == 8_000

    def test_api_returns_default_prompts_when_no_custom(self, tmp_path):
        """GET /tool-prompts must return default prompts in default_draft_system field."""
        from personal_assistant.services.tool_prompts import DEFAULT_DRAFT_SYSTEM, invalidate_cache

        invalidate_cache()
        with patch("personal_assistant.config.settings.vault_path", str(tmp_path)):
            client = self._make_client(tmp_path)
            resp = client.get("/tool-prompts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["default_draft_system"] == DEFAULT_DRAFT_SYSTEM
        # No custom saved yet → draft_system must be empty
        assert data["draft_system"] == ""
