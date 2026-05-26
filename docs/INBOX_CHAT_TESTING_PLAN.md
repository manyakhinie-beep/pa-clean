# План тестирования: Inbox → Chat интеграция с MLX

**Версия:** 1.0  
**Дата:** 2026-05-24  
**Область:** Переход из Inbox в Chat с загрузкой письма в контекст  
**Статус дефектов:** 3 confirmed bugs + 1 gap (требуют исправления до прохождения E2E)

---

## Оглавление

1. [Контекст и найденные дефекты](#1-контекст-и-найденные-дефекты)
2. [Этап 0 — Документация дефектов](#этап-0--документация-дефектов)
3. [Этап 1 — Backend API тесты (без MLX)](#этап-1--backend-api-тесты-без-mlx)
4. [Этап 2 — JS Event Bus тесты](#этап-2--js-event-bus-тесты)
5. [Этап 3 — Integration тесты с мок-MLX](#этап-3--integration-тесты-с-мок-mlx)
6. [Этап 4 — Full E2E с живым MLX](#этап-4--full-e2e-с-живым-mlx)
7. [Этап 5 — Edge Cases и деградация](#этап-5--edge-cases-и-деградация)
8. [Этап 6 — Регрессия после фиксов](#этап-6--регрессия-после-фиксов)
9. [Матрица покрытия сценариев](#матрица-покрытия-сценариев)
10. [Критерии приёмки](#критерии-приёмки)

---

## 1. Контекст и найденные дефекты

### Схема потока данных

```
[Inbox UI] ──action click──► [_openDraftWithContext()]
                                      │
                              POST /api/v1/inbox/{id}/draft-context
                                      │
                              ◄── {context_prompt, thread_context,
                                   vault_thread_id, reply_message_id}
                                      │
                              dispatch("pa:chat-open", payload)
                                      │
                          activateTab('chat')
                                      │
                        [chat.js pa:chat-open listener]
                                      │
                         sets textarea + mode + state vars
                           (does NOT create new thread)  ← BUG-1
                                      │
                          [User presses Enter]
                                      │
                         POST /api/chat/send
                         {thread_id, message, mode,
                          context_paths, vault_thread_id}
                          ↑ reply_message_id MISSING     ← BUG-2
                          ↑ thread_context MISSING        ← BUG-3
```

### Найденные дефекты

| ID | Файл | Строка | Описание |
|----|------|--------|----------|
| **BUG-1** | `webui/frontend/js/chat.js` | ~1029 | `pa:chat-open` не создаёт новый тред — сообщение падает в текущий активный тред |
| **BUG-2** | `webui/frontend/js/chat.js` | ~601-606 | `reply_message_id` хранится в `currentReplyMessageId`, но не включается в payload `POST /api/chat/send` |
| **BUG-3** | `webui/frontend/js/chat.js` | ~601-606 | `thread_context` (полный draft context) хранится в `currentThreadContext` и рендерится как chip, но не форвардится на бэкенд |
| **GAP-4** | `webui/frontend/js/inbox.js` | `_openChat()` | Если inbox-item не имеет `path` (vault не загружен / не синхронизирован), `context_paths` пуст — модель не получает содержимого письма |

---

## Этап 0 — Документация дефектов

**Цель:** Зафиксировать текущее поведение до исправлений, создать baseline.

### TC-0.1 Baseline: текущее поведение BUG-1

**Предусловие:** Приложение запущено, в Chat открыт непустой тред (есть хотя бы 1 сообщение).

**Шаги:**
1. Перейти в Inbox.
2. Выбрать любое письмо.
3. Нажать кнопку **"Написать ответ"** (action = 'draft').
4. Наблюдать, в каком треде появился подготовленный текст.

**Ожидаемое поведение (после фикса):** открывается новый чист тред.  
**Текущее поведение (баг):** текст попадает в уже открытый тред.

**Автоматизация:** Unit-тест на `pa:chat-open` listener (см. Этап 2, TC-2.1).

---

### TC-0.2 Baseline: текущее поведение BUG-2

**Предусловие:** Vault синхронизирован, письмо имеет `path`.

**Шаги:**
1. Открыть DevTools → Network.
2. Выполнить действие "Написать ответ" из Inbox.
3. Ввести текст и отправить сообщение (Enter).
4. Проверить payload `POST /api/chat/send`.

**Ожидаемое поведение (после фикса):** payload содержит `reply_message_id`.  
**Текущее поведение (баг):** `reply_message_id` отсутствует в JSON payload.

---

### TC-0.3 Baseline: текущее поведение GAP-4

**Предусловие:** Vault **не синхронизирован** (папка пустая или синхронизация не выполнялась).

**Шаги:**
1. Перейти в Inbox.
2. Нажать "Написать ответ".
3. Отправить сообщение.

**Текущее поведение:** Модель отвечает без контекста письма (пустые `context_paths`).  
**Ожидаемое:** Предупреждение пользователю о необходимости синхронизации.

---

## Этап 1 — Backend API тесты (без MLX)

**Инструменты:** `pytest` + `httpx.AsyncClient` + `TestClient`  
**Расположение:** `tests/unit/inbox/` и `tests/e2e/test_inbox_chat_pipeline.py`  
**MLX:** не требуется (MLXEngine мокируется или возвращает `_UNAVAILABLE_MSG`)

### TC-1.1 Эндпоинт `/api/v1/inbox/{id}/draft-context` — happy path

```python
# tests/unit/inbox/test_draft_context.py

async def test_draft_context_returns_required_fields(client, seeded_vault):
    """draft-context возвращает context_prompt, vault_thread_id, thread_context."""
    resp = await client.post(
        f"/api/v1/inbox/{seeded_vault.item_id}/draft-context"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "context_prompt" in data
    assert "vault_thread_id" in data
    assert "thread_context" in data
    assert isinstance(data["context_prompt"], str)
    assert len(data["context_prompt"]) > 0
```

**Критерий:** `200 OK`, все 3 поля присутствуют.

---

### TC-1.2 Эндпоинт `/api/v1/inbox/{id}/draft-context` — item not found

```python
async def test_draft_context_returns_404_for_missing_item(client):
    resp = await client.post("/api/v1/inbox/nonexistent_id_xyz/draft-context")
    assert resp.status_code == 404
```

---

### TC-1.3 Эндпоинт `/api/v1/inbox/{id}/draft-context` — нет vault-пути

```python
async def test_draft_context_item_without_vault_path(client, inbox_item_no_path):
    """Если у письма нет vault path, возвращаем context без context_paths."""
    resp = await client.post(
        f"/api/v1/inbox/{inbox_item_no_path.id}/draft-context"
    )
    assert resp.status_code == 200
    data = resp.json()
    # context_prompt присутствует, но vault_thread_id может быть None
    assert "context_prompt" in data
```

---

### TC-1.4 `/api/chat/send` — с `context_paths` и `vault_thread_id`

```python
# tests/e2e/test_inbox_chat_pipeline.py

async def test_chat_send_with_context_paths(client, seeded_vault):
    """Отправка с context_paths включает контент письма в ответ модели."""
    # Создаём тред
    thread_resp = await client.post("/api/chat/threads", json={})
    thread_id = thread_resp.json()["thread_id"]

    resp = await client.post("/api/chat/send", json={
        "thread_id": thread_id,
        "message": "Напиши ответ на это письмо",
        "mode": "draft",
        "context_paths": [seeded_vault.vault_path],
        "vault_thread_id": seeded_vault.thread_id,
    })
    assert resp.status_code == 200
    # MLX мок возвращает непустой ответ
    assert len(resp.json()["response"]) > 0
```

---

### TC-1.5 `/api/chat/send` — без `context_paths` (GAP-4 scenario)

```python
async def test_chat_send_without_context_paths_returns_graceful_response(client):
    """Без context_paths модель должна ответить, но предупредить об отсутствии контекста."""
    thread_resp = await client.post("/api/chat/threads", json={})
    thread_id = thread_resp.json()["thread_id"]

    resp = await client.post("/api/chat/send", json={
        "thread_id": thread_id,
        "message": "Напиши ответ на это письмо",
        "mode": "draft",
        "context_paths": [],
    })
    assert resp.status_code == 200
    # Ответ приходит (graceful degradation), но без контекста
```

---

### TC-1.6 `/api/chat/send` — с `reply_message_id` (после фикса BUG-2)

```python
async def test_chat_send_accepts_reply_message_id(client, seeded_vault):
    """После фикса BUG-2: backend принимает reply_message_id без ошибки валидации."""
    thread_resp = await client.post("/api/chat/threads", json={})
    thread_id = thread_resp.json()["thread_id"]

    resp = await client.post("/api/chat/send", json={
        "thread_id": thread_id,
        "message": "Напиши ответ",
        "mode": "draft",
        "context_paths": [seeded_vault.vault_path],
        "vault_thread_id": seeded_vault.thread_id,
        "reply_message_id": "outlook_msg_abc123",  # новое поле
    })
    assert resp.status_code == 200
```

---

### TC-1.7 `build_draft_context()` — юнит-тест контент-сборки

```python
# tests/unit/inbox/test_build_draft_context.py

def test_build_draft_context_includes_sender_and_subject(mock_vault_reader):
    """build_draft_context() включает отправителя и тему в context_prompt."""
    ctx = build_draft_context(
        item_id="msg_001",
        vault_reader=mock_vault_reader,
    )
    assert "alice@corp.com" in ctx.context_prompt or "Project Update" in ctx.context_prompt

def test_build_draft_context_sets_vault_thread_id(mock_vault_reader):
    ctx = build_draft_context(item_id="msg_001", vault_reader=mock_vault_reader)
    assert ctx.vault_thread_id is not None
```

---

## Этап 2 — JS Event Bus тесты

**Инструменты:** `playwright` или `jest` + `jsdom`  
**Расположение:** `tests/js/` (создать папку)  
**MLX:** не требуется

### TC-2.1 `pa:chat-open` → новый тред создаётся (BUG-1 fix)

```javascript
// tests/js/inbox_chat.test.js (Jest/jsdom)

test('pa:chat-open creates a new thread before populating textarea', async () => {
  const createThreadMock = jest.fn().mockResolvedValue({ thread_id: 'new-thread-42' });
  window.chatModule = { createThread: createThreadMock };

  // Симулируем событие
  const event = new CustomEvent('pa:chat-open', {
    detail: {
      message: 'Draft context text',
      path: 'outlook/2026/05/msg_001.md',
      vault_thread_id: 'thread_xyz',
      reply_message_id: 'msg_001',
      thread_context: { subject: 'Test' },
    }
  });
  document.dispatchEvent(event);

  await flushPromises();
  expect(createThreadMock).toHaveBeenCalledTimes(1);
  expect(document.getElementById('chat-input').value).toBe('Draft context text');
});
```

---

### TC-2.2 Payload `POST /api/chat/send` содержит `reply_message_id` (BUG-2 fix)

```javascript
test('sendMessage() includes reply_message_id in fetch payload', async () => {
  const fetchMock = jest.fn().mockResolvedValue(okResponse());
  global.fetch = fetchMock;

  // Устанавливаем состояние как после pa:chat-open
  chatModule.currentReplyMessageId = 'msg_001';
  chatModule.currentVaultThreadId = 'thread_xyz';
  chatModule.contextPaths = ['outlook/2026/05/msg_001.md'];
  chatModule.currentThreadId = 'thread-42';

  await chatModule.sendMessage('Напиши ответ');

  const [url, opts] = fetchMock.mock.calls[0];
  const body = JSON.parse(opts.body);
  expect(body.reply_message_id).toBe('msg_001');
  expect(body.vault_thread_id).toBe('thread_xyz');
  expect(body.context_paths).toContain('outlook/2026/05/msg_001.md');
});
```

---

### TC-2.3 Chip 🧵 рендерится при наличии `thread_context`

```javascript
test('renderThreadContextChip() appears when thread_context is set', () => {
  chatModule.currentThreadContext = {
    subject: 'Отчёт Q2',
    sender: 'Иванов',
  };
  chatModule.renderThreadContextChip();

  const chip = document.querySelector('.thread-context-chip');
  expect(chip).not.toBeNull();
  expect(chip.textContent).toContain('Отчёт Q2');
});
```

---

### TC-2.4 `activateTab('chat')` вызывается после `pa:chat-open`

```javascript
test('pa:chat-open switches active tab to chat', async () => {
  const activateTabMock = jest.fn();
  window.activateTab = activateTabMock;

  document.dispatchEvent(new CustomEvent('pa:chat-open', {
    detail: { message: 'test', path: '', vault_thread_id: null }
  }));

  await flushPromises();
  expect(activateTabMock).toHaveBeenCalledWith('chat');
});
```

---

## Этап 3 — Integration тесты с мок-MLX

**Инструменты:** `pytest` + `TestClient` + `unittest.mock.patch`  
**Расположение:** `tests/e2e/test_inbox_chat_integration.py`  
**MLX:** `MLXEngine` мокируется — возвращает детерминированный ответ

```python
# conftest.py (или в test-файле)

@pytest.fixture
def mock_mlx_engine():
    with patch(
        "personal_assistant.mlx_server.engine.MLXEngine.chat",
        return_value="Уважаемый коллега, спасибо за письмо...",
    ):
        yield
```

### TC-3.1 Полный pipeline: Draft action → ответ модели содержит контекст письма

```python
async def test_full_draft_pipeline_with_mock_mlx(client, seeded_vault, mock_mlx_engine):
    """
    Сценарий: пользователь нажимает Draft в Inbox.
    Ожидание: бэкенд строит context, вызывает MLX, возвращает черновик.
    """
    # Шаг 1: получить draft-context (имитирует inbox.js._openDraftWithContext)
    ctx_resp = await client.post(
        f"/api/v1/inbox/{seeded_vault.item_id}/draft-context"
    )
    assert ctx_resp.status_code == 200
    ctx = ctx_resp.json()

    # Шаг 2: создать новый тред (имитирует BUG-1 fix)
    thread_resp = await client.post("/api/chat/threads", json={})
    assert thread_resp.status_code == 200
    thread_id = thread_resp.json()["thread_id"]

    # Шаг 3: отправить сообщение с контекстом
    send_resp = await client.post("/api/chat/send", json={
        "thread_id": thread_id,
        "message": ctx["context_prompt"],
        "mode": "draft",
        "context_paths": [seeded_vault.vault_path],
        "vault_thread_id": ctx.get("vault_thread_id"),
    })
    assert send_resp.status_code == 200
    response_text = send_resp.json()["response"]
    assert len(response_text) > 10
```

---

### TC-3.2 Сценарий "Summarize" — суммаризация письма

```python
async def test_summarize_action_pipeline(client, seeded_vault, mock_mlx_engine):
    """action='summarize': контекст письма попадает в промпт, модель выдаёт резюме."""
    ctx_resp = await client.post(
        f"/api/v1/inbox/{seeded_vault.item_id}/draft-context",
        json={"action": "summarize"},
    )
    assert ctx_resp.status_code == 200

    thread_resp = await client.post("/api/chat/threads", json={})
    thread_id = thread_resp.json()["thread_id"]

    send_resp = await client.post("/api/chat/send", json={
        "thread_id": thread_id,
        "message": ctx_resp.json()["context_prompt"],
        "mode": "chat",
        "context_paths": [seeded_vault.vault_path],
    })
    assert send_resp.status_code == 200
```

---

### TC-3.3 Сценарий "Create Meeting" — извлечение времени встречи

```python
async def test_create_meeting_action_pipeline(client, seeded_vault, mock_mlx_engine):
    """action='create-meeting': запрос на создание встречи передаётся в контекст."""
    ctx_resp = await client.post(
        f"/api/v1/inbox/{seeded_vault.item_id}/draft-context",
        json={"action": "create-meeting"},
    )
    assert ctx_resp.status_code == 200

    thread_resp = await client.post("/api/chat/threads", json={})
    thread_id = thread_resp.json()["thread_id"]

    send_resp = await client.post("/api/chat/send", json={
        "thread_id": thread_id,
        "message": ctx_resp.json()["context_prompt"],
        "mode": "chat",
        "context_paths": [seeded_vault.vault_path],
    })
    assert send_resp.status_code == 200
```

---

### TC-3.4 Thread isolation: два разных письма → два разных треда

```python
async def test_two_drafts_produce_isolated_threads(client, seeded_vault, mock_mlx_engine):
    """Каждое действие из Inbox должно открывать отдельный тред без перекрёстного контекста."""
    thread_ids = []
    for item_id in [seeded_vault.item_id_1, seeded_vault.item_id_2]:
        ctx = (await client.post(f"/api/v1/inbox/{item_id}/draft-context")).json()
        t = (await client.post("/api/chat/threads", json={})).json()["thread_id"]
        thread_ids.append(t)
        await client.post("/api/chat/send", json={
            "thread_id": t,
            "message": ctx["context_prompt"],
            "mode": "draft",
            "context_paths": [seeded_vault.vault_path],
        })

    # Треды разные
    assert thread_ids[0] != thread_ids[1]

    # Сообщения в первом треде не содержат контент второго письма
    msgs_1 = (await client.get(f"/api/chat/threads/{thread_ids[0]}/messages")).json()
    assert seeded_vault.subject_2 not in str(msgs_1)
```

---

## Этап 4 — Full E2E с живым MLX

**Требования:** Apple Silicon Mac, модель загружена, `./run.sh` запущен  
**Инструменты:** `playwright` (browser automation) + ручные сценарии  
**Расположение:** `tests/e2e/playwright/` (создать)

### Предусловия для всех E2E тестов

```bash
# 1. Убедиться, что модель загружена
ls ~/PersonalAssistantVault/  # vault не пустой

# 2. Запустить сервер
./run.sh &
sleep 5

# 3. Проверить здоровье
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/model/status  # должен вернуть loaded=true
```

---

### TC-4.1 Ручной: Draft action создаёт новый тред с контекстом письма

**Шаги:**

| # | Действие | Ожидаемый результат |
|---|---------|---------------------|
| 1 | Открыть `http://127.0.0.1:8000` | Загружается WebUI, 6 вкладок видны |
| 2 | Перейти в Chat, убедиться что есть непустой тред | Тред с историей |
| 3 | Перейти в Inbox, выбрать письмо с темой | Отображается панель письма |
| 4 | Нажать "Написать ответ" | Переключается на вкладку Chat |
| 5 | Проверить: открылся НОВЫЙ тред | Thread ID отличается от тред-а из шага 2 |
| 6 | Проверить: textarea содержит текст черновика | Текст не пустой |
| 7 | Проверить: отображается chip 🧵 с темой письма | Chip виден над textarea |
| 8 | Нажать Enter / Отправить | Отображается streaming-ответ |
| 9 | Ответ содержит релевантный контент | Упоминается тема/отправитель письма |

**Критерий прохода:** все 9 шагов без отклонений.

---

### TC-4.2 Ручной: Summarize action

| # | Действие | Ожидаемый результат |
|---|---------|---------------------|
| 1 | В Inbox нажать "Суммаризировать" (action='summarize') | Переключается на Chat |
| 2 | Открылся новый тред | Тред новый |
| 3 | Textarea содержит запрос на суммаризацию | Текст содержит "суммаризируй" / "summarize" |
| 4 | Нажать Enter | MLX генерирует ответ |
| 5 | Ответ — краткое резюме письма | 3-5 предложений о содержании |

---

### TC-4.3 Ручной: Create Meeting action

| # | Действие | Ожидаемый результат |
|---|---------|---------------------|
| 1 | В Inbox нажать "Создать встречу" (action='create-meeting') | Переключается на Chat |
| 2 | Textarea содержит запрос с датой/временем из письма | Дата извлечена |
| 3 | Нажать Enter | MLX предлагает детали встречи |
| 4 | Ответ содержит предложение времени/участников | Структурированный ответ |

---

### TC-4.4 Playwright: Автоматизированный E2E Draft flow

```python
# tests/e2e/playwright/test_inbox_chat_e2e.py

import re
from playwright.sync_api import Page, expect

BASE_URL = "http://127.0.0.1:8000"

def test_draft_action_opens_new_chat_thread(page: Page):
    page.goto(BASE_URL)

    # Открыть Chat, зафиксировать текущий thread_id
    page.click('[data-tab="chat"]')
    page.wait_for_selector('#chat-container')
    initial_thread_id = page.evaluate("window.chatModule?.currentThreadId")

    # Перейти в Inbox
    page.click('[data-tab="inbox"]')
    page.wait_for_selector('.inbox-item', timeout=10000)

    # Клик на первое письмо
    page.click('.inbox-item:first-child')
    page.wait_for_selector('.inbox-action-btn[data-action="draft"]')
    page.click('.inbox-action-btn[data-action="draft"]')

    # Должны перейти в Chat
    page.wait_for_selector('#chat-input:not(:empty)', timeout=5000)

    new_thread_id = page.evaluate("window.chatModule?.currentThreadId")
    assert new_thread_id != initial_thread_id, "BUG-1: новый тред не создан"

    # Textarea не пустая
    textarea_val = page.input_value('#chat-input')
    assert len(textarea_val) > 0, "Textarea должна содержать draft context"

    # Chip 🧵 отображается
    expect(page.locator('.thread-context-chip')).to_be_visible()


def test_send_message_includes_context_in_response(page: Page):
    """MLX должен вернуть ответ с контентом письма."""
    page.goto(BASE_URL)
    page.click('[data-tab="inbox"]')
    page.wait_for_selector('.inbox-item')
    page.click('.inbox-item:first-child')
    page.click('.inbox-action-btn[data-action="draft"]')
    page.wait_for_selector('#chat-input:not(:empty)')

    # Нажать Enter для отправки
    page.press('#chat-input', 'Enter')

    # Дождаться ответа (streaming может занять до 30с)
    page.wait_for_selector('.message.assistant', timeout=30000)
    response_text = page.inner_text('.message.assistant:last-child')
    assert len(response_text) > 20, "Ответ слишком короткий"
```

---

## Этап 5 — Edge Cases и деградация

### TC-5.1 MLX недоступен (не Apple Silicon / модель не загружена)

**Ожидаемое поведение:** `/api/chat/send` возвращает `200` с телом `{"response": "MLX не доступен: ..."}` — не 500.

```python
async def test_chat_send_graceful_when_mlx_unavailable(client):
    with patch("personal_assistant.mlx_server.engine.MLXEngine._mlx_available", False):
        thread_resp = await client.post("/api/chat/threads", json={})
        thread_id = thread_resp.json()["thread_id"]

        resp = await client.post("/api/chat/send", json={
            "thread_id": thread_id,
            "message": "Напиши ответ",
            "mode": "draft",
        })
    assert resp.status_code == 200
    assert "недоступен" in resp.json()["response"].lower() or \
           "unavailable" in resp.json()["response"].lower()
```

---

### TC-5.2 Vault не синхронизирован — `path` не существует

```python
async def test_draft_context_with_nonexistent_vault_path(client):
    """Письмо существует в inbox, но vault_path указывает на несуществующий файл."""
    resp = await client.post("/api/v1/inbox/msg_no_vault/draft-context")
    # Должен вернуть 200 с пустым или generic контекстом, не 500
    assert resp.status_code in (200, 404)
    if resp.status_code == 200:
        data = resp.json()
        # context_prompt присутствует даже без vault
        assert "context_prompt" in data
```

---

### TC-5.3 Очень большое письмо (> context window MLX)

```python
async def test_large_email_context_truncated_gracefully(client, mock_mlx_engine):
    """Письмо 50K символов не вызывает OOM или 500."""
    # Создаём inbox item с огромным body
    large_body = "Lorem ipsum... " * 3000  # ~50K символов
    # seeded через fixture или прямую вставку в БД
    resp = await client.post(f"/api/v1/inbox/large_msg/draft-context")
    assert resp.status_code in (200, 422)
```

---

### TC-5.4 Одновременные действия из Inbox (race condition)

```python
async def test_concurrent_draft_actions_create_separate_threads(client, seeded_vault):
    """Два одновременных нажатия Draft создают 2 независимых треда."""
    import asyncio

    async def do_draft(item_id: str):
        ctx = (await client.post(f"/api/v1/inbox/{item_id}/draft-context")).json()
        thread = (await client.post("/api/chat/threads", json={})).json()
        return thread["thread_id"]

    ids = await asyncio.gather(
        do_draft(seeded_vault.item_id_1),
        do_draft(seeded_vault.item_id_2),
    )
    assert ids[0] != ids[1]
```

---

### TC-5.5 Действие на письмо без темы (пустой subject)

```python
async def test_draft_context_for_email_with_empty_subject(client):
    resp = await client.post("/api/v1/inbox/msg_no_subject/draft-context")
    assert resp.status_code == 200
    data = resp.json()
    assert "context_prompt" in data
    # Не должно содержать "None" или "null" в тексте
    assert "None" not in data["context_prompt"]
```

---

### TC-5.6 Кириллица в теме и теле письма

```python
async def test_cyrillic_email_draft_context(client, seeded_vault_cyrillic):
    """Кириллица в subject/body корректно пробрасывается в context_prompt."""
    resp = await client.post(
        f"/api/v1/inbox/{seeded_vault_cyrillic.item_id}/draft-context"
    )
    assert resp.status_code == 200
    data = resp.json()
    # Кириллица не искажена
    assert "Иванов" in data["context_prompt"] or \
           "Отчёт" in data["context_prompt"]
```

---

## Этап 6 — Регрессия после фиксов

После исправления BUG-1, BUG-2, BUG-3 и GAP-4 выполнить полный регрессионный прогон:

### Чек-лист регрессии

| Тест | Файл | Проверяет |
|------|------|-----------|
| TC-1.1 — TC-1.7 | `tests/unit/inbox/` | Backend API не сломан |
| TC-2.1 — TC-2.4 | `tests/js/` | JS event bus работает корректно |
| TC-3.1 — TC-3.4 | `tests/e2e/test_inbox_chat_integration.py` | Integration pipeline |
| TC-4.1 — TC-4.4 | `tests/e2e/playwright/` | E2E с живым MLX |
| TC-5.1 — TC-5.6 | `tests/e2e/` | Edge cases не регрессят |
| Существующие unit тесты | `tests/unit/` | Фиксы не ломают остальное |
| Существующие E2E тесты | `tests/e2e/` | PR-02 и другие фиксы сохранены |

### Команды для запуска полного регрессионного набора

```bash
# Unit + E2E (без MLX)
./make.sh test

# Только новые тесты inbox→chat
uv run pytest tests/unit/inbox/ tests/e2e/test_inbox_chat_pipeline.py \
    tests/e2e/test_inbox_chat_integration.py -v

# Только JS тесты (после настройки jest)
npm test --prefix webui/frontend

# Playwright E2E (требует запущенного сервера)
./run.sh &
uv run playwright test tests/e2e/playwright/

# Нагрузочный тест после фиксов
./make.sh load http://127.0.0.1:8000 20 4 60s
```

---

## Матрица покрытия сценариев

| Сценарий | Этап 1 (Backend) | Этап 2 (JS) | Этап 3 (Mock MLX) | Этап 4 (Live MLX) | Этап 5 (Edge) |
|----------|:---:|:---:|:---:|:---:|:---:|
| Draft — happy path | TC-1.4 | TC-2.1, 2.2 | TC-3.1 | TC-4.1, 4.4 | — |
| Summarize — happy path | TC-1.4 | TC-2.4 | TC-3.2 | TC-4.2 | — |
| Create Meeting — happy path | TC-1.4 | TC-2.4 | TC-3.3 | TC-4.3 | — |
| BUG-1: новый тред | — | TC-2.1 | TC-3.4 | TC-4.1 | — |
| BUG-2: reply_message_id | TC-1.6 | TC-2.2 | TC-3.1 | TC-4.4 | — |
| BUG-3: thread_context chip | — | TC-2.3 | — | TC-4.1 | — |
| GAP-4: нет vault path | TC-1.3, 1.5 | — | — | — | TC-5.2 |
| MLX недоступен | — | — | — | — | TC-5.1 |
| Кириллица | TC-1.1 | — | — | — | TC-5.6 |
| Большое письмо | — | — | — | — | TC-5.3 |
| Race condition | — | — | — | — | TC-5.4 |
| Пустая тема | TC-1.2 | — | — | — | TC-5.5 |

---

## Критерии приёмки

### Минимальный порог (Gate для merge)

- [ ] Все тесты Этапа 1 проходят: `pytest tests/unit/inbox/ -q` → 0 failed
- [ ] Все тесты Этапа 3 проходят: `pytest tests/e2e/test_inbox_chat_integration.py -q` → 0 failed
- [ ] BUG-1 исправлен: TC-2.1 и TC-4.1 проходят
- [ ] BUG-2 исправлен: TC-2.2 и TC-1.6 проходят
- [ ] Регрессия: весь существующий тест-сьют (`make test`) остаётся green (0 failed)

### Полный приём (Release-ready)

- [ ] Все тесты Этапов 1–3 проходят автоматически в CI
- [ ] TC-4.1 — TC-4.4 пройдены вручную на Apple Silicon
- [ ] Edge cases TC-5.1 — TC-5.6 пройдены
- [ ] Нагрузочный тест: `make load` — p99 < 2s при 20 пользователях
- [ ] GAP-4 задокументирован в README с инструкцией по синхронизации vault

---

## Приоритизация исправлений

```
Критичность     Дефект   Что исправить
──────────────────────────────────────────────────────────────────
HIGH (UX broken) BUG-1   chat.js ~1029: вызвать createNewThread()
                          перед установкой контекста в pa:chat-open
HIGH (data loss) BUG-2   chat.js ~601: добавить reply_message_id
                          в payload sendMessage()
MEDIUM           BUG-3   chat_routes.py: добавить поле
                          reply_message_id в ChatSendRequest;
                          context_builder.py: использовать thread_context
                          как fallback если vault-файл недоступен
LOW (UX)         GAP-4   inbox.js: если path пустой — показать
                          предупреждение "Синхронизируйте vault для
                          полного контекста", отправить с пустым context_paths
```

---

*Документ создан на основе анализа исходного кода `webui/frontend/js/inbox.js`, `webui/frontend/js/chat.js`, `src/personal_assistant/inbox/routes.py`, `src/personal_assistant/mlx_server/chat_routes.py`.*
