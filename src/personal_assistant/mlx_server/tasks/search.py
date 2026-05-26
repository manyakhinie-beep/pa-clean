"""
Search task — finds vault documents relevant to a query using keyword
pre-filtering + LLM re-ranking and answer synthesis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

from personal_assistant.mlx_server.engine import MLXEngine
from personal_assistant.mlx_server.vault_index import VaultIndex

_SEARCH_SYSTEM = (
    "Ты персональный ИИ-ассистент с доступом к заметкам, письмам и календарю пользователя. "
    "Отвечай кратко и только на основе предоставленного контекста. "
    "Всегда отвечай на русском языке. "
    "Если ответа в контексте нет — прямо скажи об этом."
)

_SEARCH_PROMPT = """\
Пользователь ищет информацию в своём персональном хранилище (письма, события календаря, контакты).

Поисковый запрос: {query}

Найденные документы:
{context}

На основе этих документов ответь на запрос. Будь конкретен, при необходимости ссылайся на названия документов.
Ответ дай на русском языке.
"""


@dataclass
class SearchResult:
    query: str
    answer: str
    source_titles: list[str]
    doc_count: int


def search(
    query: str,
    engine: MLXEngine,
    index: VaultIndex,
    sections: Optional[list[str]] = None,
    top_k: int = 8,
    max_tokens: int = 512,
) -> SearchResult:
    """
    Search vault for query, then synthesize an answer with the LLM.

    Args:
        query: natural language search query
        engine: MLXEngine instance
        index: loaded VaultIndex
        sections: limit search to specific sections (None = all)
        top_k: number of candidate docs to pass to LLM
        max_tokens: max response length
    """
    logger.info(f"Search: {query!r}")

    docs = index.search(query, sections=sections, top_k=top_k)
    if not docs:
        return SearchResult(
            query=query,
            answer="No relevant documents found in the vault.",
            source_titles=[],
            doc_count=0,
        )

    context = index.build_context(docs)
    prompt = _SEARCH_PROMPT.format(query=query, context=context)

    answer = engine.ask(
        question=prompt,
        system=_SEARCH_SYSTEM,
        max_tokens=max_tokens,
        temperature=0.2,
    )

    return SearchResult(
        query=query,
        answer=answer.strip(),
        source_titles=[d.title for d in docs],
        doc_count=len(docs),
    )
