# План реализации: `personal-assistant` (pa-merge)

> Дата: 2026-05-21  
> Цель: объединить `pa-kimi` (WebUI + MLX + AppleScript) и `outlook-parser` (SQLite + .olk15)  
> в единое оффлайн-приложение с vault, дедупликацией, тредами и 6-вкладочным WebUI.

---

## Карта зависимостей между этапами

```
Э1 (scaffold) ──▶ Э2 (readers) ──▶ Э3 (sync engine) ──▶ Э4 (vault+dedup) ──▶
──▶ Э5 (MLX+API) ──▶ Э6 (WebUI) ──▶ Э7 (тесты) ──▶ Э8 (docs+polish)
```

---

## Этап 1 — Скаффолдинг проекта

**Цель:** создать структуру папок, `pyproject.toml`, скрипты запуска.

### Файлы к созданию

| Файл | Источник | Действие |
|------|----------|----------|
| `pyproject.toml` | pa-kimi | Адаптировать: добавить `chardet`, `python-slugify`, `tqdm` из outlook-parser; обновить `name = "pa-merge"`, `version = "1.0.0"` |
| `setup.sh` | pa-kimi | Скопировать, добавить `uv add outlook-parser` зависимости |
| `run.sh` | pa-kimi | Скопировать без изменений |
| `make.sh` | pa-kimi | Добавить цели `sync`, `dedup`, `e2e-merge` |
| `.env.example` | pa-kimi | Добавить переменные `PA_OUTLOOK_DB_PATH`, `PA_SYNC_SOURCES` |
| `data/classify.yaml` | pa-kimi | Расширить категориями `finance`, `hr`, `legal`, `project` |

### Структура директорий

```
pa-merge/
├── src/personal_assistant/
│   ├── readers/          # AppleScript readers (из pa-kimi)
│   ├── sync/             # НОВЫЙ: dedup, thread_tracker, sync_runner
│   ├── vault/            # Расширенный writer + index manifest
│   ├── mlx_server/       # Без изменений из pa-kimi
│   ├── webui/            # Расширенные routes + sync_routes
│   ├── personal_vault/   # Без изменений из pa-kimi
│   └── utils/
├── webui/                # Frontend (из pa-kimi)
├── tests/
│   ├── unit/             # Unit-тесты модулей sync/
│   └── e2e/              # E2E сценарии (расширение pa-kimi)
├── data/
│   └── classify.yaml
└── docs/
```

### Критерий приёмки

- `uv run pa serve` стартует без ошибок
- `uv run pytest tests/ -q` — 0 collection errors (даже если тесты пустые)

---

## Этап 2 — Читатели данных (Readers)

**Цель:** два независимых источника данных с единым интерфейсом.

### 2.1 AppleScript-читатели (из pa-kimi, без изменений)

Файлы — уже реализованы:
- `readers/applescript_base.py`
- `readers/calendar_reader.py` — Apple Calendar через osascript
- `readers/mail_reader.py` — Apple Mail через osascript
- `readers/outlook_reader.py` — Outlook через osascript

**Действие:** скопировать as-is, проверить импорты.

### 2.2 SQLite-читатель Outlook (из outlook-parser)

Файлы — перенести и адаптировать под пакет `personal_assistant`:

| Источник (outlook-parser) | Назначение в pa-merge |
|---------------------------|----------------------|
| `db_copy.py` | `readers/outlook_sqlite/db_copy.py` |
| `schema_probe.py` | `readers/outlook_sqlite/schema_probe.py` |
| `parser.py` | `readers/outlook_sqlite/parser.py` |
| `olk15_reader.py` | `readers/outlook_sqlite/olk15_reader.py` |
| `blocks_reader.py` | `readers/outlook_sqlite/blocks_reader.py` |
| `attachments.py` | `readers/outlook_sqlite/attachments.py` |
| `exporter.py` | `readers/outlook_sqlite/exporter.py` |
| `models.py` | `readers/outlook_sqlite/models.py` |

