# REMOTE_SETUP — установка pa-clean на удалённой машине

Шаг за шагом для **macOS Apple Silicon** в условиях, когда:

- shell или uv-бинарник могут оказаться под Rosetta (`x86_64`);
- корпоративный PyPI-прокси **блокирует отдельные пакеты** (`coverage`,
  иногда другие свежие версии) с HTTP 403;
- доступ к интернету и `git` — только через корпоративный прокси.

Если ваша машина — обычный M-series Mac с прямым выходом в интернет, можно
просто следовать [`README.md`](../README.md) → быстрый старт. Этот гайд —
для случаев, когда тот короткий путь падает.

---

## 0. Префлайт: архитектура shell и uv

```bash
# 0.1. Текущая архитектура shell — должна быть arm64:
uname -m
# arm64   ← ок
# x86_64  ← shell под Rosetta, см. шаг 0.3

# 0.2. Архитектура uv — желательно arm64, x86_64 тоже работает:
file "$(which uv)"
# Mach-O 64-bit executable arm64    ← идеально
# Mach-O 64-bit executable x86_64   ← uv под Rosetta; справится через явный aarch64-triplet
```

### 0.3. Если shell `x86_64` — перезапустите в arm64

Временно (для текущей сессии):

```bash
arch -arm64 bash         # или arch -arm64 zsh
uname -m                 # должно стать arm64
```

Постоянно — снять Rosetta-флаг:

1. Quit Terminal / iTerm / VS Code.
2. Finder → Applications → Utilities → найти приложение → **Cmd+I (Get Info)**.
3. Снять галку **«Open using Rosetta»**.
4. Запустить заново → `uname -m` должен показывать `arm64`.

### 0.4. Если uv — `x86_64`

`x86_64` uv через Rosetta всё-таки умеет ставить arm64 Python (запрашивает
`cpython-X.Y-macos-aarch64` у python-build-standalone). `fix_env.sh` это делает
явно. **Перестановка uv не обязательна**, но если хотите чисто:

```bash
# Удалить старый uv (часто в ~/.cargo/bin/ или ~/.local/bin/):
rm "$(which uv)"
# Поставить в arm64-shell:
curl -LsSf https://astral.sh/uv/install.sh | sh
exec bash    # или exec zsh — перезапуск shell для PATH
file "$(which uv)"      # должно быть arm64
```

---

## 1. Клон + сборка venv

```bash
git clone <git-remote-url> ~/Projects/pa-clean
cd ~/Projects/pa-clean

# Скрипт сам найдёт/поставит arm64 Python и пересоздаст .venv:
./fix_env.sh --online
```

Если `fix_env.sh --online` падает на загрузке Python — попробуйте передать
прокси через переменные окружения:

```bash
export HTTPS_PROXY=http://<corp-proxy>:8080
export UV_PYTHON_INSTALL_MIRROR="<your-mirror>"   # если есть mirror python-build-standalone
./fix_env.sh --online
```

Если корпоративный proxy блокирует **конкретный пакет** (например `coverage`):

- `pyproject.toml` уже разделяет dev и cov группы: дефолтный `uv sync`
  устанавливает только runtime + dev без coverage.
- `fix_env.sh --online` использует `uv sync --group dev` → coverage НЕ
  тянется → блок не срабатывает.
- Если хотите coverage и прокси даёт доступ:
  `uv sync --group cov` отдельно.

### 1.1. Минимальная prod-сборка (`--no-dev`) — рекомендуемый путь

Если на машине **не нужно** запускать тесты или линтеры (только сервер
для повседневной работы), используйте `--no-dev` — это пропускает
установку pytest/ruff/mypy и всех других dev-инструментов.

**Самый короткий путь — `make.sh`:**

```bash
./make.sh                 # uv sync --no-dev + webui build + pa serve
./make.sh --no-serve      # только установить и собрать, без запуска
./make.sh --skip-webui    # пропустить npm-сборку (использовать webui/dist из git)
./make.sh --dev           # включить dev-группу (pytest/ruff/…) — если прокси разрешает
./make.sh --help          # справка
```

`make.sh` делает три шага: `uv sync --no-dev`, `(cd webui && npm install && npm run build)`,
`exec uv run --no-sync pa serve`. Если что-то пошло не так — см. `fix_env.sh --online`
(восстановление сломанного .venv с нуля).

**Вручную пошагово**, если нужен контроль:

