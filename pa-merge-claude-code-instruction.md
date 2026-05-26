# Инструкция для Claude Code: Аудит и рефакторинг проекта pa-merge

## Роль и контекст

Ты — старший инженер-аудитор и fullstack-разработчик. Твоя задача — провести полный аудит проекта **pa-merge**, восстановить его работоспособность, обеспечить покрытие тестами и подготовить чистую версию для продакшена. Проект работает в экосистеме macOS и использует локальные ИИ-модели через MLX, интеграцию с Mail и Calendar.

---

## 1. Фаза разведки (Discovery)

### 1.1. Структура проекта
```bash
# Выполни в терминале внутри проекта pa-merge
find . -type f -not -path '*/\.git/*' -not -path '*/node_modules/*' -not -path '*/venv/*' | head -100
tree -L 3 -I 'node_modules|venv|__pycache__|.git|dist|build'
```

**Что зафиксировать:**
- [ ] Стек технологий (языки, фреймворки, библиотеки)
- [ ] Точки входа (entry points): CLI, WebUI, API
- [ ] Конфигурационные файлы и переменные окружения
- [ ] Зависимости: `requirements.txt`, `package.json`, `pyproject.toml`, `Cargo.toml`
- [ ] Наличие документации: README, ARCHITECTURE.md, API docs

### 1.2. Анализ работоспособности
```bash
# Попытка сборки/запуска
git log --oneline -20
make --version 2>/dev/null && make build || echo "No Makefile"
npm run build 2>/dev/null || echo "No npm build"
docker-compose ps 2>/dev/null || echo "No docker"
```

**Что зафиксировать:**
- [ ] Состояние основной ветки (main/master): собирается ли?
- [ ] Ошибки при сборке или запуске
- [ ] Устаревшие зависимости (vulnerabilities, deprecated packages)
- [ ] Жестко закодированные пути или ключи

---

## 2. Фаза аудита функционала

### 2.1. Карта функционала (Functionality Map)
Составь таблицу всех функций проекта:

| ID | Функция | Модуль | Точка входа | Статус | Примечания |
|----|---------|--------|-------------|--------|------------|
| F1 | ... | ... | ... | ??? | ... |

**Методика:**
1. Прочитай все маршруты/команды/экраны
2. Для каждой функции определи: входные данные, ожидаемый результат, побочные эффекты
3. Отметь функции, связанные с ИИ-инструментами (LLM, MLX, embedding)

### 2.2. Аудит интеграций

#### MLX (локальные модели)
- [ ] Проверить инициализацию MLX в коде
- [ ] Убедиться, что модели загружаются из корректных путей или скачиваются автоматически
- [ ] Проверить fallback на CPU если GPU недоступна
- [ ] Замерить производительность (latency) базовых операций

#### Apple Mail
- [ ] Проверить доступ к Mail 
- [ ] Проверить права доступа (Sandbox, TCC, Full Disk Access)
- [ ] Проверить чтение и декодирование RTF/HTML
- [ ] Проверить отправку/создание черновиков через AppleScript или API

#### Apple Calendar
- [ ] Проверить чтение событий через EventKit или AppleScript
- [ ] Проверить создание событий/встреч
- [ ] Проверить проверку доступности слотов (free/busy)

### 2.3. Аудит архитектуры
- [ ] Разделение слоёв: UI / Business Logic / Data / Integrations
- [ ] Наличие единого конфигурационного слоя
- [ ] Обработка ошибок и логирование
- [ ] Управление состоянием (state management)

---

## 3. Фаза: Вкладка «Правила» (AI Tool Settings)

### 3.1. Требования к отображению настроек
Все параметры ИИ-инструментов **должны** иметь UI-отображение во вкладке «Правила»:

**Обязательные настройки для отображения:**
| Настройка | Тип | Описание | Используется в |
|-----------|-----|----------|----------------|
| `mlx_model_path` | string | Путь к локальной модели MLX | MLX inference |
| `mlx_temperature` | float [0.0-2.0] | Температура сэмплирования | MLX generation |
| `mlx_max_tokens` | integer | Максимум токенов | MLX generation |
| `mlx_top_p` | float | Nucleus sampling | MLX generation |
| `mail_auto_draft` | boolean | Автосоздание черновиков | Mail integration |
| `mail_summary_prompt` | text | Промт для суммаризации | Mail AI features |
| `calendar_check_conflicts` | boolean | Проверка конфликтов | Calendar integration |
| `calendar_default_duration` | integer | Длительность встречи по умолчанию | Calendar creation |
| `e2e_test_mode` | boolean | Режим тестирования без side-effects | Tests |