**Адаптации:**
- Заменить `loguru.logger` везде вместо `logging.getLogger`
- Добавить `__init__.py` с `OutlookSQLiteReader` как единой точкой входа
- `OutlookSQLiteReader.fetch_messages(days_back)` → возвращает `list[MailMessage]` (модель из `personal_assistant.models`)
- `OutlookSQLiteReader.fetch_events(days_back, days_forward)` → `list[CalendarEvent]`
- Конвертация: `outlook_parser.models.Email` → `personal_assistant.models.MailMessage`

### 2.3 Унифицированный интерфейс читателя

```python
# readers/__init__.py
class DataSourceReader(Protocol):
    def fetch_messages(self, days_back: int) -> list[MailMessage]: ...
    def fetch_events(self, days_back: int, days_forward: int) -> list[CalendarEvent]: ...
```

Реализации: `OutlookAppleScriptReader`, `OutlookSQLiteReader`, `AppleCalendarReader`, `AppleMailReader`.

### Тесты этапа 2

```
tests/unit/readers/test_outlook_sqlite_reader.py
  - test_db_copy_creates_tempfile
  - test_schema_probe_returns_column_map
  - test_parser_with_fixture_db  (in-memory SQLite с seed-данными)
  - test_model_conversion_email_to_mail_message
  - test_model_conversion_meeting_to_calendar_event
  - test_cyrillic_normalization
```

---

## Этап 3 — Sync Engine (ядро синхронизации)

**Цель:** единый запуск синхронизации из всех источников с дедупликацией и трекингом тредов.

### 3.1 `sync/dedup_engine.py`

Логика дедупликации:

```python
def dedup_key(source: str, item_id: str, subject: str, date: datetime) -> str:
    raw = f"{source}:{item_id}:{subject}:{date.date()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

class DedupEngine:
    """
    Хранит seen_keys в SQLite (vault/.dedup.db).
    При конфликте: сохраняет более новую версию + пишет в sync.log.
    """
    def is_duplicate(self, key: str) -> bool: ...
    def register(self, key: str, source: str, path: Path) -> None: ...
    def resolve_conflict(self, key: str, newer_item, older_path: Path) -> Path: ...
```

**Критерии:**
- Одно и то же письмо из AppleScript и SQLite → записывается только один .md файл
- При конфликте (разный hash контента, одинаковый key) → логируется в `vault/sync.log`, остаётся более свежая версия

### 3.2 `sync/thread_tracker.py`

Трекинг цепочек писем:

```python
class ThreadTracker:
    """
    Для email: группировка по In-Reply-To / References заголовкам.
    Для встреч: серия по ical UID или subject+organizer+time_window (24h).
    Создаёт threads/<thread_id>.md с ссылками на исходные файлы.
    """
    def assign_thread(self, msg: MailMessage) -> str:
        # 1. Если есть in_reply_to → ищем родительский тред
        # 2. Иначе: compute_thread_id(subject) из applescript_base
        ...
    
    def write_thread_index(self, thread_id: str, items: list[Path]) -> Path:
        # Создаёт vault/threads/<thread_id>.md
        ...
```

### 3.3 `sync/sync_runner.py`

Оркестратор синхронизации:

```python
class SyncRunner:
    """
    1. Получает данные из всех активных источников
    2. Прогоняет через DedupEngine
    3. Назначает thread_id через ThreadTracker
    4. Записывает .md файлы через EnhancedVaultWriter
    5. Обновляет vault/index.json (манифест для RAG)
    6. Пишет отчёт в vault/sync.log
    """
    def run(
        self,
        sources: list[str] = ["calendar", "outlook_as", "outlook_sqlite"],
        days_back: int = 30,
        days_forward: int = 90,
    ) -> SyncReport: ...
```

### 3.4 `sync/index_manifest.py`

Поддержание `vault/index.json`:

```json
{
  "generated_at": "2026-05-21T10:00:00Z",
  "total_docs": 1247,
  "sections": {
    "calendar": {"count": 312, "last_updated": "..."},
    "outlook": {"count": 890, "last_updated": "..."},
    "threads": {"count": 45, "last_updated": "..."}
  },
  "tags": {"urgency:important": 34, "category:finance": 12}
}
```

