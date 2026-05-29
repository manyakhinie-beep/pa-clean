#!/usr/bin/env bash
# ff_start.sh — запуск PaClean из исходников.
#
# Вызывается двойным кликом по PaClean.command на Рабочем столе
# (см. ff_install.sh).  Открывает Терминал, стартует сервер, открывает
# браузер.  Ctrl+C или закрытие окна останавливает сервер.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# ── Утилиты вывода ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}▶${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; exit 1; }

# Цвет приветствия + размер шрифта при двойном клике делают окно более
# дружелюбным для не-инженера.
clear || true
cat <<'EOF'

  ┌──────────────────────────────────────────────┐
  │           PaClean — личный ассистент         │
  │                                              │
  │  Запускаю сервер и открываю браузер.         │
  │  Это окно нужно держать открытым — закроете  │
  │  его, PaClean остановится.                   │
  └──────────────────────────────────────────────┘

EOF

# ── Sanity-проверки ──────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]]   || fail "Только macOS"
[[ "$(uname -m)" == "arm64" ]] || fail "Только Apple Silicon"

if ! command -v uv >/dev/null 2>&1; then
    # uv мог быть установлен в ~/.local/bin — попробуем подцепить
    export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || fail "uv не найден.  Сначала запустите scripts/ff_install.sh"

[[ -d "$REPO_ROOT/.venv" ]]                       || fail ".venv отсутствует.  Запустите scripts/ff_install.sh"
[[ -f "$REPO_ROOT/webui/dist/index.html" ]]       || fail "webui/dist/ отсутствует.  Запустите scripts/ff_install.sh"
[[ -f "$REPO_ROOT/.env" ]]                        || fail ".env отсутствует.  Запустите scripts/ff_install.sh"

PORT=$(grep -E '^PA_SERVER_PORT=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2)
PORT="${PORT:-8765}"
HOST=$(grep -E '^PA_SERVER_HOST=' "$REPO_ROOT/.env" | head -1 | cut -d= -f2)
HOST="${HOST:-127.0.0.1}"
URL="http://$HOST:$PORT"

# ── Если уже запущено — открываем и выходим ──────────────────────────────────
if curl -sSf --max-time 2 "$URL/api/health" >/dev/null 2>&1 || \
   curl -sSf --max-time 2 "$URL/" >/dev/null 2>&1; then
    warn "PaClean уже работает на $URL — открываю браузер"
    open "$URL"
    echo
    echo "  Если хотите остановить запущенный сервер, найдите ранее открытое"
    echo "  окно Терминала с PaClean и нажмите в нём Ctrl+C."
    echo
    read -r -p "  Нажмите Enter, чтобы закрыть это окно..."
    exit 0
fi

# ── Открываем браузер через 3 секунды (фоном) ────────────────────────────────
(
    sleep 3
    open "$URL" >/dev/null 2>&1 || true
) &

# ── Старт сервера ────────────────────────────────────────────────────────────
ok "Стартую PaClean на $URL"
echo
echo "  Что дальше:"
echo "    • Браузер откроется сам через 3 секунды"
echo "    • Если не открылся — откройте вручную: $URL"
echo "    • Чтобы остановить — Ctrl+C прямо в этом окне"
echo

# uv run pa serve — основной запуск.  Сервер пишет логи и в stdout этого
# окна, и в ~/Library/Logs/PaClean/server.log параллельно.
LOG_DIR="$HOME/Library/Logs/PaClean"
mkdir -p "$LOG_DIR"

exec uv run --no-sync pa serve 2>&1 | tee -a "$LOG_DIR/server.log"
