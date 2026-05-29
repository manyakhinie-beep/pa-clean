#!/usr/bin/env bash
# build_pilot.sh — собирает macOS .app + .dmg для пилотного тестирования.
#
# Что делает:
#   1. uv build --wheel        → dist/pa_clean-*.whl
#   2. uv export --no-dev      → packaging/requirements-pyapp.txt
#   3. cargo install pyapp     → dist/bin/pyapp (Rust launcher с embedded
#                                wheelhouse — содержит весь pa-clean целиком)
#   4. wrap → dist/PaClean.app (Info.plist + executable + ad-hoc подпись)
#   5. dmg → dist/PaClean-pilot-<version>-arm64.dmg
#
# Что НЕ делает:
#   - не подписывает Developer ID (нет certificate-а).  Используется ad-hoc
#     подпись (codesign --sign -) — корпоративные MDM-маки могут блокировать.
#     Для команды разработки и friendly testers через Ctrl-клик → Открыть
#     работает.
#   - не нотарирует (требует Developer ID).
#   - не качает модельные веса — это делается мастером WebUI при первом
#     запуске у пользователя.
#
# Требования:
#   - macOS arm64 (только Apple Silicon — MLX не работает на Intel).
#   - uv (uv.lock в репозитории).
#   - cargo (Rust toolchain; install: curl https://sh.rustup.rs | sh -s -- -y).
#   - create-dmg (опционально; brew install create-dmg).  Без него dmg-шаг
#     пропускается, остаётся только .app.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# ── Argument parsing ──────────────────────────────────────────────────────────
LAUNCHER_FROM=""
SKIP_WHEELHOUSE_STRICT=false
for arg in "$@"; do
    case "$arg" in
        --launcher-from=*)
            # Путь к уже собранному PyApp launcher (например, скачанный
            # из GitHub Actions артефакта).  Полезно когда локально
            # ``cargo install pyapp`` падает из-за заблокированного
            # ``index.crates.io`` в корпоративной сети.
            LAUNCHER_FROM="${arg#*=}" ;;
        --lenient-wheelhouse)
            # Не падать если wheelhouse получился меньше 50 wheels —
            # для отладки.  В обычной сборке требуем полный.
            SKIP_WHEELHOUSE_STRICT=true ;;
        --help|-h)
            cat <<EOF
Usage: $0 [--launcher-from=PATH] [--lenient-wheelhouse]

  --launcher-from=PATH      Use a pre-built PyApp launcher binary
                            (skips Rust/cargo build).  Useful when
                            crates.io is blocked locally; build via
                            GitHub Actions, download artifact, pass here.
  --lenient-wheelhouse      Don't fail if wheel download is partial.
EOF
            exit 0 ;;
    esac
done

# ── Утилиты вывода ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}▶${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; exit 1; }

# ── Проверки окружения ────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]] || fail "build_pilot.sh works on macOS only."
[[ "$(uname -m)" == "arm64" ]] || fail "build_pilot.sh requires Apple Silicon (arm64)."

command -v uv >/dev/null || fail "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
if [[ -z "$LAUNCHER_FROM" ]]; then
    command -v cargo >/dev/null || fail "cargo not found. Install: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
Tip: if your network blocks crates.io, run the build on GitHub Actions
(see .github/workflows/build-pilot.yml) and pass the launcher artifact
via --launcher-from=<path>."
else
    # Валидируем --launcher-from путь СРАЗУ, не после 2-минутного wheelhouse.
    LAUNCHER_FROM_RESOLVED="$LAUNCHER_FROM"
    # Раскрываем ~ если передан как литерал
    LAUNCHER_FROM_RESOLVED="${LAUNCHER_FROM_RESOLVED/#\~/$HOME}"
    [[ -f "$LAUNCHER_FROM_RESOLVED" ]] || fail "--launcher-from points to a missing file:
    $LAUNCHER_FROM_RESOLVED
Download the 'pyapp-launcher-arm64-<sha>' artifact from the GitHub
Actions run (workflow: build-pilot.yml) and try again."
    LAUNCHER_FROM="$LAUNCHER_FROM_RESOLVED"
fi

# pip — используется на шаге 3 (download wheels).  Newer uv has 'uv pip
# download' but older versions don't — мы используем тот, что доступен,
# с фолбэком на python3 -m pip.
PIP_DOWNLOAD_CMD=()
if uv pip download --help >/dev/null 2>&1; then
    PIP_DOWNLOAD_CMD=(uv pip download)
elif python3 -m pip --version >/dev/null 2>&1; then
    PIP_DOWNLOAD_CMD=(python3 -m pip download)
else
    fail "Neither 'uv pip download' nor 'python3 -m pip' is available.
Install pip:  python3 -m ensurepip --user
Or upgrade uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# ── Версия из pyproject.toml ──────────────────────────────────────────────────
VERSION=$(grep '^version = ' pyproject.toml | head -1 | sed -E 's/version = "([^"]+)"/\1/')
[[ -n "$VERSION" ]] || fail "Couldn't extract version from pyproject.toml"
PILOT_TAG="${VERSION}-pilot"
info "Building PaClean ${PILOT_TAG} for arm64"

