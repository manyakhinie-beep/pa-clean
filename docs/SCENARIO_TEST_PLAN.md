# План сценарных тестов и исправлений — MLX + AppleScript

## Цель

Создать интеграционные тесты, которые запускаются **только на реальном железе** (Apple Silicon + macOS) и проверяют полные сценарии с настоящим `mlx-lm` и настоящими вызовами AppleScript. В процессе анализа были найдены баги в продакшен-коде — они также задокументированы ниже.

---

## Что создано

### 1. `tests/scenarios/test_mlx_scenarios.py`

Сценарные тесты реального MLX-инференса. Пропускаются автоматически если:
- `mlx-lm` не установлен
- `PA_MLX_MODEL_PATH` не задан / указывает в никуда
- Не macOS

| ID | Сценарий | Что проверяется |
|---|---|---|
| SC-MLX-01 | Engine load & basic generation | Модель загружается, `generate()`/`chat()`/`stream()` возвращают непустой текст |
| SC-MLX-02 | Draft reply | `draft_reply()` генерирует осмысленный русский черновик |
| SC-MLX-03 | Summarize | `summarize_docs()` выдаёт структурированное резюме на русском |
| SC-MLX-04 | Structured extraction | Модель возвращает валидный JSON с action_items, entities, intent |
| SC-MLX-05 | LLM classify | `llm_classify_single()` определяет категорию из заданного списка |
| SC-MLX-06 | Calendar intent + MLX refinement | Сложные фразы парсятся корректно при помощи MLX |
| SC-MLX-07 | Tool calling (date_calc) | Модель либо вызывает тул, либо даёт корректную дату |

**Запуск:**
```bash
uv run pytest tests/scenarios/test_mlx_scenarios.py -v
```

### 2. `tests/scenarios/test_search_scenarios.py`

Сценарные тесты поиска по vault. Пропускаются автоматически если:
  - `sentence-transformers` не установлен или модель не сконфигурирована
  - MLX недоступен (только для LLM-синтеза)

| ID | Сценарий | Что проверяется |
|---|---|---|
| SC-SEARCH-01 | BM25 relevance | Exact title match, keyword in body, attachment names, tags, section filter |
| SC-SEARCH-02 | Vector search | Семантический поиск, кросс-языковой поиск (если модель доступна) |
| SC-SEARCH-03 | Hybrid RRF | BM25 + вектор → RRF, фильтр по секциям |
| SC-SEARCH-04 | Date parsing | ISO, DD.MM.YYYY, русские/английские месяцы, диапазоны |
| SC-SEARCH-05 | API endpoints | /search/docs, /search/hybrid, /search/stream, /index/build, /vault/mention |
| SC-SEARCH-06 | Edge cases | Пустой vault, спецсимволы, очень длинный запрос, single-char |
| SC-SEARCH-07 | VaultIndex utilities | get_thread, build_context, ui_preview, short_summary |
| SC-SEARCH-08 | LLM synthesis | Синтез ответа на основе найденных документов (через shared engine) |

**Запуск:**
```bash
# Без MLX-синтеза (быстро)
uv run pytest tests/scenarios/test_search_scenarios.py -v -k "not TestSearchLLMSynthesis"

# Только LLM-синтез
uv run pytest tests/scenarios/test_search_scenarios.py::TestSearchLLMSynthesis -v
```

### 3. `tests/scenarios/test_applescript_scenarios.py`

Сценарные тесты реальных вызовов AppleScript. Пропускаются автоматически если:
- Не macOS
- Нет разрешения Automation для Terminal

| ID | Сценарий | Что проверяется |
|---|---|---|
| SC-AS-01 | AppleScript runner | `run_applescript()` возвращает строки и Unicode |
| SC-AS-02 | Calendar read | `CalendarReader` видит календари и события |
| SC-AS-03 | Calendar write + cleanup | `create_event()` создаёт событие, оно появляется в fetch, потом удаляется |
| SC-AS-04 | Mail read | `MailReader` читает ящики и письма, пропускает noise-папки |
| SC-AS-05 | Intent parser (rule-based) | Русские даты/время/длительность/участники парсятся корректно |
| SC-AS-06 | Thread ID stability | `compute_thread_id()` стабилен для Re:/Отв:/Fwd: |

**Запуск:**
```bash
uv run pytest tests/scenarios/test_applescript_scenarios.py -v
```

### 4. `tests/scenarios/test_calendar_scenarios.py`

Сценарные тесты календарной интеграции (не требуют Calendar.app — используют моки).

