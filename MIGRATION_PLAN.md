# MIGRATION_PLAN — pa-merge → pa-clean (Фаза 6)

Цель: собрать чистую, воспроизводимую версию проекта **pa-clean** из
валидированного кода **pa-merge**, включая функционал постадийно, с гейтом
(ruff + mypy + тесты) на каждом этапе.

## Выбранная стратегия

- **Цель миграции:** отдельный репозиторий в `pa-clean` (`git init` с нуля).
  `pa-merge` остаётся нетронутым источником истории и эталоном для сверки.
- **Гранулярность:** постадийно, 7 этапов по §6.2. Переход к следующему этапу
  только после зелёного гейта текущего.
- **История:** в `pa-clean` каждый этап — один осмысленный commit
  (`stage-N: …`). Без переноса грязной истории pa-merge.

## 0. Разделение труда (песочница ↔ Mac)

Часть проверок нельзя выполнить вне macOS/Apple Silicon, плюс git-операции идут
только на машине пользователя.

| Действие | Где | Почему |
|----------|-----|--------|
| `git init` / `git add` / `git commit` | **Mac** | git-операции выполняются на машине пользователя |
| `ruff check` | Mac (или песочница) | детерминирован, платформо-независим |
| `mypy src` | **Mac** | нужен установленный `mlx-lm` (есть только под darwin/arm64) |
| `pytest -m "(unit or e2e) or (scenario and not live)"` | Mac или песочница | герметично, без модели/прав |
| `pytest -m "scenario and live …"` | **Mac** | реальные MLX/Mail/Calendar + права Automation |
| `webui && npm run build` | Mac или песочница | node-сборка |
| Предварительная сверка манифестов и копий | песочница | быстрая проверка перед коммитом |

## 6.1. Подготовка

### Инициализация репозитория (на Mac)

```bash
cd /Users/imaniakhin/Projects/pa-clean
git init
git branch -m main
# Скелет (создаём пустые целевые каталоги)
mkdir -p src/personal_assistant tests/{unit,e2e,scenarios} webui docs .github/workflows
```

### Что НЕ переносим (exclusion manifest)

Никогда не копируем в чистый репозиторий:

| Категория | Пути | Причина |
|-----------|------|---------|
| Кэши | `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`, `htmlcov/`, `**/__pycache__/`, `pytest-cache-files-*`, `*.pyc` | артефакты, пересоздаются |
| Окружение | `.venv/`, `logs/`, `webui/node_modules/`, `webui/dist/` | пересобирается из lock-файлов |
| Dev-данные | `data/` (локальный vault + `config.json`-оверлей) | пользовательские/секретные данные, gitignored |
| **Секреты** | `.env` | **содержит ключи — переносим только `.env.example`** |
| Мусор-файлы | `__rmtest_5`, `souls.md`, `souls 2.md`, `docs/souls.md` | пустышки/временные |
| Крупные бинарники | любые `*.pdf` / выгрузки > 1 МБ | раздувают репозиторий |
| На ревью (keep/drop) | `fix_env.sh`, `make.sh`, `run.sh`, `setup.sh`, `AGENTS.md`, часть `docs/*` | оставить только актуальное; см. этап 7 |

### Базовые файлы (этап 0, до функционала)

Переносим и сразу коммитим каркас сборки:

```
pyproject.toml   uv.lock   .python-version   .gitignore   .env.example
```

После переноса — пересобрать окружение и зафиксировать чистоту базы:

```bash
uv sync
git secrets --scan 2>/dev/null || true      # либо: trufflehog filesystem .
git add -A && git commit -m "stage-0: build scaffolding (pyproject, uv.lock, gitignore, env.example)"
```

## 6.2. Порядок включения функционала

Сопоставление этапов §6.2 с реальными модулями `personal_assistant`.
Зависимости: `readers/applescript_base.py` нужен этапам 4 и 5; `config.py` и
`models.py` — всем; общий `tests/conftest.py` переносится на этапе 1 и далее
дополняется.

