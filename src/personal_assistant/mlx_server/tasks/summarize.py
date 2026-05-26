"""
Summarize task — condenses a set of emails (or any vault docs) into
key points / action items.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from personal_assistant.mlx_server.engine import MLXEngine
from personal_assistant.mlx_server.vault_index import VaultDoc, VaultIndex
from personal_assistant.services.tool_prompts import get_tool_prompts


def _summarize_system() -> str:
    """Вернуть активный системный промпт для суммаризации (пользовательский или дефолтный)."""
    return get_tool_prompts().effective_summarize()

_SUMMARIZE_PROMPT = """\
Резюмируй следующие {n} письмо(а/писем) / документ(а/ов):

1. Ключевые тезисы (3–7 пунктов)
2. Задачи к выполнению (если есть)
3. Открытые вопросы (если есть)

Документы:
{context}

Ответ дай на русском языке.
"""

_THREAD_PROMPT = """\
Следующие письма являются частью переписки на тему: "{topic}"

{context}

Предоставь:
1. Краткое резюме переписки (что происходило, в хронологическом порядке)
2. Текущий статус / итог
3. Мои задачи к выполнению
4. Ключевые участники

Ответ дай на русском языке.
"""


@dataclass
class SummaryResult:
    topic: str
    summary: str
    doc_count: int
    source_titles: list[str] = field(default_factory=list)


def summarize_docs(
    docs: list[VaultDoc],
    engine: MLXEngine,
    index: VaultIndex,
    topic: str = "documents",
    max_tokens: int = 768,
) -> SummaryResult:
    """Summarize an arbitrary list of vault docs."""
    logger.info(f"Summarizing {len(docs)} docs on topic: {topic!r}")

    context = index.build_context(docs, max_chars=8_000)
    prompt = _SUMMARIZE_PROMPT.format(n=len(docs), context=context)

    summary = engine.ask(
        question=prompt,
        system=_summarize_system(),
        max_tokens=max_tokens,
        temperature=0.3,
    )

    return SummaryResult(
        topic=topic,
        summary=summary.strip(),
        doc_count=len(docs),
        source_titles=[d.title for d in docs],
    )


def summarize_thread(
    topic: str,
    engine: MLXEngine,
    index: VaultIndex,
    top_k: int = 15,
    max_tokens: int = 768,
) -> SummaryResult:
    """
    Find emails related to a topic/thread and summarize the conversation.

    Args:
        topic: subject or keywords identifying the thread
        engine: MLXEngine instance
        index: loaded VaultIndex
        top_k: max number of emails to include
        max_tokens: max response length
    """
    logger.info(f"Summarizing thread: {topic!r}")

    docs = index.get_thread(topic, top_k=top_k)
    if not docs:
        return SummaryResult(
            topic=topic,
            summary=f"No emails found related to '{topic}'.",
            doc_count=0,
        )

    # Sort by date if available
    docs.sort(key=lambda d: d.date or "")

    context = index.build_context(docs, max_chars=8_000)
    prompt = _THREAD_PROMPT.format(topic=topic, context=context)

    summary = engine.ask(
        question=prompt,
        system=_summarize_system(),
        max_tokens=max_tokens,
        temperature=0.3,
    )

    return SummaryResult(
        topic=topic,
        summary=summary.strip(),
        doc_count=len(docs),
        source_titles=[d.title for d in docs],
    )


def summarize_contact(
    email: str,
    engine: MLXEngine,
    index: VaultIndex,
    max_tokens: int = 512,
) -> SummaryResult:
    """
    Summarize all correspondence with a specific contact.
    """
    logger.info(f"Summarizing correspondence with: {email}")

    docs = index.get_contact_mails(email, top_k=20)
    if not docs:
        return SummaryResult(
            topic=f"Correspondence with {email}",
            summary=f"No emails found from {email}.",
            doc_count=0,
        )

    return summarize_docs(
        docs=docs,
        engine=engine,
        index=index,
        topic=f"Correspondence with {email}",
        max_tokens=max_tokens,
    )
