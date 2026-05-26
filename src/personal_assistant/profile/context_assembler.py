"""
Profile-aware context assembler.

Composes with the existing ``ContextAssembler`` from ``context_builder``
and injects ``UserProfile`` + ``AIAssistantConfig`` + optional
PersonalVault thread context into the system prompt.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from personal_assistant.mlx_server.context_builder import get_assembler
from personal_assistant.profile.models import AIAssistantConfig, UserProfile
from personal_assistant.profile.service import load_config, load_profile
from personal_assistant.utils.timezone import format_to_msk_prompt_str

# Rough heuristic: 1 token ≈ 3 chars for mixed ru/en text
_CHARS_PER_TOKEN = 3


def validate_thread_exists(thread_id: str) -> bool:
    """
    Check whether a PersonalVault thread exists.

    Normalises *thread_id* (strip whitespace) before lookup.
    """
    from personal_assistant.personal_vault.db import get_thread

    if not thread_id or not isinstance(thread_id, str):
        return False
    tid = thread_id.strip()
    if not tid:
        return False
    try:
        return get_thread(tid) is not None
    except Exception as exc:
        logger.warning(f"[validate_thread] lookup failed for {tid!r}: {exc}")
        return False


def _load_mail_thread_context(thread_id: str) -> str:
    """
    Search VaultIndex for .md files whose frontmatter ``thread_id`` matches
    the given 12-char hex hash and build a mail-thread context block.

    This is the fallback used when ``vault_thread_id`` refers to a mail thread
    hash rather than a PersonalVault SQLite thread UUID.
    """
    try:
        from personal_assistant.mlx_server.server import state

        index = state.index
        if index is None or not index.docs:
            return ""

        tid = thread_id.strip().lower()
        mail_docs = [
            d for d in index.docs
            if str(d.frontmatter.get("thread_id", "")).strip().lower() == tid
        ]
        if not mail_docs:
            # Also search by direct filename match (single doc opened from vault)
            return ""

        # Sort chronologically
        mail_docs.sort(key=lambda d: str(d.date or ""))

        from personal_assistant.mlx_server.context_builder import _VAULT_SNIPPET_CHARS

        parts: list[str] = []
        total_chars = 0
        budget = 8000
        for doc in mail_docs:
            snippet = doc.raw[:_VAULT_SNIPPET_CHARS]
            if total_chars + len(snippet) > budget:
                remaining = budget - total_chars
                if remaining < 200:
                    break
                snippet = snippet[:remaining]
            label = f"📧 ПИСЬМО «{doc.title}»"
            parts.append(f"\n[{label}]\n{snippet}")
            total_chars += len(snippet)

        if not parts:
            return ""

        count = len(mail_docs)
        suffix = "о" if count == 1 else "а" if count < 5 else ""
        return (
            f"\n--- КОНТЕКСТ ПЕРЕПИСКИ ({count} письм{suffix}) ---"
            + "".join(parts)
            + "\n--- /КОНТЕКСТ ПЕРЕПИСКИ ---"
        )
    except Exception as exc:
        logger.warning(f"[profile_asm] failed to load mail thread {thread_id!r}: {exc}")
        return ""


class ProfileAwareAssembler:
    """Wraps the base assembler and enriches the prompt with profile data."""

    def __init__(self) -> None:
        self._base = get_assembler()

    def build(
        self,
        user_message: str,
        history: list[dict],
        context_paths: list[str],
        mode: str = "chat",
        vault_thread_id: Optional[str] = None,
        reply_message_id: Optional[str] = None,
    ) -> dict:
        """
        Returns a dict with keys:
          system_prompt, messages, vault_refs, tool_specs
        """
        # BUG-3 fix: if context_paths is empty but reply_message_id is set,
        # try to auto-resolve the vault path from the inbox index so the model
        # still has document context even when the frontend omitted the path
        # (GAP-4 scenario: vault not synced at the time of the inbox action).
        # Must run BEFORE self._base.build() so the resolved path is included.
        if reply_message_id and not context_paths:
            try:
                from personal_assistant.inbox.routes import _get_index
                idx = _get_index()
                if idx is not None:
                    items = idx.get_items()
                    matched = next(
                        (it for it in items if getattr(it, "id", None) == reply_message_id),
                        None,
                    )
                    if matched and getattr(matched, "path", None):
                        context_paths = [matched.path]
                        logger.info(
                            f"[profile_asm] BUG-3 fallback: resolved path "
                            f"{matched.path!r} for reply_message_id={reply_message_id!r}"
                        )
            except Exception as exc:
                logger.debug(f"[profile_asm] reply_message_id path resolution skipped: {exc}")

        # 1. Base context (persona, souls, GTD, tools, vault snippets)
        ctx = self._base.build(
            user_message=user_message,
            history=history,
            context_paths=context_paths,
            mode=mode,
        )

        # 2. Load profile & config
        profile = load_profile()
        config = load_config()

        # 3. Build profile block
        profile_block = _build_profile_block(profile, config)

        # 4a. Auto-inject today's vault context from PersonalVault DB
        pv_db_block = ""
        try:
            from personal_assistant.mlx_server.context_builder import load_pv_db_context
            pv_db_block = load_pv_db_context()
        except Exception as exc:
            logger.debug(f"[profile_asm] PV DB context unavailable: {exc}")

        # 4b. Optional PersonalVault thread context
        vault_block = ""
        if vault_thread_id and mode in ("draft", "summarize", "chat"):
            tid = vault_thread_id.strip()
            if validate_thread_exists(tid):
                try:
                    from personal_assistant.personal_vault.context import (
                        build_context as build_vault_ctx,
                    )

                    vc = build_vault_ctx(tid, mode=mode, max_chars=8000)  # type: ignore[arg-type]
                    vault_block = (
                        f"\n--- КОНТЕКСТ ПЕРЕПИСКИ ---\n"
                        f"{vc['system_prompt']}\n"
                        f"--- /КОНТЕКСТ ПЕРЕПИСКИ ---"
                    )
                except Exception as exc:
                    logger.warning(
                        f"[profile_asm] failed to load vault thread {tid}: {exc}"
                    )
                    vault_block = (
                        f"\n[Контекст переписки {tid} недоступен: {exc}]"
                    )
            else:
                logger.info(
                    f"[profile_asm] vault thread {tid!r} not in PV DB — "
                    "trying VaultIndex mail thread search"
                )
                vault_block = _load_mail_thread_context(tid)

        # 5. Assemble system prompt (profile → pv_today → base_ctx → vault_thread)
        prompt_parts = [profile_block]
        if pv_db_block:
            prompt_parts.append(pv_db_block)
        prompt_parts.append(ctx["system_prompt"])
        if vault_block:
            prompt_parts.append(vault_block)
        system_prompt = "\n".join(p for p in prompt_parts if p).strip()

        # 6. Truncate history if total exceeds token budget
        messages = list(ctx["messages"])
        max_chars = config.max_context_tokens * _CHARS_PER_TOKEN
        total = len(system_prompt) + sum(len(m.get("content", "")) for m in messages)
        if total > max_chars:
            messages = _truncate_history(messages)
            logger.warning(
                f"[profile_asm] context truncated: {total} → ~{max_chars} chars "
                f"(mode={mode}, thread={vault_thread_id})"
            )

        return {
            "system_prompt": system_prompt,
            "messages": messages,
            "vault_refs": ctx.get("vault_refs", []),
            "tool_specs": ctx.get("tool_specs", []),
        }


def _build_profile_block(profile: UserProfile, config: AIAssistantConfig) -> str:
    parts: list[str] = []
    parts.append("--- ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ---")
    parts.append(f"Имя пользователя: {profile.full_name or '—'}")
    if profile.user_email:
        parts.append(f"Email пользователя: {profile.user_email}")
    parts.append(f"Предпочтительный язык: {profile.preferred_language}")
    parts.append(f"Тон общения пользователя: {profile.communication_tone.value}")
    if profile.context_notes:
        parts.append(f"Контекстные заметки: {profile.context_notes}")

    # Delegation identity hint — resolves "мне" / "мне" in chat queries
    if profile.full_name or profile.user_email:
        name_part = profile.full_name or ""
        email_part = f" <{profile.user_email}>" if profile.user_email else ""
        identity = (name_part + email_part).strip()
        parts.append(
            f"ВАЖНО: когда пользователь пишет «мне», «на мой адрес», «от меня» или "
            f"«делегировать мне» — подставляй реальные данные: {identity}. "
            "Никогда не используй заглушки вроде [Ваше имя] или [Ваш email]."
        )
    parts.append("--- /ПРОФИЛЬ ---")

    parts.append("")
    parts.append("--- КОНФИГУРАЦИЯ АССИСТЕНТА ---")
    parts.append(f"Имя ассистента: {config.name}")
    parts.append(f"Язык ответов: {config.response_language}")
    parts.append(f"Стиль ответов: {config.tone_style.value}")
    if config.system_prompt_template:
        parts.append(f"Дополнительные инструкции: {config.system_prompt_template}")
    parts.append("--- /КОНФИГУРАЦИЯ ---")

    parts.append("")
    parts.append(f"Текущая дата и время: {format_to_msk_prompt_str()}.")
    return "\n".join(parts)


def _truncate_history(messages: list[dict]) -> list[dict]:
    """
    Keep the first message, last 3 messages, drop the middle.
    Insert a placeholder when truncation occurs.
    """
    if len(messages) <= 4:
        return messages

    first = messages[0]
    last_three = messages[-3:]
    placeholder = {
        "role": "system",
        "content": (
            "[Часть истории сообщений опущена для экономии контекста. "
            "Сохранены первое и три последних сообщения.]"
        ),
    }
    return [first, placeholder] + last_three
