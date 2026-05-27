"""
tool_prompts.py — хранение, валидация и подстановка пользовательских промптов
для AI-инструментов (черновик письма, суммаризация).

Хранилище: vault/.tool_prompts.json  (рядом с vault-данными, не в git)
Fallback: встроенные константы из draft_reply.py / summarize.py

Защита от prompt injection:
  - максимальная длина 2 000 символов
  - удаление управляющих символов и нулевых байт
  - блокировка конструкций «<|system|>», «###System», «IGNORE PREVIOUS»
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from personal_assistant.config import settings

# ─── Константы ───────────────────────────────────────────────────────────────

_MAX_PROMPT_LEN = 8_000   # символов
_PROMPTS_FILENAME = ".tool_prompts.json"

# Паттерны, характерные для prompt-injection
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"###\s*system", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+a", re.IGNORECASE),
    re.compile(r"\[INST\]|\[/INST\]", re.IGNORECASE),
    re.compile(r"<s>\s*\[SYS\]", re.IGNORECASE),
]

# ─── Дефолтные промпты (fallback) ────────────────────────────────────────────
#
# Все три промпта построены по единому шаблону:
#
#   1. ИЗВЛЕЧЕНИЕ ФАКТОВ — кто / кому / что / когда из переписки.
#      Приоритет на пользователя ассистента ("ты") и последнее письмо треда.
#   2. РЕШЕНИЕ / ФОРМАТ — что делать с извлечёнными фактами:
#        — summarize → структурированное резюме с разделением «тебе»/«другим»
#        — draft     → ответ от лица пользователя (либо подсказка делегировать)
#        — delegate  → вводное письмо коллеге с просьбой исполнить
#
# «Ты» / «пользователь ассистента» в промптах — это владелец почтового
# ящика (config.user_email).  Старые письма треда — фон; основной приоритет
# держим на последнем сообщении и на задачах, адресованных пользователю.

DEFAULT_SUMMARIZE_SYSTEM = """\
Ты — персональный ассистент. Сжимаешь треды писем в actionable-резюме.

### Этап 1 — извлечение фактов

Прежде чем формировать резюме, разбери переписку и вытащи структуру:

1. КТО пишет — отправитель последнего письма в треде.
2. КОМУ пишет — все получатели; пометь тебя (пользователя ассистента) отдельно, если ты среди адресатов.
3. ЧТО должен(ы) сделать — конкретные действия, поручения, решения, ожидаемые ответы.
4. КОГДА — срок / дедлайн / временная привязка для каждого действия.

Приоритет — задачи, где ответственный ты (пользователь ассистента), и **последнее сообщение** в треде. Старые письма используй как фон, не пересказывай их целиком.

### Этап 2 — формат вывода

Ровно четыре блока в этом порядке. Пустые секции — «—», не выкидывай заголовки.

**Тебе:**
- [ ] Что: … (до КОГДА, если указано)
- [ ] Что: …

**Другим:**
- [Имя/Email]: Что (до КОГДА)
- …

**Тезисы переписки:**
- Решено: …
- Открытые вопросы: …
- Ключевые факты (цифры, имена, суммы, статусы): …

**Контекст последнего письма:**
- От: <Имя/Email>
- Кому: <список; ты — выдели>
- Главное: одна строка о сути

### Правила

- Каждый пункт — одна строка, без воды.
- Цифры, имена, даты — точно (не округляй).
- Не выдумывай: неуверенность помечай `[?]`.
- Не более 12 пунктов суммарно — режь по приоритету.
- Без markdown-таблиц и эмодзи; только списки и заголовки выше.
- Всегда отвечай на русском, независимо от языка исходника.\
"""

DEFAULT_DRAFT_SYSTEM = """\
Ты — помощник по деловой переписке. Составляешь черновик ответа на тред писем от имени пользователя.

### Этап 1 — извлечение фактов

Прежде чем писать, разбери переписку:

1. КТО — отправитель последнего письма.
2. КОМУ — получатели; пометь тебя (пользователя ассистента) отдельно, если ты в адресатах.
3. ЧТО — конкретные просьбы, вопросы, ожидаемые решения, действия.
4. КОГДА — дедлайны / сроки / временные привязки для каждого пункта.

Приоритет — задачи и вопросы, на которые отвечаешь ты, и **последнее сообщение** треда. Старая переписка — фон, не цитируй её.

### Этап 2 — решение, как использовать факты

