# INTEGRATIONS — MLX · Apple Mail · Apple Calendar

Все интеграции рассчитаны на macOS Apple Silicon и деградируют мягко: при
отсутствии прав/модели возвращается понятный результат, а не падение.

## MLX (локальный инференс)

- **Платформа.** `mlx-lm` существует только под macOS arm64 (нет колёс под
  Linux/x86). На прочих платформах инференс недоступен.
- **Модель.** Путь задаётся настройкой `mlx_model_path` (UI «Правила») или
  `PA_MLX_MODEL_PATH`. Поддерживаются локальный каталог и репозиторий
  mlx-community на HF. Если путь пуст/невалиден — `engine` возвращает
  `_UNAVAILABLE_MSG`.
- **Потоки/GPU.** Весь GPU-доступ идёт через единственный `_MLXThread`
  (`mlx_server/engine.py`) — это устраняет ошибку «no Stream(gpu, N) in current
  thread» под Starlette/AnyIO.
- **Параметры.** `temperature`, `max_tokens`, `top_p` берутся из конфига
  (`_resolve_sampling`) и адаптируются к версии API `mlx_lm`
  (`temp=` / `temperature=` / `make_sampler`).
- **Проверка.** `pytest -m "scenario and mlx"` (нужны `PA_MLX_MODEL_PATH` и
  Apple Silicon).
- **Замер latency.** Зафиксировать базовый прогон summarize/draft на целевой
  модели (открытый пункт — performance baseline в чеклисте приёмки).

## Apple Mail

- **Доступ.** Через AppleScript (`readers/applescript_base.run_applescript`),
  без PyObjC. Чтение писем — `readers/mail_reader.py`; создание черновиков —
  `services/mail_service.save_draft_reply` и
  `mlx_server/chat_routes.save_draft_mail` (`/api/chat/save-draft-mail`).
- **Права (TCC).** System Settings → Privacy & Security → **Automation** →
  разрешить терминалу/приложению управлять **Mail**. Для чтения тел может
  потребоваться **Full Disk Access**. Проверка/сброс — `tccutil`.
- **Декодирование тела.** RTF/HTML тела писем нормализуются при чтении (см.
  scenario `tests/scenarios/test_mail_body_scenarios.py`).
- **Авточерновики.** Флаг `mail_auto_draft`: когда вызов не задаёт явно, черновик
  либо тихо сохраняется в «Черновики», либо открывается окно компоновки
  (`resolve_save_to_drafts`).
- **Безопасность тестов.** При `e2e_test_mode=true` реальные черновики НЕ
  создаются — `save_draft_reply`/`save_draft_mail` возвращают симуляцию.
  Никогда не отправляйте реальные письма из тестов.
- **Legacy Outlook.** В репозитории остаётся читатель локальной БД Outlook
  (`readers/outlook_sqlite/*`, `readers/outlook_reader.py`) при заявленном
  переходе на Apple Mail. Открытый пункт (P7): удалить или спрятать за флагом.

## Apple Calendar

- **Доступ.** AppleScript. Чтение событий — `readers/calendar_reader.py` и
  скан vault (`services/calendar_service.fetch_upcoming_events`,
  `/api/v1/calendar/upcoming`). Создание — `calendar/calendar_writer.create_event`
  (только создание; существующие события не меняются/не удаляются).
- **Права (TCC).** Automation → разрешить управление **Calendar**.
- **Создание из текста.** `/api/v1/calendar/create-from-text`:
  `intent_parser` (NL → `EventDraft`) → длительность по умолчанию из
  `calendar_default_duration` (60 мин) → опц. проверка пересечений.
- **Проверка слотов (free/busy).** `calendar_service.find_conflicts(start, end,
  events)` — чистая функция (полуоткрытые интервалы; касание границ не считается
  конфликтом). Включается флагом `calendar_check_conflicts` (по умолчанию `true`);
  предупреждение добавляется в превью, создание не блокируется.
- **Безопасность тестов.** При `e2e_test_mode=true` `create_event` не вызывает
  AppleScript и возвращает `event_uid="e2e-test-mode"`. Для scenario используйте
  тестовый календарь, не основной.
- **Проверка.** `pytest -m "scenario and calendar"`.

## Эмбеддинги / векторный поиск (опционально)

Гибридный поиск (`mlx_server/vector_index.py`) — это extra-группа `vector`
(`numpy`, `sentence-transformers`): `uv sync --extra vector`. Модель —
`PA_EMBEDDING_MODEL` / `PA_EMBEDDING_MODEL_PATH`. Без неё работает BM25-поиск
(`vault_index.py`).

## Чеклист прав доступа

- [ ] Automation → Mail (создание черновиков, чтение).
- [ ] Automation → Calendar (чтение, создание).
- [ ] Full Disk Access (если требуется чтение тел/вложений).
- [ ] `PA_MLX_MODEL_PATH` указывает на валидную MLX-модель.
- [ ] Для прогонов с побочными эффектами — отдельный тестовый календарь и
      `PA_E2E_TEST_MODE=true`.

## Диагностика

- Mail/Calendar не отвечают на AppleScript → проверить, что приложение запущено
  и выданы права Automation (диалог появляется при первом обращении).
- Инференс «недоступен» → проверить `mlx_model_path`, что это Apple Silicon и
  `mlx-lm` установлен.
- Сбросить разрешения: `tccutil reset AppleEvents` (затем повторно подтвердить).