| ID | Сценарий | Что проверяется |
|---|---|---|
| SC-CAL-01 | Sync all calendars | `CalendarReader.fetch_events` синхронизирует **все** календари, включая read-only (Holidays, Birthdays) |
| SC-CAL-02 | Explicit calendar filter | `calendar_names` ограничивает выборку только указанными календарями |
| SC-CAL-03 | Parse intent — needs calendar | `parse-intent` возвращает `needs_calendar=True` и список доступных календарей, если календарь не указан в тексте |
| SC-CAL-04 | Parse intent — auto-detect calendar | `parse-intent` распознаёт календарь по ключевым словам («личный», «рабочий», «домашний») |
| SC-CAL-05 | Create with override | `create-from-text` использует `calendar_name` из запроса пользователя |
| SC-CAL-06 | Create with auto-detected calendar | `create-from-text` работает без `needs_calendar`, если календарь распознан автоматически |
| SC-CAL-07 | List all calendars | `GET /calendars` возвращает все календари, включая read-only |
| SC-CAL-08 | Writer fallback | AppleScript шаблон `calendar_writer` корректно обрабатывает `calendar_name=None` |

**Запуск:**
```bash
uv run pytest tests/scenarios/test_calendar_scenarios.py -v
```

### 5. `tests/scenarios/test_mail_body_scenarios.py`

Сценарные тесты хранения body писем в vault (не требуют Mail.app).

| ID | Сценарий | Что проверяется |
|---|---|---|
| SC-MAIL-01 | Default body fetch | `PA_MAIL_FETCH_BODY` по умолчанию `True` — body писем сохраняется |
| SC-MAIL-02 | Vault stores body | `VaultWriter.write_message` записывает полный `msg.body` в `.md` файл |
| SC-MAIL-03 | Empty body omitted | Если `msg.body` пустой, секция `## Текст письма` не создаётся |
| SC-MAIL-04 | Body available for display | Содержимое body доступно в vault-файле для отображения в Inbox |

**Запуск:**
```bash
uv run pytest tests/scenarios/test_mail_body_scenarios.py -v
```

### 6. `tests/scenarios/test_draft_scenarios.py`

Сценарные тесты сохранения черновиков в Apple Mail (не требуют Mail.app — AppleScript мокается).

| ID | Сценарий | Что проверяется |
|---|---|---|
| SC-DRAFT-01 | Resolve reply ID by stem | `_resolve_reply_message_id` находит Mail.app ID по file stem |
| SC-DRAFT-02 | Resolve reply ID by `id` frontmatter | Поиск по `id:` frontmatter работает |
| SC-DRAFT-03 | Message meta by stem | `GET /mail/message-meta` ищет по file stem, `id` frontmatter и `message_id` |
| SC-DRAFT-04 | Save draft endpoint | `POST /save-draft-mail` создаёт черновик с правильным AppleScript |
| SC-DRAFT-05 | ID resolution passthrough | Numeric `reply_to_message_id` передаётся без изменений |
| SC-DRAFT-06 | AppleScript payload | `_build_save_draft_mail_script` содержит subject, recipients, reply threading |

**Запуск:**
```bash
uv run pytest tests/scenarios/test_draft_scenarios.py -v
```

---

## Найденные баги (критичные)

### Баг 1: `engine.generate_sync()` — метод не существует

**Где:**
- `src/personal_assistant/mlx_server/tasks/extract.py:474`
- `src/personal_assistant/inbox/routes.py:786`

**Проблема:** Код вызывает `engine.generate_sync(...)`, но в `MLXEngine` такого метода нет. Есть только `generate()` (синхронный, возвращает `str`).

**Последствие:** Структурированная экстракция и inbox-суммаризация **всегда** падают в fallback (regex/heuristics), даже когда модель загружена.

**Исправление:** Заменить `generate_sync` → `generate`.

### Баг 2: `engine.model_loaded` — атрибута нет

**Где:**
- `src/personal_assistant/mlx_server/tasks/extract.py:470`
- `src/personal_assistant/inbox/routes.py:779`

**Проблема:** Проверяется `getattr(engine, "model_loaded", False)`, но у `MLXEngine` свойство называется `is_loaded`.

**Последствие:** Условие всегда `False`, поэтому MLX-путь никогда не вызывается (даже если модель загружена).

**Исправление:** Заменить `"model_loaded"` → `"is_loaded"`.

### Баг 3: `intent_parser.py` пытается итерировать строку

**Где:** `src/personal_assistant/calendar/intent_parser.py:543`

