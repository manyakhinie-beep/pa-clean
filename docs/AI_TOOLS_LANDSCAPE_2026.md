# AI-инструменты обработки почты и календаря 2026: ландшафт, сравнение с pa-clean, план доработок

> Дата: 26 мая 2026
> Дополнение к [ai-email-calendar-research.md](./ai-email-calendar-research.md) (май 2026),
> в котором закрыты этапы 1-8 (Structured Extraction, AI Priority, Follow-up,
> Thread-aware Draft, Meeting Prep, Daily Brief, Calendar Intent NLP, LLM Classify).
> Эта работа смотрит на сегодняшний рынок **после** реализации тех восьми этапов
> и отвечает на вопрос: «что ещё стоит сделать в pa-clean за 1-3 месяца, чтобы
> закрыть значимые пробелы по сравнению с лидерами 2026 года».

---

## 1. Резюме

После закрытия фаз 1-8 pa-clean покрывает базовый набор AI-функций для почты
и календаря: классификация, приоритезация, suммаризация, thread-aware
черновики, meeting prep, ежедневный brief, parse-intent для календаря и
LLM-резерв для уверенной классификации. Этого достаточно, чтобы продукт
**использовался** ежедневно. Но рынок 2026 года ушёл вперёд по четырём
направлениям, где pa-clean имеет реальные пробелы:

1. **«Лента готовых черновиков»** — Fyxer, Superhuman и M365 Copilot
   пред-генерируют черновики для каждого письма «требующего ответа» в
   фоне. У pa-clean draft создаётся по нажатию кнопки.
2. **Стиль пользователя**. Все три AI-email-конкурента учат модель на
   исходящих письмах пользователя; pa-clean использует общий промпт-шаблон.
3. **Calendar autoscheduling задач** — Reclaim, Motion, Cal AI ставят
   задачи на свободные слоты автоматически. У pa-clean это полностью
   отсутствует.
4. **Conversational search** («Ask AI» в Superhuman, Mem Chat, Shortwave
   natural language search). У pa-clean есть BM25 и чат, но нет единого
   входа «спроси что угодно про мою почту и календарь».

Зато pa-clean уверенно держит четыре преимущества, ради которых она
существует и которые **усиливаются** на фоне рынка:

- **Полная локальность** (MLX on-device, никакого облака). Только
  Apple Intelligence приближается, и то частично.
- **Markdown-vault как единый источник правды** — мы не блокируем данные
  пользователя. Reflect/Obsidian близки, но не имеют пайплайна Mail/Calendar.
- **Русский язык и корпоративные ограничения** (offline-сборка под
  прокси Сбера, никакого OpenAI). Ни один из проанализированных
  инструментов не позиционируется на русскоязычный enterprise.
- **Единый граф знаний**: проекты + треды + контакты + встречи + правила.
  Mem.ai пытается это сделать для заметок; никто — для Mail+Calendar.

Ниже — детальный разбор и тактический план на 1-3 месяца (Q3 2026), привязанный
к [FUNCTIONALITY_MAP.md](./FUNCTIONALITY_MAP.md).

---

## 2. Ландшафт инструментов 2026

### 2.1. AI-email клиенты

#### Superhuman (~$30/мес)

- **Auto Summarize**: однострочная сводка над каждым тредом, обновляется
  при поступлении новых писем. Пользователь может вообще не читать тред.
- **Instant Reply**: при открытии письма уже виден черновик в стиле
  пользователя. По заявлениям компании — «пользователи пишут письма в
  два раза быстрее».
- **Write with AI**: пользователь даёт короткие тезисы, AI собирает
  полное письмо, опираясь на inbox + calendar + web + uploaded knowledge.
- **Split Inbox + Auto Labels**: пользователь задаёт ярлык **AI-промптом**
  («сообщения от VIP», «внутренние от команды»), inbox автоматически
  делится на вкладки.
- **Ask AI**: «где оффсайт?», «ответь тремя кофейнями рядом с офисом» —
  ищет ответ по inbox + calendar + web.

#### Shortwave (Google Workspace only)

- **Thread summaries**: автоматические резюме длинных тредов.
- **Natural language search**: «что Sarah говорила про Q3 budget?» →
  AI находит и обобщает.
- **Style learning**: учит стиль пользователя по исходящим письмам.
- **Team features**: shared labels, thread sharing, assignees,
  per-thread comments.
