#!/usr/bin/env bash
# ff_install.sh — установка PaClean из исходников для friends&family-теста.
#
# Цель: за один запуск довести машину неопытного пользователя до состояния
# «двойной клик по PaClean.command → открывается WebUI в браузере».
#
# Что делает:
#   1. Проверяет: macOS 14+, Apple Silicon, доступ к интернету.
#   2. Ставит ``uv`` (если ещё нет) — без sudo, в ~/.local/bin.
#   3. Клонирует репозиторий в ~/PaClean/repo (или обновляет если есть).
#   4. uv sync --no-dev — устанавливает зависимости в .venv.
#   5. Использует уже собранный webui/dist/ из репо (npm install не требуется).
#   6. Создаёт минимальный .env и стартовые директории.
#   7. Кладёт ярлык PaClean.command на Рабочий стол для запуска.
#
# Что НЕ делает:
#   - Не требует sudo / admin-прав.
#   - Не устанавливает Homebrew, Node, Xcode CLT — не нужны.
#   - Не качает MLX-веса модели — это делается потом из WebUI.
#
# Usage:
#   curl -fsSL https://<host>/scripts/ff_install.sh | bash
#   # или
#   git clone <repo> && cd pa-clean && ./scripts/ff_install.sh

set -e

# ── Утилиты вывода ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()     { echo -e "${GREEN}✓${NC} $*"; }
info()   { echo -e "${CYAN}▶${NC} $*"; }
warn()   { echo -e "${YELLOW}⚠${NC}  $*"; }
fail()   { echo -e "${RED}✗${NC}  $*"; exit 1; }
header() { echo; echo -e "${CYAN}══${NC} $* ${CYAN}══${NC}"; }

# ── Параметры (можно переопределить через env) ────────────────────────────────
PA_REPO_URL="${PA_REPO_URL:-https://github.com/igormaniakhin/pa-clean.git}"
PA_BRANCH="${PA_BRANCH:-main}"
PA_HOME="${PA_HOME:-$HOME/PaClean}"
PA_REPO_DIR="$PA_HOME/repo"
PA_VAULT_DIR="${PA_VAULT_DIR:-$HOME/PaCleanVault}"
DESKTOP="$HOME/Desktop"

header "PaClean — установка для F&F-тестирования"
echo "  Дом приложения:   $PA_HOME"
echo "  Vault для данных: $PA_VAULT_DIR"

# ── Проверки окружения ────────────────────────────────────────────────────────
header "Проверка системы"

if [[ "$(uname)" != "Darwin" ]]; then
    fail "PaClean работает только на macOS.  У вас: $(uname)."
fi
ok "macOS обнаружен"

if [[ "$(uname -m)" != "arm64" ]]; then
    fail "Нужен Mac с процессором Apple Silicon (M1/M2/M3/M4).
Ваш Mac на Intel — MLX-модели на нём не работают.
Если вы не уверены — Меню Apple () → «Об этом Mac», ищите слово «Apple»."
fi
ok "Apple Silicon (arm64)"

MACOS_VERSION=$(sw_vers -productVersion | cut -d. -f1)
if [[ "$MACOS_VERSION" -lt 14 ]]; then
    fail "Нужен macOS Sonoma (14) или новее.  У вас: $(sw_vers -productVersion).
Обновите систему через Меню Apple → «Системные настройки» → «Общие» → «Обновление ПО»."
fi
ok "macOS $(sw_vers -productVersion) (нужен ≥ 14)"

if ! curl -sSf --head https://github.com >/dev/null 2>&1; then
    fail "Нет доступа к github.com.  Проверьте интернет / VPN / корпоративный прокси."
fi
ok "Интернет работает (github.com доступен)"

# ── Установка uv (если нужно) ────────────────────────────────────────────────
header "Проверка инструментов"

if ! command -v uv >/dev/null 2>&1; then
    info "uv не найден — устанавливаю в ~/.local/bin (без sudo)"
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
    # uv install кладёт себя в ~/.local/bin — добавляем в PATH этой сессии
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        fail "uv установился, но не виден в PATH.
Откройте новое окно терминала и перезапустите этот скрипт."
    fi
    ok "uv установлен: $(uv --version)"
else
    ok "uv уже есть: $(uv --version)"
fi

if ! command -v git >/dev/null 2>&1; then
    fail "git не установлен.  В Терминале выполните:  xcode-select --install
Откроется окно — нажмите «Установить» (займёт 5-10 минут), потом перезапустите этот скрипт."
fi
ok "git: $(git --version | head -c 30)"

# ── Клонируем (или обновляем) репозиторий ─────────────────────────────────────
header "Загрузка PaClean"

mkdir -p "$PA_HOME"