**Проблема:**
```python
for chunk in mlx_engine.generate(prompt=..., max_tokens=256, temperature=0.1):
    result_text += chunk
```

`MLXEngine.generate()` возвращает `str`, а не генератор. Цикл `for chunk in "some string":` будет итерировать **по символам**, что приведёт к бессмысленному результату или `TypeError` в зависимости от версии Python.

**Последствие:** MLX-уточнение в парсере календаря работает некорректно.

**Исправление:** Убрать цикл, присвоить результат напрямую:
```python
result_text = mlx_engine.generate(prompt=..., max_tokens=256, temperature=0.1)
```

---

## Рекомендуемые доработки (не баги, но улучшат надёжность)

### Доработка 1: Единый адаптер для `generate()`

Сейчас разные модули обращаются к `MLXEngine` напрямую. Если API `mlx-lm` снова изменится (как уже было с `temp` → `temperature` → `sampler`), придётся править во многих местах.

**Предложение:** Добавить тонкий адаптер `MLXEngine.generate_sync(prompt, **kwargs)` как псевдоним для `generate()`, чтобы внешний код не ломался при рефакторинге.

### Доработка 2: `extract.py` — кэш не используется при `force=True`

В `extract()` параметр `force=True` должен пропускать кэш, но `_try_mlx_extract()` не принимает `force` и всегда пытается MLX. Это ок, но стоит добавить явный аргумент `force` в `_try_mlx_extract` для консистентности.

### Доработка 3: Таймауты в сценарных тестах

MLX-генерация может занимать десятки секунд на больших моделях. Сейчас `pytest` использует свой дефолтный таймаут. Рекомендуется добавить:
```ini
# pyproject.toml
[tool.pytest.ini_options]
timeout = 120
```
(требуется `pytest-timeout`).

### Доработка 4: AppleScript — cleanup при падении теста

В `test_applescript_scenarios.py` cleanup событий выполняется в `yield` фикстуры. Если тест упадёт **до** `yield`, cleanup не сработает. Лучше использовать `addfinalizer`:
```python
request.addfinalizer(_cleanup_test_events)
```

### Доработка 5: Проверка качества MLX-вывода

Сейчас сценарные тесты проверяют только "не пусто" и "похоже на русский". Для regression testing полезно добавить **snapshot-тесты** (pytest-snapshot) — зафиксировать ожидаемый вывод для малой модели (например, `Phi-3.5-mini`) и сравнивать с ним.

---

## Порядок применения исправлений

1. **Немедленно** — исправить баги 1–3 (3 файла, ~5 строк).
2. **В этой же сессии** — запустить `./make.sh check` чтобы убедиться, что unit/e2e тесты не сломались.
3. **Вручную на Apple Silicon Mac** — запустить сценарные тесты:
   ```bash
   uv run pytest tests/scenarios/ -v --tb=short
   ```
4. **При успехе** — добавить `pytest-timeout` и `pytest-snapshot` в dev-зависимости (опционально).

---

## Архитектура сценарных тестов

```
tests/
├── unit/          ← быстрые, без внешних зависимостей, всегда в CI
├── e2e/           ← FastAPI TestClient, всегда в CI
└── scenarios/     ← медленные, требуют macOS + MLX, запуск вручную
    ├── conftest.py                 ← shared session fixtures (MLX engine, embedding model)
    ├── test_mlx_scenarios.py
    ├── test_applescript_scenarios.py
    └── test_search_scenarios.py
```

`make.sh` и CI-конфигурация **не должны** включать `tests/scenarios/` в стандартный прогон, потому что:
- В GitHub Actions нет Apple Silicon (или дорого)
- MLX-модель весит 4–8 ГБ, загрузка занимает время
- AppleScript требует GUI-сессии и разрешений

**Важно:** сценарные тесты с MLX запускайте **по отдельности**. Одновременный прогон всех трёх модулей в одной команде `pytest tests/scenarios/` может вызвать GPU-крэш из-за конфликтов MLX-потоков между `test_mlx_scenarios.py` и `test_search_scenarios.py`.

Рекомендуемый запуск:
```bash
# По одному файлу
uv run pytest tests/scenarios/test_mlx_scenarios.py -v
uv run pytest tests/scenarios/test_applescript_scenarios.py -v
uv run pytest tests/scenarios/test_search_scenarios.py -v -k "not TestSearchLLMSynthesis"
uv run pytest tests/scenarios/test_search_scenarios.py::TestSearchLLMSynthesis -v
```

Если понадобится запускать сценарные тесты в CI, можно использовать `macos-latest` runner на GitHub + кэшированная модель, но это отдельная задача.
