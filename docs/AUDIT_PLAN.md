# План аудита и рефакторинга: pa-merge → pa-clean

Документ составлен по инструкции проекта pa-clean и **привязан к фактическому состоянию** репозитория `pa-merge` (разведка проведена 2026-05-25). Все 7 фаз ниже учитывают то, что реально найдено в коде, а не шаблонные предположения.

---

## 0. Сводка разведки (факты, на которых строится план)

### 0.1. Стек и точки входа
- **Язык/сборка:** Python `>=3.10,<3.14` (`.python-version` фиксирует **3.12**), пакет `pa-merge` v1.0.0, сборка через `hatchling`, зависимости через **uv** (`uv.lock` присутствует).
- **Backend:** FastAPI + uvicorn. CLI-вход: `pa = personal_assistant.cli:main`. Планировщик: APScheduler.
- **Локальный ИИ:** `mlx-lm` (инференс), опционально векторный поиск (`sentence-transformers`, `numpy`), BM25 (`rank-bm25`).
- **Интеграции Apple:** Mail и Calendar через **AppleScript/osascript** (без PyObjC). Присутствует и legacy-слой Outlook (`readers/outlook_sqlite/`, `readers/outlook_reader.py`) — несмотря на коммит «remove outlook».
- **WebUI:** **vanilla JS-модули + SCSS** (НЕ React). Точка входа `webui/index.html`, модули в `webui/frontend/js/*.js`.
- **Объём:** ~23.6k строк в `src/`, ~16.6k строк тестов (35 файлов `test_*.py`), ~15.9k строк JS в `webui/`.

### 0.2. Точки входа (entry points)
| Тип | Где | Примечание |
|-----|-----|-----------|
| CLI | `src/personal_assistant/cli.py` (`pa`) | основной CLI |
| HTTP API | FastAPI-роуты: `*/routes.py` в `inbox`, `calendar`, `today`, `profile`, `reports`, `personal_vault`, `webui`, `mlx_server/chat_routes.py` | |
| MLX-сервер | `src/personal_assistant/mlx_server/server.py`, `engine.py`, `scheduler.py` | инференс + задачи |
| WebUI | `webui/index.html` + `webui/frontend/js/app.js` | вкладки: today, inbox, chat, projects, vault, search, reports, **rules**, **settings** |
| Скрипты | `make.sh`, `run.sh`, `setup.sh`, `fix_env.sh` | оболочки запуска/сборки |

### 0.3. Критические находки (drivers плана)

> ⚠️ **Среда выполнения.** В текущем Linux-песочнице **собрать и запустить нельзя**: `mlx-lm` имеет колёса только под macOS arm64, а `uv.lock` ограничен `sys_platform == 'darwin' and platform_machine == 'arm64'`. Любая проверка «собирается ли / запускается ли» и все `scenario`-тесты выполняются **на Mac пользователя (Apple Silicon)**. Это согласуется с требованием инструкции «не предполагать работоспособность — проверять запуском».

1. **Конфиг не редактируется в рантайме.** `config.py` читает переменные `PA_*` из `.env` и вычисляет значения как **атрибуты класса на этапе импорта**. Нет `config.json`, нет двусторонней привязки, нет немедленного сохранения. Требование инструкции (§3.2) пока **не выполнено** на уровне архитектуры.
2. **Вкладка «Правила» ≠ настройки ИИ.** `webui/frontend/js/rules.js` — это **матрица Эйзенхауэра + GTD-правила + «Инструменты»** (`data/rules.json`). Настройки ИИ-инструментов там **не отображаются**; частично они живут в `settings.js`. Требование §3.1 (все настройки ИИ во вкладке «Правила») **не выполнено**.
3. **Отсутствуют обязательные настройки.** Проверка по `config.py`:

   | Настройка (из §3.1) | В config.py | UI «Правила» |
   |---|---|---|
   | `mlx_model_path` | ✅ есть | ❌ нет |
   | `mlx_temperature` | ✅ есть | ❌ нет |
   | `mlx_max_tokens` | ✅ есть | ❌ нет |
   | `mlx_top_p` | ❌ **нет** | ❌ нет |
   | `mail_auto_draft` | ❌ **нет** | ❌ нет |
   | `mail_summary_prompt` | ❌ **нет** | ❌ нет |
   | `calendar_check_conflicts` | ❌ **нет** | ❌ нет |
   | `calendar_default_duration` | ❌ **нет** | ❌ нет |
   | `e2e_test_mode` | ❌ **нет** | ❌ нет |

