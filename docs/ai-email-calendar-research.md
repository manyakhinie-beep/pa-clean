# Умная обработка почты и календаря: исследование и план реализации для pa-merge

> Дата: май 2026  
> Автор: Claude (research synthesis)  
> Версия проекта: pa-merge (MLX + FastAPI + Vault)

---

## 1. Состояние рынка и технологий

### 1.1 Рыночный контекст

Рынок AI Email Assistants: **$880 млн (2025)** → **$2.38 млрд (2035)**, CAGR 10.4%.  
Ключевые игроки: Lindy, alfred_, Proton Scribe, EmailTree.ai, NewMail AI, Missive.  
Главный тренд 2025–2026: движение к **локальной обработке** (privacy-first, offline) — Proton Scribe стал первым коммерческим продуктом с on-device моделью.

### 1.2 MLX + Apple Silicon — 2026

| Чип | Модель | Скорость (tok/s) | RAM |
|-----|--------|-----------------|-----|
| M2 16GB | Mistral 7B Q4 | ~40 | 4.8 GB |
| M3 Pro | Qwen2.5 7B INT8 | ~54 | 8.1 GB |
| M4 Pro | Qwen3-30B MoE | ~130 | 18 GB |
| M5 Max | Qwen3.6-35B | ~55 | 22 GB |

**Ключевой факт (март 2026):** Ollama v0.19 перешёл на MLX backend — **1.6x** быстрее prompt processing, **2x** быстрее generation. MLX стал де-факто стандартом для Apple Silicon.

Поддерживаемые модели через `mlx-community` (4255 моделей):  
Qwen2.5/3/3.5, Mistral, Llama 3.x, Gemma 2, Phi-4, DeepSeek, SmolLM.

### 1.3 Что умеют современные AI Email/Calendar ассистенты

**Обработка почты:**
- Intent classification (просьба, уведомление, вопрос, дедлайн, FYI)
- Priority scoring (0–100) на основе отправителя, ключевых слов, паттернов поведения
- Thread summarization с выделением ключевых решений
- Action items extraction → автоматическое создание задач
- Draft generation в тоне пользователя
- Follow-up detection (письмо требует ответа, но ответа нет N дней)
- Sentiment + tone analysis
- Entity extraction (люди, компании, суммы, даты)

**Обработка календаря:**
- Natural language scheduling: "Meet next Thursday morning" → slot creation
- Meeting prep: автосбор связанных писем перед встречей
- Conflict detection и smart rescheduling
- Participant analysis (кто важен, кто часто опаздывает)
- Meeting summary + action items из записей/transcript
- Series detection (повторяющиеся встречи = отдельный тред)

**Structured output (JSON) — стандарт 2026:**  
LLM с constrained decoding возвращают надёжный JSON без regex.  
Промпт-паттерн: schema + 1 example + strict rules + validation instruction.

---

## 2. Анализ текущего состояния pa-merge

### 2.1 Уже реализовано

| Компонент | Статус | Ограничения |
|-----------|--------|-------------|
| Rule-based классификация (urgency/category) | ✅ Готово | Только ключевые слова |
| MLX summarization (TL;DR письма) | ✅ Готово | Однострочный промпт, без JSON |
| Thread tracking (цепочки писем) | ✅ Готово | Группировка, не анализ |
| Deadline extraction (regex) | ✅ Готово | Числовые форматы только |
| RAG поиск (BM25 + LanceDB) | ✅ Готово | Нет семантич. переранжирования |
| Inbox state (read/tags/project) | ✅ Готово | Без ML-приоритизации |
| Draft generation (через чат) | ✅ Готово | Thread-aware с Stage 4 |
| Today dashboard | ✅ Готово | Rule-based, нет AI scoring |
| Vault structure | ✅ Готово | Нет авто-линкинга |
| Structured extraction (Stage 1) | ✅ Готово | MLX + fallback, JSON mode |
| AI Priority Score (Stage 2) | ✅ Готово | Rule-based + MLX boost |
| Follow-up Detection (Stage 2/3) | ✅ Готово | Vault scan + threshold_days |

