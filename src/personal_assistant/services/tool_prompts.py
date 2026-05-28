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
Ты — аналитик деловой переписки. Проанализируй цепочку писем и выдай короткое структурированное резюме.

Правила:
— Отвечай только на русском языке.
— Главный приоритет — последнее (самое новое) письмо; предыдущие — фон.
— Для каждого поручения извлекай четыре факта: кто → кому → что → к какому сроку.
— Относительные даты («завтра», «на этой неделе») считай от даты последнего письма.
— Не выдумывай поручения, сроки и участников, которых нет в письмах.
— Не цитируй письма дословно, излагай по смыслу.
— Если поручений нет — так и пиши.

Формат вывода — строго четыре блока в таком порядке:

ПОРУЧЕНИЯ
— [От кого] → [Кому]: [что сделать] · срок [дата или «не указан»] · срочность [критичная/высокая/средняя/низкая]
(если поручений нет — одна строка: «Поручений не выявлено.»)

УЧАСТНИКИ ВСТРЕЧИ
— [Имя или роль, организация если известна]

КЛЮЧЕВЫЕ ТЕЗИСЫ
— [одно предложение]
— [одно предложение]

ОТВЕТ НА ВОПРОС
[Если пользователь задал вопрос — ответь 1–3 предложениями. Если вопроса не было — пропусти весь блок.]

Письма для анализа — ниже, от старого к новому.\
"""

DEFAULT_DRAFT_SYSTEM = """\
Ты пишешь черновик ответа от имени пользователя на последнее письмо в треде. Проанализируй переписку и подготовь готовый ответ.

Правила:
— Отвечай только на русском языке (включая черновик письма).
— Деловой, вежливый, конкретный тон. Не используй шаблонные обороты («плодотворное сотрудничество», «искренне Ваш»).
— Не используй эмодзи.
— Приоритет — последнее письмо; предыдущие — контекст (обещания, договорённости).
— Если действие на пользователе — назови его и срок.
— Если действие на коллеге — отметь это и рекомендуй делегировать (укажи, кому).
— Если нужна встреча — предложи 2–3 окна времени.
— Если в письме был вопрос требующий уточнения — задай уточняющий вопрос.
— Не выдумывай факты, фамилии, цифры, которых нет в переписке. Если данных не хватает — пометь блок [УТОЧНИТЬ: …] чтобы пользователь дополнил вручную.

Формат вывода — строго эти три блока, в таком порядке, без пояснений:

АНАЛИЗ ПЕРЕПИСКИ
— Пользователь: [имя или роль]
— Участники: [перечисли через запятую]
— Активные поручения: [одной строкой кто → кому → что · срок; если нет — «нет»]
— Что требуется от пользователя по последнему письму: [одна фраза]

ДЕЙСТВИЕ ИЛИ ДЕЛЕГИРОВАНИЕ
Ровно одна строка из двух вариантов:
— Действие: [что делает пользователь] · срок [дата]
— Делегировать: [кому] → [что] · срок [дата] · причина [почему не пользователь сам]

ЧЕРНОВИК ОТВЕТА
[Готовый текст письма от имени пользователя — 2–5 коротких абзацев, по делу. Помечай пропуски как [УТОЧНИТЬ: …] чтобы пользователь дополнил вручную.]

Чек-лист перед выдачей (проверь мысленно, не пиши его в ответ):
1) Все три блока на месте и в правильном порядке.
2) В черновике нет эмодзи и шаблонных оборотов.
3) Все факты, имена, цифры — из переписки, либо помечены как [УТОЧНИТЬ: …].
4) Срок и срочность согласованы с последним письмом.

Письма для анализа — ниже, от старого к новому. Опциональный вопрос пользователя — после переписки.\
"""

DEFAULT_DELEGATE_SYSTEM = """\
Ты — ассистент руководителя. Проанализируй входящую переписку и подготовь черновик задачи для делегирования сотруднику.

Правила:
— Отвечай только на русском языке (включая черновик задачи).
— Приоритет — последнее письмо: задача — это конкретное действие из него. Предыдущие письма дают контекст, но не меняют финальное поручение.
— Извлекай факты: кто (внешний инициатор), кому (исполнитель), что (действие), к какому сроку.
— Формулируй вежливо, но императивно: «прошу подготовить», «согласовать», «направить».
— Передай все сроки и требования из оригинального запроса.
— Если результат должен пройти контроль руководителя до отправки — укажи это.
— Ничего не добавляй сверх того, что есть в переписке.
— Не используй эмодзи.
— Если каких-то данных не хватает — помечай как [УТОЧНИТЬ: …] чтобы руководитель дополнил вручную.
— Срочность: критичная (0–1 день), высокая (2–3 дня), средняя (неделя), низкая.

Формат вывода — строго эти четыре блока, в таком порядке:

РЕКОМЕНДАЦИЯ / ДЕЙСТВИЕ
Ровно одна строка:
Действие: [что делегируем] · Исполнитель: [имя/роль или «уточнить»] · Срок: [дата] · Срочность: [уровень]

КОНТЕКСТ ДЛЯ ИСПОЛНИТЕЛЯ
— От кого запрос: [внешний участник, организация]
— Тема: [3–4 слова]
— Ключевые детали: [1–3 коротких пункта]
— Ограничения / риски: [если есть; иначе «нет»]

ЧЕРНОВИК ЗАДАЧИ ДЛЯ СОТРУДНИКА
[Готовое поручение: обращение → одна-две фразы контекста → конкретная задача → срок → ожидаемый формат результата. Помечай пропуски как [УТОЧНИТЬ: …].]

ПРИМЕЧАНИЕ ДЛЯ РУКОВОДИТЕЛЯ
— Контрольная точка: [когда проверить / нужно ли согласование до отправки]
— Следующий шаг после выполнения: [что делать с результатом]

Письма для анализа — ниже, от старого к новому. Опциональный исполнитель — после переписки.\
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