- Ограничен Gmail/Workspace; для корпоратива на Outlook/Exchange
  неприменим.

#### Fyxer (~$30/мес, Gmail+Outlook)

- **3-bucket auto-triage**: needs reply / FYI / marketing noise. Это
  ключевая модель: не «срочно/важно», а «надо что-то делать / просто
  читать / удалить и не показывать снова».
- **Style-matched drafts**: учится на исходящих письмах, различает тон
  «клиент vs коллега».
- **Reply frequency setting**: «как часто писать черновики» — чтобы не
  тонуть в auto-drafts.
- **Auto meeting notes**: подключается к Google Meet / MS Teams,
  пишет ключевые решения и action items.
- Цифры из их маркетинга: 81% пользователей экономят >1 ч/день,
  90% продолжают пользоваться через 3 месяца.

### 2.2. AI-календарь

#### Reclaim.ai (бесплатный/$8+/мес)

- **Tasks**: «у меня задача на 2 ч, дедлайн пятница» → AI находит
  свободный слот в календаре и блокирует его.
- **Habits**: повторяющиеся блоки («45 мин чтения каждый день
  между 8-10 утра») с гибким временем.
- **Focus Time**: защита блоков для глубокой работы.
- **Smart Meetings**: подбор оптимального времени для встречи по
  свободу всех участников.
- **Buffer Time**: автоматические перерывы между встречами.
- **Time Analytics**: еженедельный отчёт «где я провёл время».
- Интеграции: Asana, Todoist, ClickUp, Jira, Linear, Slack.

#### Motion ($19-29/мес)

- Позиционирует себя как **superapp**: AI Projects, Tasks, Calendar,
  Meetings, Docs, Notes, Reports.
- **Динамическая оптимизация**: пересчитывает расписание десятки раз в
  день при изменениях. Главная фишка — задача может «двигаться» в
  календаре сама.
- Booking pages, как у Calendly.
- AI credits модель (7500/15000 в месяц на разных тарифах).

#### Clockwise — **закрылся 27 марта 2026**

Команда ушла в Salesforce, Reclaim — официальный преемник.
Урок для нас: даже компании с серьёзным funding'ом не всегда выживают
в категории «AI calendar» — есть консолидация. Это даёт окно для
локального игрока с другим value prop.

### 2.3. Big Tech копилоты

#### Microsoft 365 Copilot (Outlook)

- **Iterative draft**: draft пишется прямо в окне, Copilot задаёт
  уточняющие вопросы, переписывает в-месте.
- **Schedule with Copilot**: одна кнопка в треде письма → Copilot
  предлагает слоты с учётом всех получателей, бронирует переговорки,
  готовит agenda.
- **Multi-attendee scheduling**: если идеального слота нет, расширяет
  диапазон и объясняет почему.
- **Meeting prep insights**: в classic Outlook — real-time context
  summaries для подготовки к встрече.
- **Copilot RSVP в чате**: ответить на приглашение прямо из Teams chat.

#### Google Gemini (Workspace, май 2026)

- **Daily Brief** (новинка I/O 2026): ночью агент сканирует Gmail/
  Calendar/Tasks → утром в почту приходит сводка дня с быстрыми
  действиями (создать reminder, ответить, открыть письмо).
- **Gemini Spark**: 24/7 agent — управляет inbox и календарём по
  заданным правилам. Требует AI Ultra ($100/мес).
- **Personal Intelligence**: разрешение Gemini читать Gmail/Photos/
  (скоро Calendar) для проактивных подсказок.

#### Apple Intelligence (macOS Mail, бесплатно с устройством)

- **Priority Messages**: автоматическая секция «срочного» наверху
  inbox: boarding passes, same-day встречи, подтверждения. На M-чипах
  работает on-device.
- **Email Summaries**: AI-сводка заменяет preview-текст под темой.
- **Smart Reply**: короткие auto-generated reply suggestions
  (типа «Да, удобно», «Спасибо», «Перенесём?»).
- **Конкуренция с pa-clean прямая**: пользователь macOS получает
  half-стек локально из коробки. Мы должны давать **глубину** там,
  где Apple даёт **широту**.

### 2.4. Local-first / Personal AI

#### Granola (meeting notes)

- **Не bot в звонке** — слушает аудио устройства локально, поэтому
  работает с Zoom/Meet/Teams без приглашения.
