#!/bin/bash
# Обёртка запуска: если Styler упал при старте, окно терминала не закрывается,
# а показывает ошибку — иначе падение выглядит как «ничего не запустилось».
DIR="${OSHH_STYLER_DIR:-/usr/share/oshh-styler}"
python3 -I "$DIR/main.py" "$@"
code=$?
if (( code != 0 )); then
  echo
  echo "OSHH Styler завершился с ошибкой (код $code)."
  read -r -p "Нажми Enter, чтобы закрыть окно… " _
fi
exit $code
