#!/usr/bin/env bash
# fix_env.sh — пересоздать venv с arm64 Python, не связанным со сломанным
# Homebrew-OpenSSL. Recovery-скрипт для случаев, когда обычный ``uv sync`` падает
# на SSL-ошибках, x86_64 mismatch'е или другом мусоре от старых установок.
#
# Использование:
#   ./fix_env.sh            офлайн: только локально доступные Python,
#                           uv sync --frozen (не лезет в сеть)
#   ./fix_env.sh --online   онлайн: uv скачает Python и пакеты,
#                           перегенерирует uv.lock из PyPI
#
# После успеха:
#   uv run pa serve         — запустить WebUI на http://127.0.0.1:8000
#   uv run pa check         — проверить TCC-права и MLX-окружение
#   open docs/UAT.md        — пройти пользовательскую приёмку
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}▶${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; exit 1; }

# ── Разбор аргументов ─────────────────────────────────────────────────────────
ONLINE=false
for arg in "$@"; do
    case "$arg" in
        --online)  ONLINE=true ;;
        --offline) ONLINE=false ;;
        -h|--help)
            sed -n '1,16p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) fail "Неизвестный аргумент: $arg  (используйте --online или --offline)" ;;
    esac
done

echo ""
if $ONLINE; then
    echo "pa-clean — fix_env [ONLINE]: пересоздание venv с загрузкой из сети"
    echo "===================================================================="
else
    echo "pa-clean — fix_env [OFFLINE]: пересоздание venv из локальных ресурсов"
    echo "======================================================================"
fi

# ── 0. Префлайт: shell должен быть arm64, не Rosetta ─────────────────────────
SHELL_ARCH=$(uname -m)
if [ "$SHELL_ARCH" != "arm64" ]; then
    echo ""
    fail "Текущий shell работает в архитектуре $SHELL_ARCH, а pa-clean запинен на arm64
(mlx-lm имеет колёса только под darwin/arm64).

Скорее всего Terminal/iTerm запущен под Rosetta. Исправьте одним из способов:

  (a) Временный prefix — перезапуск shell в arm64:
      arch -arm64 zsh
      uname -m            # должно вернуть arm64
      ./fix_env.sh --online

  (b) Постоянно — снять Rosetta-флаг с приложения:
      Quit Terminal/iTerm → Applications → Utilities → Terminal.app →
      Cmd+I (Get Info) → снять «Open using Rosetta» → запустить заново.

  (c) Если uv-бинарник тоже x86_64 (file \$(which uv) показывает x86_64):
      rm \$(which uv)
      arch -arm64 zsh
      curl -LsSf https://astral.sh/uv/install.sh | sh
      exec zsh
"
fi
ok "shell arch=arm64"

UV_ARCH=$(file "$(which uv 2>/dev/null)" 2>/dev/null | grep -oE 'arm64|x86_64' | head -1)
if [ -n "$UV_ARCH" ] && [ "$UV_ARCH" != "arm64" ]; then
    warn "uv-бинарник $UV_ARCH (через Rosetta). По умолчанию ставит x86_64 Python.
       Этот скрипт явно запросит arm64-сборку через ``cpython-X.Y-macos-aarch64``
       — обычно работает. Если упрётся — переустановите uv как arm64 (см. README)."
elif [ -n "$UV_ARCH" ]; then
    ok "uv arch=$UV_ARCH"
fi

# ── 1. Найти arm64 Python из ~/.local/share/uv (НЕ из /usr/local) ────────────
info "Поиск arm64 Python (uv-managed, не Homebrew)..."

PYTHON=""

# Ключевая проверка: путь должен быть в ~/.local/share/uv/python/, не в /usr/local
_is_uv_managed() {
    local p="$1"
    [[ "$p" == *"/.local/share/uv/python/"* ]]
}

_ssl_ok() {
    local p="$1"
    "$p" -c "import ssl; print('ok')" 2>/dev/null | grep -q "ok"
}

# Попытка 1 (только --online): скачать нужный Python из сети.
# pa-clean поддерживает 3.11–3.13; 3.13 — рекомендуемая (свежие mlx-lm колёса).
if $ONLINE; then
    for TRY_VER in "cpython-3.13-macos-aarch64" "cpython-3.12-macos-aarch64"; do
        info "Попытка: uv python install $TRY_VER ..."
        if uv python install "$TRY_VER" 2>/dev/null; then
            CANDIDATE=$(uv python find "$TRY_VER" 2>/dev/null || true)
            if [ -n "$CANDIDATE" ] && _is_uv_managed "$CANDIDATE" && _ssl_ok "$CANDIDATE"; then
                PYTHON="$CANDIDATE"
                ok "uv-managed arm64 Python: $PYTHON"
                break
            else
                warn "$TRY_VER установлен, но путь = $CANDIDATE (Homebrew?), пробуем следующий"
            fi
        fi
    done
else
    info "Офлайн-режим: пропускаем uv python install (нет сети)"
fi

# Попытка 2: уже скачанный uv-managed aarch64 в ~/.local/share/uv
if [ -z "$PYTHON" ]; then
    while IFS= read -r line; do
        P=$(echo "$line" | awk '{print $NF}')
        if _is_uv_managed "$P" && _ssl_ok "$P"; then
            PYTHON="$P"
            ok "Найден uv-managed arm64 Python: $PYTHON"
            break
        fi
    done < <(uv python list 2>/dev/null | grep "aarch64" | grep "\.local/share/uv")
fi

