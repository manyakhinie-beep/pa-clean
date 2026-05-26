# TESTING — pa-merge

## Установка

```bash
cd /path/to/pa-merge          # ВСЕГДА из корня репозитория
uv sync                        # ставит runtime + dev-группу (pytest, ruff, mypy, pytest-cov)
uv sync --extra vector         # доп.: гибридный векторный поиск (torch/sentence-transformers)
```

Требуется macOS на Apple Silicon: `mlx-lm` имеет колёса только под darwin/arm64,
и `uv.lock` запинен под эту платформу.

## Структура и маркеры

Тесты разложены по каталогам, маркеры проставляются **автоматически** по пути
(`tests/conftest.py`, хук `pytest_collection_modifyitems`) и зарегистрированы в
`pyproject.toml`:

| Маркер | Что | Источник |
|--------|-----|----------|
| `unit` | быстрые изолированные тесты (без MLX/Apple/сети) | `tests/unit/**` |
| `e2e` | in-process через FastAPI `TestClient` (MLX замокан) | `tests/e2e/**` |
| `scenario` | интеграция с реальной подсистемой | `tests/scenarios/**` |
| `mlx` | требует локальный MLX-инференс | путь содержит `mlx` |
| `mail` | Apple Mail / парсинг почты | путь: `mail/draft/outlook/inbox` |
| `calendar` | Apple Calendar / date-логика | путь содержит `calendar` |
| `live` | реальная внешняя интеграция: Automation-права Apple **или** локальная MLX-модель | явный `pytestmark` в файле |

Явные `@pytest.mark.<name>` тоже работают и складываются с авто-маркерами.

`live` ставится **вручную** (не по пути) на scenario-файлах, которым нужна
реальная периферия:

- `test_applescript_scenarios.py`, `test_draft_flow_applescript.py` — живой
  AppleScript к Calendar.app / Mail.app (нужны права Automation/TCC);
- `test_mlx_scenarios.py`, `test_search_scenarios.py` — реальная локальная
  MLX-модель (и опционально embedding-модель).

Остальные scenario-файлы (`test_calendar_scenarios.py`,
`test_draft_scenarios.py`, `test_mail_body_scenarios.py`) полностью на моках и
ни прав, ни модели не требуют. Поэтому `-m "scenario and not live"` даёт
безопасный неинтерактивный прогон — без TCC-промптов и без загрузки модели.
Проверка прав в AppleScript-файлах вынесена в setup-фикстуру (а не на import),
так что выбор `-m "not live"` не вызывает macOS-промпт Automation даже на этапе
сбора тестов.

## Запуск групп

```bash
uv run pytest -m unit                          # основной гейт (зелёный без Mac-периферии)
uv run pytest -m e2e                           # TestClient-эндпоинты
uv run pytest -m "scenario and not live"       # scenario на моках: без прав и без модели
uv run pytest -m "(unit or e2e) or (scenario and not live)"   # полный неинтерактивный гейт

# Живые интеграции — на Mac с правами Automation и/или локальной моделью:
uv run pytest -m "scenario and live"                # все живые тесты сразу
PA_MLX_MODEL_PATH=/путь/к/модели uv run pytest -m "scenario and live and mlx"  # только инференс
uv run pytest -m "scenario and live and mail"       # живой Apple Mail
uv run pytest -m "scenario and live and calendar"   # живой Apple Calendar
```

## Покрытие

`pytest-cov` настроен в `pyproject.toml` (`[tool.coverage.*]`, `source=src`,
branch). По умолчанию `--cov` НЕ в `addopts`, чтобы обычный прогон не зависел от
плагина. Явно:

```bash
uv run pytest -m unit --cov=src --cov-report=term-missing --cov-report=html
open htmlcov/index.html
```

Цель инструкции — ≥80% (на момент написания не достигнута глобально; добор —
открытый пункт P14 в FUNCTIONALITY_MAP).

## Scenario / интеграционные тесты

Не запускаются на CI-раннерах (нет модели и прав). Требуют:

- macOS Apple Silicon, установленный `mlx-lm`;
- `PA_MLX_MODEL_PATH` → путь к локальной MLX-модели (иначе тесты `skip`);
- права доступа к Mail/Calendar (TCC / Automation) — см. [INTEGRATIONS.md](INTEGRATIONS.md);
- для безопасности `PA_E2E_TEST_MODE=true` подавляет реальные side-effects
  (создание событий/черновиков) там, где это поддержано.

Scenario-тесты сами вызывают `pytest.skip(...)`, если условие не выполнено —
так что на «голой» машине они не падают, а пропускаются.

**`live` vs не-`live`.** Часть scenario-файлов — на моках (`run_applescript` и
настройки замоканы) и прав/модели не требуют; они без маркера `live` и входят в
`-m "scenario and not live"`. Файлы, реально дёргающие AppleScript или MLX,
помечены `live` — для неинтерактивного прогона исключайте их через `-m "not live"`.

`test_applescript_scenarios.py` несёт только маркеры `scenario` + `live` (без
`mail`/`calendar`, т.к. авто-маркеры ставятся по ключевому слову в пути) — он
попадает в выборку `-m "scenario and live"`, но не в `-m "… and calendar"`.

При первом живом прогоне macOS покажет промпт Automation для
Mail/Calendar/System Events — подтвердите его (или выдайте права заранее в
System Settings → Privacy & Security → Automation). До подтверждения
`osascript` блокируется; именно поэтому живые файлы исключены из
неинтерактивного гейта.

## CI

`.github/workflows/ci.yml` на `macos-14` (Apple Silicon): `uv sync` →
`ruff check src tests` (блокирующий) → `mypy src` (блокирующий) →
`pytest -m unit` (+ coverage) → `pytest -m e2e`. Scenario-группы — на
self-hosted macOS (см. комментарий в workflow). Герметичную подгруппу
`-m "scenario and not live"` можно гонять и на GitHub-hosted раннере — она не
требует прав и модели.

## Линт и типы

```bash
uv run ruff check src tests      # должно быть «All checks passed»
uv run mypy src                  # блокирующий гейт в CI (P13 закрыт)
```

## Частые проблемы

- **«no tests ran»** — почти всегда запуск не из корня репозитория.
- **`ModuleNotFoundError` для MLX/torch** — это ожидаемо вне macOS arm64;
  такие тесты помечены и/или `skip`.
- **Флаки по времени суток** — устранены в `test_daily_brief` (хелпер
  `_iso_today_at`); при добавлении календарных тестов не используйте `now ± N
  часов` для «сегодняшних» событий.