### 2.2 Отсутствуют — высокий приоритет

- **Smart meeting prep** (сбор контекста перед встречей из vault)
- **Thread-aware draft** (полный контекст треда в промпт при Draft ответа)
- **Calendar intent** ("встреча с Ивановым в четверг" → AppleScript)
- **Daily/Weekly brief** (утренний брифинг с LLM-синтезом)
- **Semantic triage** (LLM-assisted urgency для нестандартных писем)
- **Thread-aware draft** (черновик с полным контекстом треда)

---

## 3. Реализуемые фичи через MLX (локально, оффлайн)

### Матрица реализуемости

| Фича | Сложность | Требует MLX | Rule-based fallback | Приоритет |
|------|-----------|-------------|---------------------|-----------|
| Structured email extraction (JSON) | Средняя | Опционально | Regex | 🔴 Высокий |
| AI Priority Score | Средняя | Да | Keyword heuristics | 🔴 Высокий |
| Thread-aware draft | Низкая | Да | — | 🔴 Высокий |
| Follow-up detection | Низкая | Нет | Rule: > 3 дней | 🔴 Высокий |
| Daily brief | Средняя | Да | Extractive fallback | 🟡 Средний |
| Meeting prep | Средняя | Да | RAG поиск | 🟡 Средний |
| Calendar intent NLP | Высокая | Да | Regex | 🟡 Средний |
| LLM-classify (semantic) | Низкая | Да | — | 🟡 Средний |
| Smart re-ranking (RAG) | Высокая | Да | BM25 | 🟢 Низкий |
| LoRA fine-tuning на личной переписке | Очень высокая | Да | — | 🟢 Низкий |

---

## 4. Детальный план реализации

### Этап 1: Structured Extraction Engine (2–3 дня)

**Цель:** MLX возвращает структурированный JSON из письма, а не plain text.

**Что реализовать:**

```
src/personal_assistant/mlx_server/tasks/extract.py
```

```python
# POST /api/v1/inbox/{id}/extract  
# Возвращает:
{
  "action_items": [
    {"text": "Отправить отчёт до пятницы", "deadline": "2026-05-29", "assignee": "me"},
    {"text": "Согласовать бюджет с Ивановым", "deadline": null, "assignee": "me"}
  ],
  "entities": {
    "people": ["Иван Петров", "Анна Сидорова"],
    "organizations": ["ООО Ромашка"],
    "amounts": ["150 000 руб"],
    "dates": ["29 мая", "следующий вторник"]
  },
  "intent": "request",          # request | info | question | deadline | meeting | fyi
  "tone": "formal",             # formal | informal | urgent | neutral
  "reply_required": true,
  "deadline": "2026-05-29",
  "summary_one_line": "Просьба отправить финансовый отчёт до пятницы"
}
```

**Промпт (4-layer structured output):**
```
Тебе дано письмо. Верни ТОЛЬКО валидный JSON по схеме.
СХЕМА: { action_items: [...], entities: {...}, intent: str, tone: str, 
         reply_required: bool, deadline: str|null, summary_one_line: str }
ПРИМЕР: { "action_items": [{"text": "...", "deadline": "2026-05-28"}], ... }
ПРАВИЛА: deadline в ISO формате или null. intent ∈ {request|info|question|deadline|meeting|fyi}.
ВАЛИДАЦИЯ: Проверь JSON на синтаксис перед возвратом.
ПИСЬМО:
{body}
```

**Fallback:** Если MLX недоступен — использовать regex из существующего `classify.py`.

**Интеграция:**
- Вызывать при синхронизации (background) и сохранять в `data/inbox_state.json`
- `_doc_to_item` включает `action_items`, `intent`, `reply_required`
- WebUI: иконка 📋 при наличии action items, 💬 при `reply_required=true`