# ── Чистый dist ───────────────────────────────────────────────────────────────
DIST="$REPO_ROOT/dist"
APP_DIR="$DIST/PaClean.app"
rm -rf "$APP_DIR" "$DIST/PaClean-pilot"*.dmg "$DIST/pa_clean-"*.whl 2>/dev/null || true
mkdir -p "$DIST"

# ── Шаг 1: собрать Python-wheel ───────────────────────────────────────────────
info "[1/5] Building pa-clean wheel via uv build"
uv build --wheel --out-dir "$DIST" >/dev/null
WHEEL=$(ls "$DIST"/pa_clean-*.whl | head -1)
[[ -f "$WHEEL" ]] || fail "wheel build failed"
ok "wheel: $(basename "$WHEEL") ($(du -h "$WHEEL" | cut -f1))"

# ── Шаг 2: lock-файл всех runtime-зависимостей ────────────────────────────────
info "[2/5] Exporting runtime requirements for embedded wheelhouse"
LOCK="$REPO_ROOT/packaging/requirements-pyapp.txt"
uv export --no-dev --no-emit-project --format requirements-txt > "$LOCK"
ok "lock: $LOCK ($(wc -l < "$LOCK" | tr -d ' ') deps)"

# ── Шаг 3: собираем wheels всех зависимостей в один каталог ──────────────────
#
# PyApp умеет два режима embedded wheelhouse:
#   а) full embed: складываем все wheels в локальный каталог и натравливаем
#      pip --no-index --find-links=<dir>;
#   б) lock-mode: pip ставит из PyPI с хэш-проверкой (требует сеть).
# Для пилота берём (а) — оффлайн-bootstrap обязателен.

WHEELHOUSE="$DIST/wheelhouse"
rm -rf "$WHEELHOUSE"
mkdir -p "$WHEELHOUSE"
cp "$WHEEL" "$WHEELHOUSE/"

# Скачиваем все transitive wheels через pip download.  --platform=macosx_13_0_arm64
# гарантирует что MLX-обвязка и compiled-wheels берутся для нужной платформы.
# Используем заранее выбранный PIP_DOWNLOAD_CMD (uv pip download или
# python3 -m pip download) — см. блок выбора выше.
info "[3/5] Downloading transitive wheels into wheelhouse (this can take 2-5 min)"
info "    using: ${PIP_DOWNLOAD_CMD[*]}"
WHEELHOUSE_LOG="$DIST/wheelhouse.log"
if ! "${PIP_DOWNLOAD_CMD[@]}" \
    -r "$LOCK" \
    --dest "$WHEELHOUSE" \
    --platform macosx_13_0_arm64 \
    --python-version 3.13 \
    --only-binary=:all: \
    > "$WHEELHOUSE_LOG" 2>&1
then
    warn "pip download finished with errors — see $WHEELHOUSE_LOG"
    tail -10 "$WHEELHOUSE_LOG" >&2 || true
fi