- Если задача в сфере ответственности пользователя → пиши **ответ** по существу.
- Если разумнее **делегировать** (нужна экспертиза другого человека, не твоя зона) → в конце ответа добавь блок `### Кому делегировать` с предложением 1-2 кандидатов из контекста.
- Если в исходнике несколько вопросов — отвечай по пунктам (нумеровано).

### Этап 3 — формат черновика ответа

Структура:

1. Приветствие в стиле отправителя (формальное / полуформальное / на «ты» если в треде так писали).
2. Краткая реакция / благодарность — 1 строка, без воды.
3. Прямой ответ по каждому пункту последнего письма.
4. Следующий шаг и предлагаемый дедлайн (реалистичный, не «постараюсь скоро»).
5. Закрытие с CTA («жду подтверждения», «уточни Х»).

### Правила

- Каждый абзац — одна мысль.
- Без «надеюсь», «возможно», «к сожалению» без необходимости.
- Отказ — давай альтернативу или объяснение.
- Перенаправление — указывай кому и почему.
- Дедлайн нереалистичен — предлагай новую дату.
- Без эмодзи и спецсимволов.
- Подпись не пиши — Mail подставит сам.

### Формат вывода

Только тело письма, готовое к отправке. Места, где нужны данные, помечай `[УТОЧНИТЬ: что]`. Без чек-листов и секций «что нужно дополнить» — только текст письма (опционально + блок `### Кому делегировать`).

Всегда отвечай на русском, независимо от языка исходника.\
"""

DEFAULT_DELEGATE_SYSTEM = """\
Ты — помощник руководителя. Составляешь короткое вводное письмо коллеге, которому передаётся задача из входящего треда.

### Этап 1 — извлечение фактов

Прежде чем писать, разбери переписку:

1. КТО — отправитель последнего письма.
2. КОМУ — получатели (тебя — пользователя ассистента — пометь отдельно, если ты в адресатах).
3. ЧТО — конкретное поручение / просьба / вопрос, требующий действия.
4. КОГДА — дедлайн или временная привязка.

Приоритет — задача, адресованная тебе (пользователю ассистента) в **последнем письме** треда. Именно её ты передаёшь коллеге.

### Этап 2 — вводное письмо коллеге

Используя извлечённое:

1. В 1-2 предложениях перескажи суть: кто просит и о чём.
2. Сформулируй задачу одной фразой: «Прошу взять в работу …» / «Возьми, пожалуйста, …».
3. Если есть дедлайн — укажи явно. Если нет — попроси ответ «в течение дня / двух дней».
4. Если руководитель добавил заметку — встрой её как акцент.
5. Заверши вежливым CTA: «Сообщи статус» / «Ответь отправителю с копией мне».

### Правила

- Тон: деловой, нейтрально-вежливый. Без «пожалуйста, если не сложно».
- Длина: 4-7 строк. Никакой воды, общих фраз, эмодзи.
- Имена, цифры, даты — точно.
- Не цитируй всё письмо: даём только summary, Mail сам приложит forward с историей.
- Подпись не добавляй — Mail возьмёт стандартную.
- Если в исходнике несколько задач — пронумеруй.

### Формат вывода

Только тело письма-вводной, без темы и без `Кому` — тема и адресат подставляются автоматически. Никаких заголовков `### Вводная`: выводи сразу готовый текст письма.

