#!/usr/bin/env python3
"""
generate_test_vault.py — создаёт реалистичный тест-vault для проверки AI-сценариев.

Генерирует:
  - Тред «Финансовый отчёт Q2» (3 письма: входящее → мой ответ → follow-up)
  - Тред «Согласование договора» (2 письма: поручение + уточнение)
  - Встречу «Квартальный обзор» на сегодня
  - Встречу «Синк по проекту» на завтра
  - Проект «Alpha Project» с упоминанием участников
  - Отдельное срочное письмо с дедлайном «сегодня»

Использование:
    python scripts/generate_test_vault.py [--vault ~/PersonalAssistantVault]
    python scripts/generate_test_vault.py --vault /tmp/test-vault

После генерации запустите сервер и проверьте:
  - GET /api/v1/today           — брифинг и события дня
  - GET /api/v1/brief/daily     — Daily Brief
  - GET /api/v1/inbox           — тред-письма в Inbox
  - POST /api/v1/inbox/{id}/draft-context  — контекст треда
  - GET /api/v1/calendar/upcoming          — встречи
  - GET /api/v1/calendar/{id}/prep         — подготовка к встрече
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _md(fm: dict, body: str) -> str:
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            sv = str(v)
            # Quote strings that contain colons to avoid YAML parse errors
            if ":" in sv and not sv.startswith("[") and not sv.startswith("{"):
                sv = f'"{sv}"'
            lines.append(f"{k}: {sv}")
    lines.append("---")
    lines.append("")
    lines.append(body.strip())
    return "\n".join(lines)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  ✓ {path}")


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate(vault: Path, user_email: str = "igor@example.com") -> None:
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y/%m")
    yesterday = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)
    four_days_ago = now - timedelta(days=4)

    THREAD_ID = "thread_q2_report_001"
    THREAD2_ID = "thread_contract_002"

    print(f"\n📁 Vault: {vault}")
    print(f"👤 User email: {user_email}")
    print()

    # ── Thread 1: Финансовый отчёт Q2 ─────────────────────────────────────────
    print("🧵 Тред 1: Финансовый отчёт Q2")

    # msg_001: входящее поручение
    write(
        vault / "mail" / today_str / "msg_q2_001.md",
        _md(
            {
                "id": "msg_q2_001",
                "source": "mail",
                "type": "email",
                "subject": "Финансовый отчёт за Q2",
                "sender": "Петров Алексей <petrov@corp.ru>",
                "to": user_email,
                "date": _iso(four_days_ago),
                "thread_id": THREAD_ID,
                "tags": ["urgency:high", "category:finance", "reply_required"],
                "reply_required": True,
                "intent": "request",
            },
            """Игорь,

Прошу подготовить финансовый отчёт за Q2 до пятницы 30 мая.

Необходимо включить:
1. Сводку по выручке
2. Сравнение с Q1 и планом
3. Ключевые отклонения

Отчёт нужен для совета директоров.

С уважением,
Алексей Петров
Финансовый директор""",
        ),
    )

    # msg_002: мой ответ
    write(
        vault / "mail" / today_str / "msg_q2_002.md",
        _md(
            {
                "id": "msg_q2_002",
                "source": "mail",
                "type": "email",
                "subject": "Re: Финансовый отчёт за Q2",
                "sender": f"Игорь <{user_email}>",
                "to": "petrov@corp.ru",
                "date": _iso(two_days_ago),
                "thread_id": THREAD_ID,
                "tags": ["category:finance"],
                "is_mine": True,
            },
            """Алексей,

Добрый день. Принял задачу, подготовлю отчёт к указанному сроку.

Уточняю: нужен ли расчёт в USD или только в RUB?
Также — включать ли раздел по CAPEX или только OPEX?

Игорь""",
        ),
    )

    # msg_003: follow-up от Петрова
    write(
        vault / "mail" / today_str / "msg_q2_003.md",
        _md(
            {
                "id": "msg_q2_003",
                "source": "mail",
                "type": "email",
                "subject": "Re: Re: Финансовый отчёт за Q2",
                "sender": "Петров Алексей <petrov@corp.ru>",
                "to": user_email,
                "date": _iso(now - timedelta(hours=12)),
                "thread_id": THREAD_ID,
                "tags": ["urgency:critical", "deadline:today", "category:finance"],
                "reply_required": True,
                "deadline": "today",
            },
            """Игорь,

