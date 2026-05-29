#!/usr/bin/env bash
# ff_uninstall.sh — удалить PaClean с машины F&F-тестера.
#
# Удаляет: ~/PaClean (репо + venv), ~/Library/Application Support/PaClean,
# ~/Library/Logs/PaClean, ярлык на Рабочем столе.  НЕ трогает:
# ~/PaCleanVault (там пользовательские данные — пусть решит сам), .cache/
# huggingface (там может быть тяжёлая модель, удалит сам если хочет).

set -e

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}▶${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }

cat <<'EOF'

  ┌──────────────────────────────────────────────┐
  │       PaClean — удаление                     │
  └──────────────────────────────────────────────┘

EOF

echo "Будут удалены:"
echo "  ~/PaClean                                (репозиторий + Python)"
echo "  ~/Library/Application Support/PaClean    (настройки)"
echo "  ~/Library/Logs/PaClean                   (логи)"
echo "  ~/Desktop/PaClean.command                (ярлык)"
echo
echo "Что НЕ будет удалено (решите сами потом):"
echo "  ~/PaCleanVault                           (ваши синхронизированные данные)"
echo "  ~/.cache/huggingface                     (скачанная модель, ~6-12 GB)"
echo
read -r -p "Продолжить удаление? (y/N) " answer
[[ "${answer:0:1}" =~ [yYдД] ]] || { echo "Отменено."; exit 0; }

info "Удаляю..."
rm -rf "$HOME/PaClean"
rm -rf "$HOME/Library/Application Support/PaClean"
rm -rf "$HOME/Library/Logs/PaClean"
rm -f  "$HOME/Desktop/PaClean.command"
ok "PaClean удалён"

echo
echo "Чтобы освободить место от модели и vault-а:"
echo "  rm -rf ~/PaCleanVault                    # ваши данные синка"
echo "  rm -rf ~/.cache/huggingface/hub/models--RockTalk--GigaChat3.1*"
echo
