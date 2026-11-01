"""Экран блокировки (hyprlock, hypridle) и экран входа (greetd).

Конфиги правятся построчно: комментарии и ручные правки пользователя остаются на месте.
"""
import getpass
import json
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image

HOME = Path.home()
HYPRLOCK = HOME / ".config/hypr/hyprlock.conf"
HYPRIDLE = HOME / ".config/hypr/hypridle.conf"
GREETER_DIR = Path("/var/lib/oshh/greeter")
GREETER_SETTINGS = GREETER_DIR / "settings.json"
GREETER_BACKGROUND = GREETER_DIR / "background"
LOGO_DIR = HOME / ".config/oshh/lock-logo"
LOGO_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
THEME_DIR = HOME / ".config/omarchy/current/theme"
BACKGROUND = HOME / ".config/omarchy/current/background"

LOCK_PREVIEW_GRACE = 30

CLOCK_BEGIN = "# oshh-style: часы (начало)"
CLOCK_END = "# oshh-style: часы (конец)"
LOGO_BEGIN = "# oshh-style: логотип (начало)"
LOGO_END = "# oshh-style: логотип (конец)"


def section_bounds(lines, section):
    start = next((i for i, line in enumerate(lines) if re.match(rf"\s*{re.escape(section)}\s*\{{", line)), None)
    if start is None:
        return None
    depth = 0
    for index in range(start, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        if depth == 0:
            return start, index
    return None


def conf_get(text, section, key, default=None):
    lines = text.splitlines()
    bounds = section_bounds(lines, section)
    if bounds:
        for line in lines[bounds[0] + 1:bounds[1]]:
            match = re.match(rf"\s*{re.escape(key)}\s*=\s*(.*?)\s*$", line)
            if match:
                return match.group(1)
    return default


def conf_set(text, section, key, value):
    lines = text.splitlines()
    bounds = section_bounds(lines, section)
    if bounds is None:
        return text.rstrip("\n") + f"\n\n{section} {{\n    {key} = {value}\n}}\n"
    for index in range(bounds[0] + 1, bounds[1]):
        match = re.match(rf"(\s*){re.escape(key)}\s*=", lines[index])
        if match:
            lines[index] = f"{match.group(1)}{key} = {value}"
            break
    else:
        lines.insert(bounds[1], f"    {key} = {value}")
    return "\n".join(lines) + "\n"


def lock_get(section, key, default=None):
    try:
        return conf_get(HYPRLOCK.read_text(), section, key, default)
    except OSError:
        return default


def lock_set(section, key, value):
    HYPRLOCK.write_text(conf_set(HYPRLOCK.read_text(), section, key, value))


def clock_block(settings):
    font = lock_get("input-field", "font_family", "monospace")
    time_format = "%I:%M" if settings.get("clock_format") == "12" else "%H:%M"
    block = [CLOCK_BEGIN,
             "label {", "    monitor =", f'    text = cmd[update:1000] date +"{time_format}"',
             "    color = $font_color", f"    font_family = {font}", "    font_size = 96",
             "    position = 0, -120", "    halign = center", "    valign = top", "}"]
    if settings.get("date", True):
        block += ["label {", "    monitor =", '    text = cmd[update:60000] date +"%A, %-d %B"',
                  "    color = $font_color", f"    font_family = {font}", "    font_size = 22",
                  "    position = 0, -260", "    halign = center", "    valign = top", "}"]
    return block + [CLOCK_END]


def write_clock(settings):
    """Часы — свой блок с метками: в hyprlock нет настройки «показать часы», это отдельные виджеты."""
    text = re.sub(rf"\n*{re.escape(CLOCK_BEGIN)}.*?{re.escape(CLOCK_END)}\n?", "\n", HYPRLOCK.read_text(), flags=re.S)
    if settings.get("clock", False):
        text = text.rstrip("\n") + "\n\n" + "\n".join(clock_block(settings)) + "\n"
    HYPRLOCK.write_text(text)


def built_in_logos():
    omarchy = HOME / ".local/share/omarchy/icon.png"
    logos = (("/usr/share/icons/hicolor/256x256/bazzite-logo-icon.png", "Bazzite"),
             ("/usr/share/pixmaps/fedora-logo.png", "Fedora"), (str(omarchy), "Omarchy"))
    return [item for item in logos if Path(item[0]).is_file()]


def logo_settings(state):
    """Значения из конфига, сверху то, что выбрано в приложении. Выключенный логотип не теряет настройки."""
    position = lock_get("image", "position", "0, 190").split(",")
    settings = {"show": lock_get("image", "path") is not None, "path": lock_get("image", "path", ""),
                "size": int(lock_get("image", "size", "120")), "y": int(position[-1]),
                "shape": int(lock_get("image", "rounding", "-1"))}
    return {**settings, **state["lock"].get("logo", {})}


def logo_block(settings):
    return [LOGO_BEGIN, "image {", "    monitor =", f"    path = {settings['path']}", f"    size = {settings['size']}",
            f"    rounding = {settings['shape']}", "    border_size = 0", f"    position = 0, {settings['y']}",
            "    halign = center", "    valign = center", "}", LOGO_END]


def write_logo(settings):
    """Логотип — блок с метками. При первой записи он заменяет блок image из исходного конфига."""
    text = HYPRLOCK.read_text()
    marked = re.compile(rf"\n*{re.escape(LOGO_BEGIN)}.*?{re.escape(LOGO_END)}\n?", flags=re.S)
    if marked.search(text):
        text = marked.sub("\n", text)
    else:
        lines = text.splitlines()
        bounds = section_bounds(lines, "image")
        if bounds:
            del lines[bounds[0]:bounds[1] + 1]
        text = "\n".join(lines) + "\n"
    if settings["show"]:
        text = text.rstrip("\n") + "\n\n" + "\n".join(logo_block(settings)) + "\n"
    HYPRLOCK.write_text(text)


def add_logo(source):
    """Копия у нас: исходный файл можно удалить или перенести. Возвращает (путь, ошибка)."""
    source = Path(source).expanduser()
    if not source.is_file():
        return None, "Файл не найден"
    if source.suffix.lower() not in LOGO_TYPES:
        return None, "Подходят картинки: " + ", ".join(sorted(LOGO_TYPES))
    try:
        with Image.open(source) as image:
            image.verify()
    except (OSError, ValueError):
        return None, "Файл не читается как картинка"
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    target = LOGO_DIR / source.name
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target, None


def motion_demo():
    """Настоящее окно: откроется и закроется с текущими настройками, правила держат его маленьким."""
    subprocess.Popen(["xdg-terminal-exec", "--app-id=org.omarchy.oshh-demo", "--title=Анимация", "-e", "sleep", "1.5"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def idle_timeout():
    try:
        match = re.search(r"^\s*timeout\s*=\s*(\d+)", HYPRIDLE.read_text(), flags=re.M)
    except OSError:
        return 300
    return int(match.group(1)) if match else 300


def set_idle_timeout(seconds):
    HYPRIDLE.write_text(re.sub(r"^(\s*timeout\s*=\s*)\d+", rf"\g<1>{int(seconds)}", HYPRIDLE.read_text(), count=1, flags=re.M))
    subprocess.run(["omarchy-restart-hypridle"], capture_output=True, timeout=10)


def lock_now():
    subprocess.Popen(["omarchy-system-lock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def lock_preview():
    """Настоящий hyprlock с периодом без пароля. Экран не гаснет: omarchy-system-lock здесь не нужен."""
    if subprocess.run(["pidof", "hyprlock"], capture_output=True).returncode != 0:
        subprocess.Popen(["hyprlock", "--grace", str(LOCK_PREVIEW_GRACE)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


GREETER_DEFAULTS = {
    "background": "theme",
    "dim": 0.35,
    "blur": 0.5,
    "title": "OSHH",
    "clock": True,
    "remember_user": True,
    "panel": "center",
}


def greeter_settings(state):
    return {**GREETER_DEFAULTS, **state["greeter"]}


def greeter_ready():
    """Каталог создаёт образ (tmpfiles.d) с записью для группы wheel, root для настройки не нужен."""
    return GREETER_DIR.is_dir() and shutil.os.access(GREETER_DIR, shutil.os.W_OK)


def publish_greeter(settings, colors):
    """Собирает файлы для экрана входа. Экран запускается от greetd и домашний каталог не читает."""
    if not greeter_ready():
        return "Настройки сохранены и применятся после обновления образа: на этой системе нет " + str(GREETER_DIR)
    data = {
        "title": str(settings["title"])[:32],
        "dim": float(settings["dim"]),
        "blur": float(settings["blur"]),
        "clock": bool(settings["clock"]),
        "panel": settings["panel"],
        "user": getpass.getuser() if settings["remember_user"] else "",
        "image": False,
        "colors": {key: colors[key] for key in ("background", "foreground", "accent", "color1", "color8") if key in colors},
    }
    source = None
    if settings["background"] == "theme" and BACKGROUND.exists():
        source = BACKGROUND
    if source:
        temporary = GREETER_BACKGROUND.with_suffix(".tmp")
        shutil.copyfile(source, temporary)
        temporary.replace(GREETER_BACKGROUND)
        data["image"] = True
    temporary = GREETER_SETTINGS.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False))
    temporary.replace(GREETER_SETTINGS)
    return None