Всегда отвечай на русском языке.\
"""


# ─── Модель настроек ─────────────────────────────────────────────────────────


@dataclass
class DelegateContact:
    """Один сотрудник, которому можно делегировать письмо.

    Хранится в ``tool_prompts.json`` под ключом ``delegate_contacts``.
    """

    name: str
    email: str
    role: str = ""        # должность / роль для подсказки в UI
    note: str = ""        # внутренняя заметка («ускоряет договоры», …)


@dataclass
class ToolPrompts:
    """Пользовательские системные промпты для AI-тулов."""

    draft_system: str = ""
    """Системный промпт для инструмента «Черновик письма»."""

    summarize_system: str = ""
    """Системный промпт для инструмента «Суммаризация»."""

    delegate_system: str = ""
    """Системный промпт для инструмента «Делегировать»."""

    delegate_contacts: list[DelegateContact] = None  # type: ignore[assignment]
    """Список сотрудников, доступных для делегирования (Inbox → Ассистент)."""

    def __post_init__(self) -> None:
        # default_factory analogue for dataclass + frozen=False
        if self.delegate_contacts is None:
            self.delegate_contacts = []

    def effective_draft(self) -> str:
        """Вернуть активный промпт для черновика (пользовательский или дефолтный)."""
        v = self.draft_system.strip()
        return v if v else DEFAULT_DRAFT_SYSTEM

    def effective_summarize(self) -> str:
        """Вернуть активный промпт для суммаризации (пользовательский или дефолтный)."""
        v = self.summarize_system.strip()
        return v if v else DEFAULT_SUMMARIZE_SYSTEM

    def effective_delegate(self) -> str:
        """Вернуть активный промпт делегирования (пользовательский или дефолтный)."""
        v = self.delegate_system.strip()
        return v if v else DEFAULT_DELEGATE_SYSTEM


# ─── Валидация ────────────────────────────────────────────────────────────────

class PromptValidationError(ValueError):
    """Промпт не прошёл валидацию."""


def _sanitize(text: str) -> str:
    """Удалить управляющие символы, нулевые байты, нормализовать Unicode."""
    # Нормализуем в NFC (кириллица)
    text = unicodedata.normalize("NFC", text)
    # Удаляем нулевые байты и C0/C1 управляющие символы кроме \n \r \t
    text = "".join(
        ch for ch in text
        if unicodedata.category(ch) not in ("Cc", "Cs")
        or ch in ("\n", "\r", "\t")
    )
    return text.strip()


def validate_prompt(text: str, field: str = "prompt") -> str:
    """
    Валидировать и санитизировать пользовательский промпт.

    Raises:
        PromptValidationError: если промпт не соответствует требованиям.
    Returns:
        Очищенный текст промпта.
    """
    if not isinstance(text, str):
        raise PromptValidationError(f"{field}: ожидается строка")

    text = _sanitize(text)

    if len(text) > _MAX_PROMPT_LEN:
        raise PromptValidationError(
            f"{field}: слишком длинный ({len(text)} символов, максимум {_MAX_PROMPT_LEN})"
        )

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            raise PromptValidationError(
                f"{field}: обнаружен паттерн prompt-injection: «{pattern.pattern}»"
            )

    return text


# ─── Персистентность ─────────────────────────────────────────────────────────

def _prompts_path() -> Path:
    return Path(settings.vault_path) / _PROMPTS_FILENAME


def _normalize_contact(raw: dict) -> Optional[DelegateContact]:
    """Coerce a raw dict from JSON into a ``DelegateContact``.

    Rejects entries without a valid ``email`` (we need it for Mail.app), and
    trims long strings so a malformed config file never blows up the UI.
    """
    if not isinstance(raw, dict):
        return None
    email = str(raw.get("email", "")).strip()
    if not email or "@" not in email:
        return None
    return DelegateContact(
        name=str(raw.get("name", "")).strip()[:120] or email.split("@")[0],
        email=email[:200],
        role=str(raw.get("role", "")).strip()[:120],
        note=str(raw.get("note", "")).strip()[:300],
    )


def load_tool_prompts() -> ToolPrompts:
    """Загрузить промпты из vault/.tool_prompts.json. При ошибке — дефолтные."""
    path = _prompts_path()
    if not path.exists():
        return ToolPrompts()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        contacts_raw = data.get("delegate_contacts") or []
        contacts: list[DelegateContact] = []
        if isinstance(contacts_raw, list):
            for r in contacts_raw:
                c = _normalize_contact(r)
                if c:
                    contacts.append(c)
        return ToolPrompts(
            draft_system=data.get("draft_system", ""),
            summarize_system=data.get("summarize_system", ""),
            delegate_system=data.get("delegate_system", ""),
            delegate_contacts=contacts,
        )
    except Exception as exc:
        logger.warning(f"Не удалось загрузить tool_prompts.json: {exc}")
        return ToolPrompts()


def save_tool_prompts(prompts: ToolPrompts) -> None:
    """Сохранить промпты в vault/.tool_prompts.json."""
    path = _prompts_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Dump via asdict — handles nested DelegateContact correctly.
        payload = asdict(prompts)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"tool_prompts.json сохранён: {path}")
    except Exception as exc:
        logger.error(f"Ошибка сохранения tool_prompts.json: {exc}")
        raise


# ─── Модуль-уровневый кэш ─────────────────────────────────────────────────────

_cached: Optional[ToolPrompts] = None


def get_tool_prompts(force_reload: bool = False) -> ToolPrompts:
    """Вернуть промпты с кэшированием (lazy load)."""
    global _cached
    if _cached is None or force_reload:
        _cached = load_tool_prompts()
    return _cached


def invalidate_cache() -> None:
    """Сбросить кэш (вызывать после save_tool_prompts)."""
    global _cached
    _cached = None