# Попытка 3: Anaconda arm64 (самодостаточный, свой OpenSSL)
if [ -z "$PYTHON" ]; then
    for CANDIDATE in \
        /opt/anaconda3/bin/python3.13 \
        /opt/anaconda3/bin/python3.12 \
        /opt/anaconda3/bin/python3.11 \
        /opt/miniconda3/bin/python3.13 \
        /opt/miniconda3/bin/python3.12 \
        /opt/miniconda3/bin/python3.11 \
        "$HOME/anaconda3/bin/python3.13" \
        "$HOME/anaconda3/bin/python3.12" \
        "$HOME/anaconda3/bin/python3.11"
    do
        if [ -f "$CANDIDATE" ]; then
            ARCH=$("$CANDIDATE" -c "import platform; print(platform.machine())" 2>/dev/null || echo "")
            if [ "$ARCH" = "arm64" ] && _ssl_ok "$CANDIDATE"; then
                PYTHON="$CANDIDATE"
                ok "Anaconda arm64 Python: $PYTHON"
                break
            fi
        fi
    done
fi

if [ -z "$PYTHON" ]; then
    if $ONLINE; then
        fail "Не найден рабочий arm64 Python (ssl ok) без Homebrew-зависимостей.

Варианты:
  1. brew install openssl@3  — починит сломанный /usr/local/bin/python3.12
  2. conda install openssl   — если используете Anaconda
  3. curl -LsSf https://astral.sh/uv/install.sh | sh   — установить uv с нуля"
    else
        fail "Не найден локальный arm64 Python (ssl ok).

Варианты:
  1. Подключить интернет и запустить: ./fix_env.sh --online
  2. brew install openssl@3  — починит сломанный /usr/local/bin/python3.12
  3. conda install openssl   — если используете Anaconda"
    fi
fi

# ── 2. Проверить выбранный Python ─────────────────────────────────────────────
info "Используем Python: $PYTHON"
ARCH=$("$PYTHON" -c "import platform; print(platform.machine())")
[ "$ARCH" = "arm64" ] || fail "arch=$ARCH (нужен arm64)"
ok "arch=arm64, ssl=ok"

# ── 3. Удалить старый venv ────────────────────────────────────────────────────
info "Удаление старого venv..."
rm -rf .venv
if $ONLINE; then
    rm -f uv.lock
    ok "Старый venv и lock удалены"
else
    ok "Старый venv удалён (lock сохранён для офлайн-установки)"
fi

# ── 4. Создать новый venv ─────────────────────────────────────────────────────
info "Создание venv с Python: $PYTHON"
uv venv --python "$PYTHON"
ok "venv создан: $(grep '^home' .venv/pyvenv.cfg 2>/dev/null || echo .venv)"

# ── 5. Проверить arch в новом venv ───────────────────────────────────────────
VENV_ARCH=$(uv run python -c "import platform; print(platform.machine())" 2>/dev/null || echo "unknown")
if [ "$VENV_ARCH" != "arm64" ]; then
    fail "venv Python сообщает arch=$VENV_ARCH — проблема не решена"
fi
ok "venv arch=arm64 подтверждён"

# ── 6. Разрешить зависимости и установить ────────────────────────────────────
if $ONLINE; then
    info "uv lock (разрешение зависимостей из PyPI)..."
    uv lock
    ok "Lock файл сгенерирован"

    info "uv sync --group dev..."
    uv sync --group dev
else
    if [ ! -f uv.lock ]; then
        fail "uv.lock не найден — для первой установки нужен: ./fix_env.sh --online"
    fi
    info "uv sync --frozen --group dev (без обращения к сети)..."
    uv sync --frozen --group dev
fi
ok "Зависимости установлены"

# ── 7. Установить пакет в editable-режиме ────────────────────────────────────
info "Установка pa-clean (editable)..."
uv pip install -e . --no-deps
ok "Пакет установлен"

# ── 8. Проверки ──────────────────────────────────────────────────────────────
echo ""
info "Проверка результата..."

SSL=$(uv run python -c "import ssl; print('ok')" 2>/dev/null || echo "FAIL")
echo "  ssl:       $SSL"

MLX=$(uv run python -c "import mlx; print('ok')" 2>/dev/null || echo "not installed")
echo "  mlx:       $MLX"

MLXLM=$(uv run python -c "import mlx_lm; print('ok')" 2>/dev/null || echo "not installed")
echo "  mlx_lm:    $MLXLM"

PA=$(uv run python -c "import personal_assistant; print('ok')" 2>/dev/null || echo "FAIL")
echo "  pa import: $PA"

CLI=$(uv run pa --help >/dev/null 2>&1 && echo "ok" || echo "FAIL")
echo "  pa CLI:    $CLI"

echo ""
if [ "$SSL" = "ok" ] && [ "$PA" = "ok" ] && [ "$CLI" = "ok" ]; then
    ok "Готово!"
    echo ""
    echo "Дальше:"
    echo "  uv run pa check           — проверить TCC-права и MLX"
    echo "  uv run pa serve           — запустить WebUI (http://127.0.0.1:8000)"
    echo "  open docs/UAT.md          — пройти пользовательскую приёмку"
    echo ""
    if [ ! -f webui/dist/index.html ]; then
        warn "WebUI dist не собран — выполните:"
        echo "    (cd webui && npm install && npm run build)"
    fi
    if [ "$MLXLM" = "not installed" ]; then
        warn "mlx_lm не установлен — LLM-инференс недоступен (чат вернёт 503)"
        if $ONLINE; then
            warn "Убедитесь, что mlx-lm прописан в pyproject.toml и повторите --online"
        else
            warn "Для установки mlx_lm нужен интернет: ./fix_env.sh --online"
        fi
    fi
else
    fail "Остались проблемы. Вывод выше показывает что именно сломано."
fi