```bash
# Один раз — поставить только runtime-зависимости
uv sync --no-dev

# Каждый запуск — пропустить авто-sync (читает уже готовый .venv)
uv run --no-sync pa check
uv run --no-sync pa serve
```

**Важно** (исправлено в этом коммите): `pyproject.toml` теперь содержит
`[tool.uv] default-groups = []`. Это означает, что **`uv run pa check`
и `uv run pa serve` работают «из коробки»** без флага `--no-sync` — uv
больше не пытается ставить dev-группу при первом запуске. Флаг
`--no-sync` всё ещё полезен, если хочется явно пропустить любую
переустановку.

Что попадает в `--no-dev`-venv:

| Группа         | Включена в `--no-dev`? | Назначение                              |
|----------------|------------------------|------------------------------------------|
| `[project]`    | ✅                      | runtime: FastAPI, uvicorn, mlx-lm, и т.д.|
| `[dependency-groups.dev]`  | ❌          | pytest, ruff, mypy, httpx-test           |
| `[dependency-groups.cov]`  | ❌          | coverage (бывает блокируется корп-прокси) |

Преимущества:
- **Быстрее**: venv весит ~700 МБ вместо ~1.2 ГБ.
- **Меньше шансов нарваться на блок прокси** — pytest и ruff не тянутся.
- **Чище pip-зависимости** в `uv.lock`-чтении.

Минусы:
- `pytest`, `ruff`, `mypy` недоступны — для CI/проверки качества нужна
  отдельная dev-машина или `uv sync --group dev` поверх.
- `pa check` всё ещё работает (это runtime-команда из самого пакета).

Замена `fix_env.sh --online` для prod-сборки:

```bash
# В корне репо
uv sync --no-dev
(cd webui && npm install && npm run build)   # см. § 3
```

Готово — `.venv/bin/pa` доступен, `pa serve` запускается без dev-инструментов.

---

## 2. Обход auto-sync в `uv run` (если sync падает на проксе)

`uv run <команда>` по умолчанию делает авто-sync ВСЕХ групп перед запуском.
Если прокси блокирует какой-то пакет, авто-sync падает 403, и команда не
запускается даже если venv уже готов.

Workaround — флаг `--no-sync`:

```bash
uv run --no-sync pa check
uv run --no-sync pa serve
uv run --no-sync pytest -m unit
```

`--no-sync` пропускает sync; используется то, что уже стоит в `.venv`.

Если хочется чтобы это было дефолтом — добавьте в `.env` (читается uv):

```dotenv
UV_NO_SYNC=1
```

или в shell rc (`~/.bashrc` / `~/.zshrc`):

```bash
export UV_NO_SYNC=1
```

---

## 3. WebUI build

```bash
(cd webui && npm install && npm run build)
ls webui/dist/index.html        # должен существовать после успешной сборки
```

Если корпоративный npm-прокси блокирует — настройте `.npmrc`:

```bash
echo 'registry=<your-corp-npm-mirror>' > ~/.npmrc
```

---

## 4. MLX-модель

Модель в git не хранится (10-20 GB). Два пути:

### 4.1. Скопировать с другой машины

```bash
# с вашей рабочей машины:
rsync -avz --progress ~/models/<model-dir>/ user@remote-host:~/models/<model-dir>/
```

### 4.2. Скачать из HuggingFace

Если корпоративный HF mirror доступен:

```bash
export HF_ENDPOINT=https://<your-hf-mirror>
uv run --no-sync hf download mlx-community/<repo> --local-dir ~/models/<name>
```

Без mirror — нужен прямой доступ к huggingface.co (часто заблокирован
корпоративным прокси, тогда только 4.1).

---

## 5. Конфигурация `.env`

```bash
cp .env.example .env
$EDITOR .env
```

Минимальный набор для запуска:

```dotenv
PA_VAULT_PATH=~/PersonalAssistantVault
PA_MLX_MODEL_PATH=/Users/<you>/models/<model-dir>
PA_USER_EMAIL=ваш-корпоративный-email@example.com

# Опционально, если нужны mail/calendar/contacts по умолчанию:
PA_SYNC_SOURCES=calendar,mail
```

---

## 6. Первичная проверка

```bash
# Проверка установки:
uv run --no-sync pa check

# Должно показать:
#   Calendar.app ✓
#   Mail.app ✓
#   osascript ✓
#   Apple Silicon M-chip ✓ (или жёлтое для Intel — но MLX тогда не работает)
#   MLX model exists ✓ (если PA_MLX_MODEL_PATH верный)
```