4. **Pytest-маркеры не зарегистрированы.** В `pyproject.toml` нет секции `markers`; в тестах используются только `parametrize`/`skipif`. Инструкция требует маркеры `unit/e2e/scenario/mlx/mail/calendar`.
5. **Покрытие не настроено.** Нет конфигурации coverage; целевое значение по инструкции — **≥80%**.
6. **CI отсутствует.** Каталог `.github/` есть, но **workflow-файлов нет**. Нужен GitHub Actions с macOS-раннером.
7. **Гигиена репозитория.** Найдены: `pyproject.toml.bak`, `readers/__init__.py.bak`, пустой `souls.md`, `.DS_Store`, `.coverage.*`, `.index_cache.pkl`, `.fuse_hidden*` в `data/`, legacy outlook-код. `.env` **не** в git (хорошо), отслеживается только `.env.example` — но скан секретов всё равно нужен.

---

## Фаза 1 — Разведка (Discovery)

**Статус: частично выполнена (см. §0). Остаётся проверить на Mac.**

- [x] Зафиксирован стек, точки входа, конфиг-файлы, зависимости (uv/pyproject), наличие документации (`README.md`, `AGENTS.md`, `docs/*`).
- [ ] **На Mac (Apple Silicon):** воспроизводимая установка `uv sync` → запуск `./run.sh` / `pa --help` / старт FastAPI; зафиксировать ошибки сборки/запуска.
- [ ] `git log` уже снят (последние коммиты: «some tests», «fix body bug», «fix chat draft»…). Проверить наличие незакоммиченных артефактов и `.bak`-файлов.
- [ ] Проверить устаревшие/уязвимые зависимости (`uv pip list --outdated`, аудит).
- [ ] Найти жёстко закодированные пути/ключи (`grep` по `/Users/`, токенам, абсолютным путям).

**Артефакт фазы:** `docs/DISCOVERY.md` — стек, точки входа, состояние сборки, список рисков.

---

## Фаза 2 — Аудит функционала

### 2.1. Карта функционала (Functionality Map)
Составить таблицу по реально существующим роутам/командам/экранам. Каркас (заполнить статусами после прогона на Mac):

| ID | Функция | Модуль | Точка входа | Статус | Примечания |
|----|---------|--------|-------------|--------|------------|
| F1 | Синхронизация Calendar → vault | `readers/calendar_reader.py`, `services/calendar_service.py` | CLI/API | ??? | AppleScript |
| F2 | Синхронизация Mail → vault | `readers/mail_reader.py`, `services/mail_service.py` | CLI/API | ??? | AppleScript; legacy Outlook рядом |
| F3 | Запись vault (md + frontmatter) | `vault/writer.py`, `personal_vault/*` | внутр. | ??? | шаблоны Jinja2 |
| F4 | Дедуп/threading писем | `sync/dedup_engine.py`, `sync/thread_tracker.py`, `services/thread_graph_service.py` | внутр. | ??? | **core pa-merge logic** |
| F5 | Классификация (Эйзенхауэр/GTD) | `services/rule_engine.py` (`data/rules.json`) | API/UI «Правила» | ??? | |
| F6 | MLX-инференс (summarize/classify/draft/extract/search) | `mlx_server/tasks/*`, `engine.py` | API | ??? | macOS arm64 |
| F7 | Tool-calling pipeline | `mlx_server/tools/{router,executor,validator,date_calc}.py` | внутр. | ??? | |
| F8 | Черновики ответов | `services/draft_context_service.py`, `mlx_server/tasks/draft_reply.py` | API/UI inbox | ??? | `mail_auto_draft` (нет) |
| F9 | Создание событий / intent | `calendar/intent_parser.py`, `calendar/calendar_writer.py` | API | ??? | `calendar_default_duration` (нет) |
| F10 | Daily brief / meeting prep | `services/daily_brief_service.py`, `services/meeting_prep_service.py`, `today/*` | API/UI today | ??? | |
| F11 | Отчёты | `reports/{generator,store,routes}.py`, `services/report_service.py` | API/UI reports | ??? | |
| F12 | Профиль / souls | `profile/*` | API/UI | ??? | `souls.md` пуст |
| F13 | Поиск (BM25 + vector) | `mlx_server/{vector_index,vault_index}.py` | API/UI search | ??? | optional `vector` |

