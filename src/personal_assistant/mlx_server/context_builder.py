"""
Context Assembler Pipeline.

Builds the complete prompt context for every chat turn:
  1. Persona (souls.md + persona.json)
  2. User profile + preferences
  3. Current date / timezone
  4. GTD / Eisenhower rules
  5. Available tools (registry + date_calc)
  6. Vault snippets (@mentions or semantic search)
  7. Thread history (sliding window / truncation)

Returns a dict ready for MLXEngine.chat() / stream().
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger

from personal_assistant.config import settings

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_SOULS_FILE = _PROJECT_ROOT / "souls.md"
_PERSONA_FILE = _PROJECT_ROOT / "data" / "persona.json"
_GTD_FILE = _PROJECT_ROOT / "data" / "gtd_rules.json"
_EISEN_FILE = _PROJECT_ROOT / "data" / "eisenhower.json"
_TOOLS_FILE = _PROJECT_ROOT / "tools" / "registry.json"

_HISTORY_CHAR_LIMIT = 4000
_HISTORY_MSG_LIMIT = 20
_VAULT_SNIPPET_CHARS = 6000   # enough for full mail body + frontmatter
_MAX_VAULT_REFS = 5


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _load_text(path: Path) -> str:
    if path.exists():
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def _now_str() -> str:
    """Current date/time in Russian-friendly format (MSK)."""
    from personal_assistant.utils.timezone import format_to_msk_prompt_str
    return format_to_msk_prompt_str()


# ---------------------------------------------------------------------------
# Vault snippet loader
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                import yaml as _yaml

                fm = _yaml.safe_load(text[3:end]) or {}
            except Exception:
                fm = {}
            return fm, text[end + 4 :].strip()
    return {}, text.strip()


def _obj_label(fm: dict, stem: str) -> str:
    t = str(fm.get("type", "")).lower()
    title = fm.get("title") or stem
    if t in ("event", "calendar-event"):
        start = str(fm.get("start", "") or "")
        loc = fm.get("location", "")
        return f"📅 ВСТРЕЧА «{title}» [{start}]{' @ ' + loc if loc else ''}"
    if t in ("mail", "mail-message", "email"):
        sender = (
            fm.get("from")
            or fm.get("sender_name")
            or fm.get("sender_email", "")
        )
        date = str(fm.get("date", "") or "")[:10]
        return f"📧 ПИСЬМО «{title}» от {sender} [{date}]"
    if t == "contact":
        email = fm.get("email", stem)
        org = fm.get("organization", "")
        return f"👤 КОНТАКТ «{title}» <{email}>{' (' + org + ')' if org else ''}"
    return f"📄 ДОКУМЕНТ «{title}»"


def load_vault_snippets(paths: list[str]) -> list[dict]:
    """Load .md content for the given vault paths (security-checked)."""
    vault_root = settings.vault_path.resolve()
    results: list[dict] = []
    for path_str in paths[:_MAX_VAULT_REFS]:
        try:
            p = Path(path_str).resolve()
            p.relative_to(vault_root)
            if p.exists() and p.suffix == ".md":
                raw = p.read_text(encoding="utf-8", errors="replace")
                fm, body = _parse_frontmatter(raw)
                label = _obj_label(fm, p.stem)
                snippet = raw[:_VAULT_SNIPPET_CHARS]
                results.append(
                    {
                        "path": str(p),
                        "label": label,
                        "snippet": snippet,
                        "frontmatter": fm,
                    }
                )
            else:
                logger.debug(f"[context] skip vault path: {path_str}")
        except ValueError:
            logger.warning(f"[context] vault path outside root: {path_str}")
        except OSError as e:
            logger.warning(f"[context] vault read error: {path_str} — {e}")
    return results


def search_vault_for_context(query: str, top_k: int = 3) -> list[dict]:
    """BM25 search over vault for relevant snippets."""
    from personal_assistant.mlx_server.server import state

    index = state.index
    if index is None or not query.strip():
        return []
    docs = index.search(query, top_k=top_k)
    results: list[dict] = []
    for d in docs:
        raw = d.raw[:_VAULT_SNIPPET_CHARS]
        fm = d.frontmatter
        label = _obj_label(fm, d.path.stem)
        results.append(
            {
                "path": str(d.path),
                "label": label,
                "snippet": raw,
                "frontmatter": fm,
            }
        )
    return results


# ---------------------------------------------------------------------------
# History window
# ---------------------------------------------------------------------------


def trim_history(
    messages: list[dict], char_limit: int = _HISTORY_CHAR_LIMIT
) -> list[dict]:
    """
    Keep messages from the tail until *char_limit* is reached.
    Always preserve the very first system message if present.
    """
    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
    pool = messages[1:] if system_msg else messages

    total = 0
    kept: list[dict] = []
    for m in reversed(pool):
        total += len(m.get("content", ""))
        if total > char_limit and kept:
            break
        kept.append(m)
    kept.reverse()

    if system_msg:
        kept.insert(0, system_msg)

    # If we dropped messages, prepend a tiny summarisation hint (optional)
    if len(kept) < len(messages):
        logger.debug(f"[context] trimmed history {len(messages)} → {len(kept)} msgs")
    return kept[-_HISTORY_MSG_LIMIT:]


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


def load_tool_specs() -> list[dict]:
    specs: list[dict] = []
    registry = _load_json(_TOOLS_FILE)
    for t in registry.get("tools", []):
        if t.get("enabled", True):
            specs.append(
                {
                    "name": t["id"],
                    "description": t["description"],
                }
            )
    # Always inject date_calc
    from personal_assistant.mlx_server.tools.date_calc import tool_spec

    specs.append(tool_spec())
    return specs


# ---------------------------------------------------------------------------
# Thread context loader (Stage 4: Thread-Aware Draft)
# ---------------------------------------------------------------------------

_THREAD_MSG_BODY_LIMIT = 800   # chars per message body in system prompt
_THREAD_MAX_MSGS = 8           # max messages to inject


def _load_vault_thread_context(thread_id: str) -> Optional[str]:
    """
    Load all vault messages belonging to *thread_id* and format them as a
    system-prompt block so the model has full thread context for draft replies.

    Returns None if nothing is found or on any error (graceful degradation).
    """
    if not thread_id:
        return None
    try:
        from personal_assistant.mlx_server import server as _srv  # noqa: PLC0415
        idx = getattr(_srv.state, "index", None)
        if idx is None:
            return None

        # Collect messages in this thread
        msgs: list[dict] = []
        for doc in idx.docs:
            fm = doc.frontmatter
            doc_thread = str(fm.get("thread_id") or "").strip()
            if doc_thread != thread_id:
                continue
            sender_raw = str(
                fm.get("sender_name") or fm.get("sender") or fm.get("from") or ""
            ).strip()
            body = doc.content.strip()
            # strip HTML tags that leak into vault .md
            import re as _re  # noqa: PLC0415
            body = _re.sub(r"<[^>]+>", " ", body)
            body = _re.sub(r"\n{3,}", "\n\n", body).strip()
            if len(body) > _THREAD_MSG_BODY_LIMIT:
                body = body[:_THREAD_MSG_BODY_LIMIT] + "\n…[сокращено]"
            msgs.append({
                "sender": sender_raw or "?",
                "date": str(fm.get("date") or "")[:10],
                "body": body,
            })

        if not msgs:
            return None

        # Sort chronologically and keep newest N
        msgs.sort(key=lambda m: m["date"])
        msgs = msgs[-_THREAD_MAX_MSGS:]

        lines: list[str] = [
            f"\n--- ИСТОРИЯ ТРЕДА (thread_id: {thread_id}) ---",
            f"Ниже приведены {len(msgs)} сообщений этой переписки в хронологическом порядке.",
            "Используй эту историю при составлении черновика ответа.\n",
        ]
        for i, msg in enumerate(msgs, 1):
            lines.append(f"[{i}] {msg['date']} | {msg['sender']}")
            lines.append(msg["body"])
            lines.append("")
        lines.append("--- /ИСТОРИЯ ТРЕДА ---")
        return "\n".join(lines)

    except Exception as exc:
        logger.debug(f"[context_builder] thread context load failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main assembler
# ---------------------------------------------------------------------------


class ContextAssembler:
    """Builds system prompt + message list for one chat turn."""

    def __init__(self) -> None:
        self.persona = _load_json(_PERSONA_FILE)
        self.souls = _load_text(_SOULS_FILE)
        self.gtd = _load_json(_GTD_FILE)
        self.eisen = _load_json(_EISEN_FILE)
        self.tool_specs = load_tool_specs()

    # -- public API ---------------------------------------------------------

    def build(
        self,
        user_message: str,
        history: list[dict],
        context_paths: list[str],
        mode: str = "chat",
        vault_thread_id: Optional[str] = None,
    ) -> dict:
        """
        Returns {
            "system_prompt": str,
            "messages": list[dict],           # trimmed history + current user msg
            "vault_refs": list[dict],         # metadata for UI
            "tool_specs": list[dict],
        }

        vault_thread_id: when set, thread messages from the vault are injected
            into the system prompt so the model has full thread context for
            draft replies and summarisation.
        """
        # 1. Vault snippets (explicit @mentions)
        vault_refs = load_vault_snippets(context_paths)

        # 2. If no explicit refs, try semantic search for ALL modes including chat
        if not vault_refs and mode in ("search", "summarize", "draft", "chat"):
            vault_refs = search_vault_for_context(user_message, top_k=3)

        # 3. Thread context injection (Stage 4: Thread-Aware Draft)
        thread_context_block: Optional[str] = None
        if vault_thread_id:
            thread_context_block = _load_vault_thread_context(vault_thread_id)

        # 4. System prompt
        system_prompt = self._make_system_prompt(
            vault_refs, mode, thread_context_block=thread_context_block
        )

        # 5. History window
        trimmed = trim_history(history)

        # 6. Append current user message
        messages = trimmed + [{"role": "user", "content": user_message}]

        return {
            "system_prompt": system_prompt,
            "messages": messages,
            "vault_refs": vault_refs,
            "tool_specs": self.tool_specs,
        }

    # -- internals ----------------------------------------------------------

    def _make_system_prompt(
        self,
        vault_refs: list[dict],
        mode: str,
        thread_context_block: Optional[str] = None,
    ) -> str:
        parts: list[str] = []

        # Identity
        ai_name = self.persona.get("assistant_name", "Ассистент")
        ai_style = self.persona.get("assistant_style", "")
        ai_focus = self.persona.get("assistant_focus", "")
        user_name = self.persona.get("user_name", "")
        user_role = self.persona.get("user_role", "")

        parts.append(f"Ты — персональный AI-ассистент по имени {ai_name}.")
        if ai_style:
            parts.append(f"Стиль общения: {ai_style}.")
        if ai_focus:
            parts.append(f"Специализация: {ai_focus}.")
        if user_name:
            desc = f"Тебя использует {user_name}"
            if user_role:
                desc += f" ({user_role})"
            parts.append(desc + ".")

        # Date / timezone
        # Include full weekday so the model NEVER needs date_calc for "what day is today"
        parts.append(
            f"\nТекущая дата и день недели: {_now_str()}. "
            "Для вопросов о ТЕКУЩЕЙ дате, времени или дне недели — отвечай НАПРЯМУЮ "
            "из поля «Текущая дата и день недели:» выше, НЕ вызывай никакие инструменты. "
            "date_calc используй ТОЛЬКО для арифметики: «через N дней», «следующий понедельник», "
            "«сколько дней между датами» и т.п. "
            "При упоминании конкретных дат всегда возвращай формат YYYY-MM-DD."
        )

        # Souls.md
        if self.souls:
            parts.append(
                f"\n--- ПЕРСОНАЛИЗАЦИЯ ---\n{self.souls}\n--- /ПЕРСОНАЛИЗАЦИЯ ---"
            )

        # GTD / Eisenhower
        gtd_rules = self.gtd.get("rules", [])
        if gtd_rules:
            parts.append("\n--- GTD ПРАВИЛА ---")
            for r in gtd_rules:
                parts.append(
                    f"- {r.get('action', 'inbox')} (квадрант {r.get('quadrant', '?')})"
                )
            parts.append("--- /GTD ---")

        eisen_tasks = self.eisen.get("tasks", [])
        if eisen_tasks:
            parts.append("\n--- МАТРИЦА ЭЙЗЕНХАУЭРА ---")
            for t in eisen_tasks[:5]:
                parts.append(f"- [{t.get('quadrant', '?')}] {t.get('title', '')}")
            parts.append("--- /ЭЙЗЕНХАУЭР ---")

        # Tools
        if self.tool_specs:
            parts.append("\n--- ДОСТУПНЫЕ ИНСТРУМЕНТЫ ---")
            tool_names = [t["name"] for t in self.tool_specs]
            for t in self.tool_specs:
                parts.append(f"- {t['name']}: {t['description']}")
            parts.append(
                f"\nДОСТУПНЫЕ ИНСТРУМЕНТЫ: только {', '.join(tool_names)}. "
                "НЕ вызывай инструменты с другими именами (calendar, email, search, vault и т.п.) — "
                "их НЕ СУЩЕСТВУЕТ и вызов завершится ошибкой. "
                "Данные о встречах, письмах и задачах уже содержатся в блоке PERSONALVAULT выше — "
                "используй их НАПРЯМУЮ для ответа, не вызывая никаких инструментов.\n"
                "Используй date_calc ТОЛЬКО для арифметики дат (через N дней, следующий понедельник и т.п.):\n"
                '<|function_call|>{"name": "date_calc", "arguments": {"expression": "..."}}'
            )
            parts.append("--- /ИНСТРУМЕНТЫ ---")

        # Thread context (Stage 4: Thread-Aware Draft) — injected BEFORE vault refs
        # so the model sees the full thread history when composing a draft reply.
        if thread_context_block:
            parts.append(thread_context_block)

        # Calendar context — always inject upcoming vault events for chat/draft
        # so the model can answer schedule questions ("что у меня завтра?")
        # WITHOUT relying on BM25 matching the user's natural-language query
        # against event titles. Without this block the model hallucinates a
        # plausible Russian schedule when asked about an unmatched date.
        if mode in ("chat", "draft"):
            try:
                from personal_assistant.services.calendar_service import (
                    fetch_upcoming_events,
                )

                upcoming = fetch_upcoming_events(days_forward=7)
                parts.append("\n--- КАЛЕНДАРЬ: ВСЕ СОБЫТИЯ НА 7 ДНЕЙ ВПЕРЁД ---")
                if upcoming:
                    for ev in upcoming[:30]:
                        title = ev.get("title") or "(без названия)"
                        date_s = ev.get("date") or ""
                        loc = ev.get("location") or ""
                        loc_part = f" — {loc}" if loc else ""
                        parts.append(f"- {date_s}: {title}{loc_part}")
                else:
                    parts.append("(на ближайшие 7 дней событий в vault не найдено)")
                parts.append("--- /КАЛЕНДАРЬ ---")
                parts.append(
                    "Это ЕДИНСТВЕННЫЙ источник данных о расписании пользователя. "
                    "Если запрашиваемая дата НЕ упомянута в блоке КАЛЕНДАРЬ выше — "
                    "ответь честно: «на эту дату событий не запланировано» или "
                    "«в vault нет данных за этот период». НЕ ВЫДУМЫВАЙ события "
                    "(встречи, звонки, ланчи и т.п.) которых нет в блоке выше."
                )
            except Exception as exc:
                logger.debug(f"[context_builder] calendar fetch failed: {exc}")

        # Vault context
        if vault_refs:
            parts.append("\n--- КОНТЕКСТ ИЗ PERSONALVAULT ---")
            for ref in vault_refs:
                parts.append(f"\n[{ref['label']}]")
                parts.append(ref["snippet"])
            parts.append("\n--- /КОНТЕКСТ ---")

        # Mode instruction
        mode_instr = {
            "search": "Ответь кратко, с ссылками на источники из vault.",
            "summarize": "Составь структурированное резюме по предоставленным материалам.",
            "draft": "Напиши черновик ответа в деловом стиле. Предложи варианты формулировок.",
            "chat": "Отвечай на том языке, на котором задан вопрос. Будь краток и конкретен.",
        }.get(mode, "")
        if mode_instr:
            parts.append(f"\nРежим: {mode_instr}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Date helpers for vault context
# ---------------------------------------------------------------------------


def _get_date_prefix_msk(date_str: str) -> str:
    """Return YYYY-MM-DD in MSK timezone from any ISO date/datetime string.

    Handles PyYAML-parsed strings (space separator, +HH:MM offset),
    strftime-produced strings (+HHMM without colon), plain date strings,
    and naive datetimes (treated as MSK).

    Returns empty string on parse failure.
    """
    import re as _re

    from personal_assistant.utils.timezone import _MSK

    if not date_str or date_str in ("None", "unknown", "none"):
        return ""
    s = str(date_str).strip()
    if len(s) < 10 or s[4:5] != "-" or s[7:8] != "-":
        return ""
    # Plain date only
    if len(s) == 10:
        return s
    try:
        from datetime import datetime
        # Normalize PyYAML space-separator → T
        s = s.replace(" ", "T", 1)
        # Normalize +HHMM (no colon) → +HH:MM
        s = _re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", s)
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            return dt.astimezone(_MSK).date().isoformat()
        return dt.date().isoformat()
    except Exception:
        # Fall back to raw prefix — good enough for same-day comparisons
        return date_str[:10]


def _extract_time_str(date_str: str) -> str:
    """Extract HH:MM time portion from a date/datetime string."""
    if not date_str:
        return ""
    s = str(date_str)
    for sep in ("T", " "):
        if sep in s:
            time_part = s.split(sep, 1)[1][:5]
            if len(time_part) == 5 and time_part[2] == ":":
                return time_part
    return ""


# ---------------------------------------------------------------------------
# PersonalVault context (today + upcoming) — SQLite DB + VaultIndex (.md)
# ---------------------------------------------------------------------------


def load_pv_db_context(max_today: int = 20, max_upcoming: int = 7) -> str:
    """Build today's vault context from *both* data sources:

    1. PersonalVault SQLite DB  — items added via ``POST /api/v1/vault/items``
    2. VaultIndex (.md files)   — items written by sync pipeline (Mail/Calendar/Outlook)

    Both sources are normalised to the same entry tuple and deduplicated by
    ``(item_type, title_lower, date_prefix)`` so items that exist in both
    stores appear only once.

    Dates are always normalised to **MSK (Europe/Moscow)** before comparison.

    :param max_today: Max number of today's entries to include.
    :param max_upcoming: Max number of upcoming meeting entries to include.
    :returns: Formatted string block or empty string.
    """
    from datetime import timedelta

    from personal_assistant.utils.timezone import get_now_msk

    today = get_now_msk().date()           # ← MSK, not system-local
    today_str = today.isoformat()          # "YYYY-MM-DD"
    week_end = (today + timedelta(days=7)).isoformat()

    # Each entry: (item_type, time_str, subject, sender, body_snippet)
    today_entries: list[tuple[str, str, str, str, str]] = []
    # Each entry: (date_prefix, title)
    upcoming_entries: list[tuple[str, str]] = []
    # Dedup key: (item_type, title_lower, date_prefix)
    seen: set[tuple[str, str, str]] = set()

    # ------------------------------------------------------------------
    # Source 1: SQLite PersonalVault DB
    # ------------------------------------------------------------------
    try:
        from personal_assistant.personal_vault.db import list_items
        all_items = list_items(limit=300)
        for it in all_items:
            dp = _get_date_prefix_msk(it.date_iso)
            if not dp:
                continue
            key = (it.item_type, it.subject.lower(), dp)
            if key in seen:
                continue
            if dp == today_str:
                seen.add(key)
                time_str = _extract_time_str(it.date_iso)
                body = (it.full_body or "")[:150].replace("\n", " ").strip()
                today_entries.append((it.item_type, time_str, it.subject,
                                      it.sender or "", body))
            elif it.item_type == "meeting" and today_str < dp <= week_end:
                seen.add(key)
                upcoming_entries.append((dp, it.subject))
    except Exception as exc:
        logger.debug(f"[context] PV SQLite unavailable: {exc}")

    # ------------------------------------------------------------------
    # Source 2: VaultIndex (.md files — populated by sync pipeline)
    # ------------------------------------------------------------------
    try:
        from personal_assistant.mlx_server.server import state  # lazy — avoids circular import
        index = state.index
        if index is not None and index.docs:
            for doc in index.docs:
                raw_date = doc.date  # str | None (from VaultDoc.date property)
                if not raw_date:
                    continue
                dp = _get_date_prefix_msk(str(raw_date))
                if not dp:
                    continue
                # Map section → item_type
                if doc.section == "calendar":
                    item_type = "meeting"
                elif doc.section == "mail":
                    item_type = "email"
                else:
                    continue  # skip contacts etc.
                key = (item_type, doc.title.lower(), dp)  # type: ignore[assignment]
                if key in seen:
                    continue
                if dp == today_str:
                    seen.add(key)
                    time_str = _extract_time_str(str(raw_date))
                    if item_type == "meeting":
                        loc = doc.frontmatter.get("location", "")
                        body_src = f"Место: {loc}" if loc else doc.content.strip()[:150]
                        body = body_src.replace("\n", " ").strip()
                    else:
                        sender = doc.sender_email or str(doc.frontmatter.get("from", ""))
                        body = doc.content.strip()[:120].replace("\n", " ")
                        today_entries.append((item_type, time_str, doc.title,
                                              sender, body))
                        continue
                    today_entries.append((item_type, time_str, doc.title, "", body))
                elif item_type == "meeting" and today_str < dp <= week_end:
                    seen.add(key)
                    upcoming_entries.append((dp, doc.title))
    except Exception as exc:
        logger.debug(f"[context] VaultIndex unavailable: {exc}")

    if not today_entries and not upcoming_entries:
        return ""

    # Sort by time
    today_entries.sort(key=lambda x: x[1] or "")
    upcoming_entries.sort(key=lambda x: x[0])

    lines: list[str] = ["--- PERSONALVAULT: АКТУАЛЬНЫЙ КОНТЕКСТ ---"]

    if today_entries:
        lines.append(f"\nСЕГОДНЯ ({today_str}):")
        for item_type, time_str, subject, sender, body in today_entries[:max_today]:
            t = f"[{time_str}] " if time_str else ""
            if item_type == "meeting":
                lines.append(f"  📅 {t}ВСТРЕЧА: «{subject}»")
                if body:
                    lines.append(f"       Детали: {body}")
            else:
                sender_part = f" от {sender}" if sender else ""
                lines.append(f"  📧 {t}ПИСЬМО: «{subject}»{sender_part}")
                if body:
                    lines.append(f"       Фрагмент: {body}")

    if upcoming_entries:
        lines.append(f"\nПРЕДСТОЯЩИЕ ВСТРЕЧИ (до {week_end}):")
        for date_prefix, title in upcoming_entries[:max_upcoming]:
            lines.append(f"  📅 [{date_prefix}] «{title}»")

    lines.append("--- /PERSONALVAULT ---")
    return "\n".join(lines)


# Singleton
_assembler: Optional[ContextAssembler] = None


def get_assembler() -> ContextAssembler:
    global _assembler
    if _assembler is None:
        _assembler = ContextAssembler()
    return _assembler
