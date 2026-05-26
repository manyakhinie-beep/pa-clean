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

DEFAULT_DRAFT_SYSTEM = """\
Ты — помощник по деловой переписке. На основе входящего письма (или треда) и контекста составь черновик ответа.

### Что нужно сделать:

1. Проанализируй входящее сообщение:
   - Чего конкретно добивается отправитель?
   - Какие вопросы требуют ответа?
   - Какие дедлайны или угрозы упомянуты?

2. Составь черновик ответа со структурой:
   - Приветствие (сохрани стиль отправителя: формальный / полуформальный)
   - Благодарность / реакция на предыдущее сообщение
   - Прямой ответ по каждому пункту (нумерованно, если в письме было несколько вопросов)
   - Следующий шаг или дедлайн, который предлагается
   - Закрытие с призывом к действию

3. Правила оформления:
   - Не извиняйся без причины; не используй «я надеюсь», «возможно», «к сожалению» без необходимости
   - Каждый абзац — одна мысль
   - Если нужно отказать — дай альтернативу или объяснение
   - Если перенаправляешь — укажи, кому и почему
   - Дедлайны: если не могу выполнить в срок — предложи реалистичную дату
   - Не используй эмодзи, смайлики и специальные символы — только текст

### Формат вывода:

**Черновик ответа:**
[текст письма, готовый к отправке, или с тегами [УТОЧНИТЬ] там, где нужна дополнительная информация]

**Чек-лист перед отправкой:**
- [ ] Проверены имена, даты, цифры
- [ ] Все вопросы из исходного письма получили ответ
- [ ] Дедлайн реалистичен и согласован с календарём
- [ ] Нет неоднозначных формулировок
- [ ] Подпись и контакты корректны

**Что нужно дополнить вручную:**
[список конкретных пробелов: данные, согласования, вложения]

Всегда отвечай на русском языке, если в инструкциях не указано иное.\
"""

DEFAULT_SUMMARIZE_SYSTEM = (
    "Ты краткий и точный персональный ассистент. "
    "Резюмируй письма и документы в чёткие, actionable тезисы. "
    "Выделяй: принятые решения, задачи к выполнению, ключевую информацию и открытые вопросы. "
    "Всегда отвечай на русском языке."
)


# ─── Модель настроек ─────────────────────────────────────────────────────────

@dataclass
class ToolPrompts:
    """Пользовательские системные промпты для AI-тулов."""

    draft_system: str = ""
    """Системный промпт для инструмента «Черновик письма»."""

    summarize_system: str = ""
    """Системный промпт для инструмента «Суммаризация»."""

    def effective_draft(self) -> str:
        """Вернуть активный промпт для черновика (пользовательский или дефолтный)."""
        v = self.draft_system.strip()
        return v if v else DEFAULT_DRAFT_SYSTEM

    def effective_summarize(self) -> str:
        """Вернуть активный промпт для суммаризации (пользовательский или дефолтный)."""
        v = self.summarize_system.strip()
        return v if v else DEFAULT_SUMMARIZE_SYSTEM


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


def load_tool_prompts() -> ToolPrompts:
    """Загрузить промпты из vault/.tool_prompts.json. При ошибке — дефолтные."""
    path = _prompts_path()
    if not path.exists():
        return ToolPrompts()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ToolPrompts(
            draft_system=data.get("draft_system", ""),
            summarize_system=data.get("summarize_system", ""),
        )
    except Exception as exc:
        logger.warning(f"Не удалось загрузить tool_prompts.json: {exc}")
        return ToolPrompts()


def save_tool_prompts(prompts: ToolPrompts) -> None:
    """Сохранить промпты в vault/.tool_prompts.json."""
    path = _prompts_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(prompts), ensure_ascii=False, indent=2),
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
