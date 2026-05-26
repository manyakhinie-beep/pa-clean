# AGENTS.md — pa-merge

> Файл для AI-ассистентов, работающих с кодовой базой. Проект, документация и комментарии в коде преимущественно на русском языке.

---

## Обзор проекта

**pa-merge** — оффлайн-персональный ассистент для macOS. Синхронизирует данные из Apple Calendar и Apple Mail в локальное Markdown-хранилище (vault), а затем отвечает на вопросы пользователя через локальный MLX-инференс (Apple Silicon). Данные не покидают компьютер.

Основные возможности:
- Синхронизация Calendar.app и Mail.app через AppleScript → vault из `.md`-файлов с YAML frontmatter.
- Браузерный WebUI (vanilla JS + SCSS) с семью вкладками: Сегодня, Inbox, Чат, Vault, Проекты, Правила, Поиск, Настройки.
- Локальный чат с AI на базе MLX-модели (Mistral, GigaChat и др.) с потоковой генерацией.
- Структурированное извлечение данных из писем (intent, reply_required, deadline, action_items).
- AI Priority Score, follow-up detection, thread-aware draft generation, smart meeting prep, daily brief.
- NLP для создания событий Calendar.app из естественного языка (русский).
- Гибридный поиск BM25 + семантический (sentence-transformers).
- Tool calling с реестром инструментов (`tools/registry.json`).

---

## Технологический стек

