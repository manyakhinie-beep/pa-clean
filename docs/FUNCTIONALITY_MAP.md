# Карта функционала pa-merge (Фаза 2 аудита)

Составлено по фактическому коду (2026-05-25). Перечислены все точки входа
(CLI, HTTP API, WebUI), сопоставлены с модулями и покрытием тестами, проставлен
статус. Это «живой» документ — статусы уточняются по мере прогона на macOS.

## Легенда статусов

- ✅ **verified** — есть тесты, зелёные на Linux (без MLX/Apple).
- 🍎 **mac-only** — логика есть и протестирована, но полная проверка требует
  macOS + MLX-модель / права Mail·Calendar (scenario-тесты).
- ⚠️ **attention** — работает, но есть долг/риск (см. реестр проблем).
- ❓ **unverified** — нет выделенных автотестов; проверять вручную.

Точки входа: `pa` (CLI) · FastAPI-сервер (`pa serve`) · WebUI (вкладки).
Сервер монтирует 10 роутеров (~100 эндпоинтов).

---

## 1. Точки входа

### 1.1. CLI (`pa = personal_assistant.cli:main`)

| Команда | Назначение | Статус | Примечания |
|---------|-----------|--------|-----------|
| `pa check` | Диагностика окружения/конфига | ❓ | smoke вручную |
| `pa fix-model-config` | Починка путей MLX-модели | ❓ | macOS |
| `pa sync-calendar` | Синк Calendar → vault | 🍎 | AppleScript |
| `pa sync-mail` | Синк Mail → vault | 🍎 | AppleScript |
| `pa sync-all` | Полный синк | 🍎 | |
| `pa status` | Состояние vault/индекса | ❓ | |
| `pa serve` | Запуск FastAPI + WebUI | ❓ | проверять `e2e` |
| `pa run-tasks` | Прогон запланированных задач | ❓ | scheduler |
| `pa search` | Поиск по vault (BM25/вектор) | ⚠️ | вектор — extra |
| `pa classify` | Классификация (Эйзенхауэр/GTD) | ✅ | rule_engine покрыт |
| `pa list-models` | Список MLX-моделей | 🍎 | |
| `pa build-index` | Построение индекса vault | ❓ | |

### 1.2. WebUI — вкладки (`webui/index.html` + `frontend/js/*`)

| Вкладка | Модуль JS | Backend | Статус |
|---------|-----------|---------|--------|
| Сегодня | `today.js` | `/api/v1/today`, `/api/v1/brief` | ✅ unit (daily_brief) |
| Входящие | `inbox.js` | `/api/v1/inbox/*` | ✅ unit + e2e |
| Проекты | `projects.js` | `/projects/*` | ✅ e2e |
| Чат | `chat.js` | `/api/chat/*` | ✅ e2e + 🍎 scenario |
| Vault | `vault.js` | `/vault/*`, `/api/v1/vault/*` | ✅ e2e |
| Поиск | `search.js` | `/search/docs`, `/vault/*` | ✅ e2e |
| **Правила** | `rules.js` | `/api/v1/rules/settings`, `/rules`, `/gtd-rules`, `/eisenhower`, `/tool-prompts` | ✅ unit + e2e |
| Отчёты | `reports.js` | `/api/v1/reports/*` | ✅ e2e |
| Настройки | `settings.js` | `/settings`, `/classify/*`, `/model/*`, `/testdata/*` | ✅ e2e |

---

## 2. Карта функциональных областей

| ID | Область | Модули | Точки входа | Тесты | Статус |
|----|---------|--------|-------------|-------|--------|
| F1 | Чтение Calendar | `readers/calendar_reader.py` | CLI sync, `/api/v1/calendar/upcoming` | `unit/readers/test_calendar_reader`, `scenarios/test_calendar_scenarios` | 🍎 (unit ✅) |
| F2 | Чтение Mail | `readers/mail_reader.py`, `readers/outlook_sqlite/*` | CLI sync | `unit/readers/test_mail_reader`, `test_outlook_*`, `scenarios/test_mail_body` | 🍎 (unit ✅) |
| F3 | Запись vault | `vault/writer.py`, `personal_vault/*` | внутр., `/api/v1/vault/*` | `unit/vault/test_vault_writer`, `e2e` | ✅ |
| F4 | **Core merge** (dedup/threading) | `sync/dedup_engine.py`, `sync/thread_tracker.py`, `services/thread_graph_service.py` | внутр. | `unit/sync/*`, `unit/test_thread_graph_service` | ✅ |
| F5 | Классификация (Эйзенхауэр/GTD) | `services/rule_engine.py` (`data/rules.json`) | `pa classify`, `/rules`, `/eisenhower`, `/gtd-rules`, `/classify/*` | `e2e`, rule_engine | ✅ |
| F6 | MLX-инференс | `mlx_server/engine.py`, `tasks/{summarize,draft_reply,classify,priority,extract,search}.py` | `/api/chat/*`, внутр. | `unit/mlx/*` (логика), `scenarios/test_mlx_scenarios` | 🍎 (unit ✅) |
| F7 | Tool-calling | `mlx_server/tools/{router,executor,validator,date_calc}.py` | внутр. | `unit/mlx/test_tools_pipeline`, `test_tool_call_detection` | ✅ |
| F8 | Черновики ответов | `services/mail_service.py`, `services/draft_context_service.py`, `tasks/draft_reply.py` | `/api/chat/save-draft-mail`, `/api/v1/inbox/{id}/draft-context` | `unit/services/test_draft_context`, `test_mail_service_fix`, `e2e/test_draft_flow`, `scenarios/test_draft_*` | 🍎 (unit/e2e ✅) |
| F9 | Создание событий / intent | `calendar/intent_parser.py`, `calendar/calendar_writer.py` | `/api/v1/calendar/{parse-intent,create-from-text}`, `/api/chat/calendar/create-meeting` | `unit/calendar/test_intent_parser`, `unit/test_settings_wiring` | ✅ (запись — 🍎) |
| F10 | Daily brief / meeting prep | `services/daily_brief_service.py`, `services/meeting_prep_service.py`, `today/*` | `/api/v1/today`, `/api/v1/brief/*`, `/api/v1/calendar/{id}/prep` | `unit/services/test_daily_brief`, `test_meeting_prep` | ✅ |
| F11 | Отчёты | `reports/{generator,store,routes}.py`, `services/report_service.py` | `/api/v1/reports/*` | `e2e/test_server_routes` | ✅ |
| F12 | Профиль / persona / souls | `profile/*` | `/api/v1/profile`, `/assistant-config`, `/souls`, `/persona` | `e2e/test_server_routes` | ✅ |
| F13 | Поиск (BM25 + вектор) | `mlx_server/vault_index.py`, `vector_index.py` | `pa search`, `/search/docs` | `scenarios/test_search_scenarios` | ⚠️ вектор=extra |
| F14 | **Настройки ИИ (вкладка «Правила»)** | `config.py`, `webui/rules_settings.py`, `rules.js` | `/api/v1/rules/settings` | `unit/test_config`, `test_rules_settings_api`, `test_settings_wiring`, `e2e/test_rules_settings_e2e` | ✅ |
| F15 | Проекты + связи | `webui/routes.py` (projects), `tag_history_service.py` | `/projects/*`, `/tag-history/*` | `e2e/test_server_routes` | ✅ |
| F16 | Управление моделями / testdata | `webui/routes.py` (model/testdata), `scripts/generate_test_vault.py` | `/model/*`, `/testdata/*` | `unit/services/test_daily_brief::TestGeneratedVault` | ⚠️ 🍎 |

