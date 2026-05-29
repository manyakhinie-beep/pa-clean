#!/usr/bin/env bash
# build_pyinstaller.sh — собрать PaClean.app через PyInstaller + PyWebView.
#
# Альтернатива build_pilot.sh (который через PyApp).  Главные отличия:
#   * НЕ нужен Rust toolchain / cargo
#   * НЕ нужен доступ к crates.io
#   * Бандл self-contained (~600-800 MB), запускается полностью офлайн
#   * Открывает нативное macOS-окно через PyWebView (не системный браузер)
#   * Нет PyApp self-update, обновление через ре-скачивание .dmg
#
# Запуск:
#   ./scripts/build_pyinstaller.sh             # сборка в dist/
#   ./scripts/build_pyinstaller.sh --clean     # rm -rf dist/ перед сборкой
#
# Требования:
#   - macOS arm64 (только Apple Silicon)
#   - uv (для wheel-builder и temp-venv)
#   - PyPI доступен (нужен только при первой сборке для установки
#     PyInstaller + PyWebView + pyobjc)

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

# ── Парсинг аргументов ────────────────────────────────────────────────────────
CLEAN=false
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=true ;;
        --help|-h)
            cat <<EOF
Usage: $0 [--clean]
  --clean    Remove dist/ and build/ before building.
EOF
            exit 0 ;;
    esac
done

# ── Проверки окружения ────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]]   || fail "macOS only."
[[ "$(uname -m)" == "arm64" ]] || fail "Apple Silicon only (MLX dependency)."
command -v uv >/dev/null       || fail "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"

VERSION=$(grep '^version = ' pyproject.toml | head -1 | sed -E 's/version = "([^"]+)"/\1/')
[[ -n "$VERSION" ]] || fail "couldn't extract version from pyproject.toml"
info "Building PaClean $VERSION via PyInstaller+PyWebView"

# ── Очистка ───────────────────────────────────────────────────────────────────
if $CLEAN; then
    info "[clean] rm -rf dist/ build/ packaging/pyinstaller/build/"
    rm -rf dist build packaging/pyinstaller/build packaging/pyinstaller/dist 2>/dev/null || true
fi

# ── Шаг 1: WebUI должен быть собран ──────────────────────────────────────────
info "[1/4] Checking webui/dist/"
if [[ ! -f "$REPO_ROOT/webui/dist/index.html" ]]; then
    fail "webui/dist/ отсутствует.  Соберите перед запуском:
    cd webui && npm install && npm run build"
fi
ok "WebUI assets: $(du -sh webui/dist | cut -f1)"

# ── Шаг 2: временный venv с PyInstaller + PyWebView ─────────────────────────
BUILD_VENV="$REPO_ROOT/.venv-pyinstaller"
info "[2/4] Setting up build venv ($BUILD_VENV)"

if [[ ! -d "$BUILD_VENV" ]]; then
    uv venv --python 3.13 "$BUILD_VENV"
fi

# shellcheck disable=SC1091
source "$BUILD_VENV/bin/activate"

info "Installing pa-clean (editable) + build deps"
# Editable установка чтобы PyInstaller видел свежие изменения исходников
uv pip install --quiet -e "$REPO_ROOT"
# PyInstaller — сам сборщик
uv pip install --quiet 'pyinstaller>=6.10'
# PyWebView + pyobjc для macOS Cocoa backend
uv pip install --quiet 'pywebview>=5.0'
uv pip install --quiet 'pyobjc-core>=10.0' 'pyobjc-framework-WebKit>=10.0' 'pyobjc-framework-Cocoa>=10.0'

ok "Build env ready"

# ── Шаг 3: PyInstaller ───────────────────────────────────────────────────────
info "[3/4] Running PyInstaller (5-15 minutes typically)"

cd "$REPO_ROOT/packaging/pyinstaller"
rm -rf build dist 2>/dev/null || true

pyinstaller --clean --noconfirm PaClean.spec 2>&1 | grep -vE '^[0-9]+ INFO: ' || true

APP_DIR="$REPO_ROOT/packaging/pyinstaller/dist/PaClean.app"
[[ -d "$APP_DIR" ]] || fail "PyInstaller did not produce $APP_DIR"

ok "Built: $APP_DIR ($(du -sh "$APP_DIR" | cut -f1))"

# ── Шаг 4: ad-hoc подпись + перемещение в основной dist/ ─────────────────────
info "[4/4] Ad-hoc signing + moving to dist/"

# Без подписи macOS на arm64 вообще не запустит.  Ad-hoc подходит для
# friendly testers (через Ctrl+click → Open); для широкого пилота
# нужен Developer ID (см. docs/PILOT_DISTRIBUTION.md).
codesign --force --deep --sign - "$APP_DIR" 2>&1 \
    | grep -v "^.*: replacing existing signature" || true
codesign --verify --verbose "$APP_DIR" 2>&1 | tail -2

mkdir -p "$REPO_ROOT/dist"
FINAL_APP="$REPO_ROOT/dist/PaClean.app"
rm -rf "$FINAL_APP"
mv "$APP_DIR" "$FINAL_APP"
ok "Moved: $FINAL_APP"

# Опционально: .dmg для удобной раздачи
if command -v create-dmg >/dev/null; then
    DMG="$REPO_ROOT/dist/PaClean-${VERSION}-arm64.dmg"
    rm -f "$DMG"
    info "Building .dmg"
    create-dmg \
        --volname "PaClean ${VERSION}" \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "PaClean.app" 175 190 \
        --app-drop-link 425 190 \
        --no-internet-enable \
        "$DMG" "$FINAL_APP" >/dev/null 2>&1 || warn "create-dmg failed, скиппнул"
    [[ -f "$DMG" ]] && ok "DMG: $DMG ($(du -h "$DMG" | cut -f1))"
else
    warn "create-dmg не установлен — пропускаю .dmg.  brew install create-dmg"
fi

echo
ok "Build complete."
echo "  Run:  open $FINAL_APP"
echo "  Logs: ~/Library/Logs/PaClean/server.log"