### Тесты этапа 3

```
tests/unit/sync/
  test_dedup_engine.py
    - test_first_write_not_duplicate
    - test_same_key_is_duplicate
    - test_conflict_keeps_newer
    - test_dedup_db_persists_across_runs

  test_thread_tracker.py
    - test_reply_chains_same_thread
    - test_unrelated_messages_different_threads
    - test_thread_index_file_created
    - test_meeting_series_grouped

  test_sync_runner.py
    - test_sync_with_mock_readers
    - test_sync_creates_md_files
    - test_sync_report_counts
    - test_partial_failure_graceful_degradation
```

---

## Этап 4 — Расширенный VaultWriter + формат .md

**Цель:** обновить формат frontmatter согласно спецификации.

### 4.1 `vault/writer.py` (обновление)

Новый frontmatter:

```yaml
---
id: "outlook_msg_abc123"
source: "outlook"         # outlook | calendar | outlook_sqlite
type: "email"             # email | meeting
subject: "Отчёт по проекту"
sender: "Иванов <ivan@corp.ru>"
date: "2026-05-20T14:30:00+03:00"
thread_id: "thread_xyz789"
tags: ["urgency:important", "category:finance"]
attachments: ["file_01.pdf"]
sha256: "a1b2c3..."       # hash контента для дедупликации
sync_source: "outlook_sqlite"  # конкретный читатель
dedup_key: "abc123def456"
---
```

**Изменения в `VaultWriter`:**
- Добавить поле `sha256` = `hashlib.sha256(body.encode()).hexdigest()`
- Добавить поле `dedup_key`
- Добавить `thread_id` (из ThreadTracker)
- Обновить шаблоны `templates/mail.md.j2` и `templates/event.md.j2`
- Добавить `templates/thread.md.j2` для thread-index файлов

### 4.2 Шаблон `templates/thread.md.j2`

```markdown
---
id: "{{ thread_id }}"
type: "thread"
subject: "{{ subject }}"
message_count: {{ items | length }}
date_range: ["{{ first_date }}", "{{ last_date }}"]
participants: {{ participants | tojson }}
tags: {{ tags | tojson }}
---
# Тред: {{ subject }}

Цепочка из {{ items | length }} сообщений.

## Сообщения

{% for item in items %}
- [{{ item.date }} — {{ item.subject }}]({{ item.rel_path }})
{% endfor %}
```

### 4.3 Сохранение вложений

- Путь: `vault/attachments/<dedup_key[:8]>/<slugified_name>`
- Транслитерация кириллицы: `unicodedata.normalize('NFC', name)` + `python-slugify`
- Только из SQLite-источника (AppleScript не возвращает байты вложений)

### Тесты этапа 4

```
tests/unit/vault/
  test_vault_writer_frontmatter.py
    - test_sha256_field_present
    - test_thread_id_field_present
    - test_cyrillic_subject_slugified
    - test_attachment_path_normalized

  test_thread_template.py
    - test_thread_md_renders_links
    - test_thread_md_participant_list
```

---

## Этап 5 — MLX Server + Sync API

**Цель:** добавить API для синхронизации, интегрировать SyncRunner в планировщик.

### 5.1 `webui/sync_routes.py` (новый файл)

```python
router = APIRouter(prefix="/sync")

@router.post("/run")
async def run_sync(background_tasks: BackgroundTasks, sources: list[str] = None):
    """Запустить синхронизацию в фоне. Возвращает job_id."""

@router.get("/status")
async def sync_status():
    """Статус последней синхронизации (из sync.log)."""

@router.get("/log")
async def sync_log(lines: int = 100):
    """Последние строки vault/sync.log."""
```

### 5.2 Обновление `mlx_server/scheduler.py`

Добавить шаг синхронизации в `run_pipeline()`:

```python
# Шаг 0: Синхронизация данных (если включена)
if settings.sync_on_schedule:
    sync_runner = SyncRunner(vault_path=vault)
    sync_report = sync_runner.run(sources=settings.sync_sources)
    report["sync"] = sync_report.to_dict()
```

