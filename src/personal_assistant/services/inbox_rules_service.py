"""
Inbox rules service — applies user-defined GTD + structured rules to inbox
items so that the Срочно / Важно / Ответить filters honour the rules the
user configured in the "Правила" tab.

Previously the inbox filtered urgency only on vault frontmatter tags
(``urgency:critical``, ``срочно``, …).  GTD-rules (``data/gtd_rules.json``)
and structured rules (``data/rules.json``) were configurable in the UI
but had **no effect** on the inbox list — defeating the user's mental
model that "правила для Срочно-Важно-Ответить должны работать в Inbox".

How the merge works (per item, all signals OR-combined):

* Existing tag-based detection (``_TAG_URGENT`` / ``_TAG_IMPORT``) stays.
* Structured rule match → set flags from ``eisenhower_quadrant``:
    - q1 (Срочно & Важно) → is_urgent + is_important
    - q2 (Важно, не срочно) → is_important
    - q3 (Срочно, не важно) → is_urgent
    - q4 → no flag (still classified but doesn't surface in filters)
  ``action_type == EXECUTE`` (do now) also forces ``followup_needed``.
* GTD rule keyword match → same quadrant mapping (without contacts).
* Rule ``tags`` are appended to the item's tag list so badges render.
* ``followup`` keyword in rule tags / GTD action also flips
  ``followup_needed`` so users can craft a custom "Ответить" rule.

The classifier ignores disabled rules and works without an MLX engine
(pure keyword/contact matching).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from personal_assistant.services.deadline_extractor import (
    extract_deadline,
    fits_horizon,
)
from personal_assistant.services.rule_engine import (
    ActionType,
    EisenhowerQuadrant,
    Rule,
    classify_item,
)

# Tags that signal "needs reply" — applied by either rule.tags or GTD action.
_FOLLOWUP_TOKENS = {"ответить", "followup", "follow-up", "follow_up", "reply"}


def _load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"[inbox_rules] could not load {path}: {exc}")
        return default


def _project_root() -> Path:
    # src/personal_assistant/services/inbox_rules_service.py → project root
    return Path(__file__).resolve().parents[3]


def load_structured_rules() -> list[Rule]:
    """Load structured rules from ``data/rules.json``.

    Mirrors ``rule_engine.load_rules`` but is duplicated here so the inbox
    can rely on a stable surface even if rule_engine signatures change.
    """
    raw = _load_json(_project_root() / "data" / "rules.json", [])
    if not isinstance(raw, list):
        return []
    result: list[Rule] = []
    for item in raw:
        try:
            result.append(Rule.model_validate(item))
        except Exception as exc:  # noqa: BLE001 — malformed user input
            logger.debug(f"[inbox_rules] skipping invalid rule {item!r}: {exc}")
    return result


def load_gtd_rules() -> list[dict]:
    """Load GTD rules from ``data/gtd_rules.json``.

    GTD rule shape: ``{id, keyword, action, quadrant}``.
    """
    raw = _load_json(_project_root() / "data" / "gtd_rules.json", {"rules": []})
    if isinstance(raw, dict):
        rules = raw.get("rules") or []
    else:
        rules = raw
    return [r for r in rules if isinstance(r, dict) and (r.get("keyword") or "").strip()]


# Cap body inclusion so very large MIME blobs don't blow up keyword scanning.
# 4 KB is enough for the first few paragraphs of a typical work email,
# which is where the user-visible keywords usually appear.  Anything longer
# is almost certainly quoted thread history or a signature/footer block.
_BODY_SCAN_LIMIT = 4000


def _item_text(item: dict) -> str:
    """Build the searchable text blob the rule engine matches against.

    Previously only subject + sender + preview (≈180 chars) were scanned,
    so a keyword like «счёт» that appeared further down in the body never
    matched and the user saw «Правила не работают».  Including the body
    (truncated to 4 KB) fixes the reported bug «добавление в Правила и
    применение сейчас не работает» without making the scan unbounded.
    """
    body = item.get("body") or ""
    if isinstance(body, str) and len(body) > _BODY_SCAN_LIMIT:
        body = body[:_BODY_SCAN_LIMIT]
    parts = [
        item.get("subject") or "",
        item.get("sender_name") or "",
        item.get("sender_role") or "",
        item.get("preview") or item.get("body_preview") or "",
        body,
    ]
    return " ".join(p for p in parts if p)


def _item_contacts(item: dict) -> list[str]:
    addrs: list[str] = []
    for key in ("sender_email", "from"):
        v = item.get(key)
        if v and isinstance(v, str):
            addrs.append(v)
    return addrs


def _flags_from_quadrant(q: str) -> tuple[bool, bool]:
    """Map an Eisenhower quadrant string to (is_urgent, is_important)."""
    q = (q or "").lower()
    if q == "q1":
        return True, True
    if q == "q2":
        return False, True
    if q == "q3":
        return True, False
    return False, False


def _has_followup_signal(tokens: list[str]) -> bool:
    return any(t.lower() in _FOLLOWUP_TOKENS for t in tokens if isinstance(t, str))


def apply_rules_to_item(
    item: dict,
    structured_rules: Optional[list[Rule]] = None,
    gtd_rules: Optional[list[dict]] = None,
) -> dict:
    """Mutate *item* in place: OR-combine rule flags into existing fields.

    Always preserves any flag that was already True (tag-based detection
    runs first in ``_doc_to_item``).  Adds rule-derived tags so badges /
    other filters still see them.  Returns the (mutated) item for chaining.
    """
    structured_rules = structured_rules or []
    gtd_rules = gtd_rules or []

    # No-op fast path: без правил извлекать deadline незачем — это и
    # экономит regex-проходы, и сохраняет контракт «без правил — никаких
    # мутаций item» (тест ``test_no_rules_is_noop``).
    if not structured_rules and not gtd_rules:
        return item

    text = _item_text(item)
    contacts = _item_contacts(item)

    extra_tags: list[Any] = []

    # ── Извлечение срока из письма ──────────────────────────────────────
    # «Срок» — это дата, к которой пользователю требуется выполнить
    # действие.  Извлекаем один раз, сохраняем в item для UI и для
    # фильтрации правил по deadline_horizon.  Reference — дата самого
    # письма, если есть, иначе текущий момент.
    deadline = None
    try:
        # Дата письма (вариант "date" или "received_date") — для
        # относительных фраз вида «через 2 недели».
        from datetime import datetime as _dt

        ref_raw = item.get("date") or item.get("received_date") or ""
        ref_dt: Optional[_dt] = None
        if isinstance(ref_raw, str) and ref_raw:
            try:
                ref_dt = _dt.fromisoformat(ref_raw.replace("Z", "+00:00"))
            except ValueError:
                ref_dt = None
        deadline = extract_deadline(text, reference_date=ref_dt)
        if deadline is not None:
            item["deadline"] = deadline.isoformat()
    except Exception as exc:  # noqa: BLE001 — extractor никогда не валит inbox
        logger.debug(f"[inbox_rules] deadline extraction failed for {item.get('id')!r}: {exc}")
        deadline = None

    # ── Structured rules ────────────────────────────────────────────────
    if structured_rules:
        # Отфильтруем правила, у которых deadline_horizon не подходит под
        # извлечённый срок письма.  Правила с horizon="any" (default)
        # проходят всегда — back-compat.
        eligible_structured = [
            r for r in structured_rules
            if fits_horizon(deadline, getattr(r, "deadline_horizon", "any") or "any")
        ]
        result = classify_item(text, contacts, eligible_structured)
        if result.matched_rule_id:
            urg, imp = _flags_from_quadrant(result.eisenhower_quadrant.value)
            if urg:
                item["is_urgent"] = True
            if imp:
                item["is_important"] = True

            # action_type EXECUTE = "do now" → treat as needs-reply
            if result.action_type == ActionType.EXECUTE:
                item["followup_needed"] = True

            # Explicit followup keyword in rule.tags → followup_needed
            if _has_followup_signal(result.tags):
                item["followup_needed"] = True

            # Merge rule tags so badges + tag chips reflect the rule decision.
            for t in result.tags:
                extra_tags.append({"label": str(t), "cls": "rule"})
            item.setdefault("matched_rules", []).append({
                "id": result.matched_rule_id,
                "name": result.matched_rule_name,
                "quadrant": result.eisenhower_quadrant.value,
                "action": result.action_type.value,
            })

    # ── GTD rules (simple keyword → quadrant) ───────────────────────────
    if gtd_rules:
        haystack = text.lower()
        for r in gtd_rules:
            kw = (r.get("keyword") or "").strip().lower()
            if not kw or kw not in haystack:
                continue
            # Фильтр по deadline_horizon — то же поведение что у структурных.
            horizon = (r.get("deadline_horizon") or "any").strip() or "any"
            if not fits_horizon(deadline, horizon):
                continue
            quadrant = r.get("quadrant") or EisenhowerQuadrant.Q2.value
            urg, imp = _flags_from_quadrant(quadrant)
            if urg:
                item["is_urgent"] = True
            if imp:
                item["is_important"] = True
            action = (r.get("action") or "").strip().lower()
            if _has_followup_signal([action]):
                item["followup_needed"] = True
            item.setdefault("matched_rules", []).append({
                "id": str(r.get("id") or ""),
                "name": kw,
                "quadrant": quadrant,
                "action": action or "inbox",
            })
            break  # first match wins, same as rule_engine.classify_item

    # ── Merge extra tags (rule-derived) into the item ───────────────────
    if extra_tags:
        existing = item.get("tags") or []
        # Avoid duplicate labels
        seen = {(t.get("label") if isinstance(t, dict) else str(t)) for t in existing}
        for t in extra_tags:
            label = t.get("label") if isinstance(t, dict) else str(t)
            if label and label not in seen:
                existing.append(t)
                seen.add(label)
        item["tags"] = existing

    return item


def apply_rules_to_items(items: list[dict]) -> list[dict]:
    """Apply the configured GTD + structured rules to every item in-place.

    Loads the rule files once, then runs ``apply_rules_to_item`` per row.
    Safe to call even if no rules are configured (no-op).
    """
    structured = load_structured_rules()
    gtd = load_gtd_rules()
    if not structured and not gtd:
        return items
    for it in items:
        try:
            apply_rules_to_item(it, structured, gtd)
        except Exception as exc:  # noqa: BLE001 — never block inbox list on a bad rule
            logger.warning(f"[inbox_rules] skipped item {it.get('id')!r}: {exc}")
    return items
