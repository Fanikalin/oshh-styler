#!/bin/bash
# Обёртка запуска. Две защиты:
# 1) выбирает python3, в котором есть нужные модули (в PATH может быть чужой
#    интерпретатор без rich, тогда «No module named rich» при установленном пакете);
# 2) если Styler упал при старте — окно терминала не закрывается, а показывает ошибку.
DIR="${OSHH_STYLER_DIR:-/usr/share/oshh-styler}"

die() {
  echo
  echo "OSHH Styler: $1"
  read -r -p "Нажми Enter, чтобы закрыть окно… " _
  exit 1
}

PY=
for candidate in /usr/bin/python3 python3; do
  if "$candidate" -I -c 'import textual, rich, PIL' >/dev/null 2>&1; then
    PY=$candidate
    break
  fi
done
[[ -n $PY ]] || die "не найден python3 с модулями textual, rich, pillow. Поставь пакеты python-textual, python-rich, python-pillow."

"$PY" -I "$DIR/main.py" "$@"
code=$?
if (( code != 0 )); then
  echo
  echo "OSHH Styler завершился с ошибкой (код $code)."
  read -r -p "Нажми Enter, чтобы закрыть окно… " _
fi
exit $code