### 5.3 Обновление `config.py`

Новые переменные:

```python
sync_sources: list[str] = ["calendar", "outlook_as"]  # или + "outlook_sqlite"
sync_on_schedule: bool = True
outlook_db_path: Optional[Path] = None  # авто-определение если None
sync_days_back: int = 30
sync_days_forward: int = 90
```

### 5.4 Обновление `mlx_server/server.py`

Подключить `sync_routes.py`:

```python
from personal_assistant.webui.sync_routes import router as sync_router
app.include_router(sync_router)
```

### Тесты этапа 5

```
tests/unit/
  test_sync_routes.py
    - test_post_sync_run_returns_job_id
    - test_get_sync_status_returns_json
    - test_sync_log_returns_lines

  test_scheduler_with_sync.py
    - test_pipeline_calls_sync_when_enabled
    - test_pipeline_skips_sync_when_disabled
```

---

## Этап 6 — WebUI (6 вкладок)

**Цель:** обновить WebUI для отображения данных из обоих источников.

### Вкладки и компоненты

| Вкладка | Статус | Изменения |
|---------|--------|-----------|
| **Чат** | ✅ есть в pa-kimi | Добавить @-упоминание контактов из Outlook |
| **Vault** | ✅ есть | Добавить колонку `source` (outlook/calendar/outlook_sqlite); фильтр по source |
| **Проекты** | ✅ есть | Без изменений |
| **Правила** | ✅ есть | Без изменений |
| **Поиск** | ✅ есть | Добавить фильтр по `thread_id`, разделу `threads/` |
| **Настройки** | ✅ есть | Добавить блок "Синхронизация": sources, days_back, schedule; кнопка "Синхронизировать сейчас" |

### Настройки — новые поля

```javascript
// settings.js — добавить секцию Sync
const syncSection = {
  sync_sources: ["calendar", "outlook_as", "outlook_sqlite"],
  sync_days_back: 30,
  sync_on_schedule: true,
  outlook_db_path: "",  // пусто = авто-определение
}
```

### Тесты этапа 6

```
tests/e2e/
  test_s15_sync_via_ui.py
    - test_sync_button_triggers_run
    - test_settings_save_sync_config
    - test_vault_tab_shows_source_column
```

---

## Этап 7 — E2E тесты (расширение)

**Цель:** покрыть все новые сценарии, убедиться что `./make.sh e2e` → 0 failed.

### Новые E2E сценарии

#### Сценарий S15: Sync от SQLite-источника

```python
# tests/e2e/test_s15_outlook_sqlite_sync.py
"""
S15-SETUP  : создать in-memory SQLite с 3 письмами (2 в треде)
S15-SYNC   : POST /sync/run с sources=["outlook_sqlite_mock"]
S15-VAULT  : проверить создание 3 .md файлов с правильным frontmatter
S15-THREAD : проверить создание vault/threads/<thread_id>.md
S15-DEDUP  : повторный запуск sync → 0 новых файлов, 3 skipped
S15-CLEANUP: удалить тестовые файлы
"""
```

#### Сценарий S16: Дедупликация между источниками

```python
# tests/e2e/test_s16_cross_source_dedup.py
"""
S16-LOAD   : записать письмо через OutlookAppleScriptReader (mock)
S16-SYNC   : записать то же письмо через OutlookSQLiteReader (mock)
S16-CHECK  : в vault должен быть только 1 файл (не 2)
S16-LOG    : vault/sync.log содержит запись о конфликте
"""
```

#### Сценарий S17: Thread tracking

```python
# tests/e2e/test_s17_thread_tracking.py
"""
S17-LOAD   : загрузить 4 письма: 2 → тред A, 2 → тред B
S17-THREAD : GET /api/v1/vault/threads — убедиться, что 2 треда
S17-INDEX  : проверить thread_id в frontmatter каждого письма
S17-CHAT   : POST /api/chat/send с vault_thread_id=thread_A
             → контекст содержит оба письма треда A
"""
```