### 3.2. Реализация
- [ ] Создать/обновить компонент `RulesPanel` / `SettingsView`
- [ ] Все поля должны быть двусторонне связаны (binding) с конфигом
- [ ] При изменении настройки — немедленное сохранение в `config.json` / UserDefaults / DB
- [ ] Валидация ввода (например, temperature не может быть > 2.0)
- [ ] Подсказки (tooltips) для каждой настройки

### 3.3. Использование настроек при вызове
- [ ] Проверить, что `MLXClient` читает параметры из конфига, а не из хардкода
- [ ] Проверить, что Mail-функции проверяют флаг `mail_auto_draft`
- [ ] Проверить, что Calendar-функции используют `calendar_default_duration`
- [ ] Добавить middleware/decorator для инъекции настроек перед вызовом инструмента

---

## 4. Фаза: Тестирование

### 4.1. Unit-тесты
**Целевое покрытие: >= 80%**

```bash
# Python
pytest --cov=src --cov-report=html --cov-report=term-missing

# JS/TS
jest --coverage --coverageDirectory=./coverage
```

**Обязательные модули для покрытия:**
- [ ] Конфигурация и парсинг настроек (`config.py`, `settings.ts`)
- [ ] MLX wrapper / inference engine
- [ ] Парсеры Mail 
- [ ] Calendar helpers (date math, slot checking)
- [ ] Бизнес-логика мерджа/обработки данных (core pa-merge logic)

### 4.2. E2E-тесты (WebUI)
**Инструменты:** Playwright / Cypress / Selenium (в зависимости от стека)

**Сценарии E2E:**
- [ ] Открытие приложения / WebUI
- [ ] Навигация во вкладку «Правила»
- [ ] Изменение настройки MLX -> сохранение -> проверка в `config.json`
- [ ] Запуск ИИ-операции через WebUI -> проверка вызова MLX
- [ ] Полный цикл: Mail -> AI-обработка -> Calendar (если применимо)

```python
# Пример Playwright (Python)
def test_rules_tab_saves_mlx_settings(page):
    page.goto("http://localhost:3000")
    page.click("text=Правила")
    page.fill("[data-testid=\'mlx-temp\']", "0.7")
    page.click("[data-testid=\'save-rules\']")

    config = read_config()
    assert config["mlx_temperature"] == 0.7
```

### 4.3. Scenario / Integration тесты
**Цель:** проверить реальные интеграции с MLX, Mail, Calendar

#### MLX Scenario Tests
```python
@pytest.mark.scenario
@pytest.mark.mlx
def test_mlx_local_inference_with_configured_model():
    """Использует реальный MLX с моделью из настроек."""
    settings = load_rules()
    model = load_mlx_model(settings.mlx_model_path)
    result = model.generate("Summarize: hello world", 
                           temp=settings.mlx_temperature)
    assert len(result) > 0
    assert result != "ERROR"
```

#### Mail Scenario Tests
```python
@pytest.mark.scenario  
@pytest.mark.mail
def test_read_last_10_emails_local():
    """Читает реальные письма из локального Outlook DB."""
    mails = mail_client.get_recent(limit=10)
    assert len(mails) <= 10
    for mail in mails:
        assert mail.subject is not None
        # Проверка декодирования body
        assert mail.body is not None or mail.rtf is not None
```

#### Calendar Scenario Tests
```python
@pytest.mark.scenario
@pytest.mark.calendar
def test_check_availability_and_create_meeting():
    """Проверяет слоты и создает тестовую встречу (в тестовом календаре)."""
    slots = calendar.get_free_slots(date="2026-05-26", duration_minutes=30)
    assert len(slots) > 0

    # Создание в тестовом календаре (не основном!)
    event = calendar.create_test_event(
        title="[TEST] pa-merge scenario",
        start=slots[0].start,
        duration=30
    )
    assert event.id is not None

    # Cleanup
    calendar.delete_event(event.id)
```

