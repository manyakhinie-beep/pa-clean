# RULES — вкладка «Правила» и настройки ИИ

Вкладка «Правила» в WebUI объединяет три области:

1. **Матрица Эйзенхауэра** — ручная сортировка задач по квадрантам.
2. **GTD-правила** — правила классификации (`data/rules.json`,
   `services/rule_engine.py`): ключевые слова/контакты → квадрант + action-type.
3. **Инструменты ИИ** — редактируемые параметры ИИ-инструментов
   (`data/config.json`). Описаны ниже.

Промпт суммаризации редактируется отдельно — во вкладке «Инструменты»
(см. раздел [«Промпт суммаризации»](#промпт-суммаризации)).

## Настройки ИИ-инструментов

Источник правды — `EDITABLE_FIELDS` в `config.py`. Каждая настройка имеет тип,
диапазон, дефолт и переменную окружения `PA_<KEY>`.

| Настройка | Тип | Диапазон | Дефолт | Env | Где применяется |
|-----------|-----|----------|--------|-----|-----------------|
| `mlx_model_path` | str | — | `""` | `PA_MLX_MODEL_PATH` | загрузка модели в `engine` |
| `mlx_temperature` | float | 0.0–2.0 | `0.3` | `PA_MLX_TEMPERATURE` | сэмплирование (`_resolve_sampling`) |
| `mlx_max_tokens` | int | 1–32768 | `1024` | `PA_MLX_MAX_TOKENS` | предел длины ответа |
| `mlx_top_p` | float | 0.0–1.0 | `1.0` | `PA_MLX_TOP_P` | nucleus sampling |
| `mail_auto_draft` | bool | — | `false` | `PA_MAIL_AUTO_DRAFT` | `mail_service.resolve_save_to_drafts` |
| `calendar_check_conflicts` | bool | — | `true` | `PA_CALENDAR_CHECK_CONFLICTS` | `calendar/routes` (find_conflicts) |
| `calendar_default_duration` | int | 1–1440 | `60` | `PA_CALENDAR_DEFAULT_DURATION` | `intent_parser` (длительность по умолчанию) |
| `e2e_test_mode` | bool | — | `false` | `PA_E2E_TEST_MODE` | подавление side-effects (Mail/Calendar) |

> `mlx_context_chars` (12000) задаётся только через env/`.env` и не редактируется
> в UI (это не «настройка инструмента», а лимит контекста).

## Порядок разрешения и приоритет

Значение = дефолт → перекрывается `PA_*`/`.env` → перекрывается
`data/config.json`. Оверлей `config.json` имеет высший приоритет, поэтому правки
из вкладки «Правила» всегда побеждают значения из `.env`. Путь оверлея —
`<project>/data/config.json` или `PA_CONFIG_PATH`.

`Settings.update(values)` валидирует все значения **до** применения
(all-or-nothing), применяет их к работающему процессу немедленно и атомарно
пишет в `config.json` (temp-файл + `os.replace`). Невалидный или битый
`config.json` игнорируется — остаются дефолты.

## API

```
GET   /api/v1/rules/settings   → { settings, schema, config_path }
PATCH /api/v1/rules/settings   { "<key>": <value>, ... }  → { ok, settings }
```

`GET` отдаёт текущие значения и саму схему (label/help/min/max/group), из
которой UI генерирует форму и подсказки. `PATCH` принимает частичный набор;
невалидное значение → `400`, неизвестный ключ → `400`. Пример приёмки:

```python
def test_rules_tab_saves_mlx_settings(client):
    client.patch("/api/v1/rules/settings", json={"mlx_temperature": 0.7})
    config = json.loads(open("data/config.json").read())
    assert config["mlx_temperature"] == 0.7
```

## UI (вкладка «Правила» → «Инструменты ИИ»)

`webui/frontend/js/rules.js` (`initAiSettings`) тянет `GET .../settings` и
**автогенерирует** форму из схемы: тип контрола по `type`, подсказки из `help`,
группировка по `group` (MLX / Почта / Календарь / Тестирование). Изменение поля
сохраняется сразу (`PATCH` одного ключа), есть кнопка «Сохранить всё»
(`data-testid="save-rules"`) и клиентская валидация диапазонов, зеркалящая схему.
Поля помечены `data-testid="set-<field>"`.

## Промпт суммаризации

Единый канон — `summarize_system` в `vault/.tool_prompts.json`
(`services/tool_prompts.py`), который уже используется задачей суммаризации
(`mlx_server/tasks/summarize.py`). Он валидируется против prompt-injection и
редактируется через `GET/POST /tool-prompts` (под-вкладка «Инструменты»).
Отдельной config-настройки `mail_summary_prompt` нет — чтобы не плодить два
конкурирующих хранилища.

## Промпт делегирования и список сотрудников

Действие **Inbox → Ассистент → 🤝 Делегировать** (клавиша `D`) формирует
короткую вводную для пересылки письма коллеге. Промпт и список коллег
редактируются в **Правила → Инструменты**:

- `delegate_system` (textarea) — системный промпт. По умолчанию формирует
  4-7-строчное вводное письмо в деловом тоне. Может быть переписан под
  стиль команды. Та же валидация prompt-injection, лимит 8000 символов.
- `delegate_contacts` (таблица: ФИО / Email / Роль / Заметка) — список
  сотрудников, появляющийся в модалке делегирования. Email обязателен,
  остальные поля — для UI-подсказок. Дубликаты по email (case-insensitive)
  отбрасываются на сохранении.

Хранится в `vault/.tool_prompts.json`:

```json
{
  "draft_system":     "",
  "summarize_system": "",
  "delegate_system":  "",
  "delegate_contacts": [
    {"name": "Анна Сидорова", "email": "anna@corp.ru", "role": "HR",    "note": ""},
    {"name": "Иван Петров",   "email": "ivan@corp.ru", "role": "юрист", "note": "договоры"}
  ]
}
```

**API:**

- `GET  /tool-prompts` — возвращает `delegate_system`,
  `delegate_contacts`, плюс `effective_delegate_system` (override либо
  дефолт) и `default_delegate_system` для кнопки «Восстановить».
- `POST /tool-prompts` принимает все четыре поля. Валидирует промпты,
  фильтрует контакты без email, дедуплицирует по email.
- `GET  /api/v1/inbox/delegate-contacts` — лёгкий endpoint только для
  picker'а в Inbox (без промптов).
- `POST /api/v1/inbox/{item_id}/delegate-suggest` с
  `{target_email, note}` → возвращает `{intro, subject, contact,
  mlx_used, draft_payload}`. Frontend сразу POST'ит `draft_payload` в
  `/api/chat/save-draft-mail`, и Mail.app открывает compose-окно.

**UX-flow:**

1. Открыть письмо в Inbox → правая панель «Ассистент» → кнопка
   **🤝 Делегировать** (или клавиша `D`).
2. Появляется модалка со списком коллег (radio) и полем «Заметка для
   коллеги» — опционально («ускорь, ждут к среде»).
3. **«👁 Предпросмотр»** — показывает текст вводной без открытия Mail.
4. **«✉️ Открыть в Mail»** — открывает Mail.app compose-окно с заполненными
   To / Subject / Body. Пользователь нажимает Send в Mail.

Если MLX-движок недоступен (Intel Mac, server-side error) — служба
строит детерминированный шаблон без вызова модели. В ответе ставится
`mlx_used: false`, в модалке отображается «⚙️ Шаблон без MLX».

## Валидация (сводно)

- `float`/`int` — приведение типа + проверка `min`/`max` (включительно).
- `bool` — принимает `true/false`, `1/0`, `yes/no`, `on/off`.
- `str`/`text` — приводится к строке.
- Неизвестный ключ → `KeyError` (API: `400`).