| Слой | Технология |
|---|---|
| Язык | Python 3.11–3.13 (требование MLX: `<3.14`) |
| Пакетный менеджер | [uv](https://github.com/astral-sh/uv) (Astral) — `uv sync`, `uv run` |
| Бэкенд | FastAPI + Uvicorn |
| Модели данных | Pydantic v2 |
| Локальный LLM | `mlx-lm` (только macOS arm64 / Apple Silicon M1+) |
| Векторный поиск | `sentence-transformers` + LanceDB (опционально, Stage M2) |
| Полнотекстовый поиск | `rank-bm25` |
| Планировщик | APScheduler (cron-синхронизация) |
| CLI | Click + Rich |
| Логирование | loguru |
| Frontend | Vanilla JS, SCSS (sass compiler), без фреймворков |
| Node.js | 18+ (только для сборки SCSS → CSS и копирования JS) |
| Тесты | pytest, pytest-mock, pytest-asyncio, httpx |
| Линтер | ruff |
| Типизация | mypy (`--ignore-missing-imports`) |
| Нагрузочное тестирование | Locust |

---

## Структура проекта

```
pa-merge/
├── src/personal_assistant/          # Основной Python-пакет
│   ├── config.py                    # Настройки из .env (класс Settings)
│   ├── models.py                    # Pydantic-модели: Contact, CalendarEvent, MailMessage
│   ├── cli.py                       # Точка входа CLI: `pa <command>`
│   ├── readers/                     # Читатели данных
│   │   ├── applescript_base.py      # Утилиты osascript, compute_thread_id
│   │   ├── calendar_reader.py       # Apple Calendar → CalendarEvent
│   │   ├── mail_reader.py           # Apple Mail → MailMessage
│   │   ├── outlook_reader.py        # Outlook (верхний уровень)
│   │   └── outlook_sqlite/          # Парсер Outlook SQLite + .olk15MsgSource
│   ├── sync/                        # Движок синхронизации
│   │   ├── dedup_engine.py          # Дедупликация по fingerprint
│   │   └── thread_tracker.py        # Группировка писем в треды
│   ├── vault/
│   │   └── writer.py                # VaultWriter — запись .md с frontmatter
│   ├── mlx_server/                  # FastAPI-приложение и инференс
│   │   ├── server.py                # FastAPI app, lifespan, маршруты верхнего уровня
│   │   ├── engine.py                # MLXEngine: load, stream, generate
│   │   ├── chat_routes.py           # /api/chat/*
│   │   ├── chat_db.py               # SQLite для истории чата
│   │   ├── context_builder.py       # Сборка system prompt
│   │   ├── vault_index.py           # BM25-индекс vault
│   │   ├── vector_index.py          # Семантический индекс (Stage M2)
│   │   ├── scheduler.py             # APScheduler, cron, pipeline
│   │   ├── tools/                   # Tool-calling инфраструктура
│   │   │   ├── date_calc.py         # Инструмент вычисления дат
│   │   │   ├── executor.py          # Выполнение tool calls
│   │   │   ├── router.py            # Маршрутизация по имени инструмента
│   │   │   └── validator.py         # Валидация аргументов
│   │   └── tasks/                   # Режимы чата и фоновые задачи
│   │       ├── classify.py          # Rule-based классификация
│   │       ├── draft_reply.py       # Генерация черновика ответа
│   │       ├── extract.py           # Структурированное извлечение из писем
│   │       ├── llm_classify_service.py # LLM-assisted классификация (Stage 8)
│   │       ├── priority.py          # AI Priority Score
│   │       ├── search.py            # Поиск по vault
│   │       └── summarize.py         # Суммаризация
│   ├── webui/
│   │   └── routes.py                # FastAPI-роуты WebUI (/vault/*, /search, …)
│   ├── profile/                     # Профиль пользователя и конфиг ассистента
│   ├── personal_vault/              # SQLite: ручные задачи, встречи, треды
│   ├── services/                    # Высокоуровневые сервисы
│   │   ├── mail_service.py
│   │   ├── calendar_service.py
│   │   ├── daily_brief_service.py
│   │   ├── draft_context_service.py
│   │   ├── followup_service.py
│   │   ├── meeting_prep_service.py
│   │   ├── rule_engine.py
│   │   ├── tag_history_service.py
│   │   ├── tool_prompts.py          # Кастомные системные промпты
│   │   ├── vault_filter_service.py
│   │   └── report_service.py
│   ├── reports/                     # Генерация и хранение отчётов
│   ├── calendar/                    # Calendar Intent NLP (Stage 7)
│   │   ├── intent_parser.py         # Парсер естественного языка
│   │   ├── calendar_writer.py       # Запись событий через AppleScript
│   │   └── routes.py                # API: parse-intent, create-from-text
│   ├── inbox/                       # API Inbox (v1)
│   │   └── routes.py
│   ├── today/                       # API «Сегодня» и Daily Brief
│   │   ├── routes.py
│   │   └── brief_routes.py
│   ├── utils/
│   │   ├── timezone.py              # MSK helpers
│   │   └── name_extractor.py        # Извлечение ФИО
│   └── templates/
│       └── mail.md.j2               # Jinja2-шаблон для .md писем
│
├── webui/                           # Фронтенд
│   ├── index.html                   # Точка входа
│   ├── frontend/
│   │   ├── js/                      # app.js, api.js, chat.js, vault.js, inbox.js, …
│   │   └── styles/
│   │       ├── main.scss            # Корневой импорт
│   │       ├── variables.scss       # Токены
│   │       └── components/          # _inbox.scss, _vault.scss, …
│   ├── dist/                        # Собранные файлы (коммитятся!)
│   ├── scripts/bundle-js.js         # Node-скрипт: копирует JS и index.html в dist/
│   └── package.json                 # Единственная devDep: sass
│
├── tests/
│   ├── conftest.py                  # Общие фикстуры (tmp_vault, sample_event, …)
│   ├── unit/                        # Модульные тесты
│   │   ├── readers/
│   │   ├── sync/
│   │   ├── vault/
│   │   ├── mlx/
│   │   ├── calendar/
│   │   ├── inbox/
│   │   └── services/
│   ├── e2e/                         # Сквозные тесты через FastAPI TestClient
│   │   └── test_server_routes.py
│   └── load/
│       └── locustfile.py            # Нагрузочные тесты
│
├── data/                            # Данные проекта
│   ├── classify.yaml                # Правила классификации + llm_classify конфиг
│   ├── persona.json                 # Имя/стиль ассистента
│   ├── gtd_rules.json               # GTD-правила
│   ├── eisenhower.json              # Матрица Эйзенхауэра
│   ├── inbox_state.json             # Состояние Inbox
│   ├── extraction_cache.json        # Кэш MLX-экстракции
│   └── llm_classify_cache.json      # Кэш LLM-классификации
│
├── tools/
│   └── registry.json                # Реестр доступных инструментов (tool calling)
│
├── scripts/
│   └── generate_test_vault.py       # Генератор тестового vault
│
├── pyproject.toml                   # Зависимости, ruff, mypy, pytest
├── .env.example                     # Шаблон конфигурации
├── make.sh                          # Task runner (build, run, test, lint, …)
├── setup.sh                         # Первичная установка (аналог make.sh build)
└── run.sh                           # Запуск сервера с проверками
```

---

## Команды сборки и запуска

Все команды выполняются через `./make.sh` или напрямую через `uv` / `npm`.

```bash
# ── Первый запуск ──
./make.sh build          # Полная сборка: Python deps + WebUI + .env

# ── Запуск ──
./make.sh run            # Сервер с авто-пересборкой WebUI (рекомендуется)
./make.sh dev            # FastAPI с auto-reload
./make.sh serve          # Сервер без проверки WebUI
./run.sh                 # Альтернатива make.sh run (проверяет SCSS и MLX)

# ── WebUI ──
./make.sh webui          # Собрать SCSS → CSS, скопировать JS
./make.sh webui-watch    # Watch-режим SCSS

# ── Синхронизация данных (только macOS) ──
./make.sh sync           # Синхронизировать Calendar + Mail → vault
uv run pa sync-all       # Через CLI
uv run pa sync-calendar  # Только Calendar
uv run pa sync-mail      # Только Mail

# ── Индексы ──
uv run pa build-index    # Построить векторный индекс (Stage M2)

# ── Тесты ──
./make.sh check          # lint + type-check + полный тест-сьют (CI)
./make.sh test           # Только unit-тесты
./make.sh test-fast      # Все тесты, остановиться на первой ошибке (-x)
./make.sh e2e            # E2E-сценарии
./make.sh test-cov       # Все тесты

# ── Качество кода ──
./make.sh lint           # ruff check src/ tests/
./make.sh format         # ruff format + auto-fix
./make.sh type-check     # mypy src/personal_assistant
```

### WebUI build

Сборка фронтенда — двухэтапная:
1. `sass frontend/styles/main.scss dist/css/main.css --style=compressed --no-source-map`
2. `node scripts/bundle-js.js` — копирует `frontend/js/*.js` в `dist/js/` и `index.html` в `dist/`.

Файлы в `webui/dist/` коммитятся в репозиторий — сервер отдаёт их как статику. `make.sh run` автоматически пересобирает WebUI, если исходники (`*.scss`, `*.js`, `index.html`) новее `dist/index.html`.

---

## Конфигурация

Все настройки — через `.env` (создаётся из `.env.example` при `./make.sh build`).

**Ключевые переменные:**
- `PA_VAULT_PATH` — путь к vault (по умолчанию `~/PersonalAssistantVault`).
- `PA_SYNC_SOURCES` — источники синхронизации: `calendar,mail`.
- `PA_MLX_MODEL_PATH` — абсолютный путь к локальной MLX-модели.
- `PA_SERVER_HOST` / `PA_SERVER_PORT` — адрес сервера (по умолчанию `127.0.0.1:8000`).
- `PA_SCHEDULE_ENABLED` / `PA_SCHEDULE_CRON` — авто-синхронизация по расписанию.
- `PA_MAIL_FETCH_ATTACHMENTS` — сохранять вложения из Mail.
- `PA_CLASSIFY_CONFIG_PATH` — путь к `classify.yaml` (по умолчанию `data/classify.yaml`).

Конфигурация загружается в `personal_assistant.config.settings` (класс `Settings`). Все переменные окружения имеют префикс `PA_`. Функции-хелперы: `_env()`, `_env_int()`, `_env_bool()`, `_env_float()`.

---

## Стратегия тестирования

- **Unit-тесты** (`tests/unit/`) — изолированные, не требуют macOS, MLX, сети или реального vault. Используют фикстуры `tmp_vault`, `sample_event`, `sample_mail`, `fixture_outlook_dir` из `conftest.py`.
- **E2E-тесты** (`tests/e2e/`) — сквозные сценарии через `fastapi.testclient.TestClient`. Покрывают HTTP-контракты всех API-эндпоинтов.
- **Load-тесты** (`tests/load/locustfile.py`) — Locust, запускаются через `./make.sh load`.

Запуск конкретного модуля:
```bash
uv run pytest tests/unit/sync/ -v
uv run pytest tests/unit/vault/ -v
uv run pytest tests/unit/mlx/ -v
uv run pytest tests/e2e/test_server_routes.py -v
```

**Важно:** тесты должны проходить без `mlx-lm` и без AppleScript. Если тест требует MLX — используйте `unittest.mock` или `pytest.mock`.

---

## Стиль кода и линтинг

- **Форматтер / линтер:** ruff (`line-length = 100`).
- **Импорты:** ruff сортирует импорты автоматически (`select = ["E", "F", "W", "I"]`).
- **Игнор:** `E501` (line too long) — линия 100 символов является мягким ограничением.
- **Типизация:** mypy с `ignore_missing_imports = true`.
- **Docstrings:** модули и публичные функции должны иметь docstrings на русском или английском.
- **Future imports:** во всех новых файлах используй `from __future__ import annotations`.

Перед коммитом:
```bash
./make.sh check   # lint + type-check + unit + e2e
```

---

## Архитектурные соглашения

### Vault-файлы

Каждый синхронизированный объект — `.md` с YAML frontmatter:

```yaml
---
message_id: "<abc123@corp.ru>"
thread_id: "e38582b9759b"
title: "Отчёт за май"
type: mail-message
source: mail
sender_name: "Иван Петров"
from: "ivan@corp.ru"
date: 2026-05-20T14:30:00+03:00
tags: [urgency:important, category:finance]
attachments:
  - path: attachments/abc123/report.pdf
    name: report.pdf
    size: 204800
---
```

Треды группируются по `thread_id` (MD5[:12] нормализованной темы без `Re:/Fwd:/Отв:`).

### Классификация

- Rule-based классификатор определён в `data/classify.yaml`.
- Теги имеют формат `urgency:urgent`, `category:finance`, `action:needs_reply`.
- LLM-assisted классификация (Stage 8) срабатывает, когда `rule_confidence < threshold` (по умолчанию 0.4).
- После изменения `classify.yaml` в UI есть кнопка «Применить к vault» — теги обновляются без перезапуска сервера.

### Tool Calling

- Реестр инструментов: `tools/registry.json`.
- Диспетчеризация: `mlx_server/tools/router.py` → `_run_builtin()`.
- Встроенный инструмент `date_calc` поддерживает русские и английские относительные даты.
- Чтобы добавить инструмент:
  1. Создать `src/personal_assistant/mlx_server/tools/my_tool.py` с функцией `run(args: dict) -> str`.
  2. Добавить запись в `tools/registry.json`.
  3. Добавить диспетчеризацию в `router.py`.
  4. Указать в системном промпте через `context_builder.py`.

### MLX-инференс

- `mlx-lm` работает **только на Apple Silicon M1+** (macOS arm64). На Intel Mac сервер запустится, но чат вернёт 503.
- Модель загружается при старте сервера, если `--preload-model` (по умолчанию включено в `run.sh`).
- Все функции с MLX должны иметь graceful degradation: если `engine is None` или модель недоступна, вернуть rule-based результат или минимальный valid dict.

### Синхронизация

- Синхронизация **только читает** данные из Calendar/Mail. Оригинальные данные не изменяются.
- AppleScript вызывается через `osascript` shell-команды (без PyObjC).
- Таймауты: `PA_CALENDAR_PER_CAL_TIMEOUT` (по умолчанию 45 с), `PA_MAIL_PER_MBOX_TIMEOUT` (45 с).

---

## Безопасность

- `.env` содержит пути и токены — **никогда не коммитьте его** (уже в `.gitignore`).
- `PA_SERVER_HOST` по умолчанию `127.0.0.1` — сервер слушает только localhost.
- Вложения из Mail сохраняются в `PA_MAIL_ATTACHMENTS_PATH` (по умолчанию внутри vault).
- MLX-модель загружается локально, никакие данные не отправляются во внешние API.
- Все file-path операции используют `Path.expanduser()` для поддержки `~`.

---

## Полезные подсказки для агента

- При изменении зависимостей обновляйте `pyproject.toml`, затем `uv sync --group dev`.
- При изменении SCSS или JS не забудь `./make.sh webui` — иначе сервер отдаст старый `dist/`.
- Если нужно протестировать функцию, зависящую от vault — используй фикстуру `tmp_vault` из `conftest.py`.
- Если добавляешь новый API-эндпоинт — добавь E2E-тест в `tests/e2e/test_server_routes.py`.
- Кодовая база билингвальная: комментарии и документация на русском, имена переменных/функций — на английском.
- Все скрипты (`make.sh`, `setup.sh`, `run.sh`) используют `set -e` / `set -euo pipefail` — будь осторожен с непроверенными командами.
