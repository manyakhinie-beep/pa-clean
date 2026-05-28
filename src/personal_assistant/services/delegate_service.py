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

    :ivar intro: Body of the email sent to the colleague. With the new
        4-section system prompt this is the **«ЧЕРНОВИК ЗАДАЧИ ДЛЯ
        СОТРУДНИКА»** block extracted from the LLM output; with the
        rule-based fallback this is a deterministic short text addressed
        to the colleague.
    :ivar full_text: Complete LLM output — РЕКОМЕНДАЦИЯ / КОНТЕКСТ /
        ЧЕРНОВИК ЗАДАЧИ / ПРИМЕЧАНИЕ.  Shown in the WebUI preview so the
        manager sees the full analysis; not sent to Mail.
    :ivar subject: Suggested subject (``Поручение: …``).
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
    full_text: str = ""


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

    Pipeline:

      1. Load the manager's profile (Settings → Profile) so the LLM
         knows whose name appears in the colleague's task: "подготовь
         поручения для {full_name}".  Falls back to "руководитель"
         when the profile is empty.
      2. Hand the system prompt (``effective_delegate``) + a user
         message containing the manager profile, the chosen contact,
         the source-email facts and an optional manager note to MLX.
         The LLM produces a 4-section analysis (РЕКОМЕНДАЦИЯ / КОНТЕКСТ
         / ЧЕРНОВИК ЗАДАЧИ / ПРИМЕЧАНИЕ).
      3. Extract the «ЧЕРНОВИК ЗАДАЧИ ДЛЯ СОТРУДНИКА» section as the
         actual email body — that's what goes into the Mail.app draft.
         The full 4-section text is kept on the suggestion for the
         preview modal.
      4. When MLX is unavailable, the rule-based fallback addresses the
         colleague by name and asks them to either prepare assignments
         for the manager or research the question.
    """
    manager_profile = _load_manager_profile()
    subject = _build_subject(item)

    full_text: Optional[str] = None
    if mlx_engine:
        full_text = _build_intro_mlx(
            item=item,
            contact=contact,
            user_note=user_note,
            engine=mlx_engine,
            manager=manager_profile,
        )

    if full_text and full_text.strip():
        intro = _extract_employee_task(full_text) or full_text
        mlx_used = True
    else:
        intro = _build_intro_rule_based(item, contact, user_note, manager_profile)
        full_text = intro
        mlx_used = False

    return DelegateSuggestion(
        intro=intro.strip(),
        subject=subject,
        contact=contact,
        source_message_id=str(item.get("id", "")),
        mlx_used=mlx_used,
        full_text=full_text.strip(),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _ManagerProfile:
    """Snapshot of the assistant's user (the inbox owner) — injected
    into the delegate prompt so the colleague knows whom to deliver
    assignments to."""

    full_name: str = ""
    email: str = ""

    @property
    def first_name(self) -> str:
        return (self.full_name.split()[0] if self.full_name else "").strip()

    @property
    def display(self) -> str:
        """Render as ``Full Name <email>`` / ``Full Name`` / ``руководитель``."""
        if self.full_name and self.email:
            return f"{self.full_name} <{self.email}>"
        if self.full_name:
            return self.full_name
        if self.email:
            return self.email
        return "руководитель"


def _load_manager_profile() -> _ManagerProfile:
    """Pull ``full_name`` and ``user_email`` from Settings → Profile.

    Falls back to ``settings.user_email`` if the profile is empty.  Never
    raises — a stale or unreadable profile must not block delegation.
    """
    try:
        from personal_assistant.profile.service import load_profile
        prof = load_profile()
        name = (prof.full_name or "").strip()
        email = (prof.user_email or "").strip() if prof.user_email else ""
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.debug(f"[delegate] load_profile failed: {exc}")
        name, email = "", ""
    if not email:
        try:
            from personal_assistant.config import settings
            email = (settings.user_email or "").strip()
        except Exception:
            pass
    return _ManagerProfile(full_name=name, email=email)


# ---------------------------------------------------------------------------
# Section extraction (4-section LLM output → just the employee task body)
# ---------------------------------------------------------------------------

# Matches the section header we emit in DEFAULT_DELEGATE_SYSTEM. Made
# permissive on whitespace and ## / ### levels so a hand-edited user prompt
# still works.
_EMP_TASK_HEADER = (
    r"^\s*#{1,4}\s*(?:черновик\s+задачи(?:\s+для\s+сотрудника)?|"
    r"задача\s+для\s+сотрудника|"
    r"task\s+for\s+employee)\s*$"
)


def _extract_employee_task(text: str) -> str:
    """Return the body of the ``ЧЕРНОВИК ЗАДАЧИ ДЛЯ СОТРУДНИКА`` block.

    The standard delegate prompt produces four sections (РЕКОМЕНДАЦИЯ /
    КОНТЕКСТ / ЧЕРНОВИК ЗАДАЧИ / ПРИМЕЧАНИЕ).  Only the third one is the
    actual email body — that's what we hand to Mail.app.  This helper
    pulls it out; if the model returned a free-form text without
    sections, we return an empty string and the caller keeps the full
    text.
    """
    import re as _re
    if not text:
        return ""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _re.match(_EMP_TASK_HEADER, line, flags=_re.IGNORECASE):
            start = i + 1
            break
    if start is None:
        return ""
    # End at the next H2/H3 header or end-of-text
    end = len(lines)
    for j in range(start, len(lines)):
        if _re.match(r"^\s*#{1,4}\s+\S", lines[j]):
            end = j
            break
    body = "\n".join(lines[start:end]).strip()
    return body


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
    item: dict,
    contact: DelegateContact,
    user_note: str,
    manager: _ManagerProfile,
) -> str:
    """Deterministic fallback intro — never depends on MLX.

    Addresses the picked colleague by first name and asks them to either
    prepare a list of action items for the manager (whose profile is
    pulled from Settings) or research the question.  Matches the user's
    spec: «обращено к выбранному сотруднику; он должен подготовить
    поручения для пользователя ассистента или разобраться в вопросе».
    """
    first_name = (contact.name or "").split()[0] if contact.name else "коллега"
    subject = str(item.get("subject") or "без темы").strip()
    sender = str(item.get("sender_name") or item.get("sender_email") or "коллега").strip()
    preview = (item.get("body_preview") or item.get("preview") or "").strip()
    snippet = preview.split("\n", 1)[0][:200] if preview else ""

    manager_phrase = (
        f"для {manager.first_name}" if manager.first_name else "для руководителя"
    )

    parts: list[str] = [f"{first_name}, добрый день!"]
    parts.append(
        f"Пересылаю обращение от {sender} по теме «{subject}». "
        + (f"Кратко: {snippet}." if snippet else "Детали в пересылаемом письме ниже.")
    )
    if user_note.strip():
        parts.append(user_note.strip())
    # Two-track ask: prepare assignments for the manager OR research and report
    parts.append(
        f"Прошу разобраться в вопросе и подготовить список поручений {manager_phrase} "
        f"(что нужно сделать, кому, к какому сроку). Либо — если задача в зоне твоей "
        f"ответственности — отработай и сообщи статус."
    )
    parts.append(
        "Если нужны дополнительные данные или согласование — напиши, обсудим."
    )
    parts.append("Спасибо!")
    return "\n\n".join(parts)


def _build_intro_mlx(
    item: dict,
    contact: DelegateContact,
    user_note: str,
    engine,
    manager: _ManagerProfile,
) -> Optional[str]:
    """MLX-generated 4-section analysis via the configured ``delegate_system``
    prompt.

    Builds a context user-message that pins:
      * **Руководитель (пользователь ассистента)** — full_name + email
        from Settings → Profile so the colleague's task is framed as
        «подготовить поручения для {имя}».
      * **Сотрудник** — chosen contact (name, role, email) so the LLM
        addresses them by name and adapts the tone to the role.
      * **Источник** — sender + subject + body snippet.
      * **Заметка руководителя** — optional.
      * **Семантика поручения** — colleague should either prepare a list
        of assignments for the manager or research and report.

    Returns the full LLM output (4 sections per the system prompt); the
    caller is responsible for extracting the email-body section.  Returns
    ``None`` on any engine error so the caller falls back to rule-based.
    """
    prompts = get_tool_prompts()
    system = prompts.effective_delegate()

    subject = str(item.get("subject") or "").strip()
    sender = str(item.get("sender_name") or "").strip()
    sender_email = str(item.get("sender_email") or "").strip()
    preview = (item.get("body_preview") or item.get("preview") or "").strip()
    # Keep the body short — Qwen-7B handles ~1500-2000 chars of context well.
    body_snippet = preview[:1500]

    # Pre-computed date anchors — delegate is one-shot, no tool calling,
    # so the model would otherwise hallucinate dates for «через 2 недели»
    # and friends.  We resolve them server-side via the same
    # ``deadline_extractor`` chat-mode date_calc trusts.
    from personal_assistant.services.date_anchors import build_date_anchor_block
    anchors = build_date_anchor_block(
        email_date=str(item.get("date") or "") or None,
        email_text=f"{subject}\n\n{body_snippet}",
    )

    role_suffix = f" ({contact.role})" if contact.role else ""
    note_block = (
        f"\n\n## Заметка от руководителя\n{user_note.strip()}"
        if user_note.strip()
        else ""
    )

    prompt = (
        anchors
        + f"## Руководитель (пользователь ассистента)\n"
        f"Имя: {manager.full_name or '—'}\n"
        f"Email: {manager.email or '—'}\n\n"
        f"## Сотрудник для делегирования\n"
        f"Имя: {contact.name}{role_suffix}\n"
        f"Email: {contact.email}\n"
        + (f"Заметка: {contact.note}\n" if contact.note else "")
        + "\n"
        f"## Источник\n"
        f"От кого письмо: {sender} <{sender_email}>\n"
        f"Тема: {subject}\n\n"
        f"## Тело письма (фрагмент)\n{body_snippet}"
        f"{note_block}\n\n"
        f"## Задача\n"
        f"Подготовь анализ и черновик задачи для сотрудника по формату из "
        f"system-промпта. Сотрудник должен **либо** подготовить список "
        f"поручений для руководителя (что сделать, кому, к какому сроку), "
        f"**либо** разобраться в вопросе самостоятельно на основе анализа. "
        f"В «ЧЕРНОВИК ЗАДАЧИ ДЛЯ СОТРУДНИКА» обращайся к "
        f"{(contact.name or 'сотруднику').split()[0]} по имени; "
        f"имя руководителя — {manager.first_name or 'руководитель'}."
    )

    try:
        result = engine.ask(
            question=prompt,
            system=system,
            max_tokens=900,    # 4 sections need more room than a 4-7-line intro
            temperature=0.2,
        )
        return result.strip() if isinstance(result, str) else None
    except Exception as exc:  # noqa: BLE001 — never block delegation on MLX
        logger.warning(f"[delegate] MLX intro failed, falling back: {exc}")
        return None