---

### Этап 2: AI Priority Score ✅ Реализовано

**Цель:** Каждое письмо получает числовой приоритет 0–100, visible в inbox list.

**Алгоритм (гибридный, без обязательного MLX):**

```python
def compute_priority(item: dict, contact_graph: dict) -> int:
    score = 0
    
    # Urgency tags (из classify.py)
    if is_urgent(item):    score += 40
    elif is_important(item): score += 20
    
    # Reply required
    if item.get("reply_required"): score += 15
    
    # Sender importance (из contact_graph — частота переписки)
    sender_freq = contact_graph.get(item["sender_email"], {}).get("freq", 0)
    score += min(15, sender_freq * 3)
    
    # Deadline proximity
    deadline = item.get("deadline")
    if deadline:
        days_left = (parse_date(deadline) - today()).days
        if days_left <= 1:  score += 25
        elif days_left <= 3: score += 15
        elif days_left <= 7: score += 10
    
    # Recency (старые письма теряют приоритет)
    age_days = (today() - parse_date(item["date"])).days
    score -= min(20, age_days * 2)
    
    # Unread bonus
    if not item.get("read"): score += 5
    
    return max(0, min(100, score))
```

**MLX-усиление (опционально):** Для писем со score 30–60 (граница) — быстрый LLM pass:
```
Оцени важность письма от 0 до 10. Только число.
Письмо: {summary_one_line}
```

**UI:** Цветная полоска слева от ib-item (0–33 серый, 34–66 жёлтый, 67–100 красный).

---

### Этап 3: Follow-up Detection ✅ Реализовано

**Цель:** Автоматически выявлять письма, ожидающие ответа.

**Логика:**
```python
def detect_followup_needed(items: list[InboxItem]) -> list[str]:
    """Вернуть item_id писем, на которые нужен ответ."""
    result = []
    for item in items:
        # Письмо пришло мне, reply_required=True, нет исходящего ответа в треде
        if (item.intent == "request" or item.reply_required) \
           and not item.read \
           and age_days(item.date) > 2 \
           and not has_outgoing_in_thread(item.thread_id):
            result.append(item.id)
    return result
```

**Backend:** `GET /api/v1/inbox/followup-needed` → список item_id  
**UI:** Значок 🔔 в inbox list + секция «Ожидают ответа» в Today dashboard  
**Badge:** В nav-badge-inbox считать followup как отдельный unread тип

---

### Этап 4: Thread-Aware Draft ✅ Реализовано

**Цель:** При нажатии "Draft ответа" — в промпт передаётся ВЕСЬ тред, а не только тема.

**Реализация:**

`src/personal_assistant/services/draft_context_service.py` — ключевой сервис:
- `build_draft_context(item_id, vault_path, my_email, mlx_engine) → dict`
- Сканирует vault по `thread_id` из frontmatter → собирает все сообщения треда
- Определяет `is_mine` по `my_email` → выделяет `my_previous_replies`
- Извлекает `key_facts` regex-паттернами (дедлайн, срок, сумма, прошу, etc.)
- Строит `context_prompt` с разделителями ═══ для LLM
- Graceful fallback: работает без VaultIndex, без MLX, без vault

**Endpoint:**
```
POST /api/v1/inbox/{item_id}/draft-context
→ {
    item_id, subject, sender, sender_email, thread_id,
    thread_messages,       # хронологический список сообщений треда
    thread_summary,        # правило-/MLX-сводка переписки
    key_facts,             # ["Дедлайн: 25 мая", "Сумма: 50 000 руб."]
    my_previous_replies,   # мои ответы из истории треда
    draft_hint,            # "Тред: 3 письма · 2 факта"
    context_prompt,        # готовый промпт для /api/chat/send
    message_count
  }
```

