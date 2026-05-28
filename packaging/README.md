# packaging/ — Сборка `pa-clean` как macOS .app

> Phase 1 пилотной упаковки.  См. [docs/PILOT_DISTRIBUTION.md](../docs/PILOT_DISTRIBUTION.md)
> для контекста.

## Что здесь

| Файл                       | Назначение                                              |
|----------------------------|---------------------------------------------------------|
| `pyapp.env`                | PyApp environment-variables — описывает что и как собирать. |
| `Info.plist.template`      | Шаблон Info.plist для macOS .app-бандла.                |
| `entrypoint.py`            | Python entry-point: запускает `pa serve` + открывает браузер. |
| `requirements-pyapp.txt`   | Lock-файл для embedded wheelhouse (генерируется).       |

Сам бинарь PyApp **не** хранится в репозитории — собирается из cargo
crate каждый раз.

## Быстрый старт (локальная сборка)

```bash
# 1. Установить Rust toolchain (если ещё нет; не требует sudo).
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"

# 2. Из корня репозитория:
./scripts/build_pilot.sh

# Готовый бандл:
#   dist/PaClean.app
#   dist/PaClean-pilot-<version>-arm64.dmg
```

## Что делает сборка

1. Собирает Python wheel: `uv build --wheel` → `dist/pa_clean-*.whl`.
2. Генерирует lock-файл всех зависимостей: `uv export --no-dev`.
3. Через `cargo install pyapp` собирает Rust-launcher с embedded wheelhouse
   (все wheels впечены в бинарь — bootstrap не лазает в PyPI).
4. Оборачивает launcher в `PaClean.app` бандл с `Info.plist`.
5. Применяет ad-hoc подпись (`codesign --sign -`).  Без Apple Developer ID
   тестировщику при первом запуске нужно сделать Ctrl-клик → «Открыть»
   один раз; для команды разработчиков это норма.
6. Упаковывает `.app` в `.dmg` для удобной раздачи.

## Distribution для пилотов

Готовый `.dmg` положить на внутренний SharePoint / S3 / Google Drive.
В Slack-канал пилотов:

```
Привет! Положил pilot-сборку: <ссылка>.

Первый запуск:
1. Скачайте PaClean-pilot-arm64.dmg.
2. Дважды кликните, перетащите PaClean.app в ~/Applications.
3. Ctrl-клик по PaClean.app → «Открыть» → «Открыть» (один раз;
   потом не понадобится).
4. Первый запуск займёт 2-3 минуты — устанавливается Python.
5. Откроется браузер с интерфейсом — следуйте мастеру настройки.

Если что-то не так — нажмите кнопку «Сообщить о проблеме» в
шапке UI, она скопирует диагностику в буфер обмена.
Пишите в этот канал.
```

## Ограничения текущей фазы

- **Без Developer ID** — корпоративные маки с MDM-политикой могут
  блокировать запуск даже после Ctrl-клика.  Этот вариант подходит для
  команды разработки + friendly testers, не для широкого пилота.
- **Только arm64** — Rosetta-бандлы не собираются.  MLX на x86_64
  всё равно не работает.
- **Embedded wheelhouse** — `mlx-lm` и `sentence-transformers` тяжёлые,
  итоговый бандл ~400-500 MB.  Это нормально для embedded-варианта;
  модельные веса всё равно качаются отдельно при первом запуске.

## Следующие шаги (Phase 2)

См. [docs/PILOT_DISTRIBUTION.md](../docs/PILOT_DISTRIBUTION.md):

- Welcome-страница в WebUI при первом запуске.
- Permissions wizard (Mail / Calendar TCC).
- Model-download wizard (внутренний storage вместо HuggingFace).
- Кнопка «Сообщить о проблеме» с автоматической сборкой диагностики.
