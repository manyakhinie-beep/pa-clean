#!/usr/bin/env bash
# make_icns.sh — собрать macOS ``.icns`` иконку из одного PNG-источника.
#
# macOS .icns должен содержать 10 размеров (16/32/64/128/256/512/1024px
# + retina-варианты).  Этот скрипт берёт один большой PNG (рекомендуется
# 1024×1024) и через ``sips`` + ``iconutil`` создаёт полный icns.
#
# Usage:
#   ./scripts/make_icns.sh                                  # дефолтные пути
#   ./scripts/make_icns.sh path/to/source.png path/out.icns # явные
#
# Требования:
#   - macOS (sips и iconutil — встроенные утилиты, не Brew)
#   - PNG не меньше 512×512; идеально 1024×1024
#
# Артефакт:
#   packaging/icon.icns  — общий для PyInstaller + PyApp сборок

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Аргументы ────────────────────────────────────────────────────────────────
SOURCE="${1:-$REPO_ROOT/packaging/icon-source.png}"
OUTPUT="${2:-$REPO_ROOT/packaging/icon.icns}"

# ── Утилиты вывода ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}▶${NC} $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; exit 1; }

# ── Sanity ────────────────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]] || fail "Нужен macOS (sips/iconutil)."
command -v sips >/dev/null     || fail "sips не найден (странно — он встроен в macOS)."
command -v iconutil >/dev/null || fail "iconutil не найден."
[[ -f "$SOURCE" ]] || fail "Источник не найден: $SOURCE

Сохраните самую большую версию иконки (1024×1024 PNG) как
${SOURCE#$REPO_ROOT/} и повторите.  Или запустите с явным путём:
  $0 path/to/your-icon.png"

# Проверим размер источника — слишком мелкий PNG даст размытое icns
SRC_WIDTH=$(sips -g pixelWidth "$SOURCE" 2>/dev/null | awk '/pixelWidth/ {print $2}')
if [[ -z "$SRC_WIDTH" || "$SRC_WIDTH" -lt 512 ]]; then
    fail "Источник слишком мелкий ($SRC_WIDTH px).
Нужен PNG не меньше 512×512; идеально 1024×1024."
fi

info "Source: $SOURCE (${SRC_WIDTH}×${SRC_WIDTH})"

# ── Генерация всех 10 размеров icon.iconset/ ─────────────────────────────────
ICONSET=$(mktemp -d)/icon.iconset
mkdir -p "$ICONSET"

# Стандарт Apple: пары normal + @2x (retina).  iconutil ждёт ровно эти имена.
declare -a SIZES=(
    "16 icon_16x16.png"
    "32 icon_16x16@2x.png"
    "32 icon_32x32.png"
    "64 icon_32x32@2x.png"
    "128 icon_128x128.png"
    "256 icon_128x128@2x.png"
    "256 icon_256x256.png"
    "512 icon_256x256@2x.png"
    "512 icon_512x512.png"
    "1024 icon_512x512@2x.png"
)

info "Generating 10 sizes via sips"
for entry in "${SIZES[@]}"; do
    size="${entry%% *}"
    name="${entry#* }"
    sips -z "$size" "$size" "$SOURCE" --out "$ICONSET/$name" \
        >/dev/null 2>&1 || fail "sips failed for $name"
done
ok "iconset prepared: $(ls "$ICONSET" | wc -l | tr -d ' ') files"

# ── Сборка .icns ─────────────────────────────────────────────────────────────
info "Building $OUTPUT via iconutil"
mkdir -p "$(dirname "$OUTPUT")"
iconutil -c icns "$ICONSET" -o "$OUTPUT"

ok "$OUTPUT ($(du -h "$OUTPUT" | cut -f1))"

# ── Cleanup ──────────────────────────────────────────────────────────────────
rm -rf "$(dirname "$ICONSET")"