### 4.4. Тестовая инфраструктура
- [ ] `conftest.py` / `test-setup.ts` с фикстурами
- [ ] Маркеры pytest: `@pytest.mark.unit`, `@pytest.mark.e2e`, `@pytest.mark.scenario`, `@pytest.mark.mlx`, `@pytest.mark.mail`, `@pytest.mark.calendar`
- [ ] Запуск по группам: `pytest -m unit`, `pytest -m "scenario and mlx"`
- [ ] CI-конфигурация (GitHub Actions / GitLab CI) с macOS runner
- [ ] Отчёты Allure / HTML Coverage

---

## 5. Фаза: Документация

### 5.1. Обязательные документы
- [ ] `README.md` — быстрый старт, установка, запуск
- [ ] `ARCHITECTURE.md` — диаграмма компонентов, потоки данных
- [ ] `RULES.md` — описание всех настроек во вкладке «Правила»
- [ ] `TESTING.md` — как запускать тесты, требования к окружению
- [ ] `INTEGRATIONS.md` — MLX, Mail, Calendar: требования, права доступа, ограничения

### 5.2. Документирование кода
- [ ] Docstrings для всех публичных функций (Google/NumPy style)
- [ ] Type hints (Python) / TypeScript strict mode
- [ ] Комментарии к сложным алгоритмам мерджа

---

## 6. План миграции в чистый проект (Clean Project)

### 6.1. Подготовка
```bash
# Создать новый репозиторий / ветку
git checkout --orphan clean-main
git rm -rf .
```

### 6.2. Порядок включения функционала

| Этап | Функционал | Тесты | Критерий приёмки |
|------|-----------|-------|------------------|
| **1** | Конфигурация + вкладка «Правила» | Unit | Все настройки сохраняются, валидируются, читаются |
| **2** | Core logic pa-merge (без ИИ) | Unit + E2E | Базовые операции мерджа работают стабильно |
| **3** | MLX интеграция | Unit + Scenario | Модель загружается, инференс работает, настройки применяются |
| **4** | Mail интеграция | Unit + Scenario | Чтение писем, декодирование, создание черновиков |
| **5** | Calendar интеграция | Unit + Scenario | Чтение событий, проверка слотов, создание встреч |
| **6** | WebUI полный цикл | E2E | Пользовательский сценарий от входа до результата |
| **7** | Документация + CI | — | Все README, архитектура, автоматические проверки |

### 6.3. Критерии «чистоты» для каждого этапа
- [ ] Код проходит linting (ruff, eslint, mypy)
- [ ] 100% unit-тестов для нового кода
- [ ] Нет `TODO` / `FIXME` без задачи в трекере
- [ ] Нет секретов в коде (проверка через `git-secrets` / `truffleHog`)
- [ ] Код ревью пройден (self-review или peer)

---

## 7. Чеклист финальной приёмки

- [ ] Проект клонируется в чистую директорию и собирается по `README.md` без ошибок
- [ ] Все настройки ИИ доступны и редактируются во вкладке «Правила»
- [ ] `pytest -m unit` — зелёный
- [ ] `pytest -m e2e` — зелёный (WebUI запущен)
- [ ] `pytest -m "scenario and mlx"` — зелёный (на машине с MLX)
- [ ] `pytest -m "scenario and mail"` — зелёный (на macOS с Outlook)
- [ ] `pytest -m "scenario and calendar"` — зелёный
- [ ] Документация полная и актуальная
- [ ] Нет критических security issues
- [ ] Performance baseline зафиксирован (время инференса MLX, время мерджа)

---

## 8. Команды для Claude Code (Quick Actions)

```bash
# Аудит текущего состояния
/claude audit-project

# Запуск всех тестов
/claude test-all

# Проверка конкретной интеграции
/claude test-mlx
/claude test-mail
/claude test-calendar

# Генерация отчёта
/claude generate-report --format=markdown

# Миграция этапа в чистый проект
/claude migrate-stage --stage=3
```

---

## Примечания для исполнителя (Claude Code)

1. **Не предполагай работоспособность** — всегда проверяй фактическое состояние через запуск.
2. **macOS-specific**: Mail и Calendar требуют прав доступа. Проверяй `tccutil` и запросы авторизации.
3. **MLX**: Учитывай разные архитектуры (Apple Silicon vs Intel). На Intel MLX может не работать или работать медленно.
4. **Тестовые side-effects**: Всегда используй тестовые календари/папки для Mail. Никогда не отправляй реальные письма из тестов.
5. **Версионирование**: Фиксируй версии зависимостей жёстко (lock files) для воспроизводимости.