if [[ -d "$PA_REPO_DIR/.git" ]]; then
    info "Репозиторий уже есть — обновляю до последней версии"
    cd "$PA_REPO_DIR"
    git fetch --quiet origin "$PA_BRANCH" || warn "fetch не удался — продолжаю с локальной версией"
    git checkout --quiet "$PA_BRANCH" || true
    git pull --quiet --ff-only origin "$PA_BRANCH" 2>/dev/null || warn "pull пропущен (локальные изменения?)"
else
    info "Клонирую репозиторий в $PA_REPO_DIR"
    git clone --quiet --branch "$PA_BRANCH" --depth 1 "$PA_REPO_URL" "$PA_REPO_DIR"
fi
ok "Репозиторий готов: $PA_REPO_DIR"

cd "$PA_REPO_DIR"

# ── Устанавливаем зависимости Python ──────────────────────────────────────────
header "Установка зависимостей Python (это займёт 2-5 минут)"

# --no-dev: ставим только runtime, без pytest / ruff / mypy.
# Корпоративные PyPI-прокси иногда блокируют свежий coverage — этот режим
# обходит проблему.  Подробности см. в make.sh.
if ! uv sync --no-dev; then
    fail "uv sync не прошёл.  Возможные причины:
  • PyPI заблокирован прокси
  • не хватает места на диске
  • кэш повреждён — попробуйте: rm -rf .venv && повторите этот скрипт
"
fi
ok "Python-зависимости установлены в $PA_REPO_DIR/.venv"

# ── Проверяем что WebUI собран в репозитории ──────────────────────────────────
header "WebUI"

if [[ -f "$PA_REPO_DIR/webui/dist/index.html" ]]; then
    ok "WebUI уже собран (webui/dist/index.html на месте)"
else
    fail "webui/dist/ отсутствует.
В этом релизе ассеты должны быть зачекинены в git — у вас, видимо, более старая версия.
Обновите репозиторий: git -C \"$PA_REPO_DIR\" pull"
fi

# ── Готовим vault и .env ──────────────────────────────────────────────────────
header "Первичная настройка"

mkdir -p "$PA_VAULT_DIR"
ok "Vault: $PA_VAULT_DIR"

mkdir -p "$HOME/Library/Application Support/PaClean"
mkdir -p "$HOME/Library/Logs/PaClean"
ok "App data: ~/Library/Application Support/PaClean"
ok "Логи:     ~/Library/Logs/PaClean/server.log"

ENV_FILE="$PA_REPO_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    cat > "$ENV_FILE" <<EOF
# Автоматически сгенерировано ff_install.sh — правьте через WebUI → Правила,
# не через этот файл.
PA_VAULT_PATH=$PA_VAULT_DIR
PA_MLX_MODEL_PATH=$HOME/.cache/huggingface/hub
PA_SERVER_HOST=127.0.0.1
PA_SERVER_PORT=8765
PA_CALENDAR_DAYS_BACK=60
PA_MAIL_DAYS_BACK=60
EOF
    ok "Создан .env с дефолтами"
else
    ok ".env уже есть, не трогаю"
fi

# ── Ярлык на Рабочий стол ─────────────────────────────────────────────────────
header "Ярлык для запуска"

LAUNCHER="$PA_HOME/PaClean.command"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# PaClean — двойной клик для запуска ассистента.
# Файл автоматически сгенерирован ff_install.sh ($(date +%Y-%m-%d)).
exec "$PA_REPO_DIR/scripts/ff_start.sh"
EOF
chmod +x "$LAUNCHER"
ok "Создан лаунчер: $LAUNCHER"

if [[ -d "$DESKTOP" ]]; then
    DESKTOP_SHORTCUT="$DESKTOP/PaClean.command"
    cp "$LAUNCHER" "$DESKTOP_SHORTCUT"
    chmod +x "$DESKTOP_SHORTCUT"
    ok "Ярлык на Рабочем столе: PaClean.command (двойной клик для запуска)"
fi

# ── Финал ────────────────────────────────────────────────────────────────────
header "Установка завершена"
cat <<EOF

  ${GREEN}✓ Готово!${NC}

  Чтобы запустить PaClean:
    • Двойной клик по ${CYAN}PaClean.command${NC} на Рабочем столе
      (откроется окно Терминала — это нормально, его нельзя закрывать)
    • Браузер сам откроется на http://127.0.0.1:8765

  Чтобы остановить:
    • Нажмите Ctrl+C в окне Терминала
    • Или просто закройте это окно

  Логи на случай проблем:
    open ~/Library/Logs/PaClean/server.log

  Что-то пошло не так? Свяжитесь с разработчиком и приложите содержимое лога.

EOF
