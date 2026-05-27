# ARCHITECTURE — pa-clean

Оффлайн персональный ассистент для macOS. Синхронизирует Apple Calendar и Apple
Mail в локальное Markdown-хранилище (vault), индексирует его и отвечает на
вопросы через локальный MLX-инференс. Данные не покидают машину.

Связанные документы: [RULES.md](RULES.md) · [TESTING.md](TESTING.md) ·
[INTEGRATIONS.md](INTEGRATIONS.md) · [docs/FUNCTIONALITY_MAP.md](docs/FUNCTIONALITY_MAP.md).

## Слои

```
            ┌──────────────────────────────────────────────┐
  WebUI     │  webui/ (vanilla JS + SCSS)  →  /static, api.js │
            └───────────────┬──────────────────────────────┘
                            │ HTTP (FastAPI)
            ┌───────────────▼──────────────────────────────┐
  API       │  mlx_server/server.py  (10 роутеров)          │
            │  webui · chat · rules_settings · inbox ·       │
            │  today · brief · calendar · vault · reports ·  │
            │  profile                                       │
            └───────────────┬──────────────────────────────┘
            ┌───────────────▼──────────────────────────────┐
  Business  │  services/  (daily_brief, meeting_prep,        │
            │  mail_service, calendar_service, rule_engine,  │
            │  draft_context, thread_graph, report, …)       │
            │  mlx_server/  (engine, tasks/*, tools/*)        │
            │  sync/  (dedup_engine, thread_tracker)          │
            └───────────────┬──────────────────────────────┘
            ┌───────────────▼──────────────────────────────┐
  Data      │  vault/ (writer)  ·  personal_vault/ (sqlite)  │
            │  indexes: vault_index (BM25) · vector_index    │
            └───────────────┬──────────────────────────────┘
            ┌───────────────▼──────────────────────────────┐
  Integr.   │  readers/  applescript_base · mail_reader ·    │
            │  calendar_reader · outlook_sqlite (legacy)      │
            │  MLX (mlx-lm) · Apple Mail · Apple Calendar     │
            └──────────────────────────────────────────────┘
```

## Точки входа

- **CLI** — `pa = personal_assistant.cli:main` (click). Команды: `check`,
  `sync-calendar|mail|all`, `serve`, `search`, `classify`, `build-index`,
  `status`, `list-models`, `run-tasks`, `fix-model-config`.
- **HTTP/WebUI** — `pa serve` поднимает FastAPI (`mlx_server/server.py`),
  монтирует статику `webui/dist/` на `/static` и `index.html` на `/`.
- **WebUI** — `webui/index.html` + ES-модули `webui/frontend/js/*`; собирается
  `cd webui && npm run build` (sass + `bundle-js.js` → `webui/dist/`).

## Конфигурация (слой настроек)

Порядок разрешения (позже — главнее): встроенные дефолты → переменные
окружения `PA_*` / `.env` → оверлей `data/config.json`. Оверлей — это то, что
редактирует вкладка «Правила»; он применяется к синглтону `settings` сразу,
без рестарта. Подробно — [RULES.md](RULES.md). Реализация: `config.py`
(`Settings`, `EDITABLE_FIELDS`, `update()`/`_apply_overlay()`).

## Персистентность

| Что | Где | Формат |
|-----|-----|--------|
| Vault (письма, события, контакты) | `~/PersonalAssistantVault/` (`PA_VAULT_PATH`) | Markdown + YAML frontmatter |
| Чат-треды | `data/chat.db` | SQLite |
| PersonalVault v2 | `data/personal_vault.db` | SQLite |
| Правила Эйзенхауэра/GTD | `data/rules.json` | JSON (в git) |
| Настройки ИИ (оверлей) | `data/config.json` | JSON (gitignored) |
| Промпты тулов | `vault/.tool_prompts.json` | JSON |
| Классификатор | `data/classify.yaml` | YAML |

## MLX: модель потоков

MLX GPU-стримы привязаны к потоку, а Starlette/AnyIO гоняет генераторы в пуле
рабочих потоков. Поэтому весь GPU-доступ маршрутизируется через единственный
долгоживущий `_MLXThread` (`mlx_server/engine.py`): любой вызов
`generate/chat/stream` диспатчится в него через очередь. Параметры сэмплинга
(`temperature/max_tokens/top_p`) берутся из `settings` через `_resolve_sampling`
и адаптируются к версии `mlx_lm` (`temp=` / `temperature=` / `make_sampler`).
Если `mlx-lm` недоступен или модель не задана — методы возвращают
`_UNAVAILABLE_MSG`, а не падают.

## Ключевые потоки данных

**Синхронизация.** `readers/*` (AppleScript) → Pydantic-модели (`models.py`) →
`sync/dedup_engine` + `sync/thread_tracker` (дедуп и связывание тредов) →
`vault/writer` пишет `.md` с frontmatter и `sha256`.

**Чат с тул-коллингом.** `POST /api/chat/send` → `ProfileAwareAssembler`
собирает контекст (профиль + конфиг + vault + история) → `engine.stream` →
детектор тул-коллов (`_detect_and_run_tools`) → `tools/router→executor` →
повторная генерация при необходимости → ответ сохраняется в `chat.db`.

**Создание события.** `POST /api/v1/calendar/create-from-text` →
`calendar/intent_parser` (NL → `EventDraft`, длительность из
`calendar_default_duration`) → опц. проверка пересечений
(`calendar_service.find_conflicts`, флаг `calendar_check_conflicts`) →
`calendar/calendar_writer.create_event` (AppleScript; при `e2e_test_mode`
реальная запись подавляется).

## Обработка ошибок и логирование

`loguru` повсеместно; логи в `logs/`. Интеграции деградируют мягко: отсутствие
MLX/Apple/прав возвращает понятное сообщение или пустой результат, а не 500
(см. `engine`, `calendar/routes`, `readers`).