- Audio **не сохраняется**, только текстовые транскрипты + сводки.
- **Spaces** — командные workspace.
- 2026 Series C, добавлены personal/enterprise API.
- **Caveat**: defaults в апреле 2026 оказались «нот public by link»
  — индустрия обсуждает privacy. Урок: «local-first» как маркетинг
  ≠ «private-by-default» как реальность.

#### Mem.ai (personal AI thought partner)

- Knowledge graph строится автоматически без папок/тегов.
- **Heads Up**: проактивно показывает связанные заметки в нужный
  момент (перед встречей с человеком — твоя история с ним).
- **Mem Chat**: conversational query поверх всех заметок.
- **Agentic Chat**: AI может сам создавать/редактировать заметки.
- Облачное хранение → не подходит для приватного контекста, но
  хороший референс «как должен работать second brain over email».

#### Reflect

- Daily Notes + backlinks (как Obsidian, но с AI поверх графа).
- GPT-4 + Whisper для записи и обработки.
- **Chat with your notes** учитывает связи, а не только содержимое
  отдельной заметки. Пример: «Что я узнал о productivity tools?» —
  синтезирует ответ из 23 связанных заметок.
- Десктоп + мобайл, файлы свои в облаке Reflect.

---

## 3. Сравнительная матрица (расширенная)

Легенда: ✅ есть; 🟡 частично; ❌ нет; 🔒 cloud only; 🏠 local-only.

| Возможность | Superhuman | Shortwave | Fyxer | Reclaim | Motion | M365 Copilot | Gemini | Apple Intel. | Granola | Mem | **pa-clean** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **Email AI** | | | | | | | | | | | |
| Auto-сводка треда | ✅ | ✅ | 🟡 | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |
| Auto-draft в фоне | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | 🟡 | ✅ Smart Reply | ❌ | ❌ | ❌ |
| Стиль пользователя | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ | 🟡 | ❌ | ❌ | ❌ | ❌ |
| 3-bucket triage | 🟡 | 🟡 | ✅ | ❌ | ❌ | ❌ | 🟡 | ✅ (Prio Msg) | ❌ | ❌ | 🟡 (urg/imp/fup) |
| Ask AI inbox | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ | ✅ | 🟡 (chat+search раздельно) |
| Custom AI-labels | ✅ | 🟡 | ❌ | ❌ | ❌ | 🟡 | ❌ | ❌ | ❌ | ❌ | 🟡 (правила есть, без LLM-условий) |
| **Calendar AI** | | | | | | | | | | | |
| NL → event | 🟡 | 🟡 | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |
| Task → calendar | ❌ | ❌ | ❌ | ✅ | ✅ | 🟡 | 🟡 | ❌ | ❌ | ❌ | ❌ |
| Habits / recurring | ❌ | ❌ | ❌ | ✅ | 🟡 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Focus time | ❌ | ❌ | ❌ | ✅ | ✅ | 🟡 | ❌ | ❌ | ❌ | ❌ | ❌ |
| Multi-att. scheduling | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ | 🟡 | ❌ | ❌ | ❌ | 🟡 (конфликт-чек) |
| Scheduling links | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Daily brief | ❌ | ❌ | ❌ | 🟡 | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ (в WebUI, не email) |
| Meeting prep | 🟡 | 🟡 | ❌ | ❌ | ❌ | ✅ | 🟡 | ❌ | ✅ | 🟡 | ✅ |
| **Cross-cutting** | | | | | | | | | | | |
| Meeting transcript | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ | 🟡 | ❌ | ✅ | ❌ | ❌ |
| Knowledge graph | ❌ | 🟡 | ❌ | ❌ | ❌ | 🟡 | 🟡 | ❌ | ❌ | ✅ | 🟡 (vault links) |
| 24/7 agent | ❌ | ❌ | ❌ | 🟡 | ✅ | 🟡 | ✅ Spark | ❌ | ❌ | 🟡 | 🟡 (APScheduler) |
| **Privacy / openness** | | | | | | | | | | | |
| Local inference | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | 🟡 | 🟡 | ❌ | ✅ |
| Owns your data | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | 🟡 | ❌ | ✅ vault md |
| Open architecture | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | 🟡 API | ❌ | ✅ |
| Русский язык | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 | ✅ | ✅ | 🟡 | 🟡 | 🟡 | ✅ нативно |
| Бесплатно | ❌ | 🟡 free tier | ❌ | 🟡 free tier | ❌ | ❌ enterprise | 🟡 free Bard | ✅ с macOS | 🟡 free tier | ❌ | ✅ |
| **Стоимость/мес** | $30 | $9-29 | $30 | $0-15 | $19-29 | $30 | $0-100 | $0 | $0-20 | $20 | $0 |

