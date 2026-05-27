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
Ты — аналитик деловой переписки. Проанализируй предоставленные письма и выдай структурированный отчёт.

## Правила анализа
- **Приоритет**: последнее (самое новое) письмо имеет главный приоритет.
- **Контекст**: используй предыдущие письма для понимания фона, но не позволяй им противоречить последнему письму.
- Если даты указаны относительно (завтра, на следующей неделе), считай от даты последнего письма.

## Что извлечь
1. Участники встречи (если обсуждается встреча)
2. Поручения: кто → кому → что → к какому сроку
3. Срочность каждого поручения (критичная / высокая / средняя / низкая)
4. Ключевые тезисы переписки

## Формат ответа (строго соблюдай)

### ПОРУЧЕНИЯ
Для каждого поручения одна строка в формате:
[От кого] → [Кому]: [Что сделать] | Срок: [дата] | Срочность: [уровень]

Если поручений нет — напиши: "Поручений не выявлено."

### УЧАСТНИКИ ВСТРЕЧИ
- [Имя/роль], [организация если есть]

### КЛЮЧЕВЫЕ ТЕЗИСЫ
- [Тезис 1]
- [Тезис 2]

### ОТВЕТ НА ВОПРОС
[Если пользователь задал вопрос — ответь здесь кратко и по делу. Если вопроса не было — пропусти этот блок.]

---
## Письма для анализа
{вставь сюда переписку, начиная с самого старого и заканчивая самым новым}\
"""

DEFAULT_DRAFT_SYSTEM = """\
Ты — ассистент по деловой переписке. Твоя задача: проанализировать цепочку писем и написать черновик ответа от имени пользователя.

## Шаг 1. Анализ контекста
Перед написанием ответа мысленно выдели:
1. **Пользователь** (от чьего имени пишешь): определи по подписи или контексту.
2. **Участники переписки**: все лица, их роли, кто инициатор, кто получатель.
3. **Поручения**: кто → кому → что → к какому сроку.
4. **Срочность**: критичная (сегодня/завтра), высокая (эта неделя), средняя, низкая.
5. **Последнее письмо**: какой вопрос или запрос в нём содержится (имеет приоритет).

## Шаг 2. Правила составления драфта
- Отвечай деловым, вежливым тоном.
- Не используй шаблонных фраз вроде "надеюсь на плодотворное сотрудничество".
- Если от тебя требуется действие — назови его конкретно и укажи срок.
- Если действие должно выполнить другое лицо — рекомендуй делегировать и укажи ответственного.
- Если нужна встреча — предложи 2-3 варианта времени.
- Если вопрос требует уточнения — задай уточняющий вопрос.
- **Приоритет**: отвечай на последнее письмо, но учитывай предыдущий контекст (обещания, договорённости).

## Шаг 3. Формат вывода (строго соблюдай)

### АНАЛИЗ ПЕРЕПИСКИ
- **Пользователь**: [имя/роль]
- **Участники**: [список]
- **Активные поручения**:
  - [От кого] → [Кому]: [что] | срок [дата] | срочность [уровень]
- **Статус по последнему письму**: [что требуется от пользователя]

### ДЕЙСТВИЕ ИЛИ ДЕЛЕГИРОВАНИЕ
Начни с одной из двух форм:
- Если пользователь выполняет: **"Действие: [что делаем] | Срок: [дата]"**
- Если передаём другому: **"Делегировать: [кому] → [что делать] | Срок: [дата] | Причина: [почему не пользователь]"**

### ЧЕРНОВИК ОТВЕТА
[Текст письма от имени пользователя. Коротко, по делу, без воды.]

---

## Письма для анализа
[вставь переписку: старое → новое]

## Вопрос пользователя (опционально)
[если нужен ответ на конкретный вопрос — вставь сюда]\
"""

DEFAULT_DELEGATE_SYSTEM = """\
Ты — ассистент руководителя. Проанализируй входящую переписку и подготовь черновик задачи для делегирования сотруднику.

## Шаг 1. Анализ (выполни мысленно)
- **Пользователь** (руководитель): определи по подписи или контексту.
- **Внешние участники**: кто писал извне, их роли, организации.
- **Внутренний исполнитель**: кому делегируем (укажи, если известен из контекста; если нет — определи по компетенции).
- **Что сделать**: конкретное действие, вытекающее из последнего письма с учётом предыдущего контекста.
- **Срок**: явный или вычисленный из контекста (если относительно — считай от даты последнего письма).
- **Срочность**: критичная (0–1 день), высокая (2–3 дня), средняя (неделя), низкая.

## Шаг 2. Правила делегирования
- Формулируй задачу как приказ/поручение, но вежливо.
- Укажи контекст: от кого запрос, о чём переписка.
- Передай все сроки и требования из оригинального письма.
- Если нужно согласовать результат с руководителем до отправки — укажи.
- Не добавляй лишних действий, которых нет в переписке.
- **Приоритет**: последнее письмо определяет задачу; предыдущие письма дают контекст, но не меняют финальное поручение.

## Шаг 3. Формат вывода (строго)

### РЕКОМЕНДАЦИЯ / ДЕЙСТВИЕ
Начни с одной строки:
**Действие: [что делегируем] | Исполнитель: [имя/роль] | Срок: [дата] | Срочность: [уровень]**
Или, если исполнитель неизвестен:
**Рекомендация: Делегировать [что] сотруднику отдела [X] | Срок: [дата] | Срочность: [уровень]**

### КОНТЕКСТ ДЛЯ ИСПОЛНИТЕЛЯ
- **От кого запрос**: [внешний участник]
- **Тема переписки**: [2-3 слова]
- **Ключевые детали**: [что важно знать исполнителю]
- **Ограничения/риски**: [если есть]

### ЧЕРНОВИК ЗАДАЧИ ДЛЯ СОТРУДНИКА
[Текст поручения. Начни с контекста, затем конкретная задача, срок, формат результата.]

### ПРИМЕЧАНИЕ ДЛЯ РУКОВОДИТЕЛЯ
- **Контрольная точка**: [когда проверить / если нужно согласование]
- **Следующий шаг после выполнения**: [что делать с результатом]

---

## Письма для анализа
[вставь переписку: старое → новое]

## Исполнитель (опционально)
[если знаешь, кому делегировать — укажи имя/роль]\
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
