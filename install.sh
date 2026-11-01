#!/bin/bash
# Установка OSHH Styler одной командой (Arch):
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/Fanikalin/oshh-styler/main/install.sh)"
set -euo pipefail

REPO=Fanikalin/oshh-styler

say() { printf ':: %s\n' "$*"; }
die() { printf 'oshh-styler: %s\n' "$*" >&2; exit 1; }

[[ $(id -u) -ne 0 ]] || die "запускайте обычным пользователем, sudo скрипт попросит сам"
command -v pacman >/dev/null 2>&1 || die "нужен Arch Linux с pacman"
command -v makepkg >/dev/null 2>&1 || sudo pacman -S --needed base-devel

if ! command -v omarchy >/dev/null 2>&1; then
  say "Omarchy не найден. Разделы «Тема», «Обои», «Шрифты» и часть настройки рассчитаны на Omarchy (https://omarchy.org); остальное работает и без него."
  if read -r -p "Продолжить установку? [Y/n] " answer </dev/tty 2>/dev/null && [[ $answer == [nN]* ]]; then
    exit 1
  fi
fi

ver=$(git ls-remote --tags --refs "https://github.com/$REPO.git" | cut -d/ -f3 | sort -V | tail -1)
[[ -n $ver ]] || die "не удалось определить последнюю версию"
say "Установка OSHH Styler $ver"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/src.tar.gz" "https://github.com/$REPO/archive/refs/tags/$ver.tar.gz"
tar -xzf "$tmp/src.tar.gz" -C "$tmp"
cd "$tmp/oshh-styler-${ver#v}/packaging"
makepkg -si

say "Готово: команда oshh-styler (настройка системы: oshh-styler --setup) или пункт «OSHH Styler» в меню программ."