#### Сценарий S18: Синхронизация через WebUI

```python
# tests/e2e/test_s18_sync_webui.py
"""
S18-SETTINGS: POST /settings с sync_sources=["outlook_sqlite_mock"]
S18-RUN     : POST /sync/run → job_id
S18-STATUS  : GET /sync/status → completed
S18-VAULT   : vault содержит новые файлы с source=outlook_sqlite
"""
```

### Расширение существующих тестов

- `test_scenario_1_email_tool_call.py` — добавить проверку `sha256` в frontmatter
- `test_full_vault_ui_flow.py` — добавить проверку вкладки Настройки/Sync
- `conftest.py` — добавить `MockOutlookSQLiteReader`, `fixture_db_factory()`

### Матрица покрытия

| Компонент | Unit | E2E | Статус |
|-----------|------|-----|--------|
| `db_copy.py` | ✅ S2 | — | из outlook-parser |
| `parser.py` | ✅ S2 | — | из outlook-parser |
| `dedup_engine.py` | ✅ S3 | S16 | новый |
| `thread_tracker.py` | ✅ S3 | S17 | новый |
| `sync_runner.py` | ✅ S3 | S15, S18 | новый |
| `vault_writer.py` | ✅ S4 | S15 | обновление |
| `sync_routes.py` | ✅ S5 | S18 | новый |
| WebUI Sync | — | S18 | обновление |

---

## Этап 8 — Документация и финализация

### `README.md`

Разделы:
1. **Требования** — macOS 14+, Apple Silicon (MLX), Python 3.11+, Microsoft Outlook 16.x
2. **Установка** — `./setup.sh`, настройка `.env`
3. **Запуск** — `./run.sh`, открыть `http://127.0.0.1:8000`
4. **Синхронизация** — источники данных, дедупликация, расписание
5. **Vault** — структура директорий, формат .md, frontmatter
6. **Troubleshooting** — разрешения osascript, MLX на Intel, Outlook DB path
7. **Разработка** — `./make.sh test`, `./make.sh e2e`, `./make.sh lint`

### Финальный чеклист

- [ ] `./setup.sh` — проходит без ошибок
- [ ] `./run.sh` — сервер на `http://127.0.0.1:8000`
- [ ] WebUI: 6 вкладок, кнопка Sync
- [ ] `POST /sync/run` → создаёт .md файлы с правильным frontmatter
- [ ] Дедупликация: повторный sync → 0 новых файлов
- [ ] Трекинг тредов: `threads/<id>.md` с ссылками
- [ ] Поиск: `POST /search?q=текст` → находит по содержимому и тегам
- [ ] Чат: `@-упоминание` передаёт контекст треда
- [ ] `./make.sh e2e` → **0 failed**
- [ ] `./make.sh lint` → 0 ошибок ruff/mypy

---

## Временна́я оценка

| Этап | Сложность | Оценка |
|------|-----------|--------|
| Э1: Scaffold | Низкая | 2 ч |
| Э2: Readers | Средняя | 6 ч |
| Э3: Sync Engine | Высокая | 10 ч |
| Э4: VaultWriter | Средняя | 4 ч |
| Э5: MLX+API | Средняя | 4 ч |
| Э6: WebUI | Средняя | 6 ч |
| Э7: E2E Tests | Высокая | 8 ч |
| Э8: Docs | Низкая | 2 ч |
| **Итого** | | **~42 ч** |

---

## Риски и митигация

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| Outlook.sqlite schema меняется с версией | Средняя | `schema_probe.py` уже решает это динамически |
| AppleScript таймаут > 60s при большом inbox | Средняя | `days_back` конфигурируется, graceful timeout с partial results |
| MLX не работает на Intel | Высокая | `engine.py` возвращает 503 с понятным сообщением |
| .olk15 бандлы повреждены | Низкая | `olk15_reader.py` уже имеет fallback на RFC 822 |
| Дедупликация ломает уникальные события | Низкая | `dedup.db` можно удалить для сброса; `sync.log` сохраняет историю |