Только RUB, CAPEX не нужен.

Напоминаю: срок — сегодня до 18:00. Совет завтра с утра.

Алексей""",
        ),
    )

    # ── Thread 2: Согласование договора ───────────────────────────────────────
    print("🧵 Тред 2: Согласование договора")

    write(
        vault / "mail" / today_str / "msg_contract_001.md",
        _md(
            {
                "id": "msg_contract_001",
                "source": "mail",
                "type": "email",
                "subject": "Согласование договора с ООО Ромашка",
                "sender": "Юридический отдел <legal@corp.ru>",
                "to": user_email,
                "date": _iso(yesterday),
                "thread_id": THREAD2_ID,
                "tags": ["urgency:medium", "category:legal", "reply_required"],
                "reply_required": True,
            },
            """Добрый день,

Необходимо согласовать договор оказания услуг с ООО «Ромашка» до 28 мая.

Основные условия:
- Сумма: 2 500 000 руб.
- Срок: 6 месяцев
- Исполнитель: ООО Ромашка

Прошу подтвердить условия или прислать правки.

Юридический отдел""",
        ),
    )

    write(
        vault / "mail" / today_str / "msg_contract_002.md",
        _md(
            {
                "id": "msg_contract_002",
                "source": "mail",
                "type": "email",
                "subject": "Re: Согласование договора с ООО Ромашка",
                "sender": "Юридический отдел <legal@corp.ru>",
                "to": user_email,
                "date": _iso(now - timedelta(hours=3)),
                "thread_id": THREAD2_ID,
                "tags": ["urgency:high", "category:legal", "deadline:today"],
                "deadline": "today",
            },
            """Напоминаем: срок согласования сегодня.

Если правок нет — пришлите подтверждение для подписания.

Юрист Смирнова""",
        ),
    )

    # ── Отдельное срочное письмо ───────────────────────────────────────────────
    print("📨 Срочное письмо (одиночное)")

    write(
        vault / "mail" / today_str / "msg_urgent_standalone.md",
        _md(
            {
                "id": "msg_urgent_standalone",
                "source": "mail",
                "type": "email",
                "subject": "Срочно: Отчёт для ЦБ до 17:00",
                "sender": "Комплаенс <compliance@corp.ru>",
                "to": user_email,
                "date": _iso(now - timedelta(hours=1)),
                "tags": ["urgency:critical", "deadline:today", "category:legal"],
                "reply_required": True,
                "deadline": "today",
            },
            """Игорь,

Срочно нужен отчёт для ЦБ РФ — форма 0409401.
Дедлайн: сегодня до 17:00.

Прошу подтвердить получение.

Комплаенс-офицер""",
        ),
    )

    # ── Встречи ────────────────────────────────────────────────────────────────
    print("🗓️  Встречи")

    # Сегодняшняя встреча (через 1 час)
    soon = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    write(
        vault / "calendar" / today_str / "meeting_quarterly_review.md",
        _md(
            {
                "id": "meeting_quarterly_review",
                "source": "calendar",
                "type": "meeting",
                "title": "Квартальный обзор Q2",
                "date": _iso(soon),
                "location": "Переговорная А-201",
                "attendees": [
                    "Петров Алексей <petrov@corp.ru>",
                    "Смирнова Анна <smirnova@corp.ru>",
                    user_email,
                ],
                "tags": ["встреча", "category:meetings"],
            },
            """Повестка:
1. Финансовые результаты Q2
2. Сравнение с планом
3. Прогноз на Q3

Ответственный: Игорь""",
        ),
    )

    # Завтрашняя встреча
    tomorrow = now + timedelta(days=1)
    tomorrow_10 = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
    write(
        vault / "calendar" / today_str / "meeting_sync_alpha.md",
        _md(
            {
                "id": "meeting_sync_alpha",
                "source": "calendar",
                "type": "meeting",
                "title": "Синк по проекту Alpha",
                "date": _iso(tomorrow_10),
                "location": "Zoom",
                "attendees": [
                    "Козлов Дмитрий <kozlov@corp.ru>",
                    user_email,
                ],
                "tags": ["встреча", "category:meetings"],
            },
            """Еженедельный синк по Alpha Project.