Calendar / Mail permission могут быть `✗ Missing` — это норма для нового
Mac. macOS попросит разрешения на первом `pa sync-*`.

---

## 7. TCC permissions для Mail/Calendar

```bash
# Первый sync — macOS покажет диалоги «Terminal/VS Code wants to control
# Calendar/Mail» — нажать Allow для каждого:
uv run --no-sync pa sync-calendar --days-back 1 --days-forward 14
uv run --no-sync pa sync-mail --days-back 7
```

Если диалоги не появились или вы их закрыли:

1. **System Settings → Privacy & Security → Automation**.
2. Найти приложение, из которого запускали (Terminal, iTerm, VS Code).
3. Под ним должны появиться галочки: Mail, Calendar, System Events — включить.

Без этих прав scenario-тесты на Mail/Calendar/AppleScript и реальные sync
будут проваливаться или вешаться.

---

## 8. Запуск и smoke-проверка

```bash
uv run --no-sync pa serve
# WebUI: http://127.0.0.1:8000
```

Открыть в браузере, пройти базовый чек:

- [ ] Вкладка «Сегодня» / Daily Brief — отображается.
- [ ] Вкладка «Правила» → «Инструменты»: видны 8 настроек MLX/Mail/Calendar.
- [ ] Tool prompts (draft / summarize): textarea'ы содержат полный
      дефолтный текст с badge «Дефолтный промпт».
- [ ] classify-editor: содержит русские теги (`срочно/важно/финансы/...`).
- [ ] Чат «что у меня завтра?» — отвечает на основе vault, не выдумывает.

Дальше — пройти **полный UAT** по [`UAT.md`](UAT.md) разделы 1–13.

---

## 9. Hermetic тесты

```bash
uv run --no-sync pytest -m "(unit or e2e) or (scenario and not live)"
# Ожидаемо: 1228 passed, 14 skipped
```

Если падает — это регрессия от вашего окружения, не от кода. Пришлите
имя теста + traceback.

---

## 10. Live-тесты (опционально)

```bash
# MLX (нужна модель + Apple Silicon arm64):
PA_MLX_MODEL_PATH=/Users/<you>/models/<dir> \
  uv run --no-sync pytest -m "scenario and live and mlx" -v

# Mail / Calendar (нужны TCC permissions из шага 7):
uv run --no-sync pytest -m "scenario and live and (mail or calendar)" -v
```

---

## Шпаргалка: что положить в `~/.bashrc` (или `.zshrc`) на корпоративной машине

```bash
# Avoid Rosetta surprises in subshells:
arch -arm64 zsh </dev/null >/dev/null 2>&1 && true || alias bash='arch -arm64 bash'

# pa-clean: skip uv auto-sync (corporate proxy blocks some packages):
export UV_NO_SYNC=1

# HF mirror (если есть):
# export HF_ENDPOINT=https://<corp-hf-mirror>

# Удобный alias:
alias pa-serve='cd ~/Projects/pa-clean && uv run pa serve'
alias pa-check='cd ~/Projects/pa-clean && uv run pa check'
```

---

## Если что-то всё ещё не работает

1. **uv падает на 403** — проверьте, не пытается ли он поставить заблокированный
   пакет. `uv sync --verbose 2>&1 | tail -30` покажет конкретный URL.
   Если пакет действительно нужен — попросите разблокировать в IT, либо
   запинить версию старее в `pyproject.toml`.

2. **`mlx-lm` не ставится** — проверьте, что Python в .venv это arm64
   (`.venv/bin/python -c "import platform; print(platform.machine())"` →
   должно быть `arm64`). Если `x86_64` — `./fix_env.sh --online` ещё раз.

3. **`pa check` показывает Calendar/Mail permission missing** — нужны TCC
   permissions (шаг 7). Без них чат не сможет показать реальные данные,
   зато не виснет.

4. **WebUI пустой** — `webui/dist/index.html` отсутствует. Соберите:
   `(cd webui && npm install && npm run build)`. Hard reload в браузере.

Если ничего из этого не помогает — пришлите вывод:

```bash
{
  echo "--- arch ---"; uname -m; file "$(which uv)"
  echo "--- venv ---"; .venv/bin/python -c "import platform; print(platform.machine())"
  echo "--- pa check ---"; uv run --no-sync pa check
  echo "--- proxy ---"; env | grep -iE 'proxy|http|hf_'
} 2>&1
```