**Методическая оговорка.** «unit ✅» означает, что чистая логика (парсинг,
классификация, date-math, конфиг, dedup) проходит на Linux без MLX/Apple.
Реальная инференс-цепочка MLX и доступ к Mail/Calendar (F1, F2, F6, F8-write)
проверяются только на macOS Apple Silicon через `pytest -m "scenario and …"`.

---

## 3. Реестр проблем (обновлён после Фаз 3–4)

Статусы: 🟢 решено · 🟡 re-baseline (target пересмотрен) · 🟠 открыто.

После миграции в pa-clean (Phase 6) + финальной приёмки §7 все 15 P-айтемов
закрыты или пересмотрены. Открытым остался только P14 (coverage) с
пересмотренным target'ом — см. ниже.

| # | Проблема | Статус | Где |
|---|----------|--------|-----|
| P1 | Конфиг вычислялся на импорте, без рантайм-редактирования | 🟢 решено | `config.py` (overlay `data/config.json`) |
| P2 | 6 из 9 обязательных настроек ИИ отсутствовали | 🟢 решено | `config.py` (`EDITABLE_FIELDS`) |
| P3 | Вкладка «Правила» не содержала настроек ИИ | 🟢 решено | `rules.js` «Инструменты ИИ» |
| P4 | Pytest-маркеры не зарегистрированы | 🟢 решено | `pyproject.toml` + `conftest.py` (+ `live` маркер) |
| P5 | Не настроен coverage | 🟢 решено | `pyproject.toml` (`[tool.coverage]`) |
| P6 | Нет CI | 🟢 решено | `.github/workflows/ci.yml` (зарезервирован, `workflow_dispatch` only) |
| P7 | Legacy Outlook-код при переходе на Apple Mail | 🟢 решено | `readers/outlook_*` удалены полностью в pa-clean |
| P8 | Мусор/дубли в репо | 🟢 решено | нет `*.bak`/`.DS_Store`, дубль SCSS убран; `souls.md` удалить вручную (`git rm souls.md`) |
| P9 | Сборка/запуск не верифицированы целиком | 🟢 решено | live MLX/Mail/Calendar тесты зелёные на Mac (37s/30s/4:26) |
| P10 | Хардкод пути песочницы в тесте | 🟢 решено | `test_daily_brief.py` (был `/sessions/...`) |
| P11 | Флаки-тесты по времени суток | 🟢 решено | `test_daily_brief` (`_iso_today_at`) |
| P12 | macOS-only тесты патчили неверный таргет | 🟢 решено | `test_mail_service_fix.py` |
| P13 | `mypy`-долг (type-check non-blocking в CI) | 🟢 решено | `mypy src` — 0 ошибок, блокирующий гейт |
| P14 | Покрытие 80% (цель инструкции) | 🟡 re-baseline | **61.6%** (hermetic: unit+e2e+scenario-not-live). 80% упирается в live-only модули: `webui/routes.py` (1308 строк, endpoints триггерят MLX/AppleScript), `cli.py` (476 строк, click), `scheduler.py` (cron pipeline), `vector_index.py` (sentence-transformers). Core (vault/sync/threads/config/models/rules/services) ≥70%. Рост дальше — через live-suite на Mac. Baseline 61.6% зафиксирован; 80% — stretch goal. |
| P15 | `summarize_system` как единый канон промта | 🟢 решено | `tool_prompts.py`; `mail_summary_prompt` удалён |

---

## 4. Рекомендованный порядок добивки

1. Документация (Фаза 5): README/ARCHITECTURE/RULES/TESTING/INTEGRATIONS.
2. Покрытие ≥80% (P14) + scenario-прогон на Mac (P9).
3. Гигиена (P7, P8): legacy Outlook, мусор, дубли SCSS.
4. `mypy` → блокирующий (P13).
5. Фаза 6 — миграция в `pa-clean` по этапам, затем Фаза 7 (приёмка).