**context_builder.py** — добавлен `vault_thread_id` в `build()`:
- `_load_vault_thread_context(thread_id)` сканирует VaultIndex
- Инжектирует блок `--- ИСТОРИЯ ТРЕДА ---` в system prompt перед vault-refs

**Frontend UX (`inbox.js` + `chat.js`):**
- Кнопка «Draft ответа» показывает ⏳ и вызывает `/draft-context`
- Fallback → открывает чат с bare subject при ошибке
- Чип 🧵 в chat header с кнопками `?` (drawer) и `×` (убрать контекст)
- Thread drawer: хронологические сообщения (входящие — indigo, исходящие — green)
- Секция фактов (жёлтый блок)

**Фиксы, обнаруженные при реализации:**
- `_parse_frontmatter`: добавлен regex-fallback при YAML-ошибке (напр. `subject: Re: ...`)
- `_find_doc_in_vault_by_id`: filesystem-fallback когда VaultIndex не загружен
- Порядок роутов FastAPI: `/draft-context` объявлен до `/{item_id}`
- Sort key bug в `GET /inbox`: заменён `_date_ts()` helper вместо unary `-` на str

**В inbox.js:** `_openDraftWithContext(item)` загружает контекст, передаёт `thread_context` в `pa:chat-open` event.

---

### Этап 5: Smart Meeting Prep ✅ Реализовано

**Цель:** За 30 минут до встречи — автоматически собрать контекст из vault.

#### Реализованные компоненты

**Backend:**
- `src/personal_assistant/services/meeting_prep_service.py` — сервис сборки контекста (~400 строк)
- `src/personal_assistant/calendar/routes.py` — два endpoint:
  - `GET /api/v1/calendar/upcoming?days=7` — список предстоящих событий из vault
  - `GET /api/v1/calendar/{event_id}/prep` — полный контекст подготовки к встрече

**Формат ответа `/prep`:**
```json
{
  "event_id": "evt_001",
  "title": "Quarterly Review",
  "participants": ["Alice <alice@corp.com>"],
  "participant_emails": ["alice@corp.com"],
  "event_date": "2026-06-01T14:00:00+03:00",
  "location": "Room 101",
  "recent_emails": [...],
  "related_projects": [...],
  "previous_meetings": [...],
  "open_action_items": [...],
  "prep_brief": "Брифинг с рекомендациями (rule-based или MLX)",
  "context_prompt": "Готовый промпт для /api/chat/send",
  "event_found": true,
  "message_count": 5
}
```

**Алгоритм `build_meeting_prep`:**
1. Сканирует `vault/calendar/**/*.md` по `id` фронтматтера (или имени файла)
2. Извлекает участников из полей `attendees` / `participants` / `contacts` / `invitees`
3. Ищет письма от участников за последние 7 дней (`vault/mail/**/*.md`)
4. Ищет проекты, упоминающие участников (`vault/projects/**/*.md`)
5. Ищет предыдущие встречи с теми же людьми (`vault/calendar/**/*.md`)
6. Извлекает открытые поручения через regex (прошу/необходимо/нужно/need to/…)
7. Строит брифинг: rule-based если MLX недоступен, MLX-generated если доступен
8. Собирает `context_prompt` с разделителями `═══` для `/api/chat/send`

**Graceful degradation:**
- Работает без vault (возвращает минимальный valid dict)
- Работает без MLX (rule-based brief с полной структурой)
- При ошибке поиска события — `event_found: false`, `title: "Без названия"`

**Frontend:**
- `webui/frontend/js/today.js` — `loadUpcomingMeetings()`, `renderUpcomingMeetings()`, `_openMeetingPrep()`
- `webui/frontend/js/api.js` — `calendarUpcoming(days)`, `calendarPrep(eventId)`
- `webui/index.html` — секция `#today-meetings-section` с `.today__meeting-card`
- `webui/frontend/styles/components/_today.scss` — стили встреч (`.today__meeting-prep-btn`, etc.)

