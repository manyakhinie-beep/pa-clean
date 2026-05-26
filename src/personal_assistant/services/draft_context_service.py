"""
Thread-Aware Draft Context Service (Stage 4 / Research Stage 4).

Builds a rich context object for generating draft email replies.
Instead of just passing subject → chat, collects the full email thread
from the vault, identifies the user's previous replies, extracts key facts,
and assembles a ready-to-use context_prompt string for the MLX chat model.

Public API
----------
build_draft_context(item_id, vault_path, my_email, mlx_engine=None) -> dict
    Returns a dict with the following keys:
    - item_id          : str
    - subject          : str
    - sender           : str
    - sender_email     : str
    - thread_id        : str | None
    - thread_messages  : list[ThreadMessage]  — chronological, role in/out
    - thread_summary   : str                  — MLX or rule-based
    - key_facts        : list[str]            — extracted bullet points
    - my_previous_replies : list[dict]        — {date, body, subject}
    - draft_hint       : str                  — context-aware hint string
    - context_prompt   : str                  — full prompt ready for /api/chat/send

ThreadMessage structure (as dict):
    - role    : "incoming" | "outgoing"
    - sender  : str
    - date    : str    (ISO)
    - subject : str
    - body    : str    (first 1500 chars)
    - is_mine : bool

Graceful degradation
--------------------
- Works without vault (returns minimal context from item_id alone)
- Works without MLX engine (rule-based thread_summary and draft_hint)
- Works without thread_id (single-message context)
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MSG_BODY_LIMIT = 1_500     # chars per message body included in context
_THREAD_MSG_LIMIT = 10      # max messages to include in thread context
_KEY_FACTS_LIMIT = 8        # max key_facts items to extract


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """NFC-normalise + strip whitespace."""
    return unicodedata.normalize("NFC", (text or "").strip())


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from body. Returns (meta_dict, body_str)."""
    if not text.startswith("---"):
        return {}, text.strip()
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text.strip()
    yaml_text = text[3:end]
    try:
        import yaml as _yaml
        fm = _yaml.safe_load(yaml_text) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        # Fallback: line-by-line key: value parser for malformed YAML
        # (e.g. unquoted values containing ": " like "Re: Subject")
        fm = {}
        for line in yaml_text.splitlines():
            if ": " in line:
                k, _, v = line.partition(": ")
                k = k.strip()
                if k and not k.startswith("#"):
                    v = v.strip().strip('"').strip("'")
                    fm[k] = v
    return fm, text[end + 4:].strip()


def _extract_email(raw: str) -> str:
    """Extract bare email address from 'Name <email>' or raw string."""
    m = re.search(r"<([^>]+)>", raw or "")
    if m:
        return m.group(1).strip().lower()
    if "@" in (raw or ""):
        return raw.strip().lower()
    return ""