| Этап | Функционал | Модули (src) | Тесты | Гейт приёмки |
|------|-----------|--------------|-------|--------------|
| **1** | Конфиг + вкладка «Правила» | `__init__.py`, `config.py`, `models.py`, `report_schemas.py`, `webui/rules_settings.py` | `tests/conftest.py`, `test_config.py`, `test_rules_settings_api.py`, `test_report_schemas.py` | все 8 настроек сохраняются/валидируются/читаются; ruff+mypy; `-m unit` зелёный |
| **2** | Core pa-merge (без ИИ) | `vault/`, `personal_vault/`, `sync/`, `utils/`, `templates/`, `reports/` | соответств. `tests/unit/**` | базовый мердж/обработка стабильны; ruff+mypy; `-m unit` + `-m e2e` (базовые) |
| **3** | MLX-интеграция | `mlx_server/` (engine, server, chat_routes, vault_index, scheduler, tasks), `services/{llm_classify,summarize,daily_brief,meeting_prep}_service.py` | unit на MLX-обёртку + `scenarios/test_mlx_scenarios.py`, `test_search_scenarios.py` | модель грузится, инференс работает, настройки применяются; `-m "scenario and live and mlx"` на Mac |
| **4** | Mail-интеграция | `readers/applescript_base.py`, `readers/mail_reader.py`, `services/mail_service.py`, `inbox/`, draft-эндпоинты `mlx_server/chat_routes.py` | mail-unit + `scenarios/test_draft_scenarios.py` (моки), `test_draft_flow_applescript.py` (live), `test_mail_body_scenarios.py` | чтение писем, декод, черновики; `-m "scenario and mail"` (mocked) зелёный, live — на Mac с правами |
| **5** | Calendar-интеграция | `calendar/` (intent_parser, calendar_writer, routes), `readers/calendar_reader.py`, `services/calendar_service.py`, `today/` | calendar-unit + `test_settings_wiring.py` (сквозной: config+calendar+mail), `scenarios/test_calendar_scenarios.py` (моки), `test_applescript_scenarios.py` (live) | чтение событий, слоты, создание; `-m "scenario and calendar"` (mocked) зелёный |
| **6** | WebUI полный цикл | `webui/` (frontend js/scss, `index.html`, `scripts/`, `package.json`), `webui/routes.py`, `profile/` | `tests/e2e/**` | `npm run build` ок; пользовательский сценарий вход→результат; `-m e2e` зелёный |
| **7** | Документация + CI | `README.md`, `ARCHITECTURE.md`, `RULES.md`, `TESTING.md`, `INTEGRATIONS.md`, курир. `docs/`, `.github/workflows/ci.yml` | — | все доки актуальны; CI зелёный; чеклист §7 пройден |

На каждом этапе:

```bash
# (Mac) перенести модули + тесты этапа N из pa-merge, затем:
uv run ruff check src tests
uv run mypy src
uv run pytest -m "<маркеры этапа>"          # см. колонку «Гейт»
git add -A && git commit -m "stage-N: <функционал>"
```

## 6.3. Критерии «чистоты» (на каждом этапе)

- [ ] `ruff check src tests` — «All checks passed».
- [ ] `mypy src` — 0 ошибок.
- [ ] Unit-тесты для нового кода зелёные (для этапов 3–5 — плюс соответствующие scenario, где доступна периферия).
- [ ] Нет `TODO`/`FIXME` без привязки к задаче.
- [ ] Нет секретов (`git secrets --scan` / `trufflehog`); `.env` не закоммичен.
- [ ] Self-review диффа этапа (осознанный, минимальный набор файлов).
- [ ] Каждый этап — отдельный commit `stage-N: …`.

## 7. Финальная приёмка (после этапа 7)

- [ ] Репозиторий клонируется в чистую директорию и собирается по `README.md` без ошибок (`uv sync`).
- [ ] Все настройки ИИ доступны и редактируются во вкладке «Правила».
- [ ] `pytest -m unit` — зелёный.
- [ ] `pytest -m e2e` — зелёный.
- [ ] `pytest -m "scenario and not live"` — зелёный (герметичные сценарии).
- [ ] `pytest -m "scenario and live and mlx"` — зелёный (на Mac с моделью).
- [ ] `pytest -m "scenario and live and mail"` / `… and calendar"` — зелёный (на Mac с правами Automation).
- [ ] `ruff` и `mypy` чистые; CI (`ci.yml`) зелёный.
- [ ] Документация полная и актуальная; нет критических security-issue.
- [ ] Зафиксирован performance baseline (время инференса MLX, время мерджа).

## Заметки по решениям

- Планировочные доки (`AUDIT_PLAN.md`, `MIGRATION_PLAN.md`) положить в `docs/` чистого
  репо или оставить в корне как процесс-артефакты; `pa-merge-claude-code-instruction.md`
  — dev-only, в продуктовый репозиторий не обязателен.
- `webui/dist/` и `webui/node_modules/` не коммитим — они в `.gitignore`; сборка из
  `package.json` + `package-lock.json` на этапе 6.
- Маркер `live` (см. `TESTING.md`) позволяет держать CI на GitHub-hosted раннере
  герметичным: `-m "(unit or e2e) or (scenario and not live)"`.