**UX flow:**
1. Вкладка «Сегодня» → секция «Предстоящие встречи» — карточки с временем и участниками
2. Нажатие «Подготовиться» → кнопка блокируется, показывает ⏳
3. GET `/api/v1/calendar/{id}/prep` → переход во вкладку «Чат»
4. Чат открывается с `context_prompt` (брифинг + письма + поручения) предзаполненным
5. При ошибке — graceful fallback: «Помоги подготовиться к встрече `{title}`»

**Тесты:**
- `tests/unit/services/test_meeting_prep.py` — 29 unit-тестов (MP01–MP29)
- `tests/e2e/test_server_routes.py::TestMeetingPrep` — 15 E2E-тестов HTTP-контракта
- `tests/e2e/test_server_routes.py::TestMeetingPrepUnit` — 5 интеграционных unit-тестов

**MLX Prompt (если движок доступен):**
```
Встреча: {title} с {participants}.
Дата: {event_date}

Недавние письма от участников:
  [2026-05-28] alice@corp.com: Q2 numbers
  ...

Открытые поручения:
  - Подготовить финансовый отчёт
  ...

Составь краткий брифинг подготовки к встрече (3–5 пунктов).
```

---

### Этап 6: Daily Brief ✅ Реализовано

**Цель:** Утренний AI-сводка дня: встречи + важные письма + задачи.

**Endpoints:**
- `GET /api/v1/brief/daily?refresh=false` — получить (или перестроить) Daily Brief
- `POST /api/v1/brief/daily/generate` — фоновая генерация (BackgroundTasks)

**Расписание:** APScheduler CronTrigger `hour=5, minute=0, tz=UTC` = 08:00 МСК  
(при `PA_SCHEDULE_ENABLED=true`)

**Алгоритм:**
1. `_build_calendar_section` — события дня из `vault/calendar/` по ISO-дате, сортировка по времени, флаг `is_soon` (≤ 30 мин)
2. `_build_inbox_section` — письма с `urgency:critical/high`, `deadline:today` или `reply_required` за 7 дней
3. `_build_tasks_section` — regex-извлечение задач из mail + threads
4. `_rule_based_insight` — всегда работает (без LLM); `_mlx_insight` — если движок доступен, иначе graceful fallback
5. `_build_bullets` — топ-3 приоритета: `is_soon` → `deadline:today` → другие срочные
6. Кеш 30 минут: `vault/daily/{date}_brief_cache.json` (mtime-based TTL)

**WebUI:** Daily Brief отображается в верхней части вкладки «Сегодня»:
- Цветные чипсы (📅 встречи, 🔴 срочные, ✅ задачи)
- Кнопка ↻ обновить; кнопка 💬 «Спросить ассистента о дне»
- Русские склонения: «2 встречи», «5 встреч», «1 встреча»
- Скрыт, если vault не загружен

**Тесты:** 34 unit-теста (`tests/unit/services/test_daily_brief.py`, DB01–DB29, VG01–VG05) + 25 E2E (`tests/e2e/test_server_routes.py`, TestDailyBrief + TestGeneratedVaultE2E)

**Тест-vault:** `scripts/generate_test_vault.py` — реалистичный vault с 2 тредами, 3 встречами, срочными письмами

```sh
python scripts/generate_test_vault.py --vault /tmp/test-vault --email igor@example.com
```

```python
{
  "generated_at": "2026-05-25T08:00:00",
  "greeting": "Доброе утро, Игорь! Насыщенный день.",
  "sections": [
    {
      "title": "Сегодня в календаре",
      "items": [{"time": "10:00", "title": "Стендап", "prep_ready": true}]
    },
    {
      "title": "Требуют ответа сегодня",
      "items": [{"subject": "...", "sender": "...", "deadline": "сегодня"}]
    },
    {
      "title": "Ключевые задачи",
      "items": ["Подписать договор", "Отправить отчёт"]
    }
  ],
  "ai_insight": "У вас 3 встречи с командой и 2 срочных письма от Петрова — планируйте первые 2 часа на переписку."
}
```