**Методика:** для каждой функции зафиксировать вход/ожидаемый результат/побочные эффекты; отдельно пометить функции с ИИ (F5–F9, F13).

### 2.2. Аудит интеграций (чеклисты инструкции)
**MLX:** инициализация в `mlx_server/engine.py`; загрузка модели по `mlx_model_path` или автоскачивание; fallback на CPU/недоступность GPU; замер latency базовых задач.

**Apple Mail:** доступ через AppleScript; права (TCC / Full Disk Access / Automation); декодирование RTF/HTML тела; создание черновиков (`draft_reply`). Решить судьбу **legacy Outlook-кода** (удалить или вынести за флаг).

**Apple Calendar:** чтение событий (`calendar_reader.py`); создание событий (`calendar_writer.py`); проверка занятости слотов (free/busy) — связать с будущим `calendar_check_conflicts`.

### 2.3. Аудит архитектуры
- [ ] Разделение слоёв UI / business / data / integrations (сейчас сервисы в `services/`, ридеры в `readers/`, UI в `webui/` — оценить чистоту границ).
- [ ] **Единый конфиг-слой** — главный архитектурный долг (см. §0.3 п.1).
- [ ] Обработка ошибок и логирование (`loguru`, каталог `logs/`).
- [ ] Управление состоянием на фронте (vanilla JS, без фреймворка).

**Артефакт фазы:** `docs/FUNCTIONALITY_MAP.md` + раздел «Issues» (реестр проблем с приоритетами).

---

## Фаза 3 — Вкладка «Правила» (AI Tool Settings)

Цель: вывести **все 9 настроек ИИ** в UI «Правила» с двусторонней привязкой, немедленным сохранением, валидацией и подсказками. Это самый крупный функциональный блок по инструкции.

### 3.1. Конфиг и слой персистентности
- [ ] Расширить `config.py`: добавить отсутствующие `mlx_top_p`, `mail_auto_draft`, `mail_summary_prompt`, `calendar_check_conflicts`, `calendar_default_duration`, `e2e_test_mode` (с дефолтами и `PA_*`).
- [ ] Ввести **редактируемый рантайм-слой**: `data/config.json` (или UserDefaults-аналог) поверх env-дефолтов; убрать вычисление настроек «на импорте» в пользу загружаемого объекта `Settings`, перечитываемого после сохранения.
- [ ] API: `GET /api/rules/settings` и `POST/PATCH /api/rules/settings` (валидация + атомарная запись `config.json`).

### 3.2. UI (вкладка «Правила»)
- [ ] Добавить под-вкладку «Инструменты ИИ» в `webui/frontend/js/rules.js` + стили `styles/components/_rules.scss` (или `webui/scss/_settings.scss`).
- [ ] Поля для всех 9 настроек с `data-testid` (для E2E): `mlx-model-path`, `mlx-temp`, `mlx-max-tokens`, `mlx-top-p`, `mail-auto-draft`, `mail-summary-prompt`, `calendar-check-conflicts`, `calendar-default-duration`, `e2e-test-mode`.
- [ ] Двусторонняя привязка к конфигу; **немедленное сохранение** при изменении; кнопка `save-rules` для явного сохранения.
- [ ] Валидация (`temperature` ∈ [0.0, 2.0], `top_p` ∈ [0,1], целые > 0); подсказки (tooltips) для каждой настройки.

### 3.3. Применение настроек при вызове инструментов
- [ ] `MLXClient`/`engine.py` читает `temperature/max_tokens/top_p/model_path` **из конфига**, а не из хардкода.
- [ ] Mail-функции уважают флаг `mail_auto_draft`; суммаризация использует `mail_summary_prompt`.
- [ ] Calendar-функции используют `calendar_default_duration`; конфликт-чек включается `calendar_check_conflicts`.
- [ ] Middleware/декоратор инъекции настроек перед вызовом инструмента; `e2e_test_mode` подавляет реальные side-effects.

