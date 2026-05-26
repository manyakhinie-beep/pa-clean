# pa-clean — Personal AI Assistant

Оффлайн-ассистент для macOS: синхронизирует Apple Calendar и Apple Mail
в локальное Markdown-хранилище (vault) и отвечает на вопросы через локальный MLX-инференс.
Данные не покидают компьютер.

---

## Документация

- [ARCHITECTURE.md](ARCHITECTURE.md) — слои, потоки данных, персистентность, модель потоков MLX.
- [RULES.md](RULES.md) — вкладка «Правила» и все настройки ИИ (типы, диапазоны, API).
- [TESTING.md](TESTING.md) — запуск тестов, маркеры, покрытие, CI.
- [INTEGRATIONS.md](INTEGRATIONS.md) — MLX, Apple Mail, Apple Calendar: права доступа и ограничения.
- [docs/FUNCTIONALITY_MAP.md](docs/FUNCTIONALITY_MAP.md) — карта функционала и реестр проблем.

---

## Содержание

- [Требования](#требования)
- [Быстрый старт](#быстрый-старт)
- [Конфигурация](#конфигурация)
- [Запуск](#запуск)
- [WebUI](#webui)
- [AI-функционал](#ai-функционал)
- [Синхронизация данных](#синхронизация-данных)
- [Сервисы](#сервисы)
- [CLI-команды](#cli-команды)
- [Тестирование](#тестирование)
- [Структура проекта](#структура-проекта)
- [Устранение неисправностей](#устранение-неисправностей)

---

## Требования

| Компонент | Версия | Зачем |
|---|---|---|
| macOS | 13+ Ventura | AppleScript-интеграция с Calendar / Mail |
| Python | 3.11–3.13 | runtime + MLX-инференс |
| [uv](https://github.com/astral-sh/uv) | любая | менеджер зависимостей |
| Node.js | 18+ | пересборка WebUI (опционально — dist/ уже собран) |
| Apple Silicon M1+ | — | MLX-инференс |

> **Python 3.14+** — MLX не поддерживает cp314. Для инференса используйте Python 3.13:
> ```bash
> uv python install 3.13 && uv venv --python 3.13 && uv sync && (cd webui && npm install && npm run build)
> ```
>
> **Intel Mac** — сервер запустится, чат вернёт `503`. Vault, поиск и синхронизация работают.

---

## Быстрый старт

```bash
# 1. Клонировать
git clone https://github.com/your-org/pa-clean
cd pa-clean

# 2. Полная сборка: deps + WebUI + создать .env
cp .env.example .env
uv sync                                          # включает dev: ruff, mypy, pytest-cov
# или для прод-сборки без dev-инструментов (без coverage, ruff, mypy, pytest):
# uv sync --no-dev
(cd webui && npm install && npm run build)

# 3. Настроить .env (обязательно: vault-путь и MLX-модель)
$EDITOR .env

# 4. Проверить конфигурацию
uv run pa check

# 5. Запустить
uv run pa serve
```

WebUI доступен на **http://127.0.0.1:8000**.

---

## Конфигурация

Все настройки в `.env` (скопируйте из `.env.example`: `cp .env.example .env`).

```dotenv
# Где хранить vault (.md файлы с письмами, встречами, контактами)
PA_VAULT_PATH=~/PersonalAssistantVault

# Активные источники данных (через запятую):
#   calendar — Apple Calendar через AppleScript
#   mail     — Apple Mail через AppleScript
PA_SYNC_SOURCES=calendar,mail

# Сохранять вложения из Mail (true/false)
PA_MAIL_FETCH_ATTACHMENTS=false
PA_MAIL_ATTACHMENTS_PATH=~/PersonalAssistantVault/attachments

# Локальная MLX-модель (абсолютный путь к директории)
PA_MLX_MODEL_PATH=/Users/you/models/mlx-community/Mistral-7B-Instruct-v0.3-4bit

# Адрес и порт сервера
PA_SERVER_HOST=127.0.0.1
PA_SERVER_PORT=8000

# Автосинхронизация по расписанию
PA_SCHEDULE_ENABLED=false
PA_SCHEDULE_CRON=0 9 * * *
```

Полный список — в [`.env.example`](.env.example).

---

## Запуск

```bash
# Базовый запуск
uv run pa serve

# С предзагрузкой MLX-модели (первый чат-запрос мгновенный)
uv run pa serve --preload-model

# Пересборка WebUI после изменений в frontend/
(cd webui && npm run build)
```

---

## WebUI

Браузерный интерфейс на **http://127.0.0.1:8000** — семь вкладок:

| Вкладка | Назначение |
|---|---|
| **Сегодня** | Дашборд: события дня, срочные письма, предложения ассистента |
| **Inbox** | Входящие: письма и встречи, сортировка по срочности, просмотр деталей |
| **Чат** | Диалог с ассистентом; режимы: Чат / Поиск / Суммаризация / Черновик |
| **Vault** | 3-колоночный просмотр .md-файлов: sidebar-фильтры, деталь письма/встречи, связи (mentioned-in) |
| **Проекты** | GTD-проекты с подзадачами и ссылками на vault-документы |
| **Правила** | 3 вкладки: Матрица Эйзенхауэра · GTD-правила · Инструменты (classify, промпты, реестр) |
| **Поиск** | Гибридный поиск BM25 + семантический по всему vault |
| **Настройки** | Профиль пользователя, конфиг ассистента, расписание синхронизации |

### Вкладка «Сегодня»

Открывается первой (URL `#today`) и даёт быстрый обзор рабочего дня в трёх колонках:

**События дня** — список встреч и блоков из Apple Calendar. Цветные точки: зелёная = идёт сейчас, синяя = предстоит, серая = прошла. Чипы «бриф готов» и «срочно» подсвечивают ключевые события. Клик открывает деталь в Vault.

**Требуют внимания** — до 3 самых срочных или важных писем из Inbox. Аватар с инициалами, имя отправителя, тема, превью. Клик переходит в Inbox на это письмо.

**Ассистент предлагает** — до 3 правил-based предложений (черновик ответа на срочное письмо, бриф к ближайшей встрече, фокус-слот). Каждое действие открывает Чат с предзаполненным запросом.

Кнопки в hero-секции:
- **Спросить подробнее** — открывает чат с запросом о расписании дня
- **Открыть календарь** — переходит в Vault с фильтром `calendar`
- **🤖 сводка от ассистента** — открывает чат с запросом суммаризации дня

Поиск в шапке вкладки перенаправляет запрос на вкладку «Поиск».

API-эндпоинт: `GET /api/v1/today` — возвращает `greeting`, `bullets`, `events`, `attention`, `suggestions`, `updated_at`, `next_update`.

### Функции чата

- **@-упоминания** — прикрепить любой .md-файл из vault прямо в контекст
- **Тред-контекст** — кнопки «Суммаризировать» / «Черновик ответа» в vault передают цепочку писем в модель
- **Стриминг** — ответ появляется токен за токеном
- **Черновик в Mail** — кнопка «Открыть в Mail» создаёт compose-окно с `In-Reply-To` исходного письма; автосохранение через Cmd+S

### Вкладка «Правила»

Три суб-вкладки:

**Матрица Эйзенхауэра** — задачи в 4 квадрантах (Срочно+Важно / Важно / Срочно / Остальное). Клик по задаче переносит её в следующий квадрант. Кнопка ✦ AI Распределить отправляет запрос ассистенту в чат. Кнопка «+ Задача» добавляет задачу с выбором квадранта.

**GTD-правила** — свободные заметки (как обрабатывать входящие) + таблица структурированных правил (условие → действие). Кнопки «Применить» / «Сбросить» с отслеживанием несохранённых изменений.

**Инструменты** — настройки, влияющие на выполнение сценариев ИИ:
- `classify.yaml` — редактор правил классификации с кнопками «Сохранить», «Применить к vault», «Сбросить теги»
- Системные промпты для режимов «Черновик» и «Суммаризация» (лимит 8 000 символов)
- Реестр инструментов (tool calling) с переключателями включения

### Теги классификации

Классификатор (`classify.yaml`) присваивает тегам формат `urgency:urgent`, `category:finance` и т.д.  
Теги отображаются **цветными пилюлями** во всех трёх разделах:

| Тег | Цвет |
|---|---|
| `urgency:urgent` | красный |
| `urgency:important` | оранжевый |
| `urgency:low` | зелёный |
| `category:finance` | изумрудный |
| `category:meetings` | синий |
| `category:projects` | розовый |
| `category:hr` | оранжевый |
| `category:legal` | фиолетовый |
| `category:travel` | голубой |

После изменения `classify.yaml` → «Применить к vault» — теги мгновенно обновятся в Inbox, Vault и Поиске без перезапуска сервера.

---

## AI-функционал

### Структурированное извлечение (Stage 1)

Для каждого входящего письма ассистент автоматически вычисляет структурированный анализ:

| Поле | Описание |
|---|---|
| `intent` | Намерение письма: `question`, `request`, `info`, `urgent`, `complaint`, `approval`, `unknown` |
| `reply_required` | `true` / `false` — требует ли письмо ответа |
| `tone` | Тональность: `formal`, `informal`, `urgent`, `neutral` |
| `deadline` | ISO-дата дедлайна (если упомянута), иначе `null` |
| `action_items` | Список поручений из тела письма (≤ 10) |
| `entities` | Именованные сущности: `people`, `organizations`, `amounts`, `dates` |
| `method` | Способ анализа: `mlx` (локальный инференс), `fallback` (regex), `cached` |

**Endpoints:**

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/v1/inbox/{item_id}/extract` | Запустить анализ (параметр `force=true` для повтора) |
| GET  | `/api/v1/inbox/{item_id}/extraction` | Получить сохранённый результат |
| DELETE | `/api/v1/extraction-cache` | Сбросить MLX-кэш дедупликации (не затрагивает сохранённые результаты) |

Результат сохраняется в `data/inbox_state.json` (per-item) и кэшируется в `data/extraction_cache.json` (SHA256 по телу письма).

### AI Priority Score (Stage 2)

Каждый входящий элемент получает числовой приоритет от 0 до 100 на основе набора сигналов:

| Компонент | Сигнал | Бонус |
|---|---|---|
| Срочность | теги `urgency:urgent`, `urgency:critical` | +40 |
| Срочность | теги `urgency:medium`, `urgency:important` | +20 |
| Ответ | `extraction.reply_required = true` или тег `reply_needed` | +15 |
| Отправитель | частота контакта из vault (0–5 писем = +5, ≥ 20 = +15) | +5…+15 |
| Дедлайн | сегодня / завтра / эта неделя | +25 / +15 / +10 |
| Возраст | письмо > 7 дней | −5…−20 |
| Непрочитано | элемент не отмечен как прочитанный | +5 |
| MLX-буст | если 30 ≤ score ≤ 60 — локальная модель добавляет ±5 | ±5 |

**Метки:** `low` (< 34) · `medium` (34–66) · `high` (≥ 67)

Индикатор приоритета отображается в Inbox как цветная вертикальная полоска слева от каждого письма.

**Сортировка по приоритету:** в Inbox нажмите кнопку **↓ Дата** / **↓ Приоритет** в правой части фильтров.

**API:** `GET /api/v1/inbox?sort_by=priority` — список отсортирован по убыванию `priority`.

**Сценарии использования:**

1. Утренний review — переключить сортировку на «Приоритет», обработать top-10.
2. Дедлайны — письма с `deadline:today` автоматически попадают в `high`.
3. VIP-контакты — частые отправители получают бонус к приоритету без ручной настройки.
4. Деградация — если MLX-модель не загружена, используется только rule-based score.

---

### Follow-up Detection (Stage 2)

Ассистент автоматически помечает письма, которые ждут вашего ответа:

**Условия флага `followup_needed = true`:**
- Тип элемента: `email`
- Письмо требует ответа (`extraction.reply_required`, срочные теги, `intent` = `question`/`request`)
- С момента получения прошло ≥ `threshold_days` дней (по умолчанию 2, настраивается переменной `PA_FOLLOWUP_DAYS_THRESHOLD`)
- В том же треде нет исходящего письма от вашего email (`PA_USER_EMAIL`)

**API:**

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/v1/inbox?filter=followup` | Только элементы с `followup_needed=true` |
| GET | `/api/v1/inbox/followup-needed?threshold_days=N` | Список ID элементов, ожидающих ответа (N ∈ 0–30) |

**Индикатор в UI:** иконка 🔔 рядом с темой письма в списке Inbox. Счётчик в шапке таба «Входящие» показывает `max(unread, followup)`.

**Сценарии использования:**

1. Таб «🔔 Ответить» — одним кликом увидеть все письма, требующие ответа.
2. Быстрая проверка — `GET /api/v1/inbox/followup-needed` возвращает ID за < 50 мс без vault-перебора.
3. Настройка порога — `PA_FOLLOWUP_DAYS_THRESHOLD=1` в `.env` для более агрессивного напоминания.
4. Интеграция с чатом — скопировать `thread_id` из followup-письма и передать в чат с `@thread_id` для генерации черновика ответа.

---

### Thread-Aware Draft (Stage 4)

Когда вы нажимаете «Draft ответа» в Inbox, ассистент не просто передаёт тему письма — он собирает ВЕСЬ тред и формирует обогащённый промпт.

**Как работает:**

1. Frontend вызывает `POST /api/v1/inbox/{item_id}/draft-context`
2. `draft_context_service` сканирует vault по `thread_id` из frontmatter письма
3. Определяет ваши исходящие ответы (`is_mine` по `PA_USER_EMAIL`)
4. Извлекает ключевые факты regex-паттернами (дедлайны, суммы, поручения)
5. Строит готовый `context_prompt` и передаёт в Chat с режимом `draft`

**Ответ `/draft-context`:**

| Поле | Описание |
|---|---|
| `thread_messages` | Хронологический список всех сообщений треда |
| `thread_summary` | Краткая сводка переписки (rule-based или MLX) |
| `key_facts` | Извлечённые факты: `["Дедлайн: 25 мая", "Сумма: 50 000 руб."]` |
| `my_previous_replies` | Ваши предыдущие ответы в этом треде |
| `draft_hint` | `"Тред: 3 письма · 2 факта"` (для UI-чипа) |
| `context_prompt` | Полный промпт для MLX с ═══-разделителями |
| `message_count` | Количество сообщений в треде |

**UX в Chat:**
- Чип 🧵 в заголовке чата с кнопками `?` (drawer) и `×` (убрать контекст)
- Thread drawer: входящие письма — синяя полоса, ваши ответы — зелёная; секция ключевых фактов
- При закрытии треда чат возвращается к обычному режиму

**Graceful degradation:**
- Если vault не загружен — возвращает минимальный контекст без ошибки
- Если MLX недоступен — используется rule-based сводка
- При любой ошибке frontend делает fallback на bare subject

**Сценарии использования:**

1. **Ответ на письмо с дедлайном** — `key_facts` содержит «Дедлайн: 25 мая», модель автоматически упоминает срок в черновике.
2. **Многоходовая переписка** — `thread_messages` даёт полный контекст, черновик учитывает, что вы уже писали ранее.
3. **Делегирование** — из `my_previous_replies` видно, что задача уже подтверждена, модель не начинает "с нуля".

---

### Smart Meeting Prep (Stage 5)

Перед встречей ассистент автоматически собирает контекст из vault и открывает чат с готовым брифингом.

**Как работает:**

1. Вкладка «Сегодня» показывает секцию **«Предстоящие встречи»** (карточки с временем и участниками)
2. Нажатие «Подготовиться» → `GET /api/v1/calendar/{event_id}/prep`
3. `meeting_prep_service` сканирует vault по нескольким направлениям
4. Строит `prep_brief` (rule-based или MLX) и `context_prompt`
5. Открывает вкладку «Чат» с предзаполненным контекстом

**Endpoints:**

| Endpoint | Описание |
|---|---|
| `GET /api/v1/calendar/upcoming?days=7` | Список предстоящих событий из vault за N дней |
| `GET /api/v1/calendar/{event_id}/prep` | Полный контекст подготовки к встрече |
| `GET /api/v1/brief/daily` | Daily Brief: сводка дня (кеш 30 мин) |
| `GET /api/v1/brief/daily?refresh=true` | Daily Brief с принудительным обновлением |
| `POST /api/v1/brief/daily/generate` | Фоновая генерация Daily Brief (202 Accepted) |

**Ответ `/prep`:**

| Поле | Описание |
|---|---|
| `title` | Название встречи |
| `participants` | Список участников (display names) |
| `participant_emails` | Bare-email адреса участников |
| `recent_emails` | Письма от участников за последние 7 дней |
| `related_projects` | Проекты vault, упоминающие участников |
| `previous_meetings` | Прошлые встречи с теми же людьми |
| `open_action_items` | Незакрытые поручения из переписки (regex) |
| `prep_brief` | Брифинг с рекомендациями (rule-based или MLX) |
| `context_prompt` | Готовый промпт для `/api/chat/send` |
| `event_found` | `true` если событие найдено в vault |
| `message_count` | Общее количество найденных контекстных документов |

**Алгоритм `meeting_prep_service`:**

1. Сканирует `vault/calendar/**/*.md` по `id` frontmatter (или имени файла)
2. Извлекает участников из полей `attendees` / `participants` / `contacts` / `invitees`
3. Исключает `PA_USER_EMAIL` из списка участников
4. Ищет письма от участников за 7 дней (`vault/mail/**/*.md`)
5. Ищет проекты, упоминающие участников (`vault/projects/**/*.md`)
6. Ищет предыдущие встречи с теми же людьми (только прошедшие)
7. Извлекает открытые поручения через regex
8. Строит брифинг: rule-based без MLX, MLX-generated если движок доступен

**Graceful degradation:**
- Нет vault → минимальный valid dict, `event_found: false`
- Нет MLX → rule-based brief с полной структурой
- Ошибка в UI → fallback «Помоги подготовиться к встрече `{title}`» + toast

**Сценарии использования:**

1. **За 30 минут до встречи** — «Подготовиться» → сводка: о чём писали участники, открытые задачи, прошлые встречи.
2. **Повторяющийся синк** — `previous_meetings` показывает историю договорённостей, `open_action_items` — что не сделано.
3. **Первая встреча** — если контекста нет, brief сообщает «Встреча проводится впервые», чат открывается без ложных данных.

---

### Daily Brief (Stage 6)

AI-сводка дня: встречи, срочные письма, задачи — одним блоком на вкладке «Сегодня».

**Endpoints:**
- `GET /api/v1/brief/daily` — получить Daily Brief (из кеша или перестроить)
- `GET /api/v1/brief/daily?refresh=true` — принудительное обновление
- `POST /api/v1/brief/daily/generate` — фоновая генерация (возвращает `202 Accepted`)

**Формат ответа:**
```json
{
  "generated_at": "2026-05-25T08:00:00Z",
  "greeting": "Доброе утро, Игорь! Насыщенный день.",
  "sections": [
    {
      "title": "Сегодня в календаре",
      "items": [{"time": "10:00", "title": "Стендап", "is_soon": false}]
    },
    {
      "title": "Требуют ответа",
      "items": [{"subject": "Финансовый отчёт", "sender": "Петров", "deadline": "today"}]
    },
    {
      "title": "Ключевые задачи",
      "items": ["Подписать договор с ООО Ромашка", "Подготовить отчёт ЦБ"]
    }
  ],
  "ai_insight": "У вас 1 встреча и 3 срочных письма. Приоритет — финансовый отчёт до 18:00.",
  "bullets": ["⚡ Срочно: Финансовый отчёт Q2 (Петров, до 18:00)", "📅 10:00 Квартальный обзор Q2"],
  "stats": {"meetings_today": 1, "urgent_inbox": 3, "tasks": 2},
  "cached": false,
  "vault_loaded": true
}
```

**Алгоритм:**
1. Фильтрует события из `vault/calendar/` по сегодняшней ISO-дате
2. Собирает срочную почту: `urgency:critical/high`, `deadline:today`, `reply_required` (окно 7 дней)
3. Извлекает задачи regex-парсингом из писем и тредов
4. Генерирует `ai_insight` — rule-based (всегда) или MLX (если доступен)
5. Строит топ-3 `bullets` в порядке: скоро встречи → дедлайн сегодня → другие срочные

**Расписание:** APScheduler, `CronTrigger(hour=5, minute=0, tz=UTC)` = 08:00 МСК  
Запускается при `PA_SCHEDULE_ENABLED=true`. Кеш 30 мин: `vault/daily/{date}_brief_cache.json`.

**WebUI (вкладка «Сегодня»):**
- Блок с иконкой 🤖, цветными чипсами (📅 N встреч, 🔴 N срочных, ✅ N задач)
- Кнопка ↻ «Обновить» и 💬 «Спросить ассистента»
- Скрыт, если vault не загружен

**Тест-vault для разработки:**
```sh
python scripts/generate_test_vault.py --vault /tmp/test-vault --email igor@example.com
# Генерирует: 2 треда, 5 писем, 2 встречи, 1 проект, thread manifests, index.json
```

**Graceful degradation:**
- Vault не загружен → `{"vault_loaded": false, "sections": [], "bullets": [], "stats": {...}}`
- MLX недоступен → rule-based insight с полной структурой данных
- Ошибка раздела → логируется, остальные разделы строятся

---

### Calendar Intent NLP (Stage 7)

Создание событий в Calendar.app по естественному языку (русский).

**Примеры:**
```
"Встреча с Ивановым в следующий четверг в 15:00"
"Созвон по проекту во вторник утром на час"
"Блокировать время для отчёта в пятницу 14-16"
```

**Endpoints:**
| Endpoint | Описание |
|---|---|
| `POST /api/v1/calendar/parse-intent` | Разбор текста → EventDraft (без создания) |
| `POST /api/v1/calendar/create-from-text` | Разбор + создание события через AppleScript |
| `GET /api/v1/calendar/calendars` | Список записываемых календарей |

**Двухшаговый UX:**
1. Пользователь вводит текст → `parse-intent` → превью-карточка (дата, время, место, участники)
2. Если в тексте нет явного упоминания календаря → `parse-intent` возвращает `needs_calendar: true, available_calendars: [...]` → UI показывает выбор календаря
3. Нажимает ✓ → `create-from-text` с `confirmed=true` + `calendar_name` → событие создаётся в Calendar.app

**Формат EventDraft:**
```json
{
  "title": "Встреча с Ивановым",
  "date_iso": "2026-05-28",
  "time_str": "15:00",
  "duration_minutes": 60,
  "participants": ["Ивановым"],
  "location": "",
  "calendar_name": null,
  "start_iso": "2026-05-28T15:00:00",
  "end_iso": "2026-05-28T16:00:00",
  "confidence": 0.85,
  "warnings": []
}
```

> `calendar_name` — `null` по умолчанию (нет явного ключевого слова). Определяется автоматически из текста: «рабочая встреча» → `"Work"`, «личное» → `"Personal"`. При `null` — `parse-intent` и `create-from-text` возвращают `needs_calendar: true` и список доступных календарей.

**Точки входа в UI:**
- Вкладка «Сегодня» — поле быстрого ввода «Новая встреча» → inline preview → ✓ Создать
- Чат → `/встреча` или `/событие` → ввод описания → автоматически открывается «Сегодня» с превью

**Парсер (полностью оффлайн):** правила для дат, времени, длительности, участников, мест. MLX fallback если confidence < 0.7.

**Graceful degradation:**
- Без Calendar.app / не macOS → `create-from-text` возвращает `created: false, error: ...`
- `dry_run=true` — безопасен для CI: строит AppleScript, не выполняет (использует `"Calendar"` если `calendar_name` не задан)
- `CalendarReader.fetch_events` читает все календари включая read-only (Holidays, Birthdays)

**Тесты:** 50 unit (`test_intent_parser.py`, IP01–IP43) + 25 E2E (`TestCalendarIntentNLP`, `TestCalendarIntentParserUnit`)

---

### LLM-assisted Semantic Classification (Stage 8)

Для документов, которые rule-based классификатор не распознал (confidence < threshold), вызывается MLX-модель для семантической классификации.

**Ключевые файлы:**
- `src/personal_assistant/mlx_server/tasks/llm_classify_service.py` — сервис классификации
- `data/classify.yaml` — секция `llm_classify` с настройками
- `data/llm_classify_cache.json` — SHA-256 кэш результатов (создаётся автоматически)

**Конфигурация (`data/classify.yaml`):**
```yaml
llm_classify:
  enabled: true
  threshold: 0.4      # LLM вызывается если rule_confidence < 0.4
  batch_size: 5       # документов за одну итерацию
  categories:
    - urgent
    - important
    - meeting
    - finance
    - legal
    - travel
    - hr
    - project
    - it
    - info
  prompt: |
    Классифицируй письмо. Ответь ТОЛЬКО одним словом из списка:
    {categories}
    Тема: {subject}
    Письмо: {preview}
    Категория:
```

**API endpoints:**
| Endpoint | Описание |
|---|---|
| `POST /api/v1/classify/llm-batch` | Запуск пакетной LLM-классификации в фоне |
| `GET /api/v1/classify/stats` | Статистика: всего документов, AI-классифицировано, кэш |

**Как работает:**
1. `compute_rule_confidence(text)` → доля совпавших keyword-классификаторов (0..1)
2. Если `confidence < threshold` → вызывается MLX через `engine.ask()`
3. Результат сохраняется в `data/llm_classify_cache.json` по SHA-256 ключу
4. Документ получает теги `llm_category:<cat>` + `ai_classified`
5. В Inbox такие письма помечаются бейджем 🤖

**Frontend (Rules → Классификация):**
- Панель «🤖 ИИ-классификация» со статистикой (всего/AI/кэш)
- Кнопка «Запустить ИИ-классификацию» → POST к `/classify/llm-batch`

**Graceful degradation:**
- `engine=None` (MLX не загружен) → пропускает LLM, только кэш-хиты
- `enabled: false` в yaml → endpoint возвращает `status: disabled`

**Тесты:** 42 unit (`test_llm_classify_service.py`, LC01–LC42) + 18 E2E/integration (`TestClassifyLLMStage8`, `TestClassifyDocConfidence`)

---

### Tool Calling — реестр инструментов

Инструменты доступны модели во время генерации. Конфигурация — `tools/registry.json`.

**Формат реестра:**

```json
{
  "tools": [
    {
      "id": "date_calc",
      "enabled": true,
      "description": "...",
      "parameters": {
        "type": "object",
        "properties": {
          "expression": { "type": "string", "description": "..." }
        },
        "required": ["expression"]
      }
    }
  ]
}
```

> **Важно:** `tools/registry.json` должен существовать до старта сервера. Файл закоммичен в репозиторий. Если он отсутствует — validator возвращает пустой реестр и все tool calls проходят без валидации.

#### Встроенный инструмент `date_calc`

Преобразует любое упоминание даты в абсолютный формат YYYY-MM-DD (MSK).

Поддерживаемые выражения:

| Язык | Примеры |
|---|---|
| Русский | `сегодня`, `завтра`, `послезавтра`, `вчера`, `через 3 дня`, `через неделю`, `следующий понедельник`, `в пятницу`, `в конце недели` |
| Английский | `today`, `tomorrow`, `day after tomorrow`, `yesterday` |
| Абсолютные | `2026-12-31`, `16.05.2026`, `16.05.2026 10:00` |

Формат tool call в генерации:

```
<tool_call>{"name": "date_calc", "arguments": {"expression": "завтра"}}</tool_call>
```

или GigaChat-формат:

```
<|function_call|>{"name": "date_calc", "arguments": {"expression": "tomorrow"}}
```

#### Добавление нового инструмента

1. Создать `src/personal_assistant/mlx_server/tools/my_tool.py` с функцией `run(args: dict) -> str`
2. Добавить запись в `tools/registry.json`
3. Добавить диспетчеризацию в `tools/router.py` → `_run_builtin()`
4. Указать инструмент в системном промпте через `context_builder.py` → `load_tool_specs()`

---

## Синхронизация данных

```bash
# Все источники из PA_SYNC_SOURCES (одной командой)
uv run pa sync-all

# Отдельные источники через CLI
uv run pa sync-calendar --days-back 30 --days-forward 90
uv run pa sync-mail     --days-back 30
```

> Синхронизация **только читает** данные. Оригинальные файлы Calendar / Mail не изменяются.

### Формат vault-файлов

Каждый синхронизированный объект — `.md` файл с YAML frontmatter:

```yaml
---
message_id: "<abc123@corp.ru>"
thread_id: "e38582b9759b"          # MD5[:12] темы без Re:/Fwd:
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

# Отчёт за май

Тело письма в чистом Markdown...
```

### Вложения

При `PA_MAIL_FETCH_ATTACHMENTS=true` вложения сохраняются в `PA_MAIL_ATTACHMENTS_PATH/<message_id>/`.
Frontmatter письма содержит список `attachments` с относительными путями для последующего анализа.
Директория создаётся автоматически.

### Трекинг тредов

Письма с одинаковым нормализованным subject (без Re:/Fwd:/Отв:) автоматически группируются по `thread_id`. В vault-интерфейсе тред отображается как раскрывающийся список писем в хронологическом порядке.

---

## Сервисы

Высокоуровневые сервисы для работы с Mail и Calendar из чата и API.

### mail_service.py

```python
from personal_assistant.services.mail_service import (
    save_draft_reply,
    fetch_thread_messages,
    summarize_thread,
)

# Открыть черновик ответа в Apple Mail
save_draft_reply(
    subject="Re: Отчёт за май",
    body="Добрый день! Изучил отчёт...",
    to_recipients=["ivan@corp.ru"],
    reply_to_message_id="<abc123@corp.ru>",  # опционально, для In-Reply-To
)

# Получить все сообщения треда из vault
messages = fetch_thread_messages("e38582b9759b")
# → [{"path": ..., "title": ..., "date": ..., "sender": ..., "body_snippet": ...}, ...]

# Суммаризировать тред через MLX
result = summarize_thread("e38582b9759b", max_tokens=768)
# → {"thread_id": ..., "summary": ..., "message_count": ...}
```

### calendar_service.py

```python
from personal_assistant.services.calendar_service import (
    create_meeting_draft,
    create_event_draft,
    fetch_upcoming_events,
)
from datetime import datetime

# Создать встречу с приглашёнными в Calendar.app
create_meeting_draft(
    title="Обсуждение квартальных результатов",
    start_dt=datetime(2026, 6, 1, 10, 0),
    end_dt=datetime(2026, 6, 1, 11, 0),
    location="Переговорная А",
    notes="Повестка: ...",
    attendees=["ivan@corp.ru", "anna@corp.ru"],
)

# Создать личное событие (без приглашений)
create_event_draft(
    title="Ревью кода",
    start_dt=datetime(2026, 6, 2, 14, 0),
)

# Ближайшие события из vault
events = fetch_upcoming_events(days_forward=7)
# → [{"title": ..., "date": ..., "location": ..., "attendees": [...], "body_snippet": ...}, ...]
```

### API-эндпоинты

| Метод | Путь | Описание |
|---|---|---|
| POST | `/api/chat/mail/summarize-thread` | Суммаризировать тред `thread_id` |
| POST | `/api/chat/mail/thread-messages` | Получить сообщения треда из vault |
| POST | `/api/chat/calendar/create-meeting` | Создать встречу в Calendar.app |
| GET  | `/api/chat/calendar/upcoming` | Ближайшие события (параметр `days`) |

---

## CLI-команды

```
uv run pa --help          список всех команд
uv run pa check           проверка конфигурации и источников данных
uv run pa sync-all        синхронизация всех источников
uv run pa sync-calendar   только Apple Calendar
uv run pa sync-mail       только Apple Mail
uv run pa build-index     построить поисковый индекс
uv run pa serve           запустить FastAPI-сервер
uv run pa list-models     список рекомендованных MLX-моделей
uv run pa fix-model-config исправить int/float mismatch в config.json модели
```

---

## Тестирование

Гермети́чный гейт (unit + e2e + scenario-not-live) не требует Calendar, Mail, MLX
или сети — используются in-memory SQLite и временные директории. Live scenario
тесты — отдельным флагом, см. [TESTING.md](TESTING.md).

```bash
uv run pytest -m unit                          # юнит-тесты
uv run pytest -m e2e                           # FastAPI TestClient
uv run pytest -m "scenario and not live"       # моковые сценарии
uv run pytest -m "(unit or e2e) or (scenario and not live)"   # всё герметичное

# Live (нужен Mac + права + модель — см. TESTING.md):
PA_MLX_MODEL_PATH=/path/to/model uv run pytest -m "scenario and live and mlx"
uv run pytest -m "scenario and live and (mail or calendar)"

# С покрытием
uv run pytest -m unit --cov=src --cov-report=html

# Lint и типы
uv run ruff check src tests
uv run mypy src

# Конкретный модуль
uv run pytest tests/unit/sync/ -v
uv run pytest tests/unit/vault/ -v
uv run pytest tests/unit/mlx/ -v
```

### Покрытие по модулям

**Unit-тесты** (`tests/unit/`):

| Файл тестов | Что проверяет |
|---|---|
| `test_reader_protocol.py` | DataSourceReader Protocol |
| `test_vault_writer.py` | VaultWriter: события, письма, контакты, дедупликация, кириллица |
| `test_thread_grouping.py` | thread_id patch, chevron, группировка тредов |
| `test_dedup_engine.py` | DedupEngine: fingerprint, upgrade, fp-collision, dedup_all |
| `test_thread_tracker.py` | ThreadTracker: reply-цепочки, pre-set thread_id, рекуррентные серии |
| `test_tool_call_detection.py` | `<tool_call>` + GigaChat `<\|function_call\|>` brace-counter |
| `test_tool_prompts_e2e.py` | промпты черновика/суммаризации, лимит 8000, API-ответ |
| `test_tools_pipeline.py` | `date_calc` (RU + EN), `validator` registry + lazy reload, `router` dispatch + graceful degradation |
| `test_makesh.py` | Синтаксис make.sh, покрытие всех targets, stale-sentinel, subshell-паттерн, .gitignore completeness |

**E2E-тесты** (`tests/e2e/test_server_routes.py`):

| Класс | Что проверяет |
|---|---|
| `TestStatus` | `/health`, `/api/status`, MLX-статус |
| `TestChatV2` | `/api/chat/send`, `/api/chat/threads`, `/api/chat/history` |
| `TestVault` | `/vault/list` (section_counts, urgency_counts, total_all), `/vault/file`, `/vault/mentioned-in` |
| `TestInbox` | `/api/v1/inbox` фильтры, поля тегов, маппинг `_display_tags` (urgency/category → cls) |
| `TestInboxExtraction` | `/api/v1/inbox/{id}/extract` полная структура, entities, deadline ISO, force re-run, кэш |
| `TestExtractionUnit` | `_fallback_extract`, `_repair_json`, `_body_sha256`, cache hit/miss, `_parse_extraction_json` |
| `TestToday` | `/api/v1/today` структура ответа, форматы `updated_at`/`next_update`, shape событий/attention/suggestions, unit-тесты helpers |
| `TestSync` | `/sync/status`, `/sync` trigger |
| `TestSettings` | `/settings` GET/PATCH |
| `TestClassify` | `/classify/config`, `/classify/apply`, `/classify/reset-tags` |
| `TestSearch` | `/search` query, section-filter, tag-filter, `mode=hybrid` |
| `TestProjects` | `/api/v1/projects` CRUD, related, suggest-goal, assistant-suggests |
| `TestRules` | `/api/v1/rules/eisenhower`, `/api/v1/rules/gtd`, `/api/v1/rules/save` |
| `TestReports` | `/api/v1/reports` список, генерация, get, delete |
| `TestProfileAndConfig` | `/api/v1/profile`, `/api/v1/assistant-config` |
| `TestToolsAndPrompts` | `/tool-prompts` GET/POST, `/tools` список/toggle |
| `TestDailyBrief` | `/api/v1/brief/daily` HTTP-контракт, форматы полей, `?refresh=true`, `/generate` (202), monkeypatched vault |
| `TestGeneratedVaultE2E` | Daily Brief, draft-context, meeting-prep, calendar/upcoming с реальным test-vault (gen_vault fixture) |
| `TestCalendarIntentNLP` | `parse-intent`, `create-from-text` HTTP-контракт, time range, dry_run, reference_date, participants |
| `TestCalendarIntentParserUnit` | `_next_weekday`, `_build_iso`, `_parse_date`, AppleScript builder, `_esc_as`, `create_event(dry_run)` |

### Линтер и типизация

```bash
uv run ruff check src tests    # должно быть 0 ошибок
uv run ruff format src tests   # auto-fix
uv run mypy src                # типы (блокирующий гейт в CI)
```

---

## Структура проекта

```
pa-clean/
├── src/personal_assistant/
│   ├── config.py                      # настройки из .env (Pydantic Settings)
│   ├── models.py                      # MailMessage, CalendarEvent, Contact
│   ├── cli.py                         # Click CLI (pa sync-*, pa serve, pa check…)
│   │
│   ├── readers/                       # Читатели данных (AppleScript)
│   │   ├── applescript_base.py        # osascript-утилиты, compute_thread_id
│   │   ├── calendar_reader.py         # Apple Calendar → CalendarEvent
│   │   └── mail_reader.py             # Apple Mail → MailMessage
│   │
│   ├── sync/                          # Движок синхронизации
│   │   ├── dedup_engine.py            # дедупликация по hash(source:id:subject:date)
│   │   └── thread_tracker.py          # группировка писем в треды, patch thread_id
│   │
│   ├── vault/
│   │   └── writer.py                  # VaultWriter — запись .md с frontmatter
│   │
│   ├── mlx_server/                    # MLX-инференс + FastAPI
│   │   ├── server.py                  # FastAPI app, startup, VaultIndex
│   │   ├── engine.py                  # MLXEngine: load, stream, generate
│   │   ├── chat_routes.py             # /api/chat/* (send, threads, history, draft, mail, calendar)
│   │   ├── chat_db.py                 # SQLite для истории чата (треды/сообщения)
│   │   ├── context_builder.py         # сборка system prompt из vault + профиля
│   │   ├── vault_index.py             # BM25-индекс .md-файлов vault
│   │   ├── vector_index.py            # Semantic (sentence-transformers) индекс
│   │   ├── scheduler.py               # APScheduler, cron-синхронизация
│   │   ├── tools/                     # Tool-calling инфраструктура
│   │   │   ├── date_calc.py           # date_calc tool
│   │   │   ├── executor.py            # выполнение tool calls
│   │   │   ├── router.py              # маршрутизация по имени инструмента
│   │   │   └── validator.py           # валидация аргументов
│   │   └── tasks/                     # Режимы чата
│   │       ├── classify.py            # классификация поручений (YAML-правила)
│   │       ├── draft_reply.py         # генерация черновика письма
│   │       ├── search.py              # поиск по vault
│   │       └── summarize.py           # суммаризация переписки/встреч
│   │
│   ├── webui/
│   │   └── routes.py                  # FastAPI-роуты WebUI (/vault/*, /search, …)
│   │
│   ├── profile/                       # Профиль пользователя + конфиг ассистента
│   │   ├── models.py                  # UserProfile, AIAssistantConfig
│   │   ├── service.py                 # load/save profile & config (JSON)
│   │   ├── routes.py                  # /api/v1/profile, /api/v1/assistant-config
│   │   └── context_assembler.py       # ProfileAwareAssembler: профиль + vault
│   │
│   ├── personal_vault/                # PersonalVault SQLite (задачи, встречи вручную)
│   │   ├── db.py                      # CRUD: items, threads
│   │   ├── models.py                  # VaultItem, Thread
│   │   ├── context.py                 # build_context для AI
│   │   └── routes.py                  # /api/v1/vault/*
│   │
│   ├── services/                      # Высокоуровневые сервисы
│   │   ├── mail_service.py            # save_draft_reply, fetch_thread_messages, summarize_thread
│   │   ├── calendar_service.py        # create_meeting_draft, create_event_draft, fetch_upcoming_events
│   │   ├── tool_prompts.py            # кастомные системные промпты (draft/summarize)
│   │   ├── rule_engine.py             # движок структурированных правил
│   │   ├── tag_history_service.py     # история тегов vault
│   │   ├── vault_filter_service.py    # фильтрация документов vault
│   │   └── report_service.py          # генерация отчётов
│   │
│   ├── reports/                       # Отчёты
│   │   ├── generator.py               # генератор отчётов по vault
│   │   ├── routes.py                  # /api/v1/reports/*
│   │   └── store.py                   # хранение сгенерированных отчётов
│   │
│   ├── utils/
│   │   ├── timezone.py                # MSK timezone helpers
│   │   └── name_extractor.py          # извлечение ФИО из переписки
│   │
│   └── templates/
│       └── mail.md.j2                 # Jinja2-шаблон для .md писем
│
├── webui/                             # Vanilla JS + SCSS (без фреймворков)
│   ├── index.html                     # точка входа (сервер отдаёт напрямую)
│   ├── frontend/
│   │   ├── js/                        # app.js, api.js, chat.js, vault.js,
│   │   │                              # inbox.js, search.js, projects.js,
│   │   │                              # rules.js, settings.js, reports.js, …
│   │   └── styles/
│   │       ├── main.scss              # корневой импорт всех компонентов
│   │       ├── variables.scss         # цветовые токены, шрифты, отступы
│   │       └── components/            # _inbox.scss, _vault.scss, _rules.scss, …
│   ├── dist/                          # Собранные файлы (коммитятся в репо)
│   │   ├── css/main.css               # скомпилированный SCSS
│   │   ├── js/                        # скопированные JS-модули
│   │   └── index.html                 # копия webui/index.html
│   ├── scripts/bundle-js.js           # Node-скрипт: копирует JS + index.html в dist/
│   └── package.json                   # sass (devDep только), npm run build/watch
│
├── tests/
│   ├── conftest.py                    # общие фикстуры
│   ├── unit/
│   │   ├── readers/                   # тесты reader protocol
│   │   ├── sync/                      # dedup_engine, thread_tracker
│   │   ├── vault/                     # vault_writer, thread_grouping
│   │   └── mlx/                       # tool_call_detection, tool_prompts, tools_pipeline
│   └── e2e/                           # сквозные сценарии через FastAPI TestClient
│
├── data/
│   ├── classify.yaml                  # правила классификации (urgency/category/action)
│   ├── persona.json                   # имя/стиль ассистента
│   ├── gtd_rules.json                 # GTD/Eisenhower правила
│   └── eisenhower.json
│
├── tools/
│   └── registry.json                  # реестр доступных инструментов
│
├── .env.example                       # шаблон конфигурации
├── pyproject.toml                     # зависимости, ruff, mypy, pytest
├── uv.lock                            # запинённые версии (darwin/arm64)
└── .github/workflows/ci.yml           # CI — workflow_dispatch (manual only)
```

---

## Устранение неисправностей

### `uv: command not found`
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.cargo/env"
```

### `No solution found` при установке `mlx-lm`

**Причина 1 — Python под Rosetta** (hint: `macosx_*_x86_64` при M-чипе):
```bash
rm -rf .venv && uv sync && (cd webui && npm install && npm run build)
```

**Причина 2 — Python 3.14+** (hint: `cp314`):
```bash
rm -rf .venv && uv venv --python 3.13 && uv sync && (cd webui && npm install && npm run build)
```

**Причина 3 — Intel Mac** (hint: `x86_64`):  
MLX работает только на Apple Silicon. Сервер запустится, чат вернёт `503`.

Диагностика:
```bash
uname -m                              # arm64 = M-chip
python3 -c "import platform; print(platform.machine())"  # arm64 = нативный
uv run pa check                       # полная диагностика
```

### MLX недоступен (`503 MLX not available`)

Проверьте: Apple Silicon, Python ≤ 3.13, корректный `PA_MLX_MODEL_PATH` в `.env`.

Рекомендованные модели (~4 ГБ, 4-bit):
```bash
# Mistral 7B (универсальный)
uv run huggingface-cli download mlx-community/Mistral-7B-Instruct-v0.3-4bit \
  --local-dir ~/models/mistral

# GigaChat (лучше для русского языка)
uv run huggingface-cli download ai-sage/GigaChat-20B-A3B-instruct-4bit \
  --local-dir ~/models/gigachat
```

### Apple Mail: нет доступа к письмам

**Системные настройки → Конфиденциальность и безопасность → Автоматизация** →
разрешить терминальному приложению управлять Mail.app.

Проверить разрешения:
```bash
uv run pa check   # выведет статус AppleScript-доступа к Mail и Calendar
```

### AppleScript: `osascript not permitted`

**Системные настройки → Конфиденциальность и безопасность → Автоматизация** →
разрешить терминальному приложению управлять Calendar.app и Mail.app.

### Черновик в Mail открывается как новое письмо

Убедитесь, что открываете черновик из вкладки **Vault → тред письма** (кнопка «Черновик ответа»), а не из чата напрямую. Vault передаёт `message_id` исходного письма — только так Mail откроет compose-окно с `In-Reply-To`.

**Техническая деталь:** Mail.app игнорирует `content:bodyContent` внутри `with properties {}`. Тело письма задаётся отдельной командой `set content of newMsg to bodyContent` после `make new outgoing message`. Это поведение задокументировано в `_build_save_draft_mail_script` в `chat_routes.py`.

**Режимы:**
- `save_to_drafts=true` — письмо сохраняется в Drafts без открытия окна (`save newMsg`), Mail.app не выходит на первый план.
- `save_to_drafts=false` — Mail.app `activate`, окно открывается через `open newMsg`.

### ImportError при запуске тестов

```bash
uv sync --group dev
uv pip install -e . --no-deps
uv run pytest tests/unit/ --tb=short
```

### WebUI не обновляется

```bash
(cd webui && npm run build)    # пересобрать один раз
(cd webui && npm run watch)    # следить за изменениями SCSS (если script определён)
```

---

## Внутренние отчёты и планы

Внутренняя документация — в папке [`docs/`](docs/):

| Файл | Содержание |
|---|---|
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | Руководство пользователя — все вкладки UI, настройки, сценарии |
| [`docs/FUNCTIONALITY_MAP.md`](docs/FUNCTIONALITY_MAP.md) | Карта функционала и реестр проблем (Фаза 2 аудита) |
| [`docs/SCENARIO_TEST_PLAN.md`](docs/SCENARIO_TEST_PLAN.md) | План сценарных тестов |
| [`docs/AUDIT_PLAN.md`](docs/AUDIT_PLAN.md) | План аудита проекта (фазы 1–7) |
| [`docs/MIGRATION_PLAN.md`](docs/MIGRATION_PLAN.md) | План миграции pa-merge → pa-clean (Фаза 6) |
| [`docs/ai-email-calendar-research.md`](docs/ai-email-calendar-research.md) | Исследование AI-обработки почты и календаря |