**Today.js** отображает Daily Brief в верхней части при наличии.

---

### Этап 7: Calendar Intent NLP ✅ Реализовано

**Цель:** Natural language → AppleScript создание события.

**Примеры:**
```
"Встреча с Ивановым в следующий четверг в 15:00"
"Созвон по проекту во вторник утром на час"
"Блокировать время для отчёта в пятницу 14-16"
```

**Pipeline:**
```
NLP (MLX) → структурированный EventDraft → AppleScript → Calendar.app
```

```python
# MLX extraction:
{
  "title": "Встреча с Ивановым",
  "date": "2026-05-28",   # relative → absolute
  "time": "15:00",
  "duration_minutes": 60,
  "participants": ["Иванов"],
  "location": null,
  "calendar": "Work"
}
```

**Endpoints:**
- `POST /api/v1/calendar/parse-intent` — разбор текста → EventDraft (без создания)
- `POST /api/v1/calendar/create-from-text` — разбор + создание (два шага: preview → `confirmed=true`)
- `GET  /api/v1/calendar/calendars` — список записываемых календарей Calendar.app

**Двухшаговый UX:**
1. Ввод текста → `parse-intent` → показ превью-карточки (дата, время, участники, место)
2. Пользователь нажимает ✓ → `create-from-text?confirmed=true` → AppleScript → Calendar.app
3. `dry_run=True` — безопасен для CI: строит AppleScript, но не запускает

**UI-точки входа:**
- Вкладка «Сегодня»: поле «Новая встреча» + кнопка «+» → preview-карточка inline
- Чат: `/встреча` → ввод описания → `pa:create-event` → Today tab с превью
- Чат: `/событие` — аналогично

**Парсер (полностью оффлайн, без MLX):**
- Даты: `сегодня`, `завтра`, `послезавтра`, `в пятницу`, `во вторник`, `следующий четверг`, `через 3 дня`, `через неделю`, `25 мая`
- Время: `15:00`, `14-16` (→ 14:00 + 120 мин), `утром` (09:00), `в обед` (13:00), `вечером` (18:00), `в три часа` (15:00)
- Длительность: `на час`, `на полчаса`, `на полтора часа`, `на 30 минут`, `14-16` (range)
- Место: `в Zoom`, `в переговорной А-201`, `онлайн`, `в офисе`
- Участники: `с Ивановым`, `с Козловым и Петровым`, `с командой`
- MLX fallback для сложных фраз (если confidence < 0.7)

**AppleScript (calendar_writer.py):** устанавливает год/месяц/день/часы численно — без locale-зависимого date-парсинга.

**Тесты:** 50 unit (`tests/unit/calendar/test_intent_parser.py`, IP01–IP43) + 25 E2E (`tests/e2e/test_server_routes.py`, TestCalendarIntentNLP CI-E2E-01..15 + TestCalendarIntentParserUnit CI-U-01..10)

---

### ✅ Этап 8: LLM-assisted Semantic Classification (РЕАЛИЗОВАНО)

**Цель:** Для писем, которые rule-based классификация не распознала, использовать MLX.

**Реализовано:**

- `src/personal_assistant/mlx_server/tasks/llm_classify_service.py` — полный сервис:
  - `LLMClassifyCache` — SHA-256-кэш в `data/llm_classify_cache.json`
  - `compute_rule_confidence(text, classifiers_cfg) → float` — доля совпавших классификаторов
  - `needs_llm_classification(text, cfg, threshold) → bool`
  - `llm_classify_single(subject, preview, config, engine) → LLMClassifyResult`
  - `batch_llm_classify_vault(vault_path, engine, config, ...)` — пакетная классификация
  - `get_classify_stats(vault_path) → dict` — статистика по vault
