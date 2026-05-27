#!/usr/bin/env bash
# make.sh — one-command bootstrap + run for pa-clean on macOS Apple Silicon.
#
# What it does
#   1. uv sync --no-dev  → install only runtime deps (no pytest/ruff/mypy/cov).
#      Faster (.venv ~700 MB vs ~1.2 GB) and avoids corporate proxies that
#      block dev-only packages like ``coverage``.
#   2. (cd webui && npm install && npm run build) → builds the WebUI bundle
#      into webui/dist/.
#   3. With ``--serve`` (default): runs ``pa serve`` on http://127.0.0.1:8000.
#      With ``--no-serve``: stops after the build.
#
# Usage
#   ./make.sh                  → install + build + start the server (recommended)
#   ./make.sh --no-serve       → install + build only (CI, restart later by hand)
#   ./make.sh --skip-webui     → skip ``npm run build`` (use the committed
#                                webui/dist/ as-is)
#   ./make.sh --dev            → also install the ``dev`` group (pytest/ruff/…)
#                                — when the corporate proxy allows it
#   ./make.sh --help
#
# After a successful run with ``--serve`` (default):
#   * WebUI:           http://127.0.0.1:8000
#   * Verify install:  uv run --no-sync pa check
#   * Stop the server: Ctrl+C
#
# When to prefer ``fix_env.sh --online`` over this script
#   fix_env.sh is the recovery script: it nukes a broken .venv, locates an
#   arm64 Python via python-build-standalone, and reseeds the lock. Use it
#   when ``make.sh`` fails on SSL / Python-arch / Rosetta errors. This
#   script assumes uv and Python are already healthy.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}▶${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; exit 1; }

# ── Argument parsing ──────────────────────────────────────────────────────────
SERVE=true
SKIP_WEBUI=false
WITH_DEV=false
for arg in "$@"; do
    case "$arg" in
        --serve)        SERVE=true ;;
        --no-serve)     SERVE=false ;;
        --skip-webui)   SKIP_WEBUI=true ;;
        --dev)          WITH_DEV=true ;;
        -h|--help)
            sed -n '1,28p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) fail "Неизвестный аргумент: $arg  (используйте --help)" ;;
    esac
done

# ── Preflight: uv must be on PATH ─────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    fail "uv не найден в PATH. Установите: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi
info "uv: $(uv --version 2>&1)"

# ── 1. Install Python deps ────────────────────────────────────────────────────
if $WITH_DEV; then
    info "uv sync (с группой dev — pytest/ruff/mypy/locust)"
    uv sync --group dev
else
    info "uv sync --no-dev (только runtime; быстрее и без проксируемого блока)"
    uv sync --no-dev
fi
ok "venv готов: $(.venv/bin/python --version 2>/dev/null || python3 --version)"

# ── 2. WebUI build ────────────────────────────────────────────────────────────
if $SKIP_WEBUI; then
    warn "Пропускаем npm-сборку (--skip-webui). Будет использован существующий webui/dist."
    if [ ! -f "webui/dist/index.html" ]; then
        warn "webui/dist/index.html отсутствует — UI может не работать."
    fi
else
    if ! command -v npm >/dev/null 2>&1; then
        warn "npm не найден — пропускаю сборку WebUI. UI будет работать из закоммиченного dist/."
    else
        info "npm install + npm run build (webui)"
        (cd webui && npm install --no-fund --no-audit && npm run build) \
            || fail "Сборка WebUI упала — посмотрите вывод npm выше."
        if [ -f "webui/dist/index.html" ]; then
            ok "WebUI собран: webui/dist/index.html"
        else
            fail "После npm run build нет webui/dist/index.html — что-то пошло не так."
        fi
    fi
fi

# ── 3. Quick smoke check (env + config + paths) ───────────────────────────────
info "uv run --no-sync pa check (smoke-проверка окружения)"
if uv run --no-sync pa check 2>&1 | tail -20 | sed 's/^/    /'; then
    ok "pa check прошёл"
else
    warn "pa check вернул ошибку — см. лог выше. Это не блокирует запуск."
fi

# ── 4. Serve (or done) ────────────────────────────────────────────────────────
if $SERVE; then
    info "Запускаю pa serve на http://127.0.0.1:8000 (Ctrl+C для остановки)"
    exec uv run --no-sync pa serve
else
    ok "Сборка завершена. Запустить вручную: uv run --no-sync pa serve"
fi