Обсудить прогресс разработки и риски.""",
        ),
    )

    # ── Проект ────────────────────────────────────────────────────────────────
    print("📁 Проект: Alpha Project")

    write(
        vault / "projects" / "alpha_project.md",
        _md(
            {
                "id": "alpha_project",
                "type": "project",
                "title": "Alpha Project",
                "status": "active",
                "tags": ["project", "active"],
            },
            """# Alpha Project

Основной проект Q2-Q3.

Команда: Козлов Дмитрий (lead), Игорь (PM), Смирнова Анна (QA).

## Статус
- Backend API: 80% готов
- Frontend: 60%
- Тестирование: начинается на следующей неделе

## Открытые задачи
- Необходимо согласовать архитектуру с Козловым до пятницы
- Подготовить демо для совета директоров
- Обсудить риски с Петровым""",
        ),
    )

    # ── Thread manifest ───────────────────────────────────────────────────────
    print("🔗 Thread manifests")

    write(
        vault / "threads" / f"{THREAD_ID}.md",
        _md(
            {
                "id": THREAD_ID,
                "type": "thread",
                "subject": "Финансовый отчёт за Q2",
                "participants": [
                    "Петров Алексей <petrov@corp.ru>",
                    f"Игорь <{user_email}>",
                ],
                "date": _iso(four_days_ago),
                "tags": ["category:finance", "urgency:high"],
                "message_count": 3,
            },
            """Тред: Финансовый отчёт за Q2

Письма: msg_q2_001, msg_q2_002, msg_q2_003

Петров поручил подготовить отчёт до пятницы 30 мая.
Необходимо подготовить финансовый отчёт за Q2 с данными по выручке.""",
        ),
    )

    write(
        vault / "threads" / f"{THREAD2_ID}.md",
        _md(
            {
                "id": THREAD2_ID,
                "type": "thread",
                "subject": "Согласование договора с ООО Ромашка",
                "participants": [
                    "Юридический отдел <legal@corp.ru>",
                    f"Игорь <{user_email}>",
                ],
                "date": _iso(yesterday),
                "tags": ["category:legal", "urgency:medium"],
                "message_count": 2,
            },
            """Тред: Согласование договора с ООО Ромашка

Письма: msg_contract_001, msg_contract_002

Необходимо согласовать договор на 2 500 000 руб. до сегодня.
Прошу подтвердить условия или прислать правки.""",
        ),
    )

    # ── index.json ────────────────────────────────────────────────────────────
    print("📋 index.json")
    index = {
        "generated_at": now.isoformat(),
        "vault": str(vault),
        "threads": [THREAD_ID, THREAD2_ID],
        "mail_count": 5,
        "calendar_count": 2,
        "project_count": 1,
    }
    write(vault / "index.json", json.dumps(index, ensure_ascii=False, indent=2))

    print(f"\n✅ Vault создан: {vault}")
    print("\nПроверьте сценарии:")
    print(f"  GET /api/v1/inbox                                   → {5} писем")
    print("  POST /api/v1/inbox/msg_q2_003/draft-context         → тред из 3 писем")
    print("  POST /api/v1/inbox/msg_contract_002/draft-context   → тред из 2 писем")
    print("  GET /api/v1/calendar/upcoming                       → 2 встречи")
    print("  GET /api/v1/calendar/meeting_quarterly_review/prep  → бриф с письмами Петрова")
    print("  GET /api/v1/brief/daily                             → сводка дня")
    print("  GET /api/v1/today                                   → dashboard")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate test vault for AI scenarios")
    parser.add_argument(
        "--vault",
        default=str(Path.home() / "PersonalAssistantVault"),
        help="Vault root path (default: ~/PersonalAssistantVault)",
    )
    parser.add_argument(
        "--email",
        default="igor@example.com",
        help="User email (default: igor@example.com)",
    )
    args = parser.parse_args()

    vault_path = Path(args.vault).expanduser()
    generate(vault_path, user_email=args.email)
