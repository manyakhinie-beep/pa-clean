"""
Classify / tag task — applies classification rules to vault documents.

Classification config is loaded from a YAML file (PA_CLASSIFY_CONFIG_PATH
or vault_root/classify.yaml). If no config file exists, uses built-in defaults.

Two modes:
  1. Rule-based (keyword/contact matching) — fast, no LLM needed
  2. LLM-assisted — for semantic labels where keywords are insufficient

Example classify.yaml:
  ---
  classifiers:
    urgency:
      urgent:
        keywords: [asap, deadline, срочно, "as soon as possible"]
        contacts: [boss@company.com, cto@company.com]
      important:
        keywords: [important, priority, важно, critical]
    category:
      finance:
        keywords: [invoice, payment, budget, счет, оплата]
      meeting:
        keywords: [meeting, call, zoom, встреча, "let's sync"]
      legal:
        keywords: [contract, agreement, нда, договор, подписать]
  llm_classify:
    enabled: false
    label: sentiment
    prompt: |
      Classify the sentiment of this email as one of: positive, negative, neutral.
      Reply with just the label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timezone as _tz
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from personal_assistant.config import settings
from personal_assistant.mlx_server.engine import MLXEngine
from personal_assistant.mlx_server.tasks.llm_classify_service import (
    LLMClassifyCache,
    compute_rule_confidence,
    llm_classify_single,
    needs_llm_classification,
)
from personal_assistant.mlx_server.vault_index import VaultDoc, VaultIndex

# ---------------------------------------------------------------------------
# Deadline extraction — ищем даты в тексте письма
# ---------------------------------------------------------------------------

# Числовые форматы дат
_DATE_NUMERIC_RE = re.compile(
    r"\b(?P<d>\d{1,2})[./\-](?P<m>\d{1,2})[./\-](?P<y>\d{4})\b"
)
# ISO: 2026-05-20 или 2026-05-20T14:00
_DATE_ISO_RE = re.compile(
    r"\b(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"
    r"(?:T(?P<H>\d{2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?)?\b"
)
# Русские словесные даты: «20 мая», «20 мая 2026», «к 20-му мая»
_DATE_RU_RE = re.compile(
    r"\b(?:к\s+)?(?P<d>\d{1,2})[-\s]?(?:го|му|е)?\s+"
    r"(?P<mon>январ[яе]|феврал[яе]|март[ае]|апрел[яе]|ма[яе]|июн[яе]|"
    r"июл[яе]|август[ае]|сентябр[яе]|октябр[яе]|ноябр[яе]|декабр[яе])"
    r"(?:\s+(?P<y>\d{4}))?",
    re.IGNORECASE,
)
# «до конца дня», «сегодня до 18:00»
_TIME_RE = re.compile(r"\b(?P<H>\d{1,2}):(?P<M>\d{2})\b")

_RU_MONTHS: dict[str, int] = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

# Слова-триггеры дедлайна
_DEADLINE_TRIGGERS = re.compile(
    r"\b(до|к|дедлайн|deadline|не позднее|срок|"
    r"до\s+конца\s+дня|eod|by\s+end\s+of\s+day|by|due)\b",
    re.IGNORECASE,
)


def _ru_month_num(token: str) -> int:
    """Вернуть номер месяца по русскому токену (prefix-match)."""
    tok = token.lower()
    for prefix, num in _RU_MONTHS.items():
        if tok.startswith(prefix):
            return num
    return 0


def extract_deadlines(text: str) -> list[str]:
    """
    Извлечь дедлайны из текста и вернуть список ISO-строк.

    Поиск ведётся только в предложениях, содержащих слова-триггеры
    (до / дедлайн / не позднее / deadline / …), чтобы снизить ложные срабатывания.

    Returns:
        list[str] — ISO 8601 строки, напр. ["2026-05-20", "2026-06-01T18:00:00+00:00"]
    """
    results: list[str] = []
    today = _date.today()

    # Разбиваем на предложения и фильтруем по триггерам
    sentences = re.split(r"[.!?\n;]", text)
    trigger_sentences = [s for s in sentences if _DEADLINE_TRIGGERS.search(s)]
    search_text = " ".join(trigger_sentences) if trigger_sentences else text

    seen: set[str] = set()

    def _add(iso: str) -> None:
        if iso not in seen:
            seen.add(iso)
            results.append(iso)

    # ISO dates
    for m in _DATE_ISO_RE.finditer(search_text):
        try:
            y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
            H, M = m.group("H"), m.group("M")
            if H and M:
                dt = _datetime(y, mo, d, int(H), int(M), tzinfo=_tz.utc)
                _add(dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"))
            else:
                _add(f"{y:04d}-{mo:02d}-{d:02d}")
        except ValueError:
            pass

    # Numeric dates (d.m.yyyy or d/m/yyyy)
    for m in _DATE_NUMERIC_RE.finditer(search_text):
        try:
            d, mo, y = int(m.group("d")), int(m.group("m")), int(m.group("y"))
            _add(f"{y:04d}-{mo:02d}-{d:02d}")
        except ValueError:
            pass

    # Russian textual dates
    for m in _DATE_RU_RE.finditer(search_text):
        try:
            d = int(m.group("d"))
            mo = _ru_month_num(m.group("mon"))
            if mo == 0:
                continue
            y = int(m.group("y")) if m.group("y") else today.year
            # Если дата в прошлом — следующий год
            candidate = _date(y, mo, d)
            if candidate < today and not m.group("y"):
                y += 1
            # Ищем время в той же фразе ±30 символов
            start, end = max(0, m.start() - 30), min(len(search_text), m.end() + 30)
            snippet = search_text[start:end]
            tm = _TIME_RE.search(snippet)
            if tm:
                dt = _datetime(y, mo, d, int(tm.group("H")), int(tm.group("M")), tzinfo=_tz.utc)
                _add(dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"))
            else:
                _add(f"{y:04d}-{mo:02d}-{d:02d}")
        except ValueError:
            pass

    return results


# ---------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------


class Sentiment(str, Enum):
    """Detected emotional tone of a vault document.

    :cvar POSITIVE: Positive tone (gratitude, approval, success).
    :cvar NEGATIVE: Negative tone (problem, urgency, complaint).
    :cvar NEUTRAL: Neutral or informational.
    """

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


#: Keywords that indicate positive sentiment.
_POSITIVE_KW: list[str] = [
    "отлично", "excellent", "great", "спасибо", "thanks", "благодарю",
    "поздравляю", "congratulations", "успех", "success", "рад", "glad",
    "хорошо", "good", "прекрасно", "wonderful", "замечательно", "awesome",
    "договорились", "agreed", "подтверждаю", "confirmed", "одобрено", "approved",
    "принято", "accepted", "запущено", "launched",
]

#: Keywords that indicate negative sentiment.
_NEGATIVE_KW: list[str] = [
    "проблема", "problem", "ошибка", "error", "сбой", "failure",
    "срочно", "urgent", "критично", "critical", "сожалею", "sorry",
    "задержка", "delay", "отказ", "rejected", "отклонено", "declined",
    "блокирует", "blocked", "не работает", "broken", "упал", "crashed",
    "недовольн", "disappointed", "жалоб", "complaint", "претензи",
    "нарушение", "violation", "штраф", "penalty",
]

#: Action words that indicate a request directed at someone.
_ACTION_WORDS: list[str] = [
    "пожалуйста", "прошу", "нужно", "необходимо", "сделай", "сделайте",
    "подготовь", "подготовьте", "проверь", "проверьте", "ответь", "ответьте",
    "пришли", "пришлите", "предоставь", "предоставьте", "согласуй", "согласуйте",
    "please", "could you", "can you", "need you to", "kindly", "action required",
    "action item", "follow up", "follow-up", "respond", "reply", "review",
    "confirm", "подтвердите", "утвердите", "approve",
]


def detect_sentiment(text: str) -> Sentiment:
    """Rule-based sentiment detection over *text*.

    Counts positive vs negative keyword hits. The category with the higher
    weighted score wins; ties resolve to ``NEUTRAL``.

    :param text: Combined title + body of a vault document.
    :returns: :class:`Sentiment` enum value.
    """
    haystack = text.lower()
    pos = sum(1 for kw in _POSITIVE_KW if kw in haystack)
    neg = sum(1 for kw in _NEGATIVE_KW if kw in haystack)
    if pos > neg:
        return Sentiment.POSITIVE
    if neg > pos:
        return Sentiment.NEGATIVE
    return Sentiment.NEUTRAL


def detect_has_action_for_user(text: str, user_email: Optional[str]) -> bool:
    """Return ``True`` if *text* contains an action request addressed to *user_email*.

    Detection logic (all three conditions must hold):
      1. The user's email (or a ``To:`` / ``recipient`` reference to it) appears in the text.
      2. At least one action word is present.

    If *user_email* is ``None`` or empty the function always returns ``False``.

    :param text: Combined title + body to scan.
    :param user_email: The user's primary email from :class:`~profile.models.UserProfile`.
    :returns: Boolean flag.
    """
    if not user_email:
        return False
    haystack = text.lower()
    email_lower = user_email.lower()
    if email_lower not in haystack:
        return False
    return any(kw in haystack for kw in _ACTION_WORDS)


# ---------------------------------------------------------------------------
# Default classification rules (used when no config file exists)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict = {
    # Метки на русском, ключевые слова для matching — двуязычные (письма приходят
    # на обоих языках). Редактируется через Rules → Инструменты → classify.yaml.
    "classifiers": {
        "urgency": {
            "срочно": {
                "keywords": [
                    "asap", "urgent", "deadline", "срочно", "немедленно",
                    "as soon as possible", "by end of day", "eod", "today",
                    "сегодня", "до конца дня",
                ],
            },
            "важно": {
                "keywords": [
                    "important", "priority", "важно", "critical", "key",
                    "must", "required", "необходимо", "приоритет",
                ],
            },
        },
        "category": {
            "финансы": {
                "keywords": [
                    "invoice", "payment", "budget", "счёт", "счет", "оплата",
                    "billing", "receipts", "expense", "cost",
                    "бюджет", "расходы", "счёт-фактура",
                ],
            },
            "встреча": {
                "keywords": [
                    "meeting", "call", "zoom", "teams", "встреча", "agenda",
                    "calendar invite", "conference", "созвон", "повестка",
                    "приглашение", "конференция",
                ],
            },
            "договор": {
                "keywords": [
                    "contract", "agreement", "nda", "договор", "подписать",
                    "legal", "terms", "conditions", "соглашение", "юридич",
                ],
            },
            "требует-действия": {
                "keywords": [
                    "please", "could you", "can you", "need you", "пожалуйста",
                    "action required", "follow up", "respond", "reply",
                    "ответить", "согласовать", "выполнить",
                ],
            },
            "поездка": {
                "keywords": [
                    "trip", "travel", "flight", "hotel", "booking",
                    "командировка", "поездка", "билет", "перелёт", "бронь",
                ],
            },
            "проект": {
                "keywords": [
                    "project", "milestone", "deliverable", "roadmap", "sprint",
                    "проект", "этап", "релиз", "майлстоун",
                ],
            },
            "личное": {
                "keywords": [
                    "personal", "family", "лично", "семья", "ребёнок", "дети",
                    "родители", "personal note",
                ],
            },
        },
    },
    "llm_classify": {
        "enabled": False,
        "threshold": 0.4,
        "batch_size": 5,
        # Категории для LLM-классификации — синхронизированы с category.keys() выше
        # плюс несколько общих. Все на русском, чтобы фронтенд и vault frontmatter
        # были однородны.
        "categories": [
            "срочно", "важно", "встреча", "финансы",
            "договор", "поездка", "проект", "личное",
            "требует-действия", "информация",
        ],
        "prompt": (
            "Классифицируй письмо. Ответь ТОЛЬКО одним словом из списка "
            "(на русском):\n"
            "{categories}\n\nТема: {subject}\nПисьмо: {preview}\n\nКатегория:"
        ),
    },
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ClassifyResult:
    """Classification result for a single vault document.

    :param doc_title: Human-readable document title.
    :param doc_path: Absolute path to the ``.md`` file.
    :param labels: Rule-based labels, e.g. ``{"urgency": "urgent", "category": "finance"}``.
    :param llm_labels: Optional LLM-generated labels.
    :param matched_keywords: Keywords that triggered each classifier.
    :param sentiment: Detected emotional tone.
    :param has_action_for_user: ``True`` if the document contains an action request
        addressed to the user's registered email.
    """

    doc_title: str
    doc_path: str
    labels: dict[str, str]  # {"urgency": "urgent", "category": "finance"}
    llm_labels: dict[str, str] = field(default_factory=dict)
    matched_keywords: dict[str, list[str]] = field(default_factory=dict)
    sentiment: Sentiment = Sentiment.NEUTRAL
    has_action_for_user: bool = False
    # Поля поручения (Фича 2)
    deadlines: list[str] = field(default_factory=list)
    """ISO-даты дедлайнов, извлечённых из текста (пустой список если нет)."""
    assignee_detected: bool = False
    """True если поручение адресовано текущему пользователю (по имени или email)."""
    priority_matrix: Optional[str] = None
    """Квадрант матрицы Эйзенхауэра: urgent_important | important | urgent | routine"""
    # Stage 8: LLM confidence fields
    rule_confidence: float = 1.0
    """Fraction of rule-based classifiers that matched (0..1)."""
    llm_assisted: bool = False
    """True if the LLM was invoked for this document (Stage 8)."""
    llm_category: str = ""
    """Primary semantic category assigned by the LLM (empty if not used)."""


@dataclass
class BatchClassifyResult:
    total: int
    classified: int
    results: list[ClassifyResult]
    label_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    llm_assisted_count: int = 0
    """Number of documents classified with LLM assistance (Stage 8)."""


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def load_classify_config(config_path: Optional[Path] = None) -> dict:
    """Load classification config from YAML, falling back to defaults."""
    path = config_path or settings.classify_config_file
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            logger.info(f"Loaded classify config from {path}")
            return cfg
        except Exception as e:
            logger.warning(f"Failed to load classify config {path}: {e}")
    logger.debug("Using default classification config")
    return DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Rule-based classifier
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Shared LLM cache (module-level singleton, thread-safe for read-heavy use)
# ---------------------------------------------------------------------------

_shared_cache: Optional[LLMClassifyCache] = None


def _get_shared_cache() -> LLMClassifyCache:
    """Return the module-level shared cache, initialising once."""
    global _shared_cache
    if _shared_cache is None:
        _shared_cache = LLMClassifyCache()
    return _shared_cache


# ---------------------------------------------------------------------------
# Rule-based helpers
# ---------------------------------------------------------------------------


def _match_keywords(text: str, keywords: list[str]) -> list[str]:
    """Return keywords found in text (case-insensitive)."""
    text_lower = text.lower()
    return [kw for kw in keywords if kw.lower() in text_lower]


def _match_contacts(text: str, contacts: list[str]) -> list[str]:
    """Return contact emails found in text."""
    text_lower = text.lower()
    return [c for c in contacts if c.lower() in text_lower]


def classify_doc(
    doc: VaultDoc,
    config: Optional[dict] = None,
    engine: Optional[MLXEngine] = None,
    user_email: Optional[str] = None,
) -> ClassifyResult:
    """Classify a single vault document using rule-based + optional LLM logic.

    :param doc: Vault document to classify.
    :param config: Classification config dict (``None`` → load from file).
    :param engine: :class:`MLXEngine` for LLM-assisted classification (optional).
    :param user_email: User's primary email for ``has_action_for_user`` detection.
    :returns: :class:`ClassifyResult` with all labels + sentiment + action flag.
    """
    cfg = config or load_classify_config()
    classifiers = cfg.get("classifiers", {})
    llm_cfg = cfg.get("llm_classify", {})

    haystack = (doc.title + "\n" + doc.content).strip()
    labels: dict[str, str] = {}
    matched: dict[str, list[str]] = {}

    # Rule-based pass
    for classifier_name, label_rules in classifiers.items():
        best_label = None
        best_score = 0
        best_kws: list[str] = []

        for label_name, rules in label_rules.items():
            kws = rules.get("keywords", [])
            contacts = rules.get("contacts", [])

            found_kws = _match_keywords(haystack, kws)
            found_contacts = _match_contacts(haystack, contacts)
            score = len(found_kws) + len(found_contacts) * 2  # contacts weighted higher

            if score > best_score:
                best_score = score
                best_label = label_name
                best_kws = found_kws + found_contacts

        if best_label and best_score > 0:
            labels[classifier_name] = best_label
            matched[classifier_name] = best_kws

    # Compute rule confidence (Stage 8)
    rule_conf = compute_rule_confidence(haystack, classifiers)

    # Stage 8: LLM-assisted semantic classification
    # Triggered when rule confidence < threshold (or legacy enabled flag)
    llm_labels: dict[str, str] = {}
    llm_assisted = False
    llm_category = ""

    threshold = float(llm_cfg.get("threshold", 0.4))
    use_llm_service = (
        engine is not None
        and llm_cfg.get("enabled", False)
        and needs_llm_classification(haystack, classifiers, threshold)
    )

    if use_llm_service:
        subject = doc.title
        preview = doc.content[:600]
        try:
            llm_result = llm_classify_single(
                subject=subject,
                preview=preview,
                config=cfg,
                engine=engine,
                cache=_get_shared_cache(),
            )
            if llm_result.category and not llm_result.error:
                llm_category = llm_result.category
                llm_labels["llm_category"] = llm_category
                llm_assisted = True
                logger.debug(
                    f"LLM classified {doc.title!r} → {llm_category!r} "
                    f"(rule_conf={rule_conf:.2f})"
                )
        except Exception as exc:
            logger.warning(f"LLM classify failed for {doc.title!r}: {exc}")

    elif engine and llm_cfg.get("enabled") and llm_cfg.get("prompt") and not use_llm_service:
        # Legacy single-label LLM pass (kept for backwards compat when threshold not set)
        label_name = llm_cfg.get("label", "llm_label")
        prompt = llm_cfg["prompt"] + f"\n\nDocument:\n{haystack[:2000]}"
        try:
            result = engine.ask(question=prompt, max_tokens=20, temperature=0.1)
            llm_labels[label_name] = result.strip().split("\n")[0].strip()
        except Exception as exc:
            logger.warning(f"LLM classify (legacy) failed for {doc.title!r}: {exc}")

    # Sentiment + action flag (always rule-based, fast)
    sentiment = detect_sentiment(haystack)
    has_action = detect_has_action_for_user(haystack, user_email)

    # ── Поручения (Фича 2): дедлайны + матрица Эйзенхауэра ──────────────────
    deadlines = extract_deadlines(haystack)
    # assignee_detected = True if email action detected OR assignment classifier matched
    assignee_detected = has_action or labels.get("assignment") == "assigned"

    # Матрица Эйзенхауэра по тегам urgency + action
    is_urgent    = labels.get("urgency") == "urgent"
    is_important = labels.get("urgency") == "important" or assignee_detected
    if is_urgent and is_important:
        priority_matrix: Optional[str] = "urgent_important"
    elif is_important:
        priority_matrix = "important"
    elif is_urgent:
        priority_matrix = "urgent"
    elif labels:
        priority_matrix = "routine"
    else:
        priority_matrix = None

    return ClassifyResult(
        doc_title=doc.title,
        doc_path=str(doc.path),
        labels=labels,
        llm_labels=llm_labels,
        matched_keywords=matched,
        sentiment=sentiment,
        has_action_for_user=has_action,
        deadlines=deadlines,
        assignee_detected=assignee_detected,
        priority_matrix=priority_matrix,
        rule_confidence=rule_conf,
        llm_assisted=llm_assisted,
        llm_category=llm_category,
    )


# ---------------------------------------------------------------------------
# Batch classification with vault update
# ---------------------------------------------------------------------------


def classify_vault(
    index: VaultIndex,
    sections: Optional[list[str]] = None,
    config: Optional[dict] = None,
    engine: Optional[MLXEngine] = None,
    write_tags: bool = True,
) -> BatchClassifyResult:
    """
    Classify all (or filtered) vault documents.

    Args:
        index: loaded VaultIndex
        sections: limit to specific sections (None = mail + calendar)
        config: classification config dict (None = load from file)
        engine: MLXEngine for LLM-assisted classification (optional)
        write_tags: if True, append classification tags to each .md file's frontmatter
    """
    cfg = config or load_classify_config()
    target_sections = sections or ["mail", "calendar"]
    docs = [d for d in index.docs if d.section in target_sections]

    logger.info(f"Classifying {len(docs)} docs in sections {target_sections}")

    results: list[ClassifyResult] = []
    label_counts: dict[str, dict[str, int]] = {}

    for doc in docs:
        result = classify_doc(doc, config=cfg, engine=engine)
        results.append(result)

        # Tally counts
        for classifier, label in result.labels.items():
            label_counts.setdefault(classifier, {})
            label_counts[classifier][label] = label_counts[classifier].get(label, 0) + 1

        # Write tags back to .md file
        if write_tags and (result.labels or result.llm_assisted):
            _append_classification_tags(doc, result)

    classified = sum(1 for r in results if r.labels)
    llm_assisted_count = sum(1 for r in results if r.llm_assisted)
    logger.info(
        f"Classified {classified}/{len(docs)} docs "
        f"({llm_assisted_count} via LLM)"
    )

    # Flush LLM classify cache if used
    if engine is not None:
        _get_shared_cache().flush()

    return BatchClassifyResult(
        total=len(docs),
        classified=classified,
        results=results,
        label_counts=label_counts,
        llm_assisted_count=llm_assisted_count,
    )


def _deadline_bucket(iso: str) -> Optional[str]:
    """Convert an ISO date string to a human-readable temporal bucket.

    Buckets:
        ``today``      — deadline is today
        ``tomorrow``   — deadline is tomorrow
        ``this_week``  — deadline is within the next 7 days (excl. today/tomorrow)
        ``future``     — deadline is further in the future

    Returns ``None`` if the date is in the past or cannot be parsed.
    """
    try:
        # Accept both date-only "YYYY-MM-DD" and datetime "YYYY-MM-DDTHH:MM:SS..."
        date_part = iso[:10]
        dl_date = _date.fromisoformat(date_part)
    except (ValueError, TypeError):
        return None

    today = _date.today()
    delta = (dl_date - today).days

    if delta < 0:
        return None           # past — ignore
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta <= 7:
        return "this_week"
    return "future"


def _append_classification_tags(doc: VaultDoc, result: ClassifyResult) -> None:
    """
    Merge classification labels into the document's `tags:` frontmatter list.

    Labels are stored as flat "classifier:label" strings, e.g.:
        tags: [finance, meeting, urgency:urgent, category:finance]

    This makes them visible to VaultDoc.tags, BM25 search, vault UI filters,
    and the LLM chat context — all of which read the `tags:` field.
    """
    try:
        raw = doc.path.read_text(encoding="utf-8")

        # Collect new tag strings: "classifier:label"
        new_tag_set: set[str] = set()
        for classifier, label in result.labels.items():
            new_tag_set.add(f"{classifier}:{label}")
        for classifier, label in result.llm_labels.items():
            new_tag_set.add(f"{classifier}:{label}")

        # Stage 8: ai_classified badge
        if result.llm_assisted:
            new_tag_set.add("ai_classified")

        # Action assignment tag
        if result.assignee_detected:
            new_tag_set.add("@action_required")

        # Deadline tags — convert ISO dates to human buckets
        for dl in result.deadlines:
            bucket = _deadline_bucket(dl)
            if bucket:
                new_tag_set.add(f"@deadline:{bucket}")

        if not new_tag_set:
            return

        # Parse existing frontmatter
        if not raw.startswith("---"):
            logger.warning(f"No frontmatter in {doc.path}, skipping tag write")
            return

        end_idx = raw.find("\n---", 3)
        if end_idx == -1:
            logger.warning(f"Unclosed frontmatter in {doc.path}, skipping")
            return

        fm_text = raw[3:end_idx]  # raw YAML inside ---...---
        body = raw[end_idx + 4 :]  # everything after closing ---

        try:
            fm = yaml.safe_load(fm_text) or {}
        except Exception as exc:
            logger.warning(f"YAML parse error in {doc.path}: {exc}")
            return

        # Merge: preserve existing tags, add new ones
        existing = fm.get("tags", [])
        if isinstance(existing, str):
            existing = [existing]
        existing_set = set(str(t) for t in existing)

        # Remove stale classifier tags (e.g. old "urgency:important" before writing "urgency:urgent")
        classifiers_in_result = set(result.labels) | set(result.llm_labels)
        cleaned = [
            t
            for t in existing_set
            if not any(str(t).startswith(f"{c}:") for c in classifiers_in_result)
            # Remove stale @deadline:* tags — fresh ones will be re-added from new_tag_set
            and not str(t).startswith("@deadline:")
            # Remove stale @action_required — re-added from new_tag_set if still detected
            and str(t) != "@action_required"
        ]

        merged = sorted(set(cleaned) | new_tag_set)
        fm["tags"] = merged

        # Rebuild YAML frontmatter — dump only the changed fields cleanly
        # We rebuild the whole frontmatter block to avoid partial-edit bugs.
        new_fm_text = yaml.dump(
            fm,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).rstrip("\n")

        new_content = f"---\n{new_fm_text}\n---{body}"
        doc.path.write_text(new_content, encoding="utf-8")

    except Exception as e:
        logger.warning(f"Failed to write tags for {doc.path}: {e}")
