"""
delegate_service — generates a short intro for forwarding (delegating) an
inbox email to a colleague.

Powers the Inbox → Assistant → "🤝 Делегировать" action:

  1. Frontend picks a colleague from ``delegate_contacts`` (configured in
     Rules → Инструменты) and optionally types a short note.
  2. Backend reads the source message from the vault, then asks MLX to
     produce a 4-7 line intro that summarises the request and assigns
     the work to the colleague.  The system prompt is
     ``delegate_system`` from tool_prompts (with default fallback).
  3. Backend returns the intro + suggested subject + ready-to-forward
     payload.  The frontend then opens the chat draft panel, OR posts
     directly to ``/api/chat/save-draft-mail`` with the original
     ``message_id`` so Mail.app forwards the original thread.

Graceful degradation:
  * No MLX engine available → returns a rule-based template intro built
    from sender + subject + user note.
  * Unknown contact → still returns text the user can use; frontend
    catches the 404 and falls back to manual recipient entry.

This module is small on purpose: heavy logic lives in tool_prompts /
draft_context_service / chat_routes (Mail forwarding).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from personal_assistant.services.tool_prompts import (
    DelegateContact,
    get_tool_prompts,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DelegateSuggestion:
    """Result of generating a delegate-forward intro.

    :ivar intro: Multi-line intro text — the body of the forwarded email.
    :ivar subject: Suggested subject (``Fwd: …`` or ``Поручение: …``).
    :ivar contact: The colleague picked from ``delegate_contacts``.
    :ivar source_message_id: Vault id of the original mail item.
    :ivar mlx_used: ``True`` when the intro came from MLX, ``False`` for
        the rule-based fallback (lets the UI flag low-confidence drafts).
    """

    intro: str
    subject: str
    contact: DelegateContact
    source_message_id: str
    mlx_used: bool


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_contact(email: str) -> Optional[DelegateContact]:
    """Look up a configured colleague by email (case-insensitive)."""
    email = (email or "").strip().lower()
    if not email:
        return None
    for c in get_tool_prompts().delegate_contacts:
        if c.email.lower() == email:
            return c
    return None


def list_contacts() -> list[DelegateContact]:
    """Return the configured colleagues from Rules → Инструменты."""
    return list(get_tool_prompts().delegate_contacts)


def build_suggestion(
    *,
    item: dict,
    contact: DelegateContact,
    user_note: str = "",
    mlx_engine=None,  # optional MLX engine; injected by route
) -> DelegateSuggestion:
    """Produce intro text + subject for delegating an inbox item.

    :param item: dict returned by ``_doc_to_item`` (frontmatter + body
        preview).  Expected keys: ``id``, ``subject``, ``sender_name``,
        ``sender_email``, ``body_preview`` (preview ok).
    :param contact: target colleague.
    :param user_note: optional one-liner from the manager ("ускорь, прошу
        вернуть к среде").  Forwarded to MLX as extra context.
    :param mlx_engine: instance of MLXEngine or ``None``.  When ``None``,
        falls back to a deterministic rule-based template.
    """
    subject = _build_subject(item)
    intro = _build_intro_mlx(item, contact, user_note, mlx_engine) if mlx_engine else None
    if intro is None or not intro.strip():
        intro = _build_intro_rule_based(item, contact, user_note)
        mlx_used = False
    else:
        mlx_used = True
    return DelegateSuggestion(
        intro=intro.strip(),
        subject=subject,
        contact=contact,
        source_message_id=str(item.get("id", "")),
        mlx_used=mlx_used,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_subject(item: dict) -> str:
    """``Поручение: <тема>`` — strips Re:/Fwd: from the source subject."""
    raw = str(item.get("subject") or "").strip()
    cleaned = raw
    # Strip leading Re:/Fwd:/Отв:/Пер: (any number) so the colleague sees a
    # fresh, action-oriented title.
    while True:
        low = cleaned.lower()
        if low.startswith(("re:", "re :", "fwd:", "fw:", "отв:", "пер:")):
            cleaned = cleaned.split(":", 1)[1].strip()
        else:
            break
    if not cleaned:
        cleaned = "без темы"
    return f"Поручение: {cleaned}"


def _build_intro_rule_based(
    item: dict, contact: DelegateContact, user_note: str
) -> str:
    """Deterministic fallback intro — never depends on MLX.

    Format: greeting → reason → ask → deadline placeholder → sign-off cue.
    All in Russian. Trimmed for readability.
    """
    first_name = (contact.name or "").split()[0] if contact.name else "коллега"
    subject = str(item.get("subject") or "без темы").strip()
    sender = str(item.get("sender_name") or item.get("sender_email") or "коллега").strip()
    preview = (item.get("body_preview") or item.get("preview") or "").strip()
    snippet = preview.split("\n", 1)[0][:160] if preview else ""

    parts: list[str] = [f"{first_name}, добрый день!"]
    parts.append(
        f"Пересылаю обращение от {sender} по теме «{subject}». "
        + (f"Кратко: {snippet}." if snippet else "Детали в пересылаемом письме ниже.")
    )
    if user_note.strip():
        parts.append(user_note.strip())
    parts.append(
        "Прошу взять в работу и сообщить статус (или сразу ответить отправителю с копией мне)."
    )
    parts.append("Спасибо!")
    return "\n\n".join(parts)


def _build_intro_mlx(
    item: dict,
    contact: DelegateContact,
    user_note: str,
    engine,
) -> Optional[str]:
    """MLX-generated intro via the user-configured ``delegate_system`` prompt.

    Returns ``None`` on any engine error so the caller can fall back to the
    rule-based path.
    """
    prompts = get_tool_prompts()
    system = prompts.effective_delegate()

    subject = str(item.get("subject") or "").strip()
    sender = str(item.get("sender_name") or "").strip()
    sender_email = str(item.get("sender_email") or "").strip()
    preview = (item.get("body_preview") or item.get("preview") or "").strip()
    # Keep the body short — Qwen-7B handles ~1500-2000 chars of context well.
    body_snippet = preview[:1500]

    role_suffix = f" ({contact.role})" if contact.role else ""
    note_block = f"\n\nЗаметка от руководителя: {user_note.strip()}" if user_note.strip() else ""

    prompt = (
        f"Кому делегируем: {contact.name}{role_suffix} <{contact.email}>.\n"
        f"От кого письмо: {sender} <{sender_email}>.\n"
        f"Тема: {subject}.\n"
        f"Тело письма (фрагмент):\n{body_snippet}"
        f"{note_block}\n\n"
        f"Составь короткое (4-7 строк) тело письма для делегирования. "
        f"Только текст вводной, без темы и без `Кому`."
    )

    try:
        result = engine.ask(
            question=prompt,
            system=system,
            max_tokens=400,
            temperature=0.2,
        )
        return result.strip() if isinstance(result, str) else None
    except Exception as exc:  # noqa: BLE001 — never block delegation on MLX
        logger.warning(f"[delegate] MLX intro failed, falling back: {exc}")
        return None