### Что говорит матрица

- **pa-clean уверенно держит правый столбец «Privacy/openness»** — никто
  из глобальных игроков не предлагает локальный inference + открытый
  vault + русский язык + бесплатность в одном пакете.
- **Email AI пробелы**: auto-draft, стиль пользователя, ask AI.
- **Calendar AI пробелы**: task→calendar, habits, focus time, scheduling
  links — целая категория «AI calendar» проигнорирована.
- **Cross-cutting**: meeting transcript отсутствует, knowledge graph
  есть как vault links но без auto-discovery.

---

## 4. Gap-анализ для pa-clean

Группа A — **критичные пробелы для ежедневного UX**:

| # | Пробел | Кто это делает | Что теряет pa-clean |
|---|---|---|---|
| A1 | Auto-draft в фоне для писем «требующих ответа» | Fyxer, Superhuman, Copilot | Главный «вау-эффект» AI-email сегодня. Без него pa-clean «ещё один inbox с кнопкой Draft» |
| A2 | Style learning по исходящим письмам | Все три AI-email | Черновики звучат как ChatGPT, а не как пользователь |
| A3 | Auto-summary над каждым тредом в Inbox | Superhuman, Apple Intel., Copilot | Пользователь читает текст вместо саммари |
| A4 | Conversational «Ask AI» по inbox+calendar | Superhuman, Shortwave, Gemini, Mem | Сейчас есть chat и search, но они разные UI |

Группа B — **большие функциональные пробелы**:

| # | Пробел | Кто это делает | Стратегическая ценность |
|---|---|---|---|
| B1 | Task → calendar autoscheduling | Reclaim, Motion | Открывает новую категорию (AI calendar). Реалистично за 2-3 спринта |
| B2 | Multi-attendee availability | Reclaim, Motion, Copilot | Видим в проф. флоу (встречи с командой), важно для B2B |
| B3 | Marketing/noise filter (4-я корзина) | Fyxer | Уменьшает «шум» в Inbox — простое quick win |
| B4 | Meeting transcript локально (whisper.mlx) | Granola | Большая категория, технологически в пределах MLX-стека |

Группа C — **косметика / расширения**:

| # | Пробел | Кто это делает |
|---|---|---|
| C1 | Email-версия Daily Brief в Drafts | Gemini Daily Brief |
| C2 | Scheduling links (публичная страница доступности) | Reclaim, Motion, Calendly |
| C3 | Habits (повторяющиеся блоки) | Reclaim |
| C4 | Auto-discovery связей в vault | Mem.ai Heads Up, Reflect |
| C5 | Time analytics («где я провёл время») | Reclaim, Motion |

---

## 5. Тактический план на 1-3 месяца (Q3 2026)

Принцип ранжирования: **высокий impact × низкая стоимость × усиление moat**.
Все этапы предполагают on-device MLX, никакого облака.

### 5.1. Спринт 1 (2 недели) — Quick wins

#### S1-1. Marketing / noise filter (B3) — 2 дня

**Что**: 4-й фильтр «🗑️ Шум» в Inbox + автоматическая классификация.

**Реализация**:

- В `data/classify.yaml` добавить категорию `noise` с keywords-списком
  (unsubscribe, рассылка, скидка, % off, newsletter, …).
- `_TAG_NOISE` set в `inbox/routes.py`.
- В `inbox_rules_service.py` правила с `tags: ['noise']` → флаг `is_noise`.
- Фильтр «🗑️ Шум» в `index.html` + `inbox.js` setupFilterTabs.
- По умолчанию в фильтре «Все» шум **скрыт** (toggle «показать шум»).

**Тесты**: расширить `test_inbox_rules_service.py` на noise-кейсы (5-7 тестов).

**Тачит**: `data/classify.yaml`, `src/personal_assistant/inbox/routes.py`,
`webui/index.html`, `webui/frontend/js/inbox.js`.

