"""
mail_service.py — Service stubs for Apple Mail integration.

Provides high-level operations on top of the mail_reader AppleScript reader
and vault writer.  Each function is a thin coordinator: it delegates to the
appropriate reader / writer / LLM component rather than containing logic.

Current stubs (ready for full implementation):
  - save_draft_reply    — create a reply draft in Apple Mail via AppleScript
  - summarize_thread    — summarise a mail thread using the MLX engine
  - fetch_thread_messages — return all vault .md files belonging to a thread
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger

# ---------------------------------------------------------------------------
# save_draft_reply
# ---------------------------------------------------------------------------


def save_draft_reply(
    subject: str,
    body: str,
    to_recipients: list[str],
    cc_recipients: Optional[list[str]] = None,
    reply_to_message_id: Optional[str] = None,
    save_to_drafts: bool = False,
) -> dict:
    """Open a pre-filled reply draft in Apple Mail.

    Writes ``body`` to a UTF-8 tempfile and passes the path to an AppleScript
    that calls ``make new outgoing message`` (or ``reply origMsg`` when
    ``reply_to_message_id`` is provided).

    Args:
        subject:             Draft subject line.
        body:                Draft body text (plain text or Markdown).
        to_recipients:       List of To: recipient email addresses.
        cc_recipients:       Optional CC addresses.
        reply_to_message_id: RFC 2822 Message-ID of the original message.
                             When given, the draft is threaded as a reply.
        save_to_drafts:      True = save silently to Drafts; False = open compose window.

    Returns:
        ``{"ok": True, "message": "..."}`` on success.

    Raises:
        RuntimeError: if osascript fails or the platform is not macOS.
    """
    from personal_assistant.config import settings

    # Test mode: never create a real draft / open Mail — simulate success so
    # scenario/e2e tests can run the full flow without side effects.
    if settings.e2e_test_mode:
        logger.info(f"[mail_service] e2e_test_mode: skipped real draft {subject!r}")
        return {
            "ok": True,
            "message": "e2e_test_mode: черновик не создавался (тестовый режим)",
            "e2e": True,
        }

    import platform

    if platform.system() != "Darwin":
        raise RuntimeError("Apple Mail draft creation is only supported on macOS")

    from personal_assistant.mlx_server.chat_routes import (
        _build_save_draft_mail_script,
    )
    from personal_assistant.readers.applescript_base import run_applescript

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="pa_mail_draft_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(body)

        script = _build_save_draft_mail_script(
            subject=subject,
            body_file_path=tmp_path,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients or [],
            reply_to_message_id=reply_to_message_id,
            save_to_drafts=save_to_drafts,
        )

        run_applescript(script, timeout=30)
        logger.info(f"[mail_service] Draft opened in Mail: {subject!r}")
        msg = (
            "Черновик сохранён в папке Черновики Mail"
            if save_to_drafts
            else "Черновик открыт в Mail — нажмите Cmd+S для сохранения"
        )
        return {"ok": True, "message": msg}
    except Exception as exc:
        logger.error(f"[mail_service] save_draft_reply failed: {exc}")
        raise RuntimeError(f"Failed to open draft in Mail: {exc}") from exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# mail_auto_draft helper
# ---------------------------------------------------------------------------


def resolve_save_to_drafts(explicit: Optional[bool]) -> bool:
    """Decide whether a draft should be saved silently to the Drafts mailbox.

    Honours the ``mail_auto_draft`` setting: when the caller leaves the choice
    unspecified (``None``), auto-draft ON means "save to Drafts", OFF means
    "open a compose window for review". An explicit value always wins.
    """
    if explicit is not None:
        return explicit
    from personal_assistant.config import settings

    return settings.mail_auto_draft


# ---------------------------------------------------------------------------
# fetch_thread_messages
# ---------------------------------------------------------------------------


def fetch_thread_messages(
    thread_id: str,
    vault_path: Optional[Path] = None,
) -> list[dict]:
    """Return all vault .md files that belong to ``thread_id``.

    Scans vault/mail/**/*.md and vault/threads/**/*.md for files whose
    ``thread_id`` frontmatter field matches the given value.

    Args:
        thread_id:   Thread ID string (as written in frontmatter).
        vault_path:  Path to the vault root; defaults to settings.vault_path.

    Returns:
        List of dicts with keys: ``path``, ``title``, ``date``, ``sender``,
        ``subject``, ``body_snippet``.
    """
    from personal_assistant.config import settings

    root = vault_path or settings.vault_path

    results: list[dict] = []
    for section in ("mail", "threads"):
        section_path = root / section
        if not section_path.exists():
            continue
        for md_file in section_path.rglob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8")
                # Quick frontmatter check — avoid parsing the whole file
                if f'thread_id: "{thread_id}"' not in text and \
                   f"thread_id: {thread_id}" not in text:
                    continue
                # Parse frontmatter
                fm = _parse_frontmatter(text)
                if fm.get("thread_id") != thread_id:
                    continue
                body_start = text.find("\n---\n", text.find("---") + 3)
                body = text[body_start + 5:].strip() if body_start != -1 else ""
                results.append({
                    "path": str(md_file),
                    "title": fm.get("subject", md_file.stem),
                    "date": fm.get("date", ""),
                    "sender": fm.get("sender", ""),
                    "subject": fm.get("subject", ""),
                    "body_snippet": body[:300],
                })
            except Exception:
                continue

    results.sort(key=lambda r: r["date"])
    return results


# ---------------------------------------------------------------------------
# summarize_thread
# ---------------------------------------------------------------------------


def summarize_thread(
    thread_id: str,
    vault_path: Optional[Path] = None,
    max_tokens: int = 768,
) -> dict:
    """Summarise an email thread using the local MLX engine.

    Fetches all messages for ``thread_id`` from the vault, builds a context
    block, and asks the MLX engine to produce a concise summary.

    Args:
        thread_id:   Thread ID to summarise.
        vault_path:  Vault root path; defaults to settings.vault_path.
        max_tokens:  Max tokens for the LLM response.

    Returns:
        ``{"thread_id": ..., "summary": ..., "message_count": ...}``

    Raises:
        ValueError: if no messages are found for the thread.
    """
    from personal_assistant.mlx_server.engine import get_engine

    messages = fetch_thread_messages(thread_id, vault_path=vault_path)
    if not messages:
        raise ValueError(f"No messages found for thread_id={thread_id!r}")

    context_lines = []
    for msg in messages:
        context_lines.append(
            f"От: {msg['sender']} | {msg['date']}\n"
            f"Тема: {msg['subject']}\n"
            f"{msg['body_snippet']}\n"
            "---"
        )
    context = "\n".join(context_lines)

    prompt = (
        f"Суммаризируй эту переписку из {len(messages)} сообщений. "
        "Выдели ключевые решения, поручения и открытые вопросы.\n\n"
        f"{context}"
    )
    system = (
        "Ты помощник, суммаризирующий деловую переписку. "
        "Пиши кратко и по делу на том же языке, что и переписка."
    )

    engine = get_engine()
    summary = engine.ask(prompt, system=system, max_tokens=max_tokens)
    logger.info(f"[mail_service] Thread {thread_id!r} summarised ({len(messages)} msgs)")

    return {
        "thread_id": thread_id,
        "summary": summary,
        "message_count": len(messages),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter between first pair of ``---`` delimiters."""
    import yaml  # noqa: PLC0415

    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    try:
        return yaml.safe_load(fm_text) or {}
    except Exception:
        return {}