**Критерий приёмки фазы:** изменение настройки в UI → запись в `config.json` → значение реально применяется в вызове инструмента (подтверждается тестом из §4.2).

---

## Фаза 4 — Тестирование

### 4.1. Инфраструктура тестов (сделать первым)
- [ ] Зарегистрировать маркеры в `pyproject.toml`: `unit`, `e2e`, `scenario`, `mlx`, `mail`, `calendar`.
- [ ] Проставить маркеры по существующим каталогам (`tests/unit`, `tests/e2e`, `tests/scenarios`) — фактически уже разложено по папкам.
- [ ] Включить coverage: `pytest --cov=src --cov-report=term-missing --cov-report=html`; зафиксировать порог.
- [ ] Расширить `tests/conftest.py` / `tests/scenarios/conftest.py` фикстурами для `e2e_test_mode` и тестового календаря.

### 4.2. Unit-тесты (цель ≥80%)
Приоритетные модули: `config.py` (новый рантайм-слой), `mlx_server/engine.py`, `readers/mail_reader.py` + `outlook_sqlite/parser.py`, `readers/calendar_reader.py` + `calendar/intent_parser.py`, **core merge-логика** (`sync/dedup_engine.py`, `sync/thread_tracker.py`, `services/thread_graph_service.py`).

### 4.3. E2E-тесты (WebUI)
- [ ] Выбрать инструмент (Playwright — Python, чтобы жить в одном стеке).
- [ ] Сценарии: запуск WebUI → переход во вкладку «Правила» → изменение MLX-настройки → сохранение → проверка `config.json`; запуск ИИ-операции через UI → проверка вызова MLX; цикл Mail → AI → Calendar.
- [ ] Использовать `data-testid` из §3.2 (пример теста `test_rules_tab_saves_mlx_settings` из инструкции).

### 4.4. Scenario / Integration (на Mac)
- [ ] `scenario + mlx`: реальный инференс с моделью из настроек (`tests/scenarios/test_mlx_scenarios.py`).
- [ ] `scenario + mail`: чтение реальных писем, декодирование тела (`test_mail_body_scenarios.py`, `test_draft_*`).
- [ ] `scenario + calendar`: free/busy + создание/удаление события **в тестовом календаре** (`test_calendar_scenarios.py`).
- [ ] Жёсткое правило: никаких реальных отправок писем; только тестовые папки/календари; cleanup после теста.

### 4.5. CI
- [ ] `.github/workflows/ci.yml`: lint (`ruff`, `mypy`) + `pytest -m unit` на каждом PR.
- [ ] Отдельный job на **macOS-раннере** для `scenario`/`e2e` (где доступен MLX/Apple).
- [ ] HTML coverage как артефакт сборки.

**Команды:** `pytest -m unit` · `pytest -m e2e` · `pytest -m "scenario and mlx"` · `pytest -m "scenario and mail"` · `pytest -m "scenario and calendar"`.

---

## Фаза 5 — Документация

- [ ] `README.md` — актуализировать быстрый старт (uv, run.sh, требования macOS arm64). Уже большой (~60KB) — выверить на соответствие коду.
- [ ] `ARCHITECTURE.md` — диаграмма слоёв (readers → services → mlx_server → webui) и потоки данных.
- [ ] `RULES.md` — описание всех 9 настроек вкладки «Правила» (тип, диапазон, дефолт, где применяется).
- [ ] `TESTING.md` — как запускать группы тестов, требования к окружению (Mac, MLX, права TCC).
- [ ] `INTEGRATIONS.md` — MLX / Mail / Calendar: требования, права доступа, ограничения, судьба legacy Outlook.
- [ ] Docstrings (Google/NumPy) + type hints; `mypy` strict для нового кода.

> В `docs/` уже есть материалы (`PLAN.md`, `SCENARIO_TEST_PLAN.md`, `SYSTEM_TESTING_REPORT.md`, `USER_GUIDE.md` и др.) — переиспользовать и не дублировать.

---

## Фаза 6 — Миграция в чистый проект (pa-clean)

Перенос функционала в `pa-clean` поэтапно, каждый этап — только с зелёными тестами.