def _clean_body(text: str, max_chars: int = _MSG_BODY_LIMIT) -> str:
    """Strip tracking pixels, base64 blobs, HTML tags; truncate to max_chars."""
    text = _norm(text)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Remove base64 blobs
    text = re.sub(r"[A-Za-z0-9+/]{60,}={0,2}", "[...base64...]", text)
    # Collapse runs of blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove markdown table syntax
    text = re.sub(r"\|[^\n]+\|", "", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…[сокращено]"
    return text


def _email_matches(raw: str, my_email: str) -> bool:
    """Return True if raw sender string matches my_email (case-insensitive)."""
    if not my_email:
        return False
    cleaned = _extract_email(raw) or raw.strip().lower()
    return cleaned == my_email.lower()


# ---------------------------------------------------------------------------
# Vault scanning
# ---------------------------------------------------------------------------

def _scan_vault_for_thread(
    vault_path: Path,
    thread_id: str,
    item_id: str,
) -> list[dict]:
    """
    Scan vault mail/*.md files for messages belonging to thread_id.
    Returns list of raw dicts with keys: id, subject, sender_raw, sender_email,
    date, body, thread_id, path.
    Falls back to scanning all .md files if mail/ subfolder doesn't exist.
    """
    results: list[dict] = []

    if not vault_path or not vault_path.exists():
        return results

    # Search mail/ first, then root if nothing found
    search_roots: list[Path] = []
    mail_dir = vault_path / "mail"
    if mail_dir.exists():
        search_roots.append(mail_dir)
    search_roots.append(vault_path)

    seen_paths: set[Path] = set()

    for search_root in search_roots:
        for md_path in sorted(search_root.rglob("*.md")):
            if md_path in seen_paths:
                continue
            seen_paths.add(md_path)
            try:
                raw = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            fm, body = _parse_frontmatter(raw)
            if not isinstance(fm, dict):
                continue

            doc_thread = str(fm.get("thread_id") or "").strip()
            doc_id = str(fm.get("id") or md_path.stem).strip()
            doc_type = str(fm.get("type") or "").lower()

            # Include if thread_id matches OR is the item itself (no thread)
            if doc_thread != thread_id and doc_id != item_id:
                continue

            sender_raw = str(
                fm.get("sender_name") or fm.get("sender") or fm.get("from") or ""
            ).strip()
            sender_email_raw = str(fm.get("from") or fm.get("sender_email") or "").strip()

            results.append({
                "id": doc_id,
                "subject": _norm(str(fm.get("subject") or fm.get("title") or md_path.stem)),
                "sender_raw": sender_raw,
                "sender_email": _extract_email(sender_email_raw) or _extract_email(sender_raw),
                "date": str(fm.get("date") or ""),
                "body": _clean_body(body),
                "thread_id": doc_thread,
                "path": str(md_path),
                "doc_type": doc_type,
            })

    return results


def _find_doc_in_vault_by_id(item_id: str, vault_path: Path) -> Optional[dict]:
    """
    Scan vault .md files to find one whose frontmatter id == item_id (or stem == item_id).
    Used as fallback when the shared VaultIndex is not available.
    """
    if not vault_path or not vault_path.exists():
        return None

    search_roots: list[Path] = []
    mail_dir = vault_path / "mail"
    if mail_dir.exists():
        search_roots.append(mail_dir)
    search_roots.append(vault_path)
    seen: set[Path] = set()

    for root in search_roots:
        for md_path in sorted(root.rglob("*.md")):
            if md_path in seen:
                continue
            seen.add(md_path)
            try:
                raw = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, body = _parse_frontmatter(raw)
            if not isinstance(fm, dict):
                continue
            doc_id = str(fm.get("id") or md_path.stem).strip()
            if doc_id != item_id:
                continue
            sender_raw = str(
                fm.get("sender_name") or fm.get("sender") or fm.get("from") or ""
            ).strip()
            sender_email_raw = str(fm.get("from") or fm.get("sender_email") or "").strip()
            return {
                "id": doc_id,
                "subject": _norm(str(fm.get("subject") or fm.get("title") or md_path.stem)),
                "sender_raw": sender_raw,
                "sender_email": _extract_email(sender_email_raw) or _extract_email(sender_raw),
                "date": str(fm.get("date") or ""),
                "body": _clean_body(body),
                "thread_id": str(fm.get("thread_id") or "").strip(),
                "path": str(md_path),
                "doc_type": str(fm.get("type") or "").lower(),
            }
    return None


def _try_get_index_doc(item_id: str) -> Optional[dict]:
    """Try to get a single doc from the shared VaultIndex by item_id."""
    try:
        from personal_assistant.mlx_server import server as _srv
        idx = getattr(_srv.state, "index", None)
        if idx is None:
            return None
        for doc in idx.docs:
            doc_id = str(doc.frontmatter.get("id") or doc.path.stem).strip()
            if doc_id == item_id:
                fm = doc.frontmatter
                sender_raw = str(
                    fm.get("sender_name") or fm.get("sender") or fm.get("from") or ""
                ).strip()
                sender_email_raw = str(fm.get("from") or fm.get("sender_email") or "").strip()
                return {
                    "id": doc_id,
                    "subject": _norm(str(fm.get("subject") or fm.get("title") or doc.path.stem)),
                    "sender_raw": sender_raw,
                    "sender_email": _extract_email(sender_email_raw) or _extract_email(sender_raw),
                    "date": str(fm.get("date") or ""),
                    "body": _clean_body(doc.content),
                    "thread_id": str(fm.get("thread_id") or "").strip(),
                    "path": str(doc.path),
                    "doc_type": str(fm.get("type") or "").lower(),
                }
    except Exception as exc:
        logger.debug(f"[draft_ctx] VaultIndex lookup failed: {exc}")
    return None


# ---------------------------------------------------------------------------
# Key-fact extraction (rule-based, no MLX required)
# ---------------------------------------------------------------------------

_FACT_PATTERNS: list[tuple[str, str]] = [
    (r"дедлайн[:\s]+([^\n\.]{5,60})", "Дедлайн"),
    (r"срок[:\s]+([^\n\.]{5,60})", "Срок"),
    (r"до\s+(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)", "До"),
    (r"сумма[:\s]+([^\n\.]{3,40})", "Сумма"),
    (r"amount[:\s]+([^\n\.]{3,40})", "Amount"),
    (r"пожалуйста[,\s]+([^\.]{10,80})", "Просьба"),
    (r"прошу[,\s]+([^\.]{10,80})", "Просьба"),
    (r"необходимо[,\s]+([^\.]{10,80})", "Необходимо"),
    (r"встреча[:\s]+([^\n\.]{5,60})", "Встреча"),
    (r"meeting[:\s]+([^\n\.]{5,60})", "Meeting"),
    (r"(?:звонок|созвон)[:\s]+([^\n\.]{5,60})", "Созвон"),
]


def _extract_key_facts(messages: list[dict]) -> list[str]:
    """Extract up to _KEY_FACTS_LIMIT bullet-point facts from thread messages."""
    facts: list[str] = []
    seen: set[str] = set()

    for msg in messages:
        body = msg.get("body", "")
        for pattern, label in _FACT_PATTERNS:
            for m in re.finditer(pattern, body, re.IGNORECASE):
                fact = f"{label}: {m.group(1).strip()}"
                normalized = fact.lower()
                if normalized not in seen:
                    seen.add(normalized)
                    facts.append(fact)
                if len(facts) >= _KEY_FACTS_LIMIT:
                    return facts
    return facts


# ---------------------------------------------------------------------------
# Rule-based thread summary (fallback when MLX not available)
# ---------------------------------------------------------------------------

def _rule_thread_summary(messages: list[dict], subject: str) -> str:
    """Build a brief thread summary without MLX."""
    if not messages:
        return f"Тред «{subject}» не содержит истории писем."

    n = len(messages)
    senders = list({m.get("sender_raw") or m.get("sender_email") or "?"
                    for m in messages if not m.get("is_mine")})
    names = ", ".join(senders[:3]) + ("..." if len(senders) > 3 else "")
    dates = [m.get("date", "") for m in messages if m.get("date")]
    date_range = ""
    if dates:
        date_range = f" с {dates[0][:10]} по {dates[-1][:10]}" if len(dates) > 1 else f" от {dates[0][:10]}"

    outgoing = sum(1 for m in messages if m.get("is_mine"))
    incoming = n - outgoing

    parts = [f"Переписка по теме «{subject}»{date_range}."]
    parts.append(f"Всего {n} {'письмо' if n == 1 else 'письма' if 2 <= n <= 4 else 'писем'} "
                 f"({incoming} входящих, {outgoing} исходящих).")
    if names:
        parts.append(f"Участники: {names}.")
    return " ".join(parts)


def _mlx_thread_summary(messages: list[dict], subject: str, engine: Any) -> str:
    """Generate thread summary via MLX. Falls back to rule-based on error."""
    try:
        snippet_parts = []
        for msg in messages[-5:]:  # last 5 messages for summary context
            role = "Я" if msg.get("is_mine") else (msg.get("sender_raw") or "?")
            date = (msg.get("date") or "")[:10]
            body_snippet = (msg.get("body") or "")[:400]
            snippet_parts.append(f"[{date}] {role}: {body_snippet}")

        thread_text = "\n\n".join(snippet_parts)
        prompt = (
            f"Кратко опиши суть этой переписки по теме «{subject}» в 2–3 предложениях. "
            f"Укажи ключевые факты, решения и открытые вопросы.\n\n"
            f"ПЕРЕПИСКА:\n{thread_text}"
        )
        result = engine.ask(
            question=prompt,
            system="Ты помощник, суммаризирующий переписку. Отвечай кратко, на русском.",
            max_tokens=200,
            temperature=0.2,
        )
        return result.strip() if result else _rule_thread_summary(messages, subject)
    except Exception as exc:
        logger.debug(f"[draft_ctx] MLX summary failed ({exc}), using rule-based")
        return _rule_thread_summary(messages, subject)


# ---------------------------------------------------------------------------
# Context prompt assembler
# ---------------------------------------------------------------------------

def _build_context_prompt(
    subject: str,
    sender: str,
    messages: list[dict],
    my_previous_replies: list[dict],
    thread_summary: str,
    key_facts: list[str],
) -> str:
    """
    Build the full context_prompt string to be sent to /api/chat/send.
    This replaces the bare 'Составь черновик ответа...' message.
    """
    lines: list[str] = []

    lines.append(f"Составь черновик ответа на письмо от {sender} по теме «{subject}».")
    lines.append("")
    lines.append("═══ КОНТЕКСТ ПЕРЕПИСКИ ═══")
    lines.append(thread_summary)

    if key_facts:
        lines.append("")
        lines.append("Ключевые факты из переписки:")
        for fact in key_facts:
            lines.append(f"  • {fact}")

    if messages:
        lines.append("")
        lines.append("Хронология писем:")
        for msg in messages[-_THREAD_MSG_LIMIT:]:
            role_label = "Я (исходящее)" if msg.get("is_mine") else f"{msg.get('sender_raw') or '?'}"
            date_str = (msg.get("date") or "")[:10]
            body_snip = (msg.get("body") or "")[:600]
            lines.append(f"──── [{date_str}] {role_label} ────")
            lines.append(body_snip)

    if my_previous_replies:
        lines.append("")
        lines.append("Мои предыдущие ответы в этом треде:")
        for rep in my_previous_replies[-3:]:
            date_str = (rep.get("date") or "")[:10]
            body_snip = (rep.get("body") or "")[:400]
            lines.append(f"──── [{date_str}] ────")
            lines.append(body_snip)

    lines.append("")
    lines.append("═══ ЗАДАНИЕ ═══")
    lines.append(
        "Напиши профессиональный ответ на русском языке. "
        "Учти всю историю переписки. Начни сразу с приветствия."
    )

    return "\n".join(lines)


def _build_draft_hint(
    messages: list[dict],
    key_facts: list[str],
    my_previous_replies: list[dict],
) -> str:
    """Build a short natural-language hint string shown in the UI."""
    parts = []
    n = len(messages)
    if n > 1:
        parts.append(f"Тред: {n} писем")
    if my_previous_replies:
        parts.append(f"{len(my_previous_replies)} моих ответа")
    if key_facts:
        parts.append(f"{len(key_facts)} ключевых фактов")
    return " · ".join(parts) if parts else "Контекст из vault"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_draft_context(
    item_id: str,
    vault_path: Optional[Path] = None,
    my_email: str = "",
    mlx_engine: Any = None,
) -> dict:
    """
    Build full thread context for draft reply generation.

    Parameters
    ----------
    item_id : str
        Inbox item ID (matches frontmatter ``id:`` field or file stem).
    vault_path : Path | None
        Root of PersonalVault. If None, tries VaultIndex from app state.
    my_email : str
        User's own email address, used to identify outgoing messages.
    mlx_engine : MLXEngine | None
        Optional MLX engine for LLM-based thread summarization.

    Returns
    -------
    dict with keys:
        item_id, subject, sender, sender_email, thread_id,
        thread_messages, thread_summary, key_facts,
        my_previous_replies, draft_hint, context_prompt,
        message_count.
    """
    # Step 1: Find the target item
    target_doc = _try_get_index_doc(item_id)

    # Fallback: if VaultIndex is not loaded (e.g., in tests or cold start),
    # scan the vault filesystem directly to find the target document.
    if target_doc is None and vault_path:
        target_doc = _find_doc_in_vault_by_id(item_id, vault_path)

    subject = "Без темы"
    sender = ""
    sender_email = ""
    thread_id = ""

    if target_doc:
        subject = target_doc.get("subject") or "Без темы"
        sender = target_doc.get("sender_raw") or target_doc.get("sender_email") or ""
        sender_email = target_doc.get("sender_email") or ""
        thread_id = target_doc.get("thread_id") or ""

    # Step 2: Scan vault for thread messages
    thread_docs: list[dict] = []
    if vault_path and thread_id:
        thread_docs = _scan_vault_for_thread(vault_path, thread_id, item_id)
    elif vault_path and not thread_id and target_doc:
        # No thread_id — include only the target item
        thread_docs = [target_doc]
    elif target_doc:
        thread_docs = [target_doc]

    # Step 3: Sort chronologically, mark is_mine
    thread_docs.sort(key=lambda d: d.get("date") or "")
    for msg in thread_docs:
        msg["is_mine"] = _email_matches(
            msg.get("sender_email") or msg.get("sender_raw") or "",
            my_email,
        )

    # Annotate role for returned structure
    thread_messages: list[dict] = []
    for msg in thread_docs:
        thread_messages.append({
            "role": "outgoing" if msg["is_mine"] else "incoming",
            "sender": msg.get("sender_raw") or msg.get("sender_email") or "?",
            "sender_email": msg.get("sender_email") or "",
            "date": msg.get("date") or "",
            "subject": msg.get("subject") or subject,
            "body": msg.get("body") or "",
            "is_mine": msg["is_mine"],
        })

    # Step 4: My previous replies
    my_previous_replies = [
        {"date": m["date"], "body": m["body"], "subject": m["subject"]}
        for m in thread_messages
        if m["is_mine"]
    ]

    # Step 5: Key facts
    key_facts = _extract_key_facts(thread_docs)

    # Step 6: Thread summary
    if mlx_engine and thread_messages:
        thread_summary = _mlx_thread_summary(thread_messages, subject, mlx_engine)
    else:
        thread_summary = _rule_thread_summary(thread_messages, subject)

    # Step 7: Draft hint
    draft_hint = _build_draft_hint(thread_messages, key_facts, my_previous_replies)

    # Step 8: Full context prompt
    context_prompt = _build_context_prompt(
        subject=subject,
        sender=sender,
        messages=thread_messages,
        my_previous_replies=my_previous_replies,
        thread_summary=thread_summary,
        key_facts=key_facts,
    )

    return {
        "item_id": item_id,
        "subject": subject,
        "sender": sender,
        "sender_email": sender_email,
        "thread_id": thread_id,
        "thread_messages": thread_messages,
        "thread_summary": thread_summary,
        "key_facts": key_facts,
        "my_previous_replies": my_previous_replies,
        "draft_hint": draft_hint,
        "context_prompt": context_prompt,
        "message_count": len(thread_messages),
    }
