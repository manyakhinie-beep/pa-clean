"""
Rule engine — structured Eisenhower + ActionType classification rules.

Rules are stored in ``data/rules.json``. Each rule has:
  - keywords / contacts to match (OR within each list, both lists ANDed)
  - eisenhower_quadrant: q1 | q2 | q3 | q4
  - action_type: schedule | execute | delegate | info | skip
  - priority: int (lower = higher priority; default 100)
  - tags: list[str] to inject into context
  - enabled: bool

``classify_item(text, contacts_found, rules)`` returns the highest-priority
matching rule or a default "unclassified" result.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ActionType(str, Enum):
    """GTD-aligned action type for a classified item.

    :cvar SCHEDULE: Put on calendar — important but not immediate.
    :cvar EXECUTE: Do now — urgent and important.
    :cvar DELEGATE: Assign to someone else.
    :cvar INFO: File for reference only; no action needed.
    :cvar SKIP: Ignore / delete / not do.
    """

    SCHEDULE = "schedule"
    EXECUTE = "execute"
    DELEGATE = "delegate"
    INFO = "info"
    SKIP = "skip"


class EisenhowerQuadrant(str, Enum):
    """Eisenhower matrix quadrant.

    :cvar Q1: Urgent & Important — execute immediately.
    :cvar Q2: Important, not urgent — schedule.
    :cvar Q3: Urgent, not important — delegate.
    :cvar Q4: Not urgent & not important — skip/info.
    """

    Q1 = "q1"
    Q2 = "q2"
    Q3 = "q3"
    Q4 = "q4"


# ---------------------------------------------------------------------------
# Rule model
# ---------------------------------------------------------------------------


class Rule(BaseModel):
    """Structured classification rule.

    :param id: UUID4 short ID (8 chars).
    :param name: Human-readable rule name.
    :param keywords: Keywords that trigger this rule (case-insensitive, OR match).
    :param contacts: Email addresses that trigger this rule (OR match).
    :param eisenhower_quadrant: Target Eisenhower quadrant.
    :param action_type: Recommended action.
    :param priority: Matching priority — lower number = checked first (default 100).
    :param tags: Tags to apply to matched items.
    :param enabled: Whether the rule is active.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = Field(default="", max_length=120)
    keywords: list[str] = Field(default_factory=list)
    contacts: list[str] = Field(default_factory=list)
    eisenhower_quadrant: EisenhowerQuadrant = Field(default=EisenhowerQuadrant.Q2)
    action_type: ActionType = Field(default=ActionType.INFO)
    priority: int = Field(default=100, ge=1, le=999)
    tags: list[str] = Field(default_factory=list)
    enabled: bool = Field(default=True)
    # Окно сроков, в которое должен попадать deadline письма для срабатывания
    # правила.  Значения см. в ``deadline_extractor.DEADLINE_HORIZONS``:
    # "any" (без фильтра, default), "today", "this_week", "this_month",
    # "next_week", "next_month".  Когда horizon != "any" и письмо не
    # содержит явного срока — правило НЕ срабатывает.
    deadline_horizon: str = Field(default="any")


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    """Result of classifying a single item against the rule set.

    :param matched_rule_id: ID of the winning rule, or ``None`` if unclassified.
    :param matched_rule_name: Human-readable name of the winning rule.
    :param eisenhower_quadrant: Quadrant from the matched rule.
    :param action_type: Action type from the matched rule.
    :param tags: Tags from the matched rule.
    :param matched_keywords: Keywords/contacts that triggered the match.
    :param score: Number of matched keywords/contacts.
    """

    matched_rule_id: Optional[str] = None
    matched_rule_name: str = "Unclassified"
    eisenhower_quadrant: EisenhowerQuadrant = EisenhowerQuadrant.Q2
    action_type: ActionType = ActionType.INFO
    tags: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    score: int = 0


# ---------------------------------------------------------------------------
# Core classification function
# ---------------------------------------------------------------------------


def classify_item(
    text: str,
    contacts_found: Optional[list[str]] = None,
    rules: Optional[list[Rule]] = None,
) -> ClassificationResult:
    """Classify a text item against a list of rules.

    Matching logic:
      - If a rule has keywords AND contacts: both must match (at least one from each).
      - If only keywords: at least one must match.
      - If only contacts: at least one must match.
      - Rules with both lists require hits in both.
      - Ties resolved by priority (lower = first); first match wins.

    :param text: Combined subject + body to scan.
    :param contacts_found: Email addresses found in the item (sender, recipients).
    :param rules: Rules to evaluate (sorted by priority internally).
    :returns: :class:`ClassificationResult`.
    """
    if not rules:
        return ClassificationResult()

    haystack = text.lower()
    contacts_lower = [c.lower() for c in (contacts_found or [])]

    # Sort by priority ascending (lower number = higher priority)
    sorted_rules = sorted(
        (r for r in rules if r.enabled),
        key=lambda r: r.priority,
    )

    for rule in sorted_rules:
        kw_hits: list[str] = []
        ct_hits: list[str] = []

        if rule.keywords:
            kw_hits = [kw for kw in rule.keywords if kw.lower() in haystack]
        if rule.contacts:
            ct_hits = [c for c in rule.contacts if c.lower() in contacts_lower]

        has_kw = bool(kw_hits) if rule.keywords else True
        has_ct = bool(ct_hits) if rule.contacts else True

        if has_kw and has_ct:
            return ClassificationResult(
                matched_rule_id=rule.id,
                matched_rule_name=rule.name,
                eisenhower_quadrant=rule.eisenhower_quadrant,
                action_type=rule.action_type,
                tags=list(rule.tags),
                matched_keywords=kw_hits + ct_hits,
                score=len(kw_hits) + len(ct_hits),
            )

    return ClassificationResult()


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # project root
_RULES_FILE = _PROJECT_ROOT / "data" / "rules.json"


def _rules_file() -> Path:
    """Return the path to rules.json, creating parent dirs if needed."""
    _RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    return _RULES_FILE


def load_rules() -> list[Rule]:
    """Load rules from ``data/rules.json``.

    :returns: List of :class:`Rule` objects (empty list if file missing).
    """
    f = _rules_file()
    if not f.exists():
        return []
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        return [Rule.model_validate(r) for r in (raw if isinstance(raw, list) else [])]
    except Exception:
        return []


def save_rules(rules: list[Rule]) -> None:
    """Atomically save rules to ``data/rules.json``.

    :param rules: List of :class:`Rule` objects to persist.
    """
    f = _rules_file()
    data = [r.model_dump() for r in rules]
    tmp_fd, tmp_path = tempfile.mkstemp(dir=f.parent, suffix=".json.tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, f)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