#### S1-2. Auto-summary line в Inbox-list (A3) — 3 дня

**Что**: однострочная AI-сводка над темой каждого письма в списке.
Заменяет (или дополняет) `body_preview`.

**Реализация**:

- При фоновой синхронизации (или по cron) для каждого нового mail-документа
  через MLX (или structured-extract который уже есть) генерировать поле
  `ai_summary_oneline` и писать в `data/inbox_state.json` + frontmatter.
- Кэш по sha256 тела письма (как у LLM-classify).
- В `_doc_to_item` добавить `ai_summary_oneline` в payload.
- В `inbox.js` `renderList`: если есть `ai_summary_oneline` — показать
  курсивом под темой, иначе fallback на `body_preview`.
- В Rules UI добавить toggle «Авто-сводка писем» (использовать
  `mail_auto_draft`-pattern в config.py).

**Тесты**: unit на генерацию + e2e на отображение в списке.

**Тачит**: `src/personal_assistant/mlx_server/tasks/summarize.py` (если
нужно — отдельный лёгкий промпт под one-line), фоновый task,
`inbox/routes.py`, `webui/frontend/js/inbox.js`, `config.py`.

#### S1-3. Ask AI — единый command palette (A4) — 3 дня

**Что**: в Today и Inbox добавить «Cmd+K → спроси что угодно». Под
капотом — pre-built prompt, который вытащит контекст через BM25 +
vault index + last 7 days emails.

**Реализация**:

- Новый компонент `webui/frontend/js/ask_ai.js` — модалка с input
  и потоковым ответом, биндинг на Cmd+K (Mac) / Ctrl+K (Win).
- Endpoint `/api/v1/ask` — внутри:
  1. BM25 top-5 docs по query.
  2. Подгрузить `currentReplyMessageId` контекст, если открыто письмо.
  3. Стримить через `/api/chat/send` SSE.
- В шапке навигации Today/Inbox — кнопка «🔍 Спросить» с подсказкой
  про шорткат.

**Тачит**: новый `ask_ai.js`, новый endpoint в `webui/routes.py`,
`webui/index.html` + nav.

#### S1-4. Auto-draft в фоне для followup-писем (A1, MVP) — 4 дня

**Что**: при первой загрузке Inbox или фоновой синхронизации запускать
draft-generation для всех писем с флагом `followup_needed=true` (топ 10
по priority). Готовый черновик хранится в кэше; кнопка `↩️ Ответ`
теперь не делает MLX-вызов, а отдаёт уже сгенерированный текст.

**Реализация**:

- Расширить existing `draft-context` cache (новое поле `auto_draft_text`).
- Background-task через `apscheduler`: `auto_draft_recent_followups(top_n=10)`.
- Endpoint `GET /api/v1/inbox/{id}/auto-draft` (200 с текстом / 404 / 202
  если ещё генерится).
- Frontend (`inbox.js`): значок ✨ на письмах, у которых готов draft;
  при клике `↩️ Ответ` — instant fill.
- Контролируется setting `mail_auto_draft_background: bool` в Rules.

**Тесты**: unit на cache + integration на endpoint, scenario mocked
MLX engine.

**Тачит**: `src/personal_assistant/services/draft_context_service.py`,
новый background-task, `mlx_server/chat_routes.py`, `inbox.js`,
`config.py`, `rules.js`.

### 5.2. Спринт 2 (2-3 недели) — Style learning + 3-bucket

#### S2-1. Style learning по исходящим письмам (A2) — 1 неделя

**Что**: один раз при первой синхронизации (или ручному запуску
«🎨 Обучить стиль») система прогоняет N=200 последних исходящих писем
через MLX и строит «персональный стилевой профиль» — несколько
характеристик (формальность, длина, типичные приветствия/прощания,
любимые обороты, эмоджи или нет). Профиль хранится в
`data/user_voice_profile.json` и инжектируется в draft-промпт.

**Реализация**:

- Новый сервис `services/style_profile_service.py`:
  - `extract_style_features(sent_emails) -> StyleProfile`
  - Считается локально: средняя длина, ratio formal/informal markers,
    частотный список фраз-приветствий/прощаний, наличие эмоджи, %
    маркированных списков, склонность к «спасибо/благодарю».
  - Можно усилить MLX-вызовом на 5-10 примерах с просьбой описать стиль
    «3-5 предложениями».
