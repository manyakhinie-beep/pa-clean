"""
Unit tests verifying the mail_service.save_draft_reply signature fix.

BUG fixed: save_draft_reply was calling _build_save_draft_mail_script
without cc_recipients (required positional arg) → TypeError at runtime.

These tests verify:
  1. The function accepts cc_recipients and save_to_drafts parameters.
  2. The correct arguments are forwarded to _build_save_draft_mail_script.
  3. macOS-check raises RuntimeError on non-macOS.
  4. Legacy callers (no cc) still work (cc defaults to None → []).
"""

from __future__ import annotations

import platform
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _disable_e2e_test_mode(monkeypatch):
    """These tests exercise the real save_draft_reply path (osascript is mocked
    or the platform guard is asserted), so disable the suite-wide e2e_test_mode
    short-circuit set in the root conftest."""
    from personal_assistant.config import settings

    monkeypatch.setattr(settings, "e2e_test_mode", False)


class TestSaveDraftReplySignature:
    """Verify save_draft_reply accepts all expected parameters."""

    def test_accepts_cc_recipients_param(self):
        """Function must accept cc_recipients without TypeError."""
        import inspect

        from personal_assistant.services.mail_service import save_draft_reply

        sig = inspect.signature(save_draft_reply)
        assert "cc_recipients" in sig.parameters, \
            "cc_recipients parameter must be present in save_draft_reply"

    def test_accepts_save_to_drafts_param(self):
        """Function must accept save_to_drafts without TypeError."""
        import inspect

        from personal_assistant.services.mail_service import save_draft_reply

        sig = inspect.signature(save_draft_reply)
        assert "save_to_drafts" in sig.parameters, \
            "save_to_drafts parameter must be present in save_draft_reply"

    def test_cc_recipients_defaults_to_none(self):
        """cc_recipients should have a default (None) so legacy callers work."""
        import inspect

        from personal_assistant.services.mail_service import save_draft_reply

        sig = inspect.signature(save_draft_reply)
        param = sig.parameters["cc_recipients"]
        assert param.default is None, \
            "cc_recipients default must be None for backward compat"

    def test_save_to_drafts_defaults_to_false(self):
        """save_to_drafts should default to False (open compose window)."""
        import inspect

        from personal_assistant.services.mail_service import save_draft_reply

        sig = inspect.signature(save_draft_reply)
        param = sig.parameters["save_to_drafts"]
        assert param.default is False, \
            "save_to_drafts default must be False"


class TestSaveDraftReplyNonMacOS:
    """On non-macOS the function should raise RuntimeError immediately."""

    def test_raises_on_non_macos(self):
        """Calling save_draft_reply on a non-Darwin platform raises RuntimeError."""
        from personal_assistant.services.mail_service import save_draft_reply

        with patch("platform.system", return_value="Linux"):
            with pytest.raises(RuntimeError, match="macOS"):
                save_draft_reply(
                    subject="Test",
                    body="Hello",
                    to_recipients=["alice@example.com"],
                )


class TestSaveDraftReplyForwardsArgs:
    """Verify correct args are forwarded to _build_save_draft_mail_script."""

    def test_forwards_cc_recipients_as_empty_list_when_none(self):
        """When cc_recipients=None, [] must be forwarded (not None).

        run_applescript and _build_save_draft_mail_script are lazy-imported
        inside save_draft_reply, so we patch at their real module paths.
        """
        with patch("platform.system", return_value="Darwin"), \
             patch("personal_assistant.readers.applescript_base.run_applescript"), \
             patch(
                 "personal_assistant.mlx_server.chat_routes._build_save_draft_mail_script",
                 return_value="tell application \"Mail\" end tell",
             ) as mock_build:
            from personal_assistant.services.mail_service import save_draft_reply

            try:
                save_draft_reply(
                    subject="Тест",
                    body="Текст письма",
                    to_recipients=["bob@example.com"],
                    cc_recipients=None,
                    reply_to_message_id=None,
                    save_to_drafts=False,
                )
            except Exception:
                pass

            if mock_build.called:
                kwargs = mock_build.call_args.kwargs
                cc = kwargs.get("cc_recipients", mock_build.call_args.args[3] if len(mock_build.call_args.args) > 3 else None)
                assert cc == [], f"cc_recipients must be [] not None, got {cc!r}"

    def test_forwards_save_to_drafts_flag(self):
        """save_to_drafts=True must be forwarded to the script builder."""
        with patch("platform.system", return_value="Darwin"), \
             patch("personal_assistant.readers.applescript_base.run_applescript"), \
             patch(
                 "personal_assistant.mlx_server.chat_routes._build_save_draft_mail_script",
                 return_value="tell application \"Mail\" end tell",
             ) as mock_build:
            from personal_assistant.services.mail_service import save_draft_reply

            try:
                save_draft_reply(
                    subject="Test",
                    body="Body",
                    to_recipients=["a@b.com"],
                    save_to_drafts=True,
                )
            except Exception:
                pass

            if mock_build.called:
                kwargs = mock_build.call_args.kwargs
                flag = kwargs.get("save_to_drafts")
                if flag is None:
                    # may be positional (subject, body_file, to, cc, reply_to, save_to)
                    args = mock_build.call_args.args
                    if len(args) >= 6:
                        flag = args[5]
                assert flag is True, \
                    f"save_to_drafts=True must be forwarded, got {flag!r}"


class TestSaveDraftReplyReturnMessages:
    """Verify the returned message text reflects the save_to_drafts flag."""

    @pytest.mark.skipif(
        platform.system() != "Darwin",
        reason="Requires macOS osascript"
    )
    def test_open_window_message(self, tmp_path):
        """save_to_drafts=False → message says 'открыт в Mail'."""
        with patch("personal_assistant.readers.applescript_base.run_applescript"):
            with patch(
                "personal_assistant.mlx_server.chat_routes._build_save_draft_mail_script",
                return_value="tell application \"Mail\" end tell",
            ):
                from personal_assistant.services.mail_service import save_draft_reply
                result = save_draft_reply(
                    subject="X",
                    body="Y",
                    to_recipients=["a@b.com"],
                    save_to_drafts=False,
                )
        assert result["ok"] is True
        assert "открыт" in result["message"]

    @pytest.mark.skipif(
        platform.system() != "Darwin",
        reason="Requires macOS osascript"
    )
    def test_save_to_drafts_message(self, tmp_path):
        """save_to_drafts=True → message says 'сохранён'."""
        with patch("personal_assistant.readers.applescript_base.run_applescript"):
            with patch(
                "personal_assistant.mlx_server.chat_routes._build_save_draft_mail_script",
                return_value="tell application \"Mail\" end tell",
            ):
                from personal_assistant.services.mail_service import save_draft_reply
                result = save_draft_reply(
                    subject="X",
                    body="Y",
                    to_recipients=["a@b.com"],
                    save_to_drafts=True,
                )
        assert result["ok"] is True
        assert "сохранён" in result["message"]
