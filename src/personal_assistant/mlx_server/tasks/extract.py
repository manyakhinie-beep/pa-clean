"""
Structured Extraction Task — Stage 1 of AI email/calendar research plan.

Extracts from email/calendar document body:
  - action_items: [{text, deadline, assignee}]
  - entities: {people, organizations, amounts, dates}
  - intent: request | info | question | deadline | meeting | fyi | unknown
  - tone: formal | informal | urgent | neutral
  - reply_required: bool
  - deadline: ISO string | null  (earliest explicit deadline)
  - summary_one_line: str

Pipeline:
  1. Try MLX model with 4-layer structured output prompt → parse JSON
  2. Validate + repair (strip markdown fences, fix trailing commas)
  3. On failure / model absent → regex-based fallback
  4. Cache result by sha256(body) in data/extraction_cache.json

Thread-safe: cache reads/writes guarded by threading.Lock.
Graceful degradation: always returns a valid ExtractionResult even without MLX.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_VALID_INTENTS = frozenset(
    {"request", "info", "question", "deadline", "meeting", "fyi", "unknown"}
)
_VALID_TONES = frozenset({"formal", "informal", "urgent", "neutral"})


@dataclass
class ActionItem:
    text: str
    deadline: Optional[str] = None   # ISO date string or null
    assignee: Optional[str] = None   # "me" | name | null


@dataclass
class Entities:
    people: list[str] = field(default_factory=list)
    organizations: list[str] = field(default_factory=list)
    amounts: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    action_items: list[ActionItem] = field(default_factory=list)
    entities: Entities = field(default_factory=Entities)
    intent: str = "unknown"
    tone: str = "neutral"
    reply_required: bool = False
    deadline: Optional[str] = None
    summary_one_line: str = ""
    # Metadata (not exposed to client directly)
    method: str = "unknown"   # "mlx" | "fallback" | "cached"
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("method", None)
        d.pop("sha256", None)
        return d


# ---------------------------------------------------------------------------
# Extraction cache
# ---------------------------------------------------------------------------

_CACHE_PATH = Path("data/extraction_cache.json")
_cache_lock = threading.Lock()


def _load_cache() -> dict[str, Any]:
    try:
        if _CACHE_PATH.exists():
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[extract] cache load failed: {exc}")
    return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except Exception as exc:
        logger.error(f"[extract] cache save failed: {exc}")


def _body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _cache_get(sha256: str) -> Optional[dict]:
    with _cache_lock:
        return _load_cache().get(sha256)


def _cache_set(sha256: str, result: dict) -> None:
    with _cache_lock:
        cache = _load_cache()
        cache[sha256] = result
        _save_cache(cache)


# ---------------------------------------------------------------------------
# MLX prompt — 4-layer structured output
# ---------------------------------------------------------------------------

_EXTRACT_SCHEMA = """{
  "action_items": [{"text": "string", "deadline": "ISO date or null", "assignee": "me|name|null"}],
  "entities": {
    "people": ["string"],
    "organizations": ["string"],
    "amounts": ["string"],
    "dates": ["string"]
  },
  "intent": "request|info|question|deadline|meeting|fyi|unknown",
  "tone": "formal|informal|urgent|neutral",
  "reply_required": true,
  "deadline": "YYYY-MM-DD or null",
  "summary_one_line": "string (max 100 chars)"
}"""

_EXTRACT_EXAMPLE = """{
  "action_items": [{"text": "Отправить отчёт", "deadline": "2026-05-29", "assignee": "me"}],
  "entities": {"people": ["Иван Петров"], "organizations": ["ООО Альфа"], "amounts": ["150 000 руб"], "dates": ["29 мая"]},
  "intent": "request",
  "tone": "formal",
  "reply_required": true,
  "deadline": "2026-05-29",
  "summary_one_line": "Просьба отправить финансовый отчёт до 29 мая"
}"""

_EXTRACT_PROMPT = """\
Извлеки структурированные данные из письма. Верни ТОЛЬКО валидный JSON по схеме ниже.

СХЕМА:
{schema}