- Профиль → `data/user_voice_profile.json` (~2 КБ).
- В `draft_reply.py` промпт получает блок:
  ```
  СТИЛЬ ПОЛЬЗОВАТЕЛЯ:
  - Тон: формально-деловой
  - Длина: 80-120 слов
  - Приветствие: "Добрый день, {Имя}!" (60% писем)
  - Прощание: "С уважением, Игорь"
  - Эмоджи: не использует
  ```
- UI: Rules → раздел «Стиль писем» — кнопка «Обновить стиль» +
  отображение текущего профиля + ручное редактирование.

**Тесты**: unit на extract_style_features (тест-кейсы синтетические),
e2e на endpoint POST `/api/v1/profile/train-voice`.

**Риски**: на маленьком sample (<20 писем) профиль будет шумным.
Mitigation: показывать предупреждение в UI, дать кнопку «использовать
дефолтный стиль».

**Тачит**: новый `services/style_profile_service.py`,
`mlx_server/tasks/draft_reply.py` (промпт),
`webui/routes.py` (endpoint), `webui/frontend/js/rules.js` (UI блок).

#### S2-2. 3-bucket triage UX (B3 расширенный) — 3 дня

**Что**: рядом с фильтрами «🔴 Срочно / 🟡 Важно / 🔔 Ответить» добавить
**сводный** view «📥 To do / 📰 To read / 🗑️ Noise» как у Fyxer, под
капотом — комбинация существующих флагов.

**Реализация**: чисто UI-перегруппировка, не новая логика:

- To do = is_urgent ∪ followup_needed
- To read = is_important − is_urgent − followup_needed
- Noise = is_noise (из S1-1)
- Остальное = «обычные» → не показывать badge

В index.html — переключатель «📑 Срочность / 📋 To-do view».

**Тачит**: `webui/index.html`, `webui/frontend/js/inbox.js`.

### 5.3. Спринт 3 (3-4 недели) — Calendar autoscheduling

#### S3-1. Task → Calendar autoscheduling MVP (B1) — 2 недели

**Что**: пользователь создаёт задачу с дедлайном и оценкой времени;
система находит свободный слот в календаре и блокирует событие.

**Реализация**:

- Новая модель `Task` в `personal_vault/models.py`:
  ```python
  Task(id, title, duration_min, deadline, priority, status,
       scheduled_event_uid, created_at, completed_at)
  ```
- `data/tasks.json` — простое хранилище (как `eisenhower.json`).
- Сервис `services/task_scheduler_service.py`:
  - `find_free_slots(duration_min, before_deadline) -> list[(start, end)]`
    — использует `fetch_upcoming_events()` + `calendar_default_duration`
    для buffer time.
  - `schedule_task(task) -> CalendarEvent` — создаёт событие через
    `calendar_writer.create_event` с тегом `[TASK]`.
  - `reschedule_on_conflict(task)` — если новая встреча перекрыла слот,
    подобрать другой.
- Endpoints:
  - `POST /api/v1/tasks` — создать задачу.
  - `POST /api/v1/tasks/{id}/schedule` — найти слот + создать event.
  - `GET /api/v1/tasks?status=open|scheduled|done`
- WebUI:
  - Новая вкладка «✅ Задачи» или интеграция в Today.
  - Карточка задачи: title, время, дедлайн, кнопка «Запланировать».
  - При нажатии — preview слотов (топ 3), confirm → calendar event.

**Тесты**:

- Unit на `find_free_slots` с фикстурой искусственного расписания
  (10+ кейсов: пустой день, плотный день, выходные, дедлайн через 1 ч).
- Unit на `schedule_task` через mocked `calendar_writer`.
- Scenario (live macOS) — создание/удаление тест-события в Test Calendar.

**Риски**: AppleScript-write события с тегом `[TASK]` — нужен надёжный
способ отличить task-event от обычного, чтобы reschedule_on_conflict
не трогал обычные встречи. Mitigation: дополнительный `notes` маркер
`{"pa_clean_task_id": "..."}`.

**Тачит**: новый `services/task_scheduler_service.py`,
`personal_vault/models.py`, `webui/routes.py`, новый или расширенный
`projects.js` (или новый `tasks.js`), `calendar/calendar_writer.py`.

#### S3-2. Multi-attendee availability search (B2) — 1 неделя

