"""
Context assembly for MLX from a PersonalVault thread.

Stateless: builds prompt on-the-fly from thread_id.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from personal_assistant.personal_vault.db import get_thread
from personal_assistant.utils.timezone import format_to_msk_prompt_str

_MAX_CHARS_DEFAULT = 12000


class ContextResult(TypedDict):
    thread_id: str
    system_prompt: str
    messages: list[dict[str, str]]
    total_chars: int


def build_context(
    thread_id: str,
    mode: Literal["draft", "summarize", "chat"] = "chat",
    max_chars: int = _MAX_CHARS_DEFAULT,
) -> ContextResult:
    """
    Assemble MLX-ready context from a thread.

    Returns {
        "thread_id": str,
        "system_prompt": str,
        "messages": list[dict],
        "total_chars": int,
    }
    """
    thread = get_thread(thread_id)
    if thread is None:
        raise ValueError(f"Thread {thread_id} not found")

    parts: list[str] = []
    parts.append(
        f"Ты — персональный AI-ассистент. Текущая дата и время: {format_to_msk_prompt_str()}."
    )

    mode_instruction = {
        "draft": (
            "Твоя задача — написать черновик ответа на основе переписки ниже. "
            "Сохрани деловой тон, упомяни ключевые детали."
        ),
        "summarize": (
            "Твоя задача — кратко суммаризировать переписку. "
            "Выдели основные темы, решения и action items."
        ),
        "chat": (
            "Отвечай на вопрос пользователя, используя предоставленную переписку как контекст. "
            "Если ответа нет в контексте — скажи об этом честно."
        ),
    }.get(mode, "")
    parts.append(mode_instruction)

    parts.append(f"\n--- ПЕРЕПИСКА (тема: {thread.root_subject}) ---")
    parts.append(f"Участники: {', '.join(thread.participants)}\n")

    running_len = sum(len(p) for p in parts)
    history: list[dict[str, str]] = []
    for item in thread.items:
        block = _format_item(item)
        if running_len + len(block) > max_chars:
            # try to fit a truncated version of the first/only item
            allowed = max_chars - running_len - 200
            if allowed > 0:
                block = _format_item(item, max_body_len=allowed)
                parts.append(block)
                running_len += len(block)
                history.append({"role": "user", "content": f"[{item.date_iso}] {item.sender}: {item.full_body[:500]}"})
            else:
                parts.append("\n[...truncated...]")
            break
        parts.append(block)
        running_len += len(block)
        history.append({"role": "user", "content": f"[{item.date_iso}] {item.sender}: {item.full_body[:500]}"})

    parts.append("--- /ПЕРЕПИСКА ---")

    system_prompt = "\n".join(parts)
    return {
        "thread_id": thread_id,
        "system_prompt": system_prompt,
        "messages": history,
        "total_chars": len(system_prompt),
    }


def _format_item(item, max_body_len: int | None = None) -> str:
    body = item.full_body
    if max_body_len is not None and len(body) > max_body_len:
        body = body[:max_body_len] + "\n[...truncated...]"
    lines: list[str] = []
    lines.append(f"\n[{item.date_iso}] {item.sender} <{item.sender_email or 'no email'}>")
    lines.append(f"Тема: {item.subject}")
    lines.append(f"Тип: {item.item_type}")
    lines.append("---")
    lines.append(body)
    if item.attachments:
        lines.append("\n[Вложения]")
        for att in item.attachments:
            lines.append(f"- {att.filename} ({att.mime_type}, {att.size_bytes} bytes)")
    return "\n".join(lines)
