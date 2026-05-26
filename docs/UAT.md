# UAT — User Acceptance Testing (pa-clean)

Ручной чеклист для пользовательского тестирования pa-clean на финальной машине.
Цель — пройти все ключевые сценарии end-to-end, как реальный пользователь.

## 0. Prerequisites

- macOS 13+ Apple Silicon
- Python 3.11–3.13 (`uv python install 3.13`)
- Node 18+ (для пересборки WebUI)
- Mail.app и Calendar.app настроены и работают
- MLX-модель скачана локально (`mlx-community/...` или ваша)
- Automation permission будет запрошен при первом sync (одобрить)

## 1. Установка с нуля (Prod-сборка)

```bash
git clone <repo> pa-clean
cd pa-clean

# Прод-сборка БЕЗ dev-инструментов (coverage, ruff, mypy, pytest — не ставятся):
uv sync --no-dev
(cd webui && npm install && npm run build)

cp .env.example .env
$EDITOR .env    # задать PA_VAULT_PATH и PA_MLX_MODEL_PATH
```

Ожидаемый результат: `uv sync --no-dev` ставит ≈90 пакетов (runtime),
без dev-группы; `npm run build` создаёт `webui/dist/`.

- [ ] `python -c "import personal_assistant.mlx_server.server"` — без ошибок.
- [ ] `webui/dist/index.html` существует.

## 2. Проверка конфигурации

```bash
uv run pa check
```

- [ ] Calendar.app и Mail.app помечены `✓ OK`.
- [ ] `osascript (shell)` — `✓ OK`.
- [ ] `Vault exists` — путь правильный.
- [ ] `Apple Silicon M-chip` — `✓ OK` (или жёлтое предупреждение для Intel).
- [ ] Если прав Automation нет — pa check советует команды для запроса.

## 3. Первый запуск + WebUI

```bash
uv run pa serve
```

Открыть `http://127.0.0.1:8000` в браузере.

- [ ] Страница загружается, видна навигация.
- [ ] Лог сервера в консоли — без ERROR.
- [ ] Открыть вкладку **«Сегодня»** — рендерится (даже если vault пустой).

## 4. Вкладка «Правила» (8 настроек ИИ)

Открыть вкладку «Правила» → подсекция «Инструменты ИИ».

- [ ] Видны все 8 настроек: `mlx_model_path`, `mlx_temperature`, `mlx_max_tokens`,
      `mlx_top_p`, `mail_auto_draft`, `calendar_check_conflicts`,
      `calendar_default_duration`, `e2e_test_mode`.
- [ ] У каждой есть tooltip / описание.
- [ ] Изменить `mlx_temperature` с дефолта (1.0) на 0.7 → нажать Save.
- [ ] Перезагрузить страницу — новое значение сохранилось.
- [ ] Проверить `data/config.json` — содержит `"mlx_temperature": 0.7`.
- [ ] Попробовать ввести 5.0 — должна вылететь валидация (диапазон 0.0–2.0).

## 5. Синхронизация Apple Calendar / Mail

```bash
uv run pa sync-calendar --days-back 7 --days-forward 7
```

- [ ] macOS запросит Automation permission для Calendar — одобрить.
- [ ] Команда завершается без ошибок, в vault появляются `.md` файлы.
- [ ] Проверить: `ls ~/PersonalAssistantVault/calendar/2026/05/` → файлы есть.
- [ ] Frontmatter содержит `title`, `start`, `end`, `attendees`.

```bash
uv run pa sync-mail --days-back 3
```

- [ ] Automation permission для Mail — одобрить.
- [ ] Письма созданы в `~/PersonalAssistantVault/mail/2026/05/`.
- [ ] Frontmatter содержит `subject`, `sender`, `date`, `thread_id`.
- [ ] Контакты появились в `~/PersonalAssistantVault/contacts/`.

## 6. Поиск (BM25)

В WebUI → вкладка «Сегодня» (или «Поиск») — ввести запрос из недавнего письма
или встречи.

- [ ] Результаты содержат релевантные документы.
- [ ] Ссылки кликаются и открывают исходный `.md`.

## 7. Чат с MLX

В WebUI → новый чат, спросить: «Что у меня запланировано на завтра?»

- [ ] Модель загружается (первый запрос — задержка ~10-30s).
- [ ] Ответ генерируется (используется контекст из vault).
- [ ] Источники указаны (links на vault docs).

Изменения настроек проверить:

- [ ] Поменять `mlx_temperature` на 0.3 в Rules → следующий чат-запрос менее «творческий».
- [ ] Поменять `mlx_max_tokens` на 256 → ответ обрезается раньше.

## 8. Создание черновика письма

В чате попросить: «Подготовь короткий ответ Иванову, что встречу переносим
на пятницу в 14:00» (или используйте Reply на конкретное письмо в Inbox).

- [ ] Черновик создан в Mail.app (проверить папку «Черновики»).
- [ ] Тема и тело корректные (на русском, без artefacts).
- [ ] Если в Rules `mail_auto_draft=true` — черновик сохранён silently;
      если `false` — открывается окно Compose для ревью.

## 9. Создание встречи из текста

В WebUI → calendar create → ввести «Встреча с Петровым в среду в 15:00 на 30 минут».

- [ ] Превью события показано (дата, время, длительность).
- [ ] Если календарь не указан → UI спрашивает «в какой календарь».
- [ ] После подтверждения событие появляется в Calendar.app.

## 10. Daily Brief

В WebUI → вкладка «Сегодня» → кнопка обновления / `Refresh daily brief`.

- [ ] Brief генерируется (события сегодня + urgent inbox + tasks).
- [ ] Содержит секции «Календарь», «Inbox», «Задачи».
- [ ] AI insight присутствует.

## 11. Регресс-чек после внесения данных

```bash
# Hermetic suite — не должна сломаться от данных:
uv run --group dev pytest -m "(unit or e2e) or (scenario and not live)"
```

- [ ] 1228 passed, 14 skipped.

## 12. Производительность

```bash
PA_MLX_MODEL_PATH=<путь-к-модели> uv run python scripts/benchmark.py
```

- [ ] Vault merge (1000 msgs) < 200ms.
- [ ] MLX cold load < 30s.
- [ ] MLX cold inference (64 tok) < 5s.
- [ ] MLX steady-state (32 tok) < 3s.

Записать цифры в `docs/PERFORMANCE.md` под раздел «Baseline (Mac)».

## 13. Финальная очистка (опционально)

Если хочется снести dev-кэши перед демо:

```bash
rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov
```

## Критерии приёмки UAT

- [ ] Все блоки 1–10 ✓
- [ ] 11 (hermetic regress) — зелёный
- [ ] 12 (perf) — в пределах нормы
- [ ] Никаких UI-ошибок в browser-console во время прохождения 3–10
- [ ] Никаких ERROR-логов сервера во время прохождения 3–10

Если все галочки — pa-clean готов к ежедневному использованию.