**Что**: при создании встречи через `/встреча Иван, Петя, …` система
не только парсит intent, но и **ищет общие свободные слоты** для всех
указанных контактов (используя vault-события + EventKit free/busy для
своего календаря).

**Реализация**:

- Расширить `calendar/intent_parser.py` — возвращать `attendees: list[str]`.
- Новый сервис `services/availability_service.py`:
  - `find_common_slots(attendees, duration, search_window) -> list[Slot]`
  - Для своего календаря: existing `fetch_upcoming_events`.
  - Для attendees: scan `vault/contacts/{email}.md` на их события,
    если есть (мы их не получаем real-time, но vault содержит inviter-
    perspective события).
  - Возвращать топ-5 слотов с пометкой «нет конфликта»/«одно письмо в
    треде намекает на занятость».
- В `parse-intent` preview-карточку добавить блок «🕐 Предлагаемые слоты»
  с возможностью выбрать.

**Тачит**: `calendar/intent_parser.py` (если ещё не парсит attendees),
новый `availability_service.py`, `calendar/routes.py` (parse-intent
endpoint), `today.js` (quick-create preview).

### 5.4. Опциональный спринт 4 (4 недели) — Stretch goals

Если темп остаётся, можно подкатить **один** из:

- **Meeting transcript на whisper.mlx** (B4): запись аудио → локальная
  транскрипция → action items в vault. ~2-3 недели. Зависит от
  `whisper.cpp`/`mlx-whisper`.
- **Email-версия Daily Brief** (C1): рендерить existing Today data в
  HTML и сохранять в `Drafts` Mail.app в 08:00 МСК. ~3-5 дней.
- **Scheduling links** (C2): публичная страница `/availability/{slug}`,
  показывает свободные слоты, гость выбирает — создаётся event.
  ~1-1.5 недели.

---

## 6. Что НЕ делать в этом квартале (намеренно)

| Идея | Почему отказ |
|---|---|
| LoRA fine-tuning модели на личной переписке | Уже отложено в существующем research как «очень высокая сложность». Сначала надо насытить стиль-профайл из S2-1 — это даёт 80% эффекта за 5% работы. |
| Webex/Zoom/Teams интеграции | Корпоративный контекст пользователя — Mail+Calendar.app + AppleScript. Чужие SaaS-API ломают local-first архитектуру. |
| Дублирование Reclaim Habits и Focus Time | Низкий приоритет относительно task scheduling. Сначала S3-1, потом возвращаемся. |
| Polish UI / темы оформления | Нет evidence что это блокирует пользователя. После S1-S3 — да. |
| Свой Pomodoro / time tracking | Out of scope «Mail+Calendar assistant». |

---

## 7. Метрики успеха (как мы поймём, что план сработал)

Для каждого пользователя, прошедшего через все 3 спринта, цель:

| Метрика | Цель Q3 2026 | Как мерить |
|---|---|---|
| % писем «требующих ответа» с готовым auto-draft | ≥ 70% | `/api/v1/inbox/stats` (новое поле `auto_draft_coverage`) |
| Среднее время от «открыл письмо» до «отправил draft» | ≤ 90 сек | UI-метрика (опционально, локально) |
| % использования Ask AI | ≥ 30% сессий | Локальный счётчик в `data/usage_stats.json` |
| Задач, спланированных через task-scheduler | ≥ 5/неделя | Длина `data/tasks.json` |
| Падений / 500-ошибок WebUI | ≤ 0.5% запросов | logs |
| Покрытие тестами в новых модулях | ≥ 80% | `pytest --cov` |

---

## 8. Привязка к FUNCTIONALITY_MAP.md

| Спринт-задача | Новый ID в карте | Раздел | Тип теста |
|---|---|---|---|
| S1-1 noise filter | F17 | services/inbox_rules_service.py | unit ✅ |
| S1-2 auto-summary | F18 | mlx_server/tasks + inbox_state | unit + scenario 🍎 |
| S1-3 Ask AI palette | F19 | webui/routes + ask_ai.js | e2e ✅ |
| S1-4 background auto-draft | F20 | services + scheduler | unit + scenario 🍎 |
| S2-1 style profile | F21 | services/style_profile_service | unit ✅ |
| S2-2 to-do view | (UI only) | inbox.js | e2e ✅ |
| S3-1 task scheduler | F22 | services/task_scheduler_service | unit + scenario 🍎 |
| S3-2 multi-att. availability | F23 | services/availability_service | unit ✅ |