| Этап | Функционал | Тесты | Критерий приёмки |
|------|-----------|-------|------------------|
| 1 | Конфиг + рантайм-слой + вкладка «Правила» | Unit | Все 9 настроек сохраняются, валидируются, читаются |
| 2 | Core merge-логика (dedup/threading, vault writer) — без ИИ | Unit + E2E | Базовый мердж стабилен |
| 3 | MLX-интеграция | Unit + Scenario | Модель грузится, инференс работает, настройки применяются |
| 4 | Mail-интеграция | Unit + Scenario | Чтение, декодирование, черновики; legacy Outlook решён |
| 5 | Calendar-интеграция | Unit + Scenario | Чтение, слоты, создание встреч |
| 6 | WebUI — полный цикл | E2E | Сценарий от входа до результата |
| 7 | Документация + CI | — | README/архитектура + автоматические проверки |

**Критерии «чистоты» каждого этапа:** проходит `ruff`/`mypy`/`eslint`; 100% unit-тестов для нового кода; нет `TODO`/`FIXME` без задачи; нет секретов (`git-secrets`/`trufflehog`); код-ревью пройдено.

**Гигиена при переносе (по находкам §0.3 п.7):** не тащить `*.bak`, `.DS_Store`, `.coverage.*`, `.index_cache.pkl`, `.fuse_hidden*`, пустой `souls.md`, дубли SCSS (`webui/scss` vs `webui/frontend/styles`); явно решить судьбу `readers/outlook_*`.

---

## Фаза 7 — Чеклист финальной приёмки

- [ ] Проект клонируется в чистую директорию и собирается по `README.md` без ошибок (на Mac).
- [ ] Все 9 настроек ИИ доступны и редактируются во вкладке «Правила».
- [ ] `pytest -m unit` — зелёный.
- [ ] `pytest -m e2e` — зелёный (WebUI запущен).
- [ ] `pytest -m "scenario and mlx"` — зелёный (машина с MLX).
- [ ] `pytest -m "scenario and mail"` — зелёный (macOS + Mail).
- [ ] `pytest -m "scenario and calendar"` — зелёный.
- [ ] Документация полная и актуальная (README, ARCHITECTURE, RULES, TESTING, INTEGRATIONS).
- [ ] Нет критических security issues; секреты не в git.
- [ ] Зафиксирован performance baseline (latency MLX, время мерджа).

---

## Приложение A. Реестр проблем (приоритизация)

| # | Проблема | Влияние | Приоритет |
|---|----------|---------|-----------|
| P1 | Конфиг вычисляется на импорте, нет рантайм-редактирования/`config.json` | Блокирует §3 | 🔴 высокий |
| P2 | 6 из 9 обязательных настроек ИИ отсутствуют в `config.py` | Блокирует §3 | 🔴 высокий |
| P3 | Вкладка «Правила» не содержит настроек ИИ | Несоответствие §3.1 | 🔴 высокий |
| P4 | Pytest-маркеры не зарегистрированы | Блокирует приёмку §7 | 🟠 средний |
| P5 | Нет настройки coverage (цель ≥80%) | Блокирует приёмку | 🟠 средний |
| P6 | Нет CI (`.github/workflows` пуст) | Блокирует §4.5/§7 | 🟠 средний |
| P7 | Legacy Outlook-код при объявленном переходе на Apple Mail | Архитектурный долг | 🟡 низкий |
| P8 | Мусор в репо (`*.bak`, `.DS_Store`, `.coverage.*`, `.fuse_hidden*`, дубли SCSS) | Чистота | 🟡 низкий |
| P9 | Сборка/запуск не верифицированы (среда без MLX) | Неизвестный риск | 🟠 средний (на Mac) |

## Приложение B. Порядок исполнения (рекомендация)
1. Фаза 1 финализировать на Mac (сборка/запуск) → закрыть P9.
2. P4–P5 (маркеры + coverage) — дёшево, разблокирует измеримость.
3. Фаза 3 целиком (P1→P2→P3) — ядро требований инструкции.
4. Фаза 4 (unit → e2e → scenario) + P6 (CI).
5. Фаза 2 (карта/аудит) ведётся параллельно как живой документ.
6. Фаза 5 (док#) и Фаза 6 (миграция по этапам) → Фаза 7 (приёмка).