- `data/classify.yaml` — добавлена секция `llm_classify` с `enabled: true`, `threshold: 0.4`, `batch_size: 5`, 10 категориями и русским промптом
- `classify.py` — интеграция: `rule_confidence`, `llm_assisted`, `llm_category` поля в `ClassifyResult`; LLM вызывается только когда `rule_confidence < threshold`
- **API endpoints:**
  - `POST /api/v1/classify/llm-batch` — запуск в фоне (BackgroundTasks)
  - `GET /api/v1/classify/stats` — статистика AI-классификации и кэша
- **Frontend:**
  - Кнопка «Запустить ИИ-классификацию» в Rules tab (Классификация → Stage 8 panel)
  - Статистика: документов, AI-классифицировано, записей в кэше
  - 🤖 badge на письмах с тегом `ai_classified` в Inbox
- **Тесты:** 42 unit-теста (LC01–LC42) + 18 E2E-тестов (LC-E2E-01..10, LC-U-01..08)

```yaml
llm_classify:
  enabled: true
  threshold: 0.4   # только если rule-confidence < 40%
  batch_size: 5    # обрабатывать пакетами в фоне
  categories: [urgent, important, meeting, finance, legal, travel, hr, project, it, info]
  prompt: |
    Классифицируй письмо. Ответь ТОЛЬКО одним словом из списка:
    {categories}
    Тема: {subject}
    Письмо: {preview}
    Категория:
```

**Важно:** Запускается только в фоне (BackgroundTasks), не блокирует основной поток.  
Кэш по SHA-256 гарантирует повторное использование без новых LLM-вызовов.

---

## 5. Рекомендуемые модели для pa-merge

### Основная (already supported): Qwen2.5 7B Instruct (MLX INT4)
- ~50 tok/s на M3 Pro
- Отлично справляется с русским языком
- Подходит для: summarization, classification, draft generation

### Для structured extraction: Qwen3 4B (MLX INT4)
- ~100+ tok/s на M3
- Structured output + tool calling
- Маленькая модель = быстрый JSON extraction

### Для тяжёлого анализа: Qwen3-30B-A3B (MoE)
- ~130 tok/s на M4 Pro 64GB
- Deep analysis, meeting prep, daily brief

### SmolLM 1.7B (опционально, fallback)
- ~300+ tok/s
- Только для приоритизации, не для генерации

```yaml
# .env.example — новые переменные
PA_EXTRACT_MODEL=mlx-community/Qwen2.5-7B-Instruct-4bit
PA_EXTRACT_MAX_TOKENS=512
PA_PRIORITY_LLM_ENABLED=true
PA_FOLLOWUP_DAYS_THRESHOLD=2
PA_BRIEF_SCHEDULE=08:00
```

---

## 6. Архитектурные решения

### 6.1 Async Processing Pipeline

```
Sync → parse docs → background tasks:
  [1] rule-based classify (sync, fast)
  [2] regex deadline extraction (sync)
  [3] MLX structured extraction (async, batched)
  [4] priority scoring (sync after extraction)
  [5] followup detection (sync)
→ write to inbox_state.json + vault frontmatter
```

**Реализация:** `APScheduler` + `asyncio.Queue` для MLX очереди.

### 6.2 Кэш-стратегия

```python
# Каждый item получает версию на основе sha256 тела
# MLX extraction кэшируется по sha256 → не пересчитывать при повторных синках
class ExtractionCache:
    _path = "data/extraction_cache.json"
    # {sha256: {action_items, intent, deadline, ...}}
```

### 6.3 Graceful degradation

```
MLX доступен → полный AI pipeline
MLX недоступен → rule-based fallback для всех фич
Intel Mac → явная ошибка в /status с подсказкой
```

---

## 7. Оценка трудозатрат