ПРИМЕР ОТВЕТА:
{example}

ПРАВИЛА:
- deadline — ближайшая явная дата в формате YYYY-MM-DD или null
- intent ∈ {{request, info, question, deadline, meeting, fyi, unknown}}
- tone ∈ {{formal, informal, urgent, neutral}}
- reply_required = true если письмо требует ответа от получателя
- summary_one_line — одно предложение, максимум 100 символов
- assignee = "me" если задача для получателя, имя если явно указан другой
- Только JSON, никакого другого текста

ПИСЬМО:
{body}

JSON:"""


def _build_prompt(body: str) -> str:
    return _EXTRACT_PROMPT.format(
        schema=_EXTRACT_SCHEMA,
        example=_EXTRACT_EXAMPLE,
        body=body[:3000],
    )


# ---------------------------------------------------------------------------
# JSON repair + validation
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _repair_json(raw: str) -> str:
    """Strip markdown fences, fix trailing commas, find first {...} block."""
    s = _JSON_FENCE_RE.sub("", raw).strip()
    s = _TRAILING_COMMA_RE.sub(r"\1", s)
    # Find the outermost JSON object
    start = s.find("{")
    if start == -1:
        raise ValueError("No JSON object found")
    depth = 0
    for i, ch in enumerate(s[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    raise ValueError("Unclosed JSON object")


def _parse_extraction_json(raw: str) -> ExtractionResult:
    repaired = _repair_json(raw)
    data = json.loads(repaired)

    # Normalise action_items
    raw_items = data.get("action_items") or []
    action_items = []
    for it in raw_items[:10]:  # cap at 10
        if isinstance(it, dict) and it.get("text"):
            action_items.append(ActionItem(
                text=str(it["text"])[:200],
                deadline=it.get("deadline") if isinstance(it.get("deadline"), str) else None,
                assignee=it.get("assignee") if isinstance(it.get("assignee"), str) else None,
            ))

    # Normalise entities
    raw_ent = data.get("entities") or {}

    def _str_list(v: Any, cap: int = 10) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(x) for x in v if isinstance(x, str)][:cap]

    entities = Entities(
        people=_str_list(raw_ent.get("people")),
        organizations=_str_list(raw_ent.get("organizations")),
        amounts=_str_list(raw_ent.get("amounts")),
        dates=_str_list(raw_ent.get("dates")),
    )

    intent = str(data.get("intent") or "unknown").lower()
    if intent not in _VALID_INTENTS:
        intent = "unknown"

    tone = str(data.get("tone") or "neutral").lower()
    if tone not in _VALID_TONES:
        tone = "neutral"

    deadline = data.get("deadline")
    if not isinstance(deadline, str):
        deadline = None
    # Validate ISO format (loose)
    if deadline and not re.match(r"\d{4}-\d{2}-\d{2}", deadline):
        deadline = None

    return ExtractionResult(
        action_items=action_items,
        entities=entities,
        intent=intent,
        tone=tone,
        reply_required=bool(data.get("reply_required", False)),
        deadline=deadline,
        summary_one_line=str(data.get("summary_one_line") or "")[:120],
        method="mlx",
    )


# ---------------------------------------------------------------------------
# Regex fallback (no MLX)
# ---------------------------------------------------------------------------

# Action item triggers (Russian + English)
_ACTION_TRIGGERS_RE = re.compile(
    r"(?:прошу|необходимо|нужно|требуется|пожалуйста|please|kindly|"
    r"could you|можешь|можете|обязательно|не забудьте|не забудь|"
    r"сделайте|сделай|подготовьте|подготовь|отправьте|отправь|"
    r"согласуйте|согласуй|проверьте|проверь|утвердите|утвердите)\s+(.+?)(?:[.!?]|$)",
    re.IGNORECASE | re.MULTILINE,
)

# Reply-required triggers
_REPLY_TRIGGERS_RE = re.compile(
    r"(?:прошу ответить|ответьте|дайте знать|let me know|please confirm|"
    r"подтвердите|жду ответа|жду вашего ответа|reply|respond|get back to me)",
    re.IGNORECASE,
)

# Intent keywords
_INTENT_MAP: list[tuple[frozenset, str]] = [
    (frozenset({"meeting", "встреча", "созвон", "zoom", "teams", "звонок", "call", "sync"}), "meeting"),
    (frozenset({"дедлайн", "deadline", "до ", "срок", "не позднее", "by end", "due"}), "deadline"),
    (frozenset({"прошу", "пожалуйста", "please", "could you", "можете", "нужно", "необходимо"}), "request"),
    (frozenset({"вопрос", "question", "спрашиваю", "уточните", "clarify", "как ", "что "}), "question"),
    (frozenset({"fyi", "к сведению", "информирую", "уведомляю", "сообщаю", "for your information"}), "fyi"),
]

# Urgency → tone
_URGENT_RE = re.compile(
    r"\b(?:срочно|asap|urgent|немедленно|как можно скорее|immediately|горящий)\b",
    re.IGNORECASE,
)

# Amount patterns (RUB + USD + EUR)
_AMOUNT_RE = re.compile(
    r"(?:\d[\d\s]*(?:руб(?:лей)?|₽|USD|\$|EUR|€)|\$\d[\d\s]*|€\d[\d\s]*)",
    re.IGNORECASE,
)

# ISO date in body
_DATE_ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DATE_RU_RE = re.compile(
    r"\b(?P<d>\d{1,2})\s+"
    r"(?P<mon>январ[яе]|феврал[яе]|март[ае]|апрел[яе]|ма[яе]|июн[яе]|"
    r"июл[яе]|август[ае]|сентябр[яе]|октябр[яе]|ноябр[яе]|декабр[яе])"
    r"(?:\s+(?P<y>\d{4}))?",
    re.IGNORECASE,
)
_RU_MONTHS: dict[str, int] = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def _ru_month_num(token: str) -> int:
    tok = token.lower()
    for prefix, num in _RU_MONTHS.items():
        if tok.startswith(prefix):
            return num
    return 0


def _extract_earliest_date(text: str) -> Optional[str]:
    """Return earliest date found in text as ISO string, or None."""
    candidates: list[date] = []
    today = date.today()

    for m in _DATE_ISO_RE.finditer(text):
        try:
            candidates.append(date.fromisoformat(m.group(1)))
        except ValueError:
            pass

    for m in _DATE_RU_RE.finditer(text):
        mon = _ru_month_num(m.group("mon"))
        if not mon:
            continue
        try:
            d = int(m.group("d"))
            y = int(m.group("y")) if m.group("y") else today.year
            dt = date(y, mon, d)
            if dt < today:
                dt = dt.replace(year=today.year + 1)
            candidates.append(dt)
        except (ValueError, TypeError):
            pass

    if not candidates:
        return None
    return min(candidates).isoformat()


def _fallback_extract(body: str) -> ExtractionResult:
    """Pure regex/heuristic extraction — no MLX required."""
    # Action items
    action_items: list[ActionItem] = []
    for m in _ACTION_TRIGGERS_RE.finditer(body):
        text = m.group(2).strip()[:200] if (m.lastindex or 0) >= 2 else m.group(1).strip()[:200]
        if len(text) > 5:
            action_items.append(ActionItem(text=text, assignee="me"))
    action_items = action_items[:5]

    # Entities: amounts + dates (people / orgs need NER — skip in fallback)
    amounts = [m.group(0) for m in _AMOUNT_RE.finditer(body)][:5]
    date_matches: list[str] = []
    for m in _DATE_RU_RE.finditer(body):
        date_matches.append(m.group(0))
    for m in _DATE_ISO_RE.finditer(body):
        date_matches.append(m.group(1))
    date_matches = list(dict.fromkeys(date_matches))[:5]

    entities = Entities(amounts=amounts, dates=date_matches)

    # Intent
    body_lower = body.lower()
    intent = "info"
    for kw_set, intent_val in _INTENT_MAP:
        if any(kw in body_lower for kw in kw_set):
            intent = intent_val
            break

    # Tone
    tone = "urgent" if _URGENT_RE.search(body) else "formal"

    # Reply required
    reply_required = bool(_REPLY_TRIGGERS_RE.search(body)) or intent == "request"

    # Deadline
    deadline = _extract_earliest_date(body)

    # Summary: first non-empty sentence
    sentences = [s.strip() for s in re.split(r"[.!?\n]", body) if s.strip()]
    summary = sentences[0][:100] if sentences else ""

    return ExtractionResult(
        action_items=action_items,
        entities=entities,
        intent=intent,
        tone=tone,
        reply_required=reply_required,
        deadline=deadline,
        summary_one_line=summary,
        method="fallback",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract(body: str, force: bool = False) -> ExtractionResult:
    """
    Extract structured information from email/calendar body text.

    Args:
        body:  Plain text body (already stripped of frontmatter).
        force: Skip cache and re-extract.

    Returns:
        ExtractionResult (always valid, never raises).
    """
    if not body or not body.strip():
        return ExtractionResult(method="fallback", summary_one_line="Пустое тело письма")

    sha = _body_sha256(body)

    # Cache hit
    if not force:
        cached = _cache_get(sha)
        if cached:
            try:
                result = _dict_to_result(cached)
                result.method = "cached"
                result.sha256 = sha
                return result
            except Exception as exc:
                logger.debug(f"[extract] cache parse failed, re-extracting: {exc}")

    # Try MLX
    result = _try_mlx_extract(body)  # type: ignore[assignment]  # narrowed below
    if result is None:
        result = _fallback_extract(body)

    result.sha256 = sha

    # Store in cache
    try:
        _cache_set(sha, result.to_dict())
    except Exception as exc:
        logger.warning(f"[extract] cache store failed: {exc}")

    return result


def _try_mlx_extract(body: str) -> Optional[ExtractionResult]:
    """Attempt MLX extraction. Returns None on any failure."""
    try:
        from personal_assistant.mlx_server import server as _srv
        engine = getattr(_srv.state, "engine", None)
        if engine is None or not getattr(engine, "is_loaded", False):
            return None

        prompt = _build_prompt(body)
        raw = engine.generate(prompt, max_tokens=600, temperature=0.1)
        result = _parse_extraction_json(raw)
        return result
    except Exception as exc:
        logger.debug(f"[extract] MLX extraction failed: {exc}")
        return None


def _dict_to_result(d: dict) -> ExtractionResult:
    """Deserialise a cached dict back to ExtractionResult."""
    raw_items = d.get("action_items") or []
    action_items = [
        ActionItem(
            text=it.get("text", ""),
            deadline=it.get("deadline"),
            assignee=it.get("assignee"),
        )
        for it in raw_items
        if isinstance(it, dict)
    ]
    raw_ent = d.get("entities") or {}
    entities = Entities(
        people=list(raw_ent.get("people") or []),
        organizations=list(raw_ent.get("organizations") or []),
        amounts=list(raw_ent.get("amounts") or []),
        dates=list(raw_ent.get("dates") or []),
    )
    return ExtractionResult(
        action_items=action_items,
        entities=entities,
        intent=str(d.get("intent") or "unknown"),
        tone=str(d.get("tone") or "neutral"),
        reply_required=bool(d.get("reply_required", False)),
        deadline=d.get("deadline"),
        summary_one_line=str(d.get("summary_one_line") or ""),
    )


# ---------------------------------------------------------------------------
# Cache management helpers (used by routes + tests)
# ---------------------------------------------------------------------------

def invalidate_cache(sha256: str) -> None:
    """Remove a single entry from the extraction cache."""
    with _cache_lock:
        cache = _load_cache()
        cache.pop(sha256, None)
        _save_cache(cache)


def cache_size() -> int:
    """Return number of cached extractions."""
    with _cache_lock:
        return len(_load_cache())


def clear_cache() -> int:
    """Clear all cached extractions. Returns number of removed entries."""
    with _cache_lock:
        cache = _load_cache()
        count = len(cache)
        _save_cache({})
        return count