Все F-добавления попадут в Phase 3 расширения функционала после
Phase 1-2 (audit) и Phase 6-7 (migration), которые уже закрыты.

---

## 9. Риски и mitigation

| Риск | Severity | Mitigation |
|---|---|---|
| Auto-draft на MLX 7B Q4 пишет «не очень» по сравнению с GPT-4 | Высокий | Style profile + temperature=0.3 + thread-aware context. Если стиль шумный — fallback на template-based draft из существующего `_TAG_DISPLAY`. |
| Background tasks нагружают M-чип во время работы пользователя | Средний | Очередь с rate-limit 1 запрос/сек + пауза если CPU > 60% (через `psutil`). |
| Task scheduler пишет в Calendar.app, конфликтует с manual edits | Высокий | Маркер `[TASK]` в notes + проверка `pa_clean_task_id` перед перезаписью. |
| Apple Intelligence обгонит pa-clean на macOS | Стратегический | Усиливать moat: cross-tool integration (Mail+Calendar+Projects), русский, открытость vault. |
| Style profile неверно отражает пользователя на маленьком sample | Низкий | Min 20 писем, UI-warning, ручное редактирование. |
| Mac на удалённой машине пользователя падает на новых background-tasks | Средний | Все новые фоновые задачи под флагом в `config.py` (default off для первого релиза), включаем после feedback. |

---

## 10. Источники

- [Superhuman Mail | AI](https://superhuman.com/products/mail/ai)
- [Superhuman Review 2026](https://ventureburn.com/superhuman-email-review/)
- [Shortwave AI Email](https://www.shortwave.com/)
- [Shortwave vs Superhuman 2026 (Zapier)](https://zapier.com/blog/shortwave-vs-superhuman/)
- [Fyxer AI Email Assistant](https://www.fyxer.com/ai-email-assistant)
- [Fyxer AI Review 2026 (Efficient.app)](https://efficient.app/apps/fyxer)
- [Reclaim.ai — AI Calendar](https://reclaim.ai/)
- [Reclaim Focus Time](https://reclaim.ai/features/focus-time)
- [Motion — AI SuperApp](https://www.usemotion.com/)
- [Motion AI Review 2026](https://rimo.app/en/blogs/motion-ai_en-US)
- [Clockwise shutdown (March 2026)](https://getclockwise.com/)
- [Reclaim vs Clockwise 2026 (Morgen)](https://www.morgen.so/blog-posts/reclaim-vs-clockwise)
- [M365 Copilot — April 2026 release notes](https://techcommunity.microsoft.com/blog/microsoft365copilotblog/what%E2%80%99s-new-in-microsoft-365-copilot--april-2026/4510935)
- [New Outlook features May 2026 (Windows Latest)](https://www.windowslatest.com/2026/05/10/new-outlook-and-outlook-classic-features-in-may-2026-include-copilot-insights-teammates-calendars-automapped-calendars/)
- [Gemini Daily Brief — Google I/O 2026 (Jetstream)](https://jetstream.blog/en/gemini-daily-brief/)
- [Gemini app I/O 2026 (9to5Google)](https://9to5google.com/2026/05/19/gemini-app-google-io-2026/)
- [Apple Intelligence Mail features (Mailbird)](https://www.getmailbird.com/apple-mail-ai-auto-filing-priority-inbox-concerns/)
- [Apple Intelligence in Mail (Apple Support)](https://support.apple.com/en-by/guide/mac-help/mchlb2dbea8f/mac)
- [Granola AI](https://www.granola.ai/)
- [Granola privacy concerns April 2026 (TheMeridiem)](https://themeridiem.com/enterprise-technology/2026/4/2/ai-meeting-tools-hit-privacy-inflection-as-granola-default-exposes-notes)
- [Mem 2.0 — Personal AI Thought Partner](https://get.mem.ai/blog/introducing-mem-2-0)
- [Mem AI Review 2026 (Saner)](https://blog.saner.ai/mem-ai-reviews/)
- [Reflect Notes](https://reflect.app/)
- [Best AI Note-taking Apps 2026 (Techno-Pulse)](https://www.techno-pulse.com/2026/04/best-ai-note-taking-apps-in-2026-notion.html)
