"""
VaultIndex — reads and indexes all .md files from the vault.

Parses YAML frontmatter and full content.
Search: BM25 (rank-bm25) — на порядок быстрее наивного keyword scan.
Cache:  pickle-кэш индекса в vault/.index_cache.pkl — мгновенная загрузка
        при повторных запусках. Инвалидируется автоматически если vault
        изменился (сравниваем mtime последнего .md файла).
"""

from __future__ import annotations

import hashlib
import pickle
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from personal_assistant.config import settings

_CACHE_FILENAME = ".index_cache.pkl"
_CACHE_VERSION = 2  # увеличь при изменении структуры VaultDoc


# ---------------------------------------------------------------------------
# Tokenizer для BM25 (простой, без внешних зависимостей)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[^\w]", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Привести к нижнему регистру и разбить на токены (слова)."""
    return [t for t in _TOKEN_RE.sub(" ", text.lower()).split() if len(t) > 1]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class VaultDoc:
    """A single parsed vault document."""

    path: Path
    section: str  # "calendar" | "mail" | "contacts"
    frontmatter: dict
    content: str  # full markdown body (after frontmatter)
    raw: str  # entire file text

    # Convenience accessors
    @property
    def title(self) -> str:
        return self.frontmatter.get("title", self.path.stem)

    @property
    def doc_type(self) -> str:
        return self.frontmatter.get("type", self.section)

    @property
    def date(self) -> Optional[str]:
        raw = (
            self.frontmatter.get("date")
            or self.frontmatter.get("start")
            or self.frontmatter.get("created")
        )
        # PyYAML парсит даты автоматически в datetime.date/datetime → приводим к строке
        return str(raw) if raw is not None else None

    @property
    def tags(self) -> list[str]:
        t = self.frontmatter.get("tags", [])
        if isinstance(t, str):
            return [t]
        return list(t)

    @property
    def attachments(self) -> list[str]:
        """Return list of attachment filenames from frontmatter 'attachments' field."""
        a = self.frontmatter.get("attachments", [])
        if isinstance(a, str):
            return [a.strip()] if a.strip() else []
        if isinstance(a, list):
            return [str(x).strip() for x in a if str(x).strip()]
        return []

    @property
    def sender_email(self) -> Optional[str]:
        # Plain email in "from" or "sender_email" frontmatter fields
        for key in ("from", "sender_email"):
            val = self.frontmatter.get(key, "")
            if val and isinstance(val, str) and "@" in val:
                return val.strip()
        # Obsidian wikilink format: [[contacts/email@domain.com]]
        s = str(self.frontmatter.get("sender", ""))
        m = re.search(r"\[\[contacts/(.+?)]]", s)
        return m.group(1) if m else None

    def short_summary(self, max_chars: int = 300) -> str:
        """Return a brief text summary for LLM context lists.
        Includes classification tags so they appear in @mention snippets and chat context.
        """
        tags = self.tags
        tag_prefix = ("[" + ", ".join(tags) + "] ") if tags else ""
        text = self.content.strip()
        # Remove markdown table syntax and headers for cleaner summary
        text = re.sub(r"\|[^\n]+\|", "", text)
        text = re.sub(r"^#{1,4} .+$", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        combined = tag_prefix + text
        return combined[:max_chars]

    def ui_preview(self, max_chars: int = 150) -> str:
        """Return a clean plain-text preview for WebUI display.

        Unlike short_summary(), this method:
        - Does NOT prepend tag/type prefixes (no "[почта]", "[контакт]" etc.)
        - Strips markdown formatting so the frontend shows readable prose
        """
        text = self.content.strip()
        # Remove YAML-like key: value lines that leak into body (e.g. "type: email")
        text = re.sub(r"^[a-z_]+:\s*.+$", "", text, flags=re.MULTILINE)
        # Remove markdown headers (# ## ###)
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
        # Remove blockquote prefixes
        text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
        # Remove bold / italic / strikethrough
        text = re.sub(r"\*{1,3}([^*\n]+)\*{1,3}", r"\1", text)
        text = re.sub(r"_{1,3}([^_\n]+)_{1,3}", r"\1", text)
        text = re.sub(r"~~([^~\n]+)~~", r"\1", text)
        # Remove inline code and code fences
        text = re.sub(r"`{1,3}[^`\n]*`{1,3}", "", text)
        # Remove markdown links → keep label
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
        # Remove markdown images
        text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
        # Remove horizontal rules and table syntax
        text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"\|[^\n]+\|", "", text)
        # Collapse excess whitespace into single spaces
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n+", " ", text).strip()
        return text[:max_chars]


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (meta_dict, body_str)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        logger.debug(f"YAML parse error in frontmatter: {e}")
        meta = {}
    body = text[m.end() :]
    return meta, body


