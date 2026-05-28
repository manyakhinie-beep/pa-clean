"""
Draft reply task — generates a draft response to an email or thread.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from personal_assistant.mlx_server.engine import MLXEngine
from personal_assistant.mlx_server.vault_index import VaultDoc, VaultIndex
from personal_assistant.services.date_anchors import build_date_anchor_block
from personal_assistant.services.tool_prompts import get_tool_prompts

# ---------------------------------------------------------------------------
# Emoji / special-character stripping
# ---------------------------------------------------------------------------

# Unicode emoji ranges: Miscellaneous Symbols, Dingbats, Emoticons, etc.
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Misc symbols & pictographs
    "\U0001F680-\U0001F6FF"  # Transport & map symbols
    "\U0001F700-\U0001F77F"  # Alchemical symbols
    "\U0001F780-\U0001F7FF"  # Geometric shapes extended
    "\U0001F800-\U0001F8FF"  # Supplemental arrows-C
    "\U0001F900-\U0001F9FF"  # Supplemental symbols & pictographs
    "\U0001FA00-\U0001FA6F"  # Chess symbols
    "\U0001FA70-\U0001FAFF"  # Symbols and pictographs extended-A
    "\U00002702-\U000027B0"  # Dingbats
    "\U000024C2-\U0001F251"  # Enclosed characters
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """Remove emoji characters from *text*, collapsing leftover double-spaces."""
    cleaned = _EMOJI_RE.sub("", text)
    # Collapse any double spaces or trailing spaces left by removed emojis
    cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned.strip()


def _draft_system() -> str:
    """Вернуть активный системный промпт для черновика (пользовательский или дефолтный)."""
    return get_tool_prompts().effective_draft()

_DRAFT_PROMPT = """\
Напиши черновик ответа на следующее письмо(а).

{instructions_block}

Оригинальное письмо(а):
{context}

Напиши ответ. Начни сразу с приветствия.
"""

_THREAD_DRAFT_PROMPT = """\
Напиши черновик ответа на переписку по теме "{topic}".

Контекст переписки (в хронологическом порядке):
{context}

{instructions_block}

Напиши ответ. Начни сразу с приветствия.
"""


@dataclass
class DraftResult:
    subject: str
    draft: str
    based_on: list[str]  # source doc titles


def draft_reply(
    doc: VaultDoc,
    engine: MLXEngine,
    index: VaultIndex,
    instructions: Optional[str] = None,
    tone: str = "professional",
    max_tokens: int = 512,
) -> DraftResult:
    """
    Draft a reply to a single email vault doc.

    Args:
        doc: the VaultDoc (mail-message) to reply to
        engine: MLXEngine instance
        index: loaded VaultIndex
        instructions: optional user instructions ("decline politely", "ask for deadline", etc.)
        tone: "professional", "friendly", "brief"
        max_tokens: max response length
    """
    logger.info(f"Drafting reply to: {doc.title!r}")

    instructions_block = ""
    if instructions:
        instructions_block = f"Инструкции: {instructions}\n"
    instructions_block += f"Тон: {tone}"

    context = index.build_context([doc], max_chars=4_000)
    # Date anchors — pre-compute «сегодня / дата письма / срок» so the
    # model uses them verbatim instead of computing «через 2 недели» on
    # its own (the classic source of hallucinated dates in drafts).
    anchors = build_date_anchor_block(
        email_date=getattr(doc, "date", None),
        email_text=getattr(doc, "content", None),
    )
    prompt = anchors + _DRAFT_PROMPT.format(
        instructions_block=instructions_block,
        context=context,
    )

    draft = engine.ask(
        question=prompt,
        system=_draft_system(),
        max_tokens=max_tokens,
        temperature=0.4,
    )

    return DraftResult(
        subject=f"Re: {doc.title}",
        draft=strip_emoji(draft.strip()),
        based_on=[doc.title],
    )


def draft_reply_to_thread(
    topic: str,
    engine: MLXEngine,
    index: VaultIndex,
    instructions: Optional[str] = None,
    tone: str = "professional",
    top_k: int = 8,
    max_tokens: int = 512,
) -> DraftResult:
    """
    Draft a reply to an email thread identified by topic/subject keywords.

    Args:
        topic: subject or keywords identifying the thread
        instructions: optional user instructions
        tone: "professional", "friendly", "brief"
        top_k: max number of thread messages to include as context
    """
    logger.info(f"Drafting reply to thread: {topic!r}")

    docs = index.get_thread(topic, top_k=top_k)
    if not docs:
        return DraftResult(
            subject=f"Re: {topic}",
            draft=f"Could not find any emails related to '{topic}'.",
            based_on=[],
        )

    docs.sort(key=lambda d: d.date or "")
    context = index.build_context(docs, max_chars=6_000)

    instructions_block = ""
    if instructions:
        instructions_block = f"Инструкции: {instructions}\n"
    instructions_block += f"Тон: {tone}"

    prompt = _THREAD_DRAFT_PROMPT.format(
        topic=topic,
        context=context,
        instructions_block=instructions_block,
    )

    draft = engine.ask(
        question=prompt,
        system=_draft_system(),
        max_tokens=max_tokens,
        temperature=0.4,
    )

    return DraftResult(
        subject=f"Re: {topic}",
        draft=strip_emoji(draft.strip()),
        based_on=[d.title for d in docs],
    )


def draft_reply_by_path(
    md_path: Path,
    engine: MLXEngine,
    index: VaultIndex,
    instructions: Optional[str] = None,
    tone: str = "professional",
    max_tokens: int = 512,
) -> DraftResult:
    """Draft a reply to the email at a specific vault file path."""
    doc = next((d for d in index.docs if d.path == md_path), None)
    if doc is None:
        raise FileNotFoundError(f"Vault doc not found: {md_path}")
    return draft_reply(doc, engine, index, instructions, tone, max_tokens)