WHEEL_COUNT=$(ls "$WHEELHOUSE"/*.whl 2>/dev/null | wc -l | tr -d ' ')
ok "wheelhouse: $WHEEL_COUNT wheels ($(du -sh "$WHEELHOUSE" | cut -f1))"

# Sanity: requirements насчитывает 100+ пакетов; 1-2 wheels означает
# что download полностью провалился (обычно — сетевой блок PyPI или
# конфликт платформы).  Запекать почти-пустой wheelhouse бессмысленно —
# бутстрап у пользователя всё равно полезет в PyPI и упадёт там же.
if [[ "$WHEEL_COUNT" -lt 50 && "$SKIP_WHEELHOUSE_STRICT" != "true" ]]; then
    fail "wheelhouse too small ($WHEEL_COUNT < 50 wheels). Check $WHEELHOUSE_LOG.
Common causes:
  * PyPI заблокирован прокси (увидите 'Could not resolve' или 403)
  * платформа не соответствует --platform=macosx_13_0_arm64
  * sdist-only пакеты в requirements-pyapp.txt
Bypass для отладки: $0 --lenient-wheelhouse"
fi

# ── Шаг 4: собираем (или импортируем) PyApp launcher ──────────────────────────
LAUNCHER="$DIST/bin/pyapp"
mkdir -p "$DIST/bin"

if [[ -n "$LAUNCHER_FROM" ]]; then
    info "[4/5] Importing pre-built PyApp launcher from $LAUNCHER_FROM"
    [[ -f "$LAUNCHER_FROM" ]] || fail "launcher not found at $LAUNCHER_FROM"
    cp "$LAUNCHER_FROM" "$LAUNCHER"
    chmod +x "$LAUNCHER"
    ok "launcher: $LAUNCHER ($(du -h "$LAUNCHER" | cut -f1)) [imported]"
else
    info "[4/5] Building PyApp launcher (cargo install pyapp)"

    # Экспортируем env-vars из packaging/pyapp.env
    set -a
    # shellcheck source=packaging/pyapp.env
    source "$REPO_ROOT/packaging/pyapp.env"
    set +a

    # Override: указываем локальные wheels и наш wheel pa-clean как primary.
    export PYAPP_PROJECT_NAME="pa-clean"
    export PYAPP_PROJECT_VERSION="$PILOT_TAG"
    export PYAPP_PROJECT_PATH="$WHEEL"
    export PYAPP_PROJECT_DEPENDENCY_FILE="$LOCK"

    # Pyapp скачает python-build-standalone (~150 MB) и встроит ссылку на него.
    # Для полного оффлайна можно подложить локальный архив через
    # PYAPP_DISTRIBUTION_PATH=<path>; для phase 1 этого не делаем.

    if ! CARGO_TARGET_DIR="$DIST/cargo-target" \
        cargo install pyapp --force --root "$DIST" 2>&1 \
        | grep -v "^   Compiling\|^    Updating\|^    Finished\|^   Installed"
    then
        fail "cargo install pyapp failed.
Common cause: your network blocks index.crates.io (corporate proxy).
Solutions:
  1) Build the launcher on GitHub Actions:
       git push -u origin <branch>        # workflow .github/workflows/build-pilot.yml
                                          # runs on macos-14 with full network
     Download the PyApp launcher binary from the artifact, then re-run:
       $0 --launcher-from=/path/to/pyapp
  2) Configure cargo to use a corporate crates.io mirror in
     ~/.cargo/config.toml (see https://doc.rust-lang.org/cargo/reference/registries.html)."
    fi

    [[ -x "$LAUNCHER" ]] || fail "PyApp launcher did not build at $LAUNCHER"
    ok "launcher: $LAUNCHER ($(du -h "$LAUNCHER" | cut -f1))"
fi

# ── Шаг 5: собираем .app бандл ────────────────────────────────────────────────
info "[5/5] Wrapping into PaClean.app bundle"

mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# Executable — переименовываем pyapp в PaClean (соответствует CFBundleExecutable)
cp "$LAUNCHER" "$APP_DIR/Contents/MacOS/PaClean"
chmod +x "$APP_DIR/Contents/MacOS/PaClean"

# Info.plist с подстановкой версии
sed "s/__VERSION__/$PILOT_TAG/g" \
    "$REPO_ROOT/packaging/Info.plist.template" \
    > "$APP_DIR/Contents/Info.plist"

# Кладём wheelhouse в Resources чтобы PyApp видел локальный путь при bootstrap
# (передаём через env-var на уровне launcher-а — см. packaging/entrypoint.py).
cp -R "$WHEELHOUSE" "$APP_DIR/Contents/Resources/wheelhouse"

# ad-hoc подпись — обязательна на arm64 даже для unsigned, иначе бандл
# вообще не запускается.  Это НЕ Developer ID, тестировщик увидит
# «приложение от неизвестного разработчика», но через Ctrl-клик → Открыть
# работает.
info "Applying ad-hoc signature"
codesign --force --deep --sign - "$APP_DIR" 2>&1 \
    | grep -v "^.*: replacing existing signature" || true
codesign --verify --verbose "$APP_DIR" 2>&1 | tail -2

ok "bundle: $APP_DIR ($(du -sh "$APP_DIR" | cut -f1))"

# ── Опциональный шаг: .dmg ────────────────────────────────────────────────────
if command -v create-dmg >/dev/null; then
    info "Building .dmg via create-dmg"
    DMG="$DIST/PaClean-${PILOT_TAG}-arm64.dmg"
    rm -f "$DMG"
    create-dmg \
        --volname "PaClean ${PILOT_TAG}" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 100 \
        --icon "PaClean.app" 175 190 \
        --app-drop-link 425 190 \
        --no-internet-enable \
        "$DMG" "$APP_DIR" >/dev/null 2>&1 || warn "create-dmg failed; falling back to zip"
    if [[ -f "$DMG" ]]; then
        ok "dmg: $DMG ($(du -h "$DMG" | cut -f1))"
    fi
else
    warn "create-dmg not installed — packing .app into a zip instead"
    ZIP="$DIST/PaClean-${PILOT_TAG}-arm64.zip"
    rm -f "$ZIP"
    (cd "$DIST" && zip -ry "$(basename "$ZIP")" "PaClean.app" >/dev/null)
    ok "zip: $ZIP ($(du -h "$ZIP" | cut -f1))"
fi

echo
ok "Build complete.  Distribute the bundle from dist/."
echo
echo "  Tester instructions:"
echo "    1. Download the .dmg or .zip."
echo "    2. Drag PaClean.app into ~/Applications."
echo "    3. Ctrl-click → Open → Open (first time only)."
echo "    4. First launch takes 1-3 minutes (Python bootstrap)."
echo "    5. WebUI opens automatically at http://127.0.0.1:8765"
