# Должностные инструкции — команда pa-clean

> Детализированные ролевые карточки для найма / переброски ресурсов внутри
> корпорации. Каждая роль привязана к конкретным модулям pa-clean и
> измеримым KPI. Подходит для копирования в JIRA / Confluence /
> внутреннюю HR-систему.
>
> Используется в связке с:
> [AGENTS.md](../AGENTS.md) (что такое pa-clean),
> [ARCHITECTURE.md](../ARCHITECTURE.md) (слои),
> [FUNCTIONALITY_MAP.md](./FUNCTIONALITY_MAP.md) (карта функционала + бэклог),
> [AI_TOOLS_LANDSCAPE_2026.md](./AI_TOOLS_LANDSCAPE_2026.md) (план развития).

## Грейды

Используется единая корпоративная шкала: **Junior** → **Middle** →
**Middle+** → **Senior** → **Lead**. В описаниях указан **минимально
достаточный** грейд — повышение допустимо при дефиците кадров, но не
понижение.

## Содержание

**Ядро (MVP-фаза, 3-5 FTE):**

1. [Tech Lead / Архитектор](#1-tech-lead--архитектор)
2. [Senior Python Engineer (Backend + macOS / AppleScript)](#2-senior-python-engineer-backend--macos--applescript)
3. [AI / ML Engineer (Prompt + MLX)](#3-ai--ml-engineer-prompt--mlx)
4. [Frontend Engineer (Vanilla JS + SCSS)](#4-frontend-engineer-vanilla-js--scss)
5. [QA Engineer](#5-qa-engineer)
6. [Product Manager](#6-product-manager)

**Фаза роста (7-9 FTE):**

7. [DevOps / Release Engineer](#7-devops--release-engineer)
8. [MLOps Engineer](#8-mlops-engineer)
9. [UX / UI Designer](#9-ux--ui-designer)
10. [L2 Support Engineer](#10-l2-support-engineer)
11. [Technical Writer](#11-technical-writer)
12. [Internal Champion / Community Manager](#12-internal-champion--community-manager)

**Промышленная эксплуатация:**

13. [Engineering Manager](#13-engineering-manager)
14. [Site Reliability Engineer (SRE)](#14-site-reliability-engineer-sre)
15. [Security / Compliance Engineer](#15-security--compliance-engineer)

---

## 1. Tech Lead / Архитектор

**Грейд:** Lead. **FTE:** 1.0. **Подчинение:** Engineering Manager
(или прямо Sponsor, если EM ещё нет).

Владеет архитектурой pa-clean целиком — от AppleScript-readers до WebUI и
MLX-инференса. Делает trade-off'ы, держит документацию актуальной, ревьюит
PR'ы. Это **самая критичная** роль: без неё проект расползается на
изолированные фичи, плодит дубли и теряет производительность.

### Обязанности

- Owns [`ARCHITECTURE.md`](../ARCHITECTURE.md),
  [`FUNCTIONALITY_MAP.md`](./FUNCTIONALITY_MAP.md). Каждая новая F-area
  (F17, F18, …) проходит через ревью архитектуры.
- Ревью всех PR'ов, влияющих на: `mlx_server/engine.py`, `readers/*`,
  `services/*`, контракт vault-frontmatter, `chat_routes.py`.
- Решает архитектурные trade-off'ы: MoE vs dense MLX, raw-source MIME vs
  plain content, sync vs async (BackgroundTasks), threading модели
  (`_MLXThread`), persistence layer.
- Поддерживает гейт качества: `mypy src` без ошибок, `ruff` чисто,
  `pytest -m unit` зелёный.
- Технический представитель проекта на встречах с InfoSec / Privacy /
  внешними командами.
- Поддерживает технический roadmap (что-первым из
  [AI_TOOLS_LANDSCAPE_2026.md](./AI_TOOLS_LANDSCAPE_2026.md) §5),
  балансирует с production-incidents.
- Mentor'ит middle-разработчиков, проводит регулярные tech-syncs.

### Must-have hard skills

- Python 3.11+ Senior level (10000+ строк production).
- FastAPI + Pydantic v2 (типизация, валидация, фоновые задачи).
- Опыт с LLM в production: API или local-inference (Hugging Face,
  Ollama, MLX, vLLM — что-то из этого использовалось в реальной задаче).
- macOS как dev-окружение, минимум общее знание AppleScript / TCC /
  permissions. Понимание osascript IPC.
- `mypy`, `ruff`, `pytest`, `uv` — пользуется ежедневно.
- Git, ревью практик (запросы на изменения, не «approve по диагонали»).

### Must-have soft skills

- Принимает решения в условиях неопределённости и фиксирует их в коде
  как комментарий «почему так» (а не «что сделано»).
- Балансирует «всё переписать как следует» и «доставить пилот к
  четвергу».
- Может стейкхолдеру объяснить, почему «нельзя просто добавить ChatGPT» —
  на одной странице.

### Nice to have

- Реальный опыт с MLX (модели в формате `mlx-community/*`).
- Знание корпоративной инфраструктуры (корп-прокси, MDM-deploy, InfoSec-процессы).
- Прошлый опыт **поддержки** legacy-проекта (не только greenfield) — знает
  цену технического долга.

### KPI

| Метрика | Цель Q3 2026 | Как мерить |
|---|---|---|
| Покрытие тестами core-модулей (vault/sync/services/mlx_server) | ≥ 70% | `pytest --cov=src` (после `uv sync --group cov`) |
| Время от bug-report до hotfix-PR | ≤ 24 ч (рабочие) | issue tracker |
| `mypy src` ошибок в main | 0 | CI / pre-commit |
| Архитектурные ревью PR'ов, влияющих на API-контракт | 100% | git blame на decision-commit'ах |
| Стейкхолдер-документы (`AI_TOOLS_LANDSCAPE`, etc.) актуальны | обновляются раз в квартал | git log докам |

### Red flags

- «Лучше переписать на TypeScript / Go» без бизнес-обоснования.
- Не любит писать docstring'и («код самодокументируемый»).
- Не понимает зачем нам lenient YAML parser, считает что «vault сам по
  себе валидный».
- Игнорирует существующие тесты (≥ 654 кейса) — пишет код без проверки
  что они зелёные.

---

## 2. Senior Python Engineer (Backend + macOS / AppleScript)

**Грейд:** Senior (Middle+ допустим при дефиците). **FTE:** 1.0.
**Подчинение:** Tech Lead.

Делает основную работу: новые роуты API, services, AppleScript-readers,
draft / delegate / calendar flows. Отвечает за то, что любая фича,
требующая Mail.app / Calendar.app, работает на реальной Mac-машине без
TCC-сюрпризов.

### Обязанности

- Развивает: `mlx_server/chat_routes.py`, `inbox/routes.py`,
  `calendar/routes.py`, `services/*`, `readers/applescript_base.py`.
- Пишет / поддерживает AppleScript-снипеты в `readers/mail_reader.py`
  и `readers/calendar_reader.py`. Знает наизусть симптомы `error -1700`
  (uncoercible to string), `-2741` (parse), `-1743` (TCC denied).
- Закрытие user-репортов («Mail draft не открывается», «CC не подтянулся»,
  «событие не появилось в Calendar») — от воспроизведения до hotfix-PR.
- Поддерживает контракт «vault → WebUI»: что попадает в frontmatter,
  как читается читателями, как сериализуется в `inbox_state.json`.
- Пишет unit-тесты для каждого добавленного route'а (см. existing
  `tests/unit/services/test_*.py` как образец стиля).
- Owns lenient YAML pipeline (`utils/frontmatter.py`) — расширяет
  на новые run-on patterns по мере обнаружения.
- Дежурный по incident-response для AppleScript-багов (1-2 раз/мес).

### Must-have hard skills

- Python 3.11+ Senior (8000+ строк production), типизация (mypy strict),
  Pydantic v2.
- Опыт работы с macOS как target-платформой минимум 6 месяцев. Может
  читать AppleScript dictionary в Script Editor → Window → Library.
- `subprocess`, `osascript`, MIME-парсинг (`email.message_from_bytes`).
- pytest fixtures, monkeypatch, `unittest.mock.MagicMock`.
- Git, FastAPI, JSON-handling, async/await (где уместно — большая часть
  pa-clean всё-таки sync).

### Must-have soft skills

- Любит debugging IPC между процессами (osascript ↔ Python).
- Терпение к «работает у меня — не работает у пользователя» (TCC, sandbox).
- Пишет понятные коммиты («fix(mail): preserve quoted history in reply»,
  а не «fix bug»).

### Nice to have

- Опыт работы с PyObjC / Swift bridge (на случай если решим уйти от
  AppleScript).
- Понимание email-протоколов на уровне «прочитал RFC 5322 на досуге».
- Background в почтовых клиентах / desktop-приложениях.

### KPI

| Метрика | Цель |
|---|---|
| Среднее время закрытия bug-репорта | ≤ 3 дня (Mac-flake — 1 день) |
| Покрытие тестами своих модулей | ≥ 75% |
| Регрессии после собственного релиза | ≤ 1 / месяц |
| Документация в `INTEGRATIONS.md` при каждом изменении AppleScript | 100% |

### Red flags

- Никогда не открывал macOS Activity Monitor.
- Считает что AppleScript можно заменить on `cmd+shift+a` через JXA на
  следующей неделе.
- Не любит вытащить лог из `~/Library/Logs/CrashReporter/`.
- В тестах патчит `os.popen` вместо `personal_assistant.readers.applescript_base.run_applescript`.

---

## 3. AI / ML Engineer (Prompt + MLX)

**Грейд:** Senior (Middle+ при дефиците). **FTE:** 0.5-1.0.
**Подчинение:** Tech Lead.

Owns AI-функции pa-clean целиком: подбор моделей, prompt-инжиниринг,
evaluation, semantic-classification, follow-up, daily-brief, meeting-prep.
**Самая дефицитная роль на текущий момент** — её отсутствие приводит к
тому, что новые промпты шипятся «по ощущениям», без замера качества.

### Обязанности

- Owns `services/tool_prompts.py` (промпты summarize / draft / delegate)
  и `mlx_server/tasks/*` (summarize, draft_reply, classify, priority,
  extract, search).
- Поддерживает `mlx_server/engine.py`: подбор MLX-моделей (Qwen 2.5 7B,
  Qwen3-30B-A3B MoE, T-Lite), параметры сэмплирования, fallback логика.
- Строит evaluation-framework: набор реальных vault-фикстур (10-30
  тредов на 3 скилла), reference outputs, метрики (наличие нужных
  секций, BLEU/ROUGE для суммаризации, human-rating).
- Прогоняет evaluation при каждом изменении дефолтных промптов
  (`DEFAULT_*_SYSTEM`).
- Внедряет новые ML-фичи из roadmap'а
  ([AI_TOOLS_LANDSCAPE_2026.md](./AI_TOOLS_LANDSCAPE_2026.md) §5):
  S2-1 style-learning по исходящим, S1-2 auto-summary в Inbox,
  S1-4 background auto-drafts.
- Поддерживает Stage 8 LLM-classify (`mlx_server/tasks/llm_classify_service.py`):
  тюнинг threshold'а, расширение `classify.yaml`-категорий.
- Пишет докум-кейсы для prompt-инженерных решений в коммит-сообщениях.

### Must-have hard skills

- Python (Middle+), знакомство с numpy / pandas / Jupyter (для
  evaluation-ноутов).
- Опыт работы с LLM в production: минимум одна реальная задача,
  где вы тюнинговали промпт под бизнес-метрику.
- Понимает разницу между temperature / top-p / top-k, знает что такое
  prompt-injection и как от него защищаться.
- Знакомство с MLX или хотя бы с локальной инференцией (Ollama,
  llama.cpp, transformers).
- Базовое понимание токенизаторов — почему кириллица «дороже» латиницы
  по токенам, как это влияет на context window.

### Must-have soft skills

- Любит численно мерить «насколько prompt N лучше prompt M на 30
  тредах», а не «вроде лучше».
- Умеет объяснить нетехническому стейкхолдеру, почему MLX 7B не GPT-4
  и где он эффективен.
- Готов читать длинные tech-репорты (papers / model-cards) и сохранять
  ссылки в `docs/AI_TOOLS_LANDSCAPE_2026.md`.

### Nice to have

- Реальный опыт fine-tuning / LoRA (особенно ценно для русского-business
  fine-tune).
- Знание structured-output techniques (constrained decoding, JSON-schema
  validation).
- Знакомство с RAG (Retrieval-Augmented Generation) — мы внутри
  фактически уже его делаем через vault-context.

### KPI

| Метрика | Цель |
|---|---|
| Evaluation suite на 3 скилла | 30+ фикстур, 2 ref-output'а на каждую |
| Promot improvement vs baseline | +15% «hit rate» по структурным критериям (наличие секций «Тебе:» / «Поручения» / «Контекст последнего письма») |
| Бенчмарк latency MLX | зафиксирован в `docs/PERFORMANCE.md` по 3 моделям × 3 устройствам |
| Style-learning S2-1 запущен | ≤ 8 недель |
| % писем «требующих ответа» с auto-draft (KPI из AI_TOOLS_LANDSCAPE §7) | ≥ 70% |

### Red flags

- «Зачем мерить — посмотрите, ответ же хороший».
- Никогда не запускал MLX/Ollama локально.
- Любит навешать на промпт «делай шаг за шагом», не проверив,
  улучшает это или ухудшает.
- Не понимает почему мы делаем `default-groups = []` в `pyproject.toml`
  (= не понимает реалии корп-прокси).

---

## 4. Frontend Engineer (Vanilla JS + SCSS)

**Грейд:** Middle+ / Senior. **FTE:** 0.5-1.0.
**Подчинение:** Tech Lead.

Развивает WebUI без фреймворков. Pa-clean намеренно использует vanilla
JS / SCSS — это упрощает деплой и сборку. Поэтому ищем не React-разраба
«через раз», а senior-frontend'а, который **уверенно** пишет без
toolchain'а.

### Обязанности

- Owns `webui/frontend/js/*`: inbox.js (~2000 строк), today.js, chat.js,
  rules.js, search.js, vault.js, projects.js, reports.js, settings.js,
  api.js.
- Owns `webui/frontend/styles/components/*.scss`.
- Развивает новые компоненты:
  - Inbox: thread grouping (Беседы), time buckets, GTD-rule chips —
    уже сделано, дальше polish и accessibility.
  - Quick search box, mark-all-read, delegate modal, draft inline-panel.
  - Reading-mode для email-body (`_emailToHtml`).
- Поддерживает cache-busting (`?v=YYYYMMDDhhmmss`) и build-pipeline
  (`webui/scripts/bundle-js.js`).
- Пишет JS-логические тесты (node-harness style, как в существующих
  smoke-checks `_emailToHtml`, `_timeBucket`, `_ruleChipsHtml`).
- A/B-тестирует новые UI-паттерны (Apple Mail × Superhuman × корпоративный стиль).
- Знает design-токены `webui/frontend/styles/variables.scss` (корпоративная палитра).

### Must-have hard skills

- Vanilla JS Senior (ES2020+, Promises, Fetch API, DOM, Custom Events,
  Intl, MutationObserver). Может писать SPA без React'а.
- SCSS / CSS3 (Flexbox, Grid, custom properties, `backdrop-filter`,
  `:has`, `@supports`).
- Понимание browser quirks (Webkit / Gecko / Blink).
- Accessibility (ARIA, keyboard navigation — у нас есть hotkeys J/K/R/D
  и нужно их расширять).
- HTML5 (semantic — мы используем `<details>`, `<summary>`, `<dialog>`
  где уместно).

### Must-have soft skills

- Любит решать UX-проблемы кодом, а не наоборот.
- Готов читать `webui/index.html` (1100+ строк inline'а — намеренно)
  без жалоб.
- Понимает разницу «как в Apple Mail» vs «как в Outlook» — наша референсная
  философия близка к Apple Mail / Superhuman.

### Nice to have

- Опыт работы с MS Office / Outlook web app — знает что users привыкли.
- Базовое знание Web Components — мы потенциально будем мигрировать
  компоненты `today__meeting-card`, `ib-delegate-modal` в WC.
- TypeScript — мы пока не используем, но планируем (Web Components без
  TS — больно).

### KPI

| Метрика | Цель |
|---|---|
| Среднее время отклика UI (load → interactive) на Today / Inbox | ≤ 200 мс |
| Lighthouse Performance Score | ≥ 90 |
| Lighthouse Accessibility | ≥ 90 |
| Покрытие смоук-тестами рендеринг-помощников (_renderItemHtml, _emailToHtml, _ruleChipsHtml) | 100% (node-harness) |
| Cache-buster bump на каждое значимое изменение | 100% |

### Red flags

- «Давайте сразу перепишем на React / Vue / Svelte».
- Не открывал DevTools → Network с галкой Disable Cache.
- Не знает что такое `:has()` или `backdrop-filter` (наш design heavily
  использует оба).
- Считает что vanilla JS — «легаси». (Vanilla JS — это **выбор**, не
  ограничение.)

---

## 5. QA Engineer

**Грейд:** Middle (Senior — bonus). **FTE:** 0.5.
**Подчинение:** Tech Lead (или PM).

### Обязанности

- Owns [`docs/UAT.md`](./UAT.md) — ручной чеклист пользовательской
  приёмки. Прогоняет ПОЛНОСТЬЮ перед каждым релизом.
- Расширяет `docs/UAT.md` после каждой новой фичи: добавляет
  тест-сценарии вместе с PR.
- Поддерживает `tests/unit/**/*.py` — может прочитать существующий
  pytest и расширить (не обязательно писать с нуля).
- Прогон scenario-тестов на Mac (`pytest -m "scenario and not live"` —
  Mac не нужен; `-m live` — нужен Mac с TCC).
- Bug triage: воспроизводит репорты, классифицирует severity, привязывает
  к существующим F-areas из `FUNCTIONALITY_MAP.md`.
- Performance baselines: time-to-first-token для chat, latency Mail
  sync, размер vault после N синков. Фиксирует в `docs/PERFORMANCE.md`.

### Must-have hard skills

- Pytest (читать + расширять).
- macOS как dev-окружение, понимает TCC permissions.
- Базовый Python (Middle).
- Git (создание веток, PR'ов).

### Must-have soft skills

- Системный, методичный — не пропустит шаг в чеклисте.
- Любит воспроизводить баги, описывает шаги.
- Не «соглашается» с разработкой — push back, если фикс не до конца.

### Nice to have

- Playwright / Selenium для WebUI-автотестов (есть в roadmap).
- AppleScript для написания smoke-сценариев.
- Опыт с большими test-suite'ами (700+ тестов как у нас).

### KPI

| Метрика | Цель |
|---|---|
| % фич, прошедших UAT перед production-релизом | 100% |
| Regression-bugs (= баги в уже-протестированной фиче) | ≤ 1 / релиз |
| Время прогона UAT-чеклиста | ≤ 1 день |
| % UAT-сценариев со скриншотами в баг-репортах | ≥ 80% |

### Red flags

- «Тестировал, всё ок» без конкретики.
- Никогда не открывал терминал.
- Не воспроизводил bug перед закрытием issue («у разраба заработало —
  закрываем»).

---

## 6. Product Manager

**Грейд:** Middle+ / Senior. **FTE:** 0.5-1.0.
**Подчинение:** Sponsor / Engineering Manager.

### Обязанности

- Owns roadmap: какой quick win из `AI_TOOLS_LANDSCAPE_2026.md` идёт
  следующим, в каком порядке.
- Eats own dog food: использует pa-clean каждый день. Без этого PM
  не понимает product.
- Регулярные интервью с pilot-пользователями (5-10 человек в первой
  волне), фиксирует feedback в issue tracker.
- Прайoritизирует backlog (RICE / WSJF), еженедельно ревьюит с TL.
- Спецификации новых фич: «Что — Зачем — Acceptance criteria». Формат
  достаточный, чтобы dev мог взять и сделать без N итераций уточнений.
- Стейкхолдер-коммуникация: статус для Sponsor, согласования с InfoSec /
  Legal / IT.
- Сравнительный анализ с конкурентами (`AI_TOOLS_LANDSCAPE_2026.md` §2)
  — обновляет каждые 3-6 мес.

### Must-have hard skills

- Опыт работы PM продуктом для внутренних пользователей (≥ 2 года).
- Базовое понимание AI / LLM — на уровне «я знаю что промпт это
  серьёзно, не игрушка».
- Умеет писать спеки: user story + flow + edge cases + AC + open questions.
- Знакомство с метриками продукта (DAU/WAU, retention, NPS).

### Must-have soft skills

- Балансирует «делать как просит пользователь» и «делать как правильно».
- Не боится говорить «нет» некритичным запросам.
- Может за 5 минут понять, является ли запрос частью existing feature
  или новой work area (читает `FUNCTIONALITY_MAP.md`).

### Nice to have

- Опыт работы с email-клиентами как продуктом.
- Знание Apple ecosystem (macOS, Mail, Calendar, iCloud quirks).

### KPI

| Метрика | Цель |
|---|---|
| % фич с написанной спецификацией перед dev'ом | 100% |
| Pilot-DAU / Total-pilot ratio | ≥ 60% |
| User-NPS после квартала пилота | ≥ 30 |
| Quarterly business review (QBR) с stakeholders | каждый квартал |

### Red flags

- Не использует продукт сам.
- Спеки в виде Slack-сообщений на 3 строки.
- Конфликтует с TL по архитектурным вопросам (зона TL).

---

## 7. DevOps / Release Engineer

**Грейд:** Senior. **FTE:** 0.5-1.0. **Подчинение:** Tech Lead.

Появляется когда пилот переходит из «3 машины разработчиков» в «50+
рабочих машин». До этого совмещается с TL / Backend.

### Обязанности

- Owns [`make.sh`](../make.sh), [`fix_env.sh`](../fix_env.sh),
  [`docs/REMOTE_SETUP.md`](./REMOTE_SETUP.md), `pyproject.toml`
  (зависимости, `[tool.uv]`, `default-groups`).
- Внутренний mirror PyPI для блокированных пакетов
  (`coverage`, `mypy`, ...).
- Codesigning, notarization Python-bundle'а для macOS (Apple Developer
  Certificate).
- Сборка self-updating bundle: обновление pa-clean без терминала на
  машине пользователя.
- CI/CD pipeline (если будет) — workflow_dispatch для тестов, лог-сбор,
  release-pipeline.
- MDM-интеграция (Jamf): автоустановка pa-clean на новые корп. Mac'и,
  принудительная TCC-allowlist.

### Must-have hard skills

- Bash / Zsh, понимает `set -e`, exit-коды, signal-handling.
- macOS deploy: codesign, notarization, Gatekeeper, TCC, MDM (Jamf).
- Python packaging: uv / pip / Hatch, virtual envs.
- Корпоративные прокси (mitm, mTLS, любой корп-стек).

### KPI

- Время полной сборки `make.sh` ≤ 5 мин (на стандартной корп-машине).
- Сбой автоустановки на новых корп-Mac'ах ≤ 5%.
- 100% релизов имеют валидный codesign.

---

## 8. MLOps Engineer

**Грейд:** Middle+ / Senior. **FTE:** 0.5. **Подчинение:** AI/ML
Engineer / Tech Lead.

Появляется когда мы хотим стабильно обновлять MLX-модели и пользоваться
несколькими моделями в параллель (default + premium tier).

### Обязанности

- Internal mirror HuggingFace для `mlx-community/*` — модели должны
  скачиваться через корп-инфру, не через `huggingface.co` напрямую.
- Подписание моделей (sha256), distribution через MDM или ad-hoc
  pkg-installer.
- Lifecycle: какие модели включаются в default, как тестируется
  evaluation после обновления.
- Performance baselines (`docs/PERFORMANCE.md`): latency / throughput
  по модели × устройство.

### Must-have hard skills

- HuggingFace Hub: API, datasets, accelerate.
- Python packaging.
- Bash / shell scripting.
- Опыт с инференс-фреймворками (MLX, Ollama, vLLM).

---

## 9. UX / UI Designer

**Грейд:** Middle+ / Senior. **FTE:** 0.5. **Подчинение:** PM (или TL).

### Обязанности

- Wireframes / mockups для новых вкладок и компонентов.
- Дизайн новых паттернов: Quick-create event, Search palette (Cmd+K),
  Delegate-flow в Inbox.
- A/B-варианты для критичных компонентов (Inbox-row, Today-dashboard).
- Поддерживает design-токены `webui/frontend/styles/variables.scss`
  (корпоративная палитра).
- Accessibility-аудит каждые 3 месяца.

### Must-have hard skills

- Figma (Senior — autolayout, variables, components).
- Системы дизайна (Atomic Design / Material).
- Знание корпоративного brand-guide (если корп-проект).

### KPI

- Time-to-first-action для новых пользователей ≤ 2 мин (без onboarding).
- Lighthouse Accessibility ≥ 95.

---

## 10. L2 Support Engineer

**Грейд:** Middle. **FTE:** 1.0 (при > 100 пользователей).
**Подчинение:** Engineering Manager / Support Lead.

### Обязанности

- Принимает escalation от L1 / пользователей.
- Owns runbook'и для типичных проблем:
  - TCC denied → walk-through System Settings
  - MLX не загружается → проверка `PA_MLX_MODEL_PATH` + arch
  - Mail-sync пустой → проверка прав + Mail.app running state
  - Vault corrupted → recovery из последнего sync
- Создаёт диагностический bundle (`pa diag` команда — нужно добавить):
  `pa check` output, `~/Library/Logs/*pa-clean*`, hashes vault md-файлов.
- Эскалирует к dev только то, что не закрывается runbook'ом.

### Must-have hard skills

- macOS Senior уровня поддержки (TCC, Activity Monitor, Console.app, MDM).
- Базовый Python + Shell — может читать stack trace.
- Empathy.

### KPI

- Среднее время закрытия L2-тикета ≤ 4 ч.
- % тикетов, закрытых без эскалации к dev ≥ 70%.

---

## 11. Technical Writer

**Грейд:** Middle. **FTE:** 0.5-1.0. **Подчинение:** PM / TL.

### Обязанности

- Owns пользовательскую документацию: [`README.md`](../README.md),
  [`docs/USER_GUIDE.md`](./USER_GUIDE.md), [`RULES.md`](../RULES.md),
  [`INTEGRATIONS.md`](../INTEGRATIONS.md), [`docs/REMOTE_SETUP.md`](./REMOTE_SETUP.md).
- Скриншоты / GIF / видео для onboarding и сложных flow'ов
  (Делегирование, Reply-All).
- Knowledge-base для пользователей (FAQ, troubleshooting).
- Release notes для каждого выпуска.

### Must-have hard skills

- Markdown свободно.
- Скриншот-tooling (CleanShot X / Snagit / встроенный macOS).
- Базовый Git.

### Must-have soft skills

- Может объяснить технику нетехническому пользователю.
- Любит уточнять у dev: «как это РЕАЛЬНО работает, чтоб не наврать в
  доке».

---

## 12. Internal Champion / Community Manager

**Грейд:** не-инженерная роль. **FTE:** 0.25-0.5 на департамент.
**Подчинение:** PM.

### Обязанности

- Один champion на 30-50 пользователей в одном департаменте.
- Локальный onboarding (5-10 мин видеозвонок) каждому новому пользователю.
- Собирает feature-requests в шаблон-форму, передаёт PM.
- Модерирует чат / форум сообщества пользователей.
- Защищает проект внутри своего департамента от FUD («это утечёт во
  внешнее облако», «нельзя ставить из непроверенного источника»).

### Must-have skills

- Soft skills прежде всего.
- Сам активный пользователь pa-clean.
- Знает свой департамент: кто что делает, какие у них pain-points.

---

## 13. Engineering Manager

**Грейд:** Senior+. **FTE:** 1.0. **Подчинение:** Sponsor / VP Engineering.

Появляется когда команда выросла > 6 человек и TL не может тянуть и
архитектуру, и менеджмент.

### Обязанности

- Hiring + onboarding + performance review.
- Спринты, ретроспективы, OKR'ы (если корп-практика).
- Защита проекта по бюджету.
- Отделяет «горящее» от «важного» — освобождает TL для тех решений.

### KPI

- Retention команды ≥ 90% / год.
- Velocity (стабильность скорости поставки) ± 20% квартал-к-кварталу.
- Engagement-score команды ≥ 4 / 5.

---

## 14. Site Reliability Engineer (SRE)

**Грейд:** Senior. **FTE:** 0.5-1.0. **Подчинение:** EM / TL.

Появляется если переходим от «pa-clean на каждой машине пользователя»
к «общий MLX-инференс через корп-сервер» (например, для Intel-Mac
пользователей или для тяжёлых моделей).

### Обязанности

- Мониторинг production (если есть сервер): availability, latency,
  ошибки.
- Capacity planning для MLX-инференса.
- Incident-response в production (только когда есть прод-инфра).
- Postmortem'ы.

### KPI

- SLO ≥ 99.5% availability.
- MTTR (mean time to repair) ≤ 1 ч.

---

## 15. Security / Compliance Engineer

**Грейд:** Senior. **FTE:** 0.5. **Подчинение:** CISO / InfoSec.

### Обязанности

- Регулярный security-audit pa-clean (раз в квартал).
- Согласование новых фич с persistence (vault, tool_prompts.json,
  delegate_contacts).
- Проверка обновлений зависимостей на CVE.
- Compliance: 152-ФЗ (РФ), GDPR, внутренняя корп-политика.
- Threat-modeling для новых интеграций (mail/calendar/MLX).

### KPI

- 0 критичных CVE в `uv.lock` старше 30 дней.
- 100% новых фич с persistence прошли security-review.
- Регулярный audit-report квартально.

---

## Минимальный стартовый состав

Для запуска пилота **достаточно 3 человек** при сильных совмещениях:

| Человек | Роли |
|---|---|
| Tech Lead (Senior+) | TL + Backend Senior + DevOps |
| AI/ML Engineer (Middle+) | ML + MLOps + Tech Writer |
| Frontend (Senior) | FE + UX (на уровне «делает дизайн в код, без figma') + QA |

PM-обязанности — раскидать между TL и AI/ML, либо взять part-time
PM (0.5 FTE с другого проекта).

После 2-3 месяцев пилота — нанимать строго в этом порядке:
1. **AI/ML Engineer full-time** (если стартовали с 0.5)
2. **QA full-time**
3. **Product Manager full-time**
4. **L2 Support** (когда > 50 пользователей)
5. **Tech Writer**
6. **DevOps / MLOps**

Уровень риска при минимальном составе: высокий, но управляемый — много
автоматизации (640+ тестов, типизация, ruff/mypy gating) компенсирует
малое QA-плечо. Без AI/ML Engineer'а — критический риск: будем шипить
prompts без evaluation.

---

## Связанные документы

- [AGENTS.md](../AGENTS.md) — что такое pa-clean (для onboarding нового
  сотрудника).
- [ARCHITECTURE.md](../ARCHITECTURE.md) — слои и потоки данных.
- [FUNCTIONALITY_MAP.md](./FUNCTIONALITY_MAP.md) — F1-F27 текущий
  функционал + бэклог.
- [AI_TOOLS_LANDSCAPE_2026.md](./AI_TOOLS_LANDSCAPE_2026.md) —
  конкуренты + roadmap 1-3 месяца.
- [TESTING.md](../TESTING.md) — для QA-инженера на старт.
- [INTEGRATIONS.md](../INTEGRATIONS.md) — для Backend / SRE / Security.
- [docs/REMOTE_SETUP.md](./REMOTE_SETUP.md) — для DevOps / L2.
- [docs/UAT.md](./UAT.md) — для QA.