| Этап | Трудоёмкость | Ценность | Рекомендация |
|------|-------------|---------|-------------|
| 1. Structured Extraction | 2–3 дня | ⭐⭐⭐⭐⭐ | ✅ Реализовано |
| 2. Priority Score | 1–2 дня | ⭐⭐⭐⭐⭐ | ✅ Реализовано |
| 3. Follow-up Detection | 1 день | ⭐⭐⭐⭐ | ✅ Реализовано |
| 4. Thread-aware Draft | 1–2 дня | ⭐⭐⭐⭐ | ✅ Реализовано |
| 5. Meeting Prep | 2–3 дня | ⭐⭐⭐⭐ | ✅ Реализовано |
| 6. Daily Brief | 2 дня | ⭐⭐⭐⭐ | ✅ Реализовано |
| 7. Calendar Intent NLP | 3–4 дня | ⭐⭐⭐ | ✅ Реализовано |
| 8. Semantic Classify | 1 день | ⭐⭐⭐ | ✅ Реализовано |

**Общий итог:** ~14–18 рабочих дней для полного внедрения.  
**MVP (этапы 1–4):** ~6–8 дней — уже трансформирует UX inbox.

---

## 8. Что делают лидеры рынка vs что будет в pa-merge

| Фича | alfred_ / Lindy | Proton Scribe | **pa-merge (план)** |
|------|-----------------|---------------|---------------------|
| Triage / Priority | ✅ Cloud | ✅ On-device | ✅ MLX + rules |
| Thread summarization | ✅ Cloud | ✅ On-device | ✅ Уже есть |
| Structured extraction | ✅ Cloud | ⚠️ Частично | ✅ Этап 1 реализован |
| Draft in your tone | ✅ Cloud | ✅ On-device | ✅ Реализовано |
| Follow-up detection | ✅ Cloud | ❌ | ✅ Этап 3 реализован |
| Meeting prep | ✅ Cloud | ❌ | ✅ Реализовано |
| Daily brief | ✅ Cloud | ❌ | ✅ Реализовано |
| Calendar NLP | ✅ Cloud | ❌ | ✅ Реализовано |
| Полная приватность | ❌ | ✅ | ✅ Всё локально |
| Кириллица / русский | ⚠️ | ⚠️ | ✅ Встроено |
| Интеграция с vault | ❌ | ❌ | ✅ Уникально |
| Связь с проектами | ❌ | ❌ | ✅ Уже есть |

**Уникальное конкурентное преимущество pa-merge:** полная приватность + русский язык + интеграция vault/projects/threads как единый граф знаний.

---

## Источники

- [15+ Best AI Email Assistants for 2026](https://sintra.ai/blog/ai-email-assistant)
- [AI Calendar Assistants: The Future of Smart Scheduling](https://www.ailoitte.com/insights/ai-calendar-assistants/)
- [Ollama is now powered by MLX on Apple Silicon](https://ollama.com/blog/mlx)
- [MLX: The Next Inference Engine for Apple Silicon](https://yage.ai/share/mlx-apple-silicon-en-20260331.html)
- [Best Local LLMs for Mac in 2026](https://insiderllm.com/guides/best-local-llms-mac-2026/)
- [Qwen 3.5 on Apple Silicon MLX](https://willitrunai.com/blog/qwen-3-5-mlx-apple-silicon-guide)
- [LLM Structured Output in 2026](https://dev.to/pockit_tools/llm-structured-output-in-2026-stop-parsing-json-with-regex-and-do-it-right-34pk)
- [Action-Item-Driven Summarization of Long Meeting Transcripts (arXiv)](https://arxiv.org/pdf/2312.17581)
- [How to summarize long email threads using AI (Missive)](https://missiveapp.com/blog/summarize-email-thread-ai)
- [Best AI Assistant for Email Triage (alfred_)](https://get-alfred.ai/blog/best-ai-assistant-for-email-triage)
- [Fine-Tuning LLMs Locally Using MLX LM (DZone)](https://dzone.com/articles/fine-tuning-llms-locally-using-mlx-lm-guide)
- [Apple Silicon LLM Benchmarks](https://llmcheck.net/benchmarks)