# ---------------------------------------------------------------------------
# VaultIndex
# ---------------------------------------------------------------------------


class VaultIndex:
    """
    Reads all .md files from the vault and provides search/retrieval.

    Call .load() once, then use .search(), .get_by_type(), .get_contact() etc.
    """

    def __init__(self, vault_path: Optional[Path] = None) -> None:
        self.vault_path = Path(vault_path or settings.vault_path).expanduser()
        self.docs: list[VaultDoc] = []
        self._bm25 = None  # BM25Okapi instance (built lazily)
        self._bm25_pool: list[VaultDoc] = []  # docs that _bm25 was built over

    # ------------------------------------------------------------------
    # Loading (with pickle cache)
    # ------------------------------------------------------------------

    def load(
        self, sections: Optional[list[str]] = None, use_cache: bool = True
    ) -> "VaultIndex":
        """
        Load all .md files from vault.

        Args:
            sections: subset of ["calendar", "mail", "contacts"] (None = all)
            use_cache: try to load from pickle cache (invalidated on vault changes)
        """
        all_sections = sections or ["calendar", "mail", "contacts"]

        if use_cache and self._try_load_cache(all_sections):
            return self

        t0 = time.time()
        self.docs = []
        for section in all_sections:
            section_path = self.vault_path / section
            if not section_path.exists():
                logger.debug(f"Секция vault отсутствует: {section_path}")
                continue
            for md_file in sorted(section_path.rglob("*.md")):
                try:
                    raw = md_file.read_text(encoding="utf-8")
                    fm, body = _parse_frontmatter(raw)
                    self.docs.append(
                        VaultDoc(
                            path=md_file,
                            section=section,
                            frontmatter=fm,
                            content=body,
                            raw=raw,
                        )
                    )
                except Exception as e:
                    logger.warning(f"Не удалось прочитать {md_file}: {e}")

        elapsed = time.time() - t0
        logger.info(
            f"Vault индекс загружен: {len(self.docs)} docs "
            f"({self._count_by_section()}) за {elapsed:.2f}s"
        )

        if use_cache:
            self._save_cache(all_sections)

        return self

    # ------------------------------------------------------------------
    # Pickle cache
    # ------------------------------------------------------------------

    def _cache_path(self) -> Path:
        return self.vault_path / _CACHE_FILENAME

    def _vault_fingerprint(self, sections: list[str]) -> str:
        """Hash based on latest mtime of all .md files in given sections."""
        mtimes = []
        for section in sections:
            p = self.vault_path / section
            if p.exists():
                for f in p.rglob("*.md"):
                    mtimes.append(f.stat().st_mtime)
        key = f"{_CACHE_VERSION}:{sorted(sections)}:{sorted(mtimes)}"
        return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()

    def _try_load_cache(self, sections: list[str]) -> bool:
        cache = self._cache_path()
        if not cache.exists():
            return False
        try:
            with open(cache, "rb") as f:
                data = pickle.load(f)
            if data.get("fingerprint") != self._vault_fingerprint(sections):
                logger.debug("Кэш vault устарел — перестраиваем")
                return False
            self.docs = data["docs"]
            logger.info(
                f"Vault индекс загружен из кэша: {len(self.docs)} docs "
                f"({self._count_by_section()})"
            )
            return True
        except Exception as e:
            logger.debug(f"Не удалось загрузить кэш: {e}")
            return False

    def _save_cache(self, sections: list[str]) -> None:
        try:
            data = {
                "fingerprint": self._vault_fingerprint(sections),
                "docs": self.docs,
            }
            with open(self._cache_path(), "wb") as f:
                pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
            logger.debug(f"Кэш vault сохранён: {self._cache_path()}")
        except Exception as e:
            logger.warning(f"Не удалось сохранить кэш: {e}")

    def invalidate_cache(self) -> None:
        """Удалить pickle-кэш (например после sync-all)."""
        cache = self._cache_path()
        if cache.exists():
            cache.unlink()
            logger.debug("Кэш vault удалён")
        self._bm25 = None

    def _count_by_section(self) -> str:
        from collections import Counter

        c = Counter(d.section for d in self.docs)
        return ", ".join(f"{k}={v}" for k, v in c.items())

    # ------------------------------------------------------------------
    # Filtering helpers
    # ------------------------------------------------------------------

    def get_by_type(self, doc_type: str) -> list[VaultDoc]:
        """Filter by frontmatter 'type' field. e.g. get_by_type('event'), get_by_type('mail')."""
        return [d for d in self.docs if d.doc_type == doc_type]

    def get_by_section(self, section: str) -> list[VaultDoc]:
        return [d for d in self.docs if d.section == section]

    def get_mails(self) -> list[VaultDoc]:
        return self.get_by_section("mail")

    def get_events(self) -> list[VaultDoc]:
        return self.get_by_section("calendar")

    def get_contacts(self) -> list[VaultDoc]:
        return self.get_by_section("contacts")

    def get_contact(self, email: str) -> Optional["VaultDoc"]:
        """Find a single contact doc by email address. Returns None if not found."""
        email_lower = email.lower().strip()
        for doc in self.get_contacts():
            # Check frontmatter "email" field
            doc_email = str(doc.frontmatter.get("email", "")).lower().strip()
            if doc_email == email_lower:
                return doc
            # Also check filename (contacts are saved as <email>.md)
            if doc.path.stem.lower() == email_lower:
                return doc
        return None

    # ------------------------------------------------------------------
    # BM25 search
    # ------------------------------------------------------------------

    def _build_bm25(self, pool: list[VaultDoc]):
        """Построить BM25-индекс для заданного пула документов.
        Индексируем: заголовок + теги (включая classification-теги) + содержимое.
        """
        from rank_bm25 import BM25Okapi

        corpus = [
            _tokenize(
                d.title
                + " "
                + " ".join(d.tags)          # tags: urgency:urgent, category:finance, …
                + " "
                + " ".join(d.attachments)   # attachment filenames: invoice.pdf, report.xlsx
                + " "
                + d.content
            )
            for d in pool
        ]
        self._bm25 = BM25Okapi(corpus)
        self._bm25_pool = pool
        logger.debug(f"BM25 построен по {len(pool)} документам")

    def search(
        self,
        query: str,
        sections: Optional[list[str]] = None,
        top_k: int = 10,
    ) -> list[VaultDoc]:
        """
        BM25-поиск по всему vault или конкретным секциям.
        Значительно быстрее наивного keyword scan на больших коллекциях.
        Возвращает до top_k наиболее релевантных документов.
        """
        pool = (
            [d for d in self.docs if d.section in sections] if sections else self.docs
        )
        if not pool:
            return []

        # Перестраиваем BM25 если пул изменился
        if self._bm25 is None or self._bm25_pool is not pool:
            self._build_bm25(pool)

        assert self._bm25 is not None
        tokens = _tokenize(query)
        scores = self._bm25.get_scores(tokens)

        # Сортируем по убыванию score (чистый Python, без numpy)
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results: list[VaultDoc] = []
        for i, score in ranked:
            if len(results) >= top_k:
                break
            if score > 0:
                results.append(pool[i])
        return results

    # ------------------------------------------------------------------
    # Thread reconstruction
    # ------------------------------------------------------------------

    def get_thread(self, query: str, top_k: int = 10) -> list[VaultDoc]:
        """
        Find mail messages related to a subject/thread query.
        Useful for summarizing a conversation thread.
        """
        return self.search(query, sections=["mail"], top_k=top_k)

    def get_contact_mails(self, email: str, top_k: int = 20) -> list[VaultDoc]:
        """Get all mails from a specific sender email."""
        email_lower = email.lower()
        results = [d for d in self.get_mails() if email_lower in d.raw.lower()]
        return results[:top_k]

    # ------------------------------------------------------------------
    # Context builder (for LLM prompts)
    # ------------------------------------------------------------------

    def build_context(
        self,
        docs: list[VaultDoc],
        max_chars: int = 10_000,
        include_full_content: bool = False,
    ) -> str:
        """
        Concatenate doc content into a single context string for LLM.
        Truncates if total exceeds max_chars.
        """
        parts: list[str] = []
        total = 0
        for i, doc in enumerate(docs, 1):
            text = doc.raw if include_full_content else doc.content
            tags_str = (", ".join(doc.tags) + " | ") if doc.tags else ""
            header = f"--- [{i}] {doc.title} ({doc.doc_type}, {doc.date or 'no date'}) | {tags_str}---\n"
            block = header + text.strip() + "\n"
            if total + len(block) > max_chars:
                remaining = max_chars - total
                if remaining > 200:
                    parts.append(block[:remaining] + "\n[truncated]")
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict:
        from collections import Counter

        c = Counter(d.section for d in self.docs)
        total = len(self.docs)
        return {
            "total": total,
            "total_docs": total,  # alias used by tests and external consumers
            "calendar": c.get("calendar", 0),
            "mail": c.get("mail", 0),
            "contacts": c.get("contacts", 0),
        }
