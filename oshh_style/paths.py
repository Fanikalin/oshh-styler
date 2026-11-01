"""Пути Omarchy для обеих раскладок.

В Omarchy 4 состояние тем переехало из ~/.config/omarchy/current в
~/.local/state/omarchy/current, а темы — из ~/.local/share/omarchy в
/usr/share/omarchy. Здесь выбирается то, что есть на машине.
"""
import os
from datetime import datetime
from pathlib import Path

HOME = Path.home()
STATE = Path(os.environ.get("OMARCHY_STATE", HOME / ".local/state/omarchy"))
_OMARCHY_DEFAULT = Path(os.environ.get("OMARCHY_PATH", HOME / ".local/share/omarchy"))


def current_dir():
    """Активная тема: новое состояние (Omarchy 4+) или старый конфиг (Omarchy 3)."""
    for candidate in (STATE / "current", HOME / ".config/omarchy/current"):
        if candidate.exists():
            return candidate
    return HOME / ".config/omarchy/current"


def omarchy_dir():
    """Каталог установки с темами: пакет в /usr/share/omarchy или checkout в домашнем каталоге."""
    for candidate in (Path("/usr/share/omarchy"), _OMARCHY_DEFAULT):
        if (candidate / "themes").is_dir():
            return candidate
    return _OMARCHY_DEFAULT


def log_line(text):
    """Обычная строка в журнал (диагностика без ошибки)."""
    try:
        target = STATE / "styler.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as output:
            output.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
    except OSError:
        pass


def terminal_name():
    """Имя терминала по цепочке родительских процессов: TERM может быть переопределён конфигом."""
    pid = os.getpid()
    for _ in range(10):
        try:
            with open(f"/proc/{pid}/comm") as handle:
                name = handle.read().strip()
            with open(f"/proc/{pid}/status") as handle:
                ppid = next(int(line.split()[1]) for line in handle if line.startswith("PPid:"))
        except (OSError, StopIteration, ValueError):
            return ""
        if name in ("kitty", "ghostty", "wezterm", "foot", "alacritty", "konsole", "xterm"):
            return name
        if pid <= 1:
            return ""
        pid = ppid
    return ""


def log_error(error):
    """Падение фонового потока не должно ронять приложение: пишем в журнал и показываем сообщение."""
    import datetime
    import traceback

    try:
        target = STATE / "styler.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as output:
            output.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {type(error).__name__}: {error}\n")
            output.write("".join(traceback.format_exception(error)).rstrip() + "\n\n")
    except OSError:
        pass
