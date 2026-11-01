"""Модель настроек: один класс Setting на всё, что можно поменять строкой в интерфейсе."""
import re
import subprocess
from pathlib import Path

from . import hypr, looks, screens

HOME = Path.home()


class Store:
    """Сохранённые настройки и снимок параметров работающего Hyprland."""

    def __init__(self):
        self.state = hypr.load_state()
        self.refresh()

    def refresh(self):
        self.options = hypr.descriptions()
        self.leaves, self.curves = hypr.animations()

    def persist(self):
        hypr.save_state(self.state)
        hypr.write_overlay(self.state)

    def set_option(self, name, value):
        error = hypr.apply_live(hypr.lua_config(name, value))
        if error:
            return error
        self.state["hypr"][name] = value
        self.persist()
        return None

    def set_many(self, options, leaves=None):
        """Применяет пакет настроек разом: иначе пресет мигал бы на каждом параметре."""
        code = [hypr.CURVES] + [hypr.lua_config(name, value) for name, value in options.items() if name in self.options]
        code += [hypr.lua_animation(leaf, spec) for leaf, spec in (leaves or {}).items()]
        error = hypr.apply_live("\n".join(code))
        if error:
            return error
        self.state["hypr"].update({name: value for name, value in options.items() if name in self.options})
        self.state["animations"].update(leaves or {})
        self.persist()
        self.refresh()
        return None

    def reset_option(self, name):
        self.state["hypr"].pop(name, None)
        self.reload()

    def reset_all(self):
        self.state["hypr"].clear()
        self.state["animations"].clear()
        self.state["env"].clear()
        self.reload()

    def reload(self):
        self.persist()
        hypr.hyprctl("reload", "config-only")
        self.refresh()

    def animation(self, leaf):
        parents = {"windowsIn": "windows", "windowsOut": "windows", "windowsMove": "windows", "fadeIn": "fade",
                   "fadeOut": "fade", "layersIn": "layers", "layersOut": "layers", "workspacesIn": "workspaces",
                   "workspacesOut": "workspaces", "specialWorkspaceIn": "specialWorkspace",
                   "specialWorkspaceOut": "specialWorkspace", "fadeSwitch": "fade", "fadeDim": "fade",
                   "fadeShadow": "fade", "fadeLayersIn": "layersIn", "fadeLayersOut": "layersOut"}
        name = leaf
        while name:
            if name in self.state["animations"]:
                return dict(self.state["animations"][name])
            live = self.leaves.get(name)
            if live and live.get("overridden"):
                return {"enabled": live["enabled"], "speed": live["speed"], "bezier": live["bezier"], "style": live["style"]}
            name = parents.get(name, "global" if name != "global" else None)
        return {"enabled": True, "speed": 10, "bezier": "default", "style": ""}

    def set_animation(self, leaf, field, value):
        spec = self.animation(leaf)
        spec[field] = value
        error = hypr.apply_live(hypr.lua_animation(leaf, spec))
        if error:
            return error
        self.state["animations"][leaf] = spec
        self.persist()
        return None


class Setting:
    """kind: bool, int, float, choice, text, color, action."""

    def __init__(self, label, hint="", kind="action", lo=None, hi=None, step=None, choices=None,
                 getter=None, setter=None, resetter=None, modified=None, wide=False, verb="запустить", confirm=None,
                 preview=None, try_key=None, file=None, leaf=None, details=None, picker=None, marks=None, guard=None):
        self.verb, self.confirm = verb, confirm
        self.preview, self.try_key, self.file, self.leaf, self.details = preview, try_key, file, leaf, details
        self.option = None
        self.title = label
        self.picker, self.marks, self.guard = picker, marks, guard
        if kind == "float" and step is None and lo is not None and hi is not None:
            step = round((hi - lo) / 40, 2) or 0.01
        self.label, self.hint, self.kind = label, hint, kind
        self.lo, self.hi, self.step, self.choices = lo, hi, step, choices or []
        self.getter, self.setter, self.resetter, self.modified_check = getter, setter, resetter, modified
        self.wide = wide

    def get(self):
        return self.getter() if self.getter else None

    def set(self, value):
        return self.setter(value) if self.setter else None

    def can_reset(self):
        return self.resetter is not None and self.modified()

    def reset(self):
        if self.resetter:
            self.resetter()

    def modified(self):
        return bool(self.modified_check and self.modified_check())

    @property
    def previewable(self):
        return self.preview is not None or self.file is not None or self.details is not None

    def text(self):
        """Содержимое для текстовой панели справа: состояние системы или файл."""
        if self.details:
            return self.details()
        try:
            return self.file.read_text(errors="replace") if self.file else ""
        except OSError:
            return "Файла пока нет"

    def choice_label(self, value):
        return next((label for item, label in self.choices if item == value), str(value))


def color_text(value):
    """Цвет Hyprland 'aarrggbb 0deg' или 'rgba(rrggbbaa)' -> '#rrggbb' для образца."""
    text = str(value)
    match = re.search(r"rgba?\(([0-9a-fA-F]{6})", text)
    if match:
        return "#" + match.group(1).lower()
    match = re.match(r"([0-9a-fA-F]{8})\b", text)
    if match:
        return "#" + match.group(1)[2:].lower()
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", text)
    if match:
        return "#" + match.group(1).lower()
    if isinstance(value, int):
        return "#%06x" % (value & 0xffffff)
    return ""


def color_value(text):
    text = text.strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return f"rgb({text})"
    if re.fullmatch(r"[0-9a-fA-F]{8}", text):
        return f"rgba({text})"
    return text


def option(store, name, label=None, hint="", lo=None, hi=None, step=None, choices=None, kind=None):
    """Параметр Hyprland. Тип и границы приходят из самого Hyprland; неизвестный параметр пропускается."""
    item = store.options.get(name)
    if item is None:
        return None
    default = item["default"]
    current = item["current"]
    lo = item.get("min") if lo is None else lo
    hi = item.get("max") if hi is None else hi
    is_gap = isinstance(default, str) and re.fullmatch(r"\d+( \d+){3}", default)
    is_color = ":col." in name or ".col." in name or name.endswith(("color", "color_inactive", "col.splash", "background_color"))

    if kind is None:
        if item.get("map") and choices is None:
            pairs = [next(iter(entry.items())) for entry in item["map"]]
            choices = [(value, title) for title, value in sorted(pairs, key=lambda pair: pair[1])]
            kind = "choice"
        elif choices:
            kind = "choice"
        elif isinstance(default, bool):
            kind = "bool"
        elif is_gap:
            kind = "int"
        elif is_color:
            kind = "color"
        elif isinstance(default, int) and lo is not None and hi is not None and hi - lo <= 2:
            kind = "float"
        elif isinstance(default, int):
            kind = "int"
        elif isinstance(default, float):
            kind = "float"
        else:
            kind = "text"

    def read():
        value = store.state["hypr"].get(name, current)
        if is_gap and isinstance(value, str):
            return int(value.split()[0])
        if kind == "color":
            return color_text(value)
        if isinstance(value, list):
            return " ".join(str(part) for part in value)
        return value

    def write(value):
        if kind == "color":
            value = color_value(str(value))
        elif isinstance(default, list):
            value = [float(part) if "." in part else int(part) for part in str(value).split()]
        return store.set_option(name, value)

    if is_gap:
        lo, hi = (0 if lo is None else lo), (100 if hi is None else hi)
    if step is None and kind == "float":
        step = 0.05 if hi is not None and hi - (lo or 0) <= 2 else 0.1
    row = Setting(label or name.rsplit(":", 1)[-1], hint or item["description"], kind, lo, hi, step or 1, choices,
                  read, write, lambda: store.reset_option(name), lambda: name in store.state["hypr"])
    row.option = item
    return row


def animation_rows(store, leaf, title, styles):
    def field(key):
        return lambda: store.animation(leaf).get(key)

    def modified():
        return leaf in store.state["animations"]

    def reset():
        store.state["animations"].pop(leaf, None)
        store.reload()

    rows = [
        Setting(f"{title}: включено", "", "bool", getter=field("enabled"), modified=modified, resetter=reset,
                setter=lambda value: store.set_animation(leaf, "enabled", value)),
        Setting(f"{title}: длительность", "В десятых долях секунды: 5 — это 0,5 с. Меньше — быстрее.", "float", 0.1, 20, 0.5,
                getter=field("speed"), modified=modified, resetter=reset,
                setter=lambda value: store.set_animation(leaf, "speed", value)),
        Setting(f"{title}: кривая", "Как меняется скорость по ходу анимации.", "choice",
                choices=[(name, name) for name in store.curves], getter=field("bezier"), modified=modified, resetter=reset,
                setter=lambda value: store.set_animation(leaf, "bezier", value)),
    ]
    if styles:
        rows.append(Setting(f"{title}: стиль", "", "choice", choices=[(style, label) for style, label in styles],
                            getter=field("style"), modified=modified, resetter=reset,
                            setter=lambda value: store.set_animation(leaf, "style", value)))
    for row in rows:
        row.leaf, row.title = leaf, title
    return rows


def lock_row(section, key, label, hint="", kind="int", lo=None, hi=None, step=1, default=0, unit_parse=int, choices=None):
    def read():
        value = screens.lock_get(section, key, default)
        try:
            return unit_parse(str(value).split(",")[0])
        except ValueError:
            return value

    def write(value):
        if key == "size":
            height = screens.lock_get(section, key, "650, 100").split(",")[-1].strip()
            screens.lock_set(section, key, f"{value}, {height}")
        else:
            screens.lock_set(section, key, value)

    return Setting(label, hint, kind, lo, hi, step, choices, read, write)


def clock_row(store, key, label, kind="bool", choices=None, default=False):
    def write(value):
        store.state["lock"][key] = value
        hypr.save_state(store.state)
        screens.write_clock(store.state["lock"])

    return Setting(label, "", kind, choices=choices, getter=lambda: store.state["lock"].get(key, default), setter=write)


def logo_rows(store):
    settings = lambda: screens.logo_settings(store.state)

    def write(key, value):
        store.state["lock"]["logo"] = {**settings(), key: value}
        hypr.save_state(store.state)
        screens.write_logo(store.state["lock"]["logo"])

    def choices():
        known = screens.built_in_logos()
        path = settings()["path"]
        return known if not path or path in [item for item, _ in known] else [*known, (path, f"свой: {Path(path).name}")]

    picture = Setting("Картинка", "Встроенные логотипы и последний свой файл.", "choice", choices=choices(),
                      getter=lambda: settings()["path"], setter=lambda value: write("path", value))

    def add(source):
        if not source:
            return None
        target, error = screens.add_logo(source)
        if error:
            return error
        write("path", str(target))
        write("show", True)
        picture.choices[:] = choices()
        return None

    def row(key, label, hint="", kind="int", **extra):
        return Setting(label, hint, kind, getter=lambda: settings()[key], setter=lambda value: write(key, value), **extra)

    return [
        row("show", "Показывать логотип", "Выключено — логотипа нет, остальные настройки сохраняются.", "bool"),
        picture,
        Setting("Свой файл", "Открывает выбор файла: PNG, JPG или WebP. Картинка скопируется в настройки приложения.", "action",
                setter=add, verb="выбрать", picker=("Логотип экрана блокировки", screens.LOGO_TYPES)),
        row("size", "Размер", "", lo=40, hi=400, step=10),
        row("y", "Положение по высоте", "От центра экрана, вверх — больше.", lo=-500, hi=500, step=10),
        row("shape", "Форма", "", "choice", choices=[(-1, "круг"), (0, "как в файле")]),
    ]


def greeter_row(store, key, label, hint="", kind="bool", lo=None, hi=None, step=None, choices=None):
    def write(value):
        store.state["greeter"][key] = value
        hypr.save_state(store.state)
        return screens.publish_greeter(screens.greeter_settings(store.state), looks.current_colors())

    return Setting(label, hint, kind, lo, hi, step, choices,
                   lambda: screens.greeter_settings(store.state)[key], write)


def file_regex_row(path, pattern, template, label, hint, kind="int", lo=None, hi=None, step=1, choices=None, after=None):
    """Значение из строки файла: файл остаётся у пользователя, меняется только эта строка."""
    def read():
        try:
            match = re.search(pattern, path.read_text(), flags=re.M)
        except OSError:
            return None
        if not match:
            return None
        return int(match.group(1)) if kind == "int" else match.group(1)

    def write(value):
        replace = template.format(value) if template else lambda match: match.group(0).replace(match.group(1), str(value))
        path.write_text(re.sub(pattern, replace, path.read_text(), count=1, flags=re.M))
        if after:
            subprocess.run(after, capture_output=True, timeout=10)

    return Setting(label, hint, kind, lo, hi, step, choices, read, write)


def waybar_layout(text, position):
    """Положение панели: сбоку толщину задаёт width, а часы повёрнуты, иначе они раздувают панель."""
    text = re.sub(r'"position":\s*"\w+"', f'"position": "{position}"', text, count=1)
    side = position in ("left", "right")
    text = re.sub(r'"(?:height|width)":(\s*\d+)', ('"width":' if side else '"height":') + r"\1", text, count=1)
    rotate = {"left": ' "rotate": 270,', "right": ' "rotate": 90,'}.get(position, "")
    return re.sub(r'("clock":\s*\{)(?:\s*"rotate":\s*\d+,)?', lambda match: match.group(1) + rotate, text, count=1)


def bar_position_row(path):
    def read():
        match = re.search(r'"position":\s*"(\w+)"', path.read_text()) if path.exists() else None
        return match.group(1) if match else None

    def write(value):
        path.write_text(waybar_layout(path.read_text(), value))
        subprocess.run(["omarchy-restart-waybar"], capture_output=True, timeout=10)

    return Setting("Положение панели", "", "choice", choices=[("top", "сверху"), ("bottom", "снизу"), ("left", "слева"), ("right", "справа")],
                   getter=read, setter=write)


def opacity_row(store, tag, index, label, hint):
    """Прозрачность окон с меткой правила omarchy: свои значения лежат в слое, правило omarchy остаётся как есть."""
    def read():
        return store.state["opacity"].get(tag, hypr.OPACITY_DEFAULT[tag])[index]

    def write(value):
        pair = list(store.state["opacity"].get(tag, hypr.OPACITY_DEFAULT[tag]))
        pair[index] = value
        error = hypr.apply_live(hypr.lua_opacity(tag, pair))
        if error:
            return error
        store.state["opacity"][tag] = pair
        store.persist()
        return None

    def reset():
        store.state["opacity"].pop(tag, None)
        store.reload()

    return Setting(label, hint, "float", 0.1, 1.0, 0.01, getter=read, setter=write, resetter=reset, modified=lambda: tag in store.state["opacity"],
                   preview={})


def wallpaper_row(key, label, hint, choices):
    def write(value):
        settings = looks.wallpaper_settings()
        settings[key] = value
        looks.save_wallpaper_settings(settings)
        return looks.apply_wallpaper()

    return Setting(label, hint, "choice", choices=choices, getter=lambda: looks.wallpaper_settings()[key], setter=write)


def run_action(label, hint, command, file=None):
    def action():
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return Setting(label, hint, "action", setter=lambda _: action(), file=file)


def fonts():
    done = subprocess.run(["omarchy-font-list"], capture_output=True, text=True, timeout=10)
    return [(line, line) for line in done.stdout.splitlines() if line]


def current_font():
    done = subprocess.run(["omarchy-font-current"], capture_output=True, text=True, timeout=10)
    return done.stdout.strip()


def cursor_themes():
    names = set()
    for root in (Path("/usr/share/icons"), HOME / ".local/share/icons", HOME / ".icons"):
        if root.is_dir():
            names.update(path.parent.name for path in root.glob("*/cursors"))
    return [(name, name) for name in sorted(names)]


def set_cursor(store, theme=None, size=None):
    env = store.state["env"]
    if theme:
        env["XCURSOR_THEME"] = env["HYPRCURSOR_THEME"] = theme
    if size:
        env["XCURSOR_SIZE"] = env["HYPRCURSOR_SIZE"] = str(size)
    store.persist()
    hypr.hyprctl("setcursor", env.get("XCURSOR_THEME", "Adwaita"), env.get("XCURSOR_SIZE", "24"))


BRANDING = HOME / ".config/omarchy/branding"
WINDOW_PRESETS = {
    "Строгий": {"general:gaps_in": 3, "general:gaps_out": 6, "general:border_size": 2, "decoration:rounding": 0,
                "decoration:shadow:enabled": False, "decoration:blur:enabled": False,
                "decoration:active_opacity": 1.0, "decoration:inactive_opacity": 1.0},
    "Мягкий": {"general:gaps_in": 6, "general:gaps_out": 14, "general:border_size": 2, "decoration:rounding": 12,
               "decoration:shadow:enabled": True, "decoration:shadow:range": 20, "decoration:blur:enabled": True,
               "decoration:blur:size": 6, "decoration:blur:passes": 3,
               "decoration:active_opacity": 1.0, "decoration:inactive_opacity": 0.96},
    "Стекло": {"general:gaps_in": 8, "general:gaps_out": 18, "general:border_size": 1, "decoration:rounding": 16,
               "decoration:shadow:enabled": True, "decoration:shadow:range": 28, "decoration:blur:enabled": True,
               "decoration:blur:size": 8, "decoration:blur:passes": 3, "decoration:blur:vibrancy": 0.3,
               "decoration:active_opacity": 0.93, "decoration:inactive_opacity": 0.82},
    "Для игр": {"general:gaps_in": 0, "general:gaps_out": 0, "general:border_size": 1, "decoration:rounding": 0,
                "decoration:shadow:enabled": False, "decoration:blur:enabled": False, "animations:enabled": False,
                "decoration:active_opacity": 1.0, "decoration:inactive_opacity": 1.0},
}
MOTION = {
    "Быстрые": (0.5, "easeOutQuint"),
    "Обычные": (1.0, "easeOutQuint"),
    "Упругие": (1.3, hypr.BOUNCE_CURVE),
    "Плавные": (1.8, "easeInOutCubic"),
}
MOTION_BASE = {"windows": 3.8, "windowsIn": 4.1, "windowsOut": 1.5, "border": 5.4, "fade": 3, "layers": 3.8, "workspaces": 3, "specialWorkspace": 3}
WINDOW_STYLES = [("slide", "выезд"), ("popin 87%", "появление"), ("gnomed", "как в GNOME")]
WORKSPACE_STYLES = [("slide", "сдвиг"), ("slidevert", "сдвиг вертикально"), ("fade", "затухание"), ("slidefade 20%", "сдвиг с затуханием")]
LAYER_STYLES = [("fade", "затухание"), ("slide", "выезд"), ("popin 90%", "появление")]
ON_OFF_MINUTES = [(60, "1 минута"), (120, "2 минуты"), (300, "5 минут"), (600, "10 минут"), (900, "15 минут"), (1800, "30 минут"), (3600, "1 час")]


def preset_row(store, name):
    return Setting(f"Пресет «{name}»", "Enter применяет. Заменяет параметры в соседних разделах, любой из них можно потом подправить.",
                   "action", setter=lambda _: store.set_many(WINDOW_PRESETS[name]), preview=WINDOW_PRESETS[name], verb="применить")


def demo_row():
    return Setting("Показать на настоящем окне", "Откроется и закроется маленькое окно с текущими настройками.", "action",
                   setter=lambda _: screens.motion_demo(), verb="показать")


def motion_row(store):
    """Четыре набора на одном ползунке, от коротких анимаций к длинным."""
    names = list(MOTION)

    def distance(name):
        factor, curve = MOTION[name]
        spec = store.animation("windows")
        return abs(float(spec["speed"]) - MOTION_BASE["windows"] * factor) + (0 if spec["bezier"] == curve else 0.5)

    def write(index):
        factor, curve = MOTION[names[index]]
        leaves = {leaf: {**store.animation(leaf), "enabled": True, "speed": round(speed * factor, 1), "bezier": curve}
                  for leaf, speed in MOTION_BASE.items()}
        return store.set_many({"animations:enabled": True}, leaves)

    def reset():
        for leaf in MOTION_BASE:
            store.state["animations"].pop(leaf, None)
        store.reload()

    return Setting("Характер анимаций", "Меняет длительность и кривую разом. Отдельные анимации можно подправить в соседних разделах.", "int",
                   0, len(names) - 1, 1, getter=lambda: names.index(min(names, key=distance)), setter=write, marks=names,
                   resetter=reset, modified=lambda: any(leaf in store.state["animations"] for leaf in MOTION_BASE), leaf="windows")



RAW_SCENES = {"general": "desktop", "decoration": "desktop", "dwindle": "desktop", "master": "desktop", "scrolling": "desktop"}


def clean(rows):
    return [row for row in rows if row is not None]


def build_tree(store):
    """Дерево настроек: от самого заметного к редкому. Лист — (заголовок, вид страницы, данные)."""
    o = lambda *args, **kwargs: option(store, *args, **kwargs)
    terminal = HOME / ".config/kitty/kitty.conf"
    waybar = HOME / ".config/waybar/config.jsonc"

    return [
        ("Тема", "󰸌", [("Цветовая тема", "themes", None)]),
        ("Обои", "\uf03e", [
            ("Обои темы", "wallpapers", None),
            ("Показ обоев", "wall", [
                wallpaper_row("MODE", "Заполнение экрана", "Как вписать обои в экран другого соотношения сторон.",
                              [("fill", "заполнить"), ("fit", "вписать"), ("center", "по центру")]),
                wallpaper_row("PAUSE", "Анимация под окнами", "Пауза не тратит ресурсы, пока обоев не видно, например в игре.",
                              [("hidden", "пауза, когда не видно"), ("stop", "выгружать, когда не видно"), ("never", "играть всегда")]),
                wallpaper_row("SPEED", "Скорость анимации", "", [("0.5", "0.5×"), ("0.75", "0.75×"), ("1.0", "1×"), ("1.5", "1.5×")]),
                o("misc:background_color", "Цвет под обоями"),
            ]),
        ]),
        ("Экран блокировки", "\uf023", [
            ("Вид", "lock", [
                lock_row("background", "blur_passes", "Размытие фона", "0 — обои как есть.", lo=0, hi=4),
                lock_row("background", "brightness", "Яркость фона", "", "float", 0.2, 1.0, 0.05, 1.0, float),
                lock_row("input-field", "size", "Ширина поля пароля", "", lo=200, hi=900, step=25, default=650),
                lock_row("input-field", "rounding", "Скругление поля", "-1 — полностью круглое.", lo=-1, hi=40, default=0),
                lock_row("input-field", "outline_thickness", "Толщина рамки", "", lo=0, hi=12, default=4),
                lock_row("input-field", "placeholder_text", "Подсказка в поле", "", "text", default="Введите пароль", unit_parse=str),
                lock_row("input-field", "fade_on_empty", "Скрывать пустое поле", "", "choice", default="false", unit_parse=str,
                         choices=[("false", "показывать"), ("true", "скрывать")]),
            ]),
            ("Логотип", "lock", logo_rows(store)),
            ("Часы и дата", "lock", [
                clock_row(store, "clock", "Показывать часы"),
                clock_row(store, "clock_format", "Формат времени", "choice", [("24", "24 часа"), ("12", "12 часов")], "24"),
                clock_row(store, "date", "Показывать дату", default=True),
            ]),
            ("Поведение", "lock", [
                Setting("Блокировать через", "Время без действий до блокировки.", "choice", choices=ON_OFF_MINUTES,
                        getter=screens.idle_timeout, setter=screens.set_idle_timeout),
                Setting("Посмотреть настоящий экран", f"Выход без пароля первые {screens.LOCK_PREVIEW_GRACE} секунд: нажмите клавишу или двиньте мышь.",
                        "action", setter=lambda _: screens.lock_preview(), verb="показать"),
                Setting("Заблокировать сейчас", "Настоящая блокировка: для выхода нужен пароль.", "action", setter=lambda _: screens.lock_now()),
            ]),
        ]),
        ("Экран входа", "\uf2f6", [
            ("Вид", "greeter", [
                greeter_row(store, "background", "Фон", "Обои темы копируются в момент изменения настройки.", "choice",
                            choices=[("theme", "обои текущей темы"), ("color", "цвет темы")]),
                greeter_row(store, "dim", "Затемнение фона", "", "float", 0.0, 0.8, 0.05),
                greeter_row(store, "blur", "Размытие фона", "", "float", 0.0, 1.0, 0.1),
                greeter_row(store, "panel", "Положение формы", "", "choice",
                            choices=[("center", "по центру"), ("left", "слева"), ("right", "справа")]),
                greeter_row(store, "title", "Заголовок", "", "text"),
                greeter_row(store, "clock", "Показывать часы"),
            ]),
            ("Вход", "greeter", [
                greeter_row(store, "remember_user", "Подставлять моё имя", "Останется ввести только пароль."),
                run_action("Показать экран входа", "На 8 секунд поверх всех окон. Вход в этом режиме не работает.",
                           ["oshh-greeter-preview"]),
            ]),
        ]),
        ("Окна", "\uf2d2", [
            ("Пресеты", "desktop", [preset_row(store, name) for name in WINDOW_PRESETS]),
            ("Зазоры и рамки", "desktop", clean([
                o("general:gaps_in", "Зазор между окнами", "", 0, 40),
                o("general:gaps_out", "Зазор от края экрана", "", 0, 60),
                o("general:gaps_workspaces", "Зазор между рабочими столами", "", 0, 60),
                o("general:border_size", "Толщина рамки", "", 0, 10),
                o("decoration:rounding", "Скругление углов", "", 0, 40),
                o("decoration:rounding_power", "Форма скругления", "2 — круг, 4 — сквиркл как в macOS.", 1, 10, 0.5),
                o("general:col.active_border", "Цвет активной рамки", "По умолчанию берётся из темы. После сброса снова следует теме."),
                o("general:col.inactive_border", "Цвет неактивной рамки"),
                o("general:resize_on_border", "Менять размер за рамку", ""),
                run_action("Режим без зазоров", "Быстрый переключатель, он важнее значений выше.", ["omarchy-hyprland-window-gaps-toggle"]),
            ])),
            ("Тени", "desktop", clean([
                o("decoration:shadow:enabled", "Тени окон", ""),
                o("decoration:shadow:range", "Размер тени", "", 0, 60),
                o("decoration:shadow:render_power", "Спад тени", "1 — мягкий, 4 — резкий.", 1, 4),
                o("decoration:shadow:sharp", "Чёткая тень", ""),
                o("decoration:shadow:color", "Цвет тени"),
                o("decoration:shadow:color_inactive", "Цвет тени неактивных окон"),
            ])),
            ("Размытие", "desktop", clean([
                o("decoration:blur:enabled", "Размытие под окнами", "Заметно только при прозрачности."),
                o("decoration:blur:variant", "Эффект", "Сложные эффекты нагружают видеокарту."),
                o("decoration:blur:size", "Радиус", "", 1, 20),
                o("decoration:blur:passes", "Проходы", "Больше — плавнее и тяжелее.", 1, 6),
                o("decoration:blur:vibrancy", "Насыщенность", "", 0, 1),
                o("decoration:blur:noise", "Шум", "", 0, 1, 0.01),
                o("decoration:blur:contrast", "Контраст", "", 0, 2),
                o("decoration:blur:brightness", "Яркость", "", 0, 2),
                o("decoration:blur:popups", "Размывать под меню", ""),
                o("decoration:blur:special", "Размывать скрытый стол", ""),
            ])),
            ("Прозрачность и затемнение", "desktop", clean([
                opacity_row(store, "default", 0, "Обычные окна, активное", "Правило omarchy по умолчанию 0.97. Умножается на общую прозрачность ниже."),
                opacity_row(store, "default", 1, "Обычные окна, неактивные", "По умолчанию 0.9."),
                opacity_row(store, "terminal", 0, "Терминалы, активный", "По умолчанию 0.97."),
                opacity_row(store, "terminal", 1, "Терминалы, неактивные", "По умолчанию 0.9."),
                o("decoration:active_opacity", "Общая: активное окно", "Множитель для всех окон сразу.", 0.1, 1, 0.01),
                o("decoration:inactive_opacity", "Общая: неактивные окна", "", 0.1, 1, 0.01),
                o("decoration:fullscreen_opacity", "Окно на весь экран", "", 0.1, 1, 0.01),
                o("decoration:dim_inactive", "Затемнять неактивные окна", ""),
                o("decoration:dim_strength", "Сила затемнения", "", 0, 1),
                o("decoration:dim_special", "Затемнять под скрытым столом", "", 0, 1),
                o("decoration:dim_around", "Затемнять вокруг модальных окон", "", 0, 1),
            ])),
        ]),
        ("Анимации", "󰐃", [
            ("Общие", "motion", clean([
                demo_row(),
                o("animations:enabled", "Анимации включены", ""),
                motion_row(store),
                o("misc:animate_manual_resizes", "Анимировать изменение размера", ""),
                o("misc:animate_mouse_windowdragging", "Анимировать перетаскивание", ""),
            ])),
            ("Окна", "motion", [demo_row()] + animation_rows(store, "windowsIn", "Открытие", WINDOW_STYLES)
             + animation_rows(store, "windowsOut", "Закрытие", WINDOW_STYLES)
             + animation_rows(store, "windows", "Перемещение", WINDOW_STYLES)),
            ("Рабочие столы", "motion", animation_rows(store, "workspaces", "Смена стола", WORKSPACE_STYLES)
             + animation_rows(store, "specialWorkspace", "Скрытый стол", WORKSPACE_STYLES)),
            ("Затухание и панели", "motion", [demo_row()] + animation_rows(store, "fade", "Затухание", [])
             + animation_rows(store, "border", "Смена рамки", [])
             + animation_rows(store, "layers", "Панели и меню", LAYER_STYLES)),
        ]),
        ("Шрифты и курсор", "\uf031", [
            ("Шрифты", "font", [
                Setting("Моноширинный шрифт", "Терминал, панель, экран блокировки.", "choice", choices=fonts(), getter=current_font, try_key="font",
                        setter=lambda value: subprocess.run(["omarchy-font-set", value], capture_output=True, timeout=30) and None),
                file_regex_row(terminal, r"^font_size\s+(\d+)", "font_size {}", "Размер шрифта терминала", "", lo=6, hi=32,
                               after=["omarchy-restart-terminal"]),
            ]),
            ("Курсор", "cursor", clean([
                Setting("Тема курсора", "", "choice", choices=cursor_themes(), try_key="cursor_theme",
                        getter=lambda: store.state["env"].get("XCURSOR_THEME", "Adwaita"), setter=lambda value: set_cursor(store, theme=value)),
                Setting("Размер курсора", "", "choice", choices=[(size, str(size)) for size in (16, 24, 32, 40, 48, 64)],
                        getter=lambda: int(store.state["env"].get("XCURSOR_SIZE", 24)), setter=lambda value: set_cursor(store, size=value)),
                o("cursor:inactive_timeout", "Прятать курсор через, с", "0 — не прятать.", 0, 30),
                o("cursor:hide_on_key_press", "Прятать при наборе текста", ""),
                o("cursor:zoom_factor", "Экранная лупа", "1 — выключена.", 1, 4, 0.1),
            ])),
        ]),
        ("Панель и заставка", "\uf2d0", [
            ("Панель", "desktop", [
                bar_position_row(waybar),
                file_regex_row(waybar, r'"(?:height|width)":\s*(\d+)', None, "Толщина панели", "", lo=20, hi=48, after=["omarchy-restart-waybar"]),
                run_action("Показать или скрыть панель", "", ["omarchy-toggle-waybar"]),
            ]),
            ("Заставка и «О системе»", "files", [
                run_action("Заставка: свой текст", "", ["omarchy-launch-floating-terminal-with-presentation", "omarchy-branding-screensaver text"], file=BRANDING / "screensaver.txt"),
                run_action("Заставка: из картинки", "", ["omarchy-launch-floating-terminal-with-presentation", "omarchy-branding-screensaver image"], file=BRANDING / "screensaver.txt"),
                run_action("Заставка: по умолчанию", "", ["omarchy-branding-screensaver", "reset"], file=BRANDING / "screensaver.txt"),
                run_action("«О системе»: свой текст", "", ["omarchy-launch-floating-terminal-with-presentation", "omarchy-branding-about text"], file=BRANDING / "about.txt"),
                run_action("«О системе»: из картинки", "", ["omarchy-launch-floating-terminal-with-presentation", "omarchy-branding-about image"], file=BRANDING / "about.txt"),
                run_action("«О системе»: по умолчанию", "", ["omarchy-branding-about", "reset"], file=BRANDING / "about.txt"),
                run_action("Ночной свет", "Вкл или выкл тёплый оттенок экрана.", ["omarchy-toggle-nightlight"]),
            ]),
        ]),
        ("Столы и раскладка", "\uf009", [
            ("Раскладка окон", "desktop", clean([
                o("general:layout", "Раскладка", "dwindle — делением, master — главное окно, scrolling — лента.",
                  choices=[("dwindle", "деление"), ("master", "главное окно"), ("scrolling", "лента")]),
                o("dwindle:preserve_split", "Сохранять направление деления", ""),
                o("dwindle:smart_split", "Деление по положению курсора", ""),
                o("master:mfact", "Доля главного окна", "", 0.1, 0.9),
                o("scrolling:column_width", "Ширина колонки ленты", "", 0.2, 1),
            ])),
            ("Рабочие столы", "desktop", clean([
                o("binds:workspace_back_and_forth", "Повторное нажатие возвращает назад", ""),
                o("binds:hide_special_on_workspace_change", "Прятать скрытый стол при смене", ""),
                o("misc:close_special_on_empty", "Закрывать пустой скрытый стол", ""),
                o("cursor:warp_on_change_workspace", "Курсор за окном при смене стола", ""),
            ])),
        ]),
        ("Все параметры Hyprland", "\uf1de", [
            (category, "raw", category) for category in sorted({name.split(":")[0] for name in store.options})
        ]),
        ("Файлы конфигурации", "\uf15c", [("Открыть и сбросить", "files", file_rows(store))]),
    ]


def raw_rows(store, category, query=""):
    query = query.lower()
    rows = []
    for name, item in sorted(store.options.items()):
        if not name.startswith(category + ":"):
            continue
        if query and query not in name.lower() and query not in item["description"].lower():
            continue
        row = option(store, name, label=name[len(category) + 1:])
        if row:
            rows.append(row)
    return rows


def file_rows(store):
    rows = []
    for title, path in (("hyprland.lua — основной конфиг", hypr.MAIN_CONFIG),
                        ("oshh-style.lua — изменения из этого приложения", hypr.OVERLAY),
                        ("hyprlock.conf — экран блокировки", screens.HYPRLOCK),
                        ("hypridle.conf — простой и сон", screens.HYPRIDLE)):
        rows.append(Setting(title, str(path), "action", setter=lambda _, target=path: hypr.open_in_editor(target), verb="открыть", file=path))
    rows.append(Setting("Сбросить все настройки приложения", "Удаляет изменения окон, анимаций и курсора. Ваш hyprland.lua не меняется.",
                        "action", setter=lambda _: store.reset_all(), verb="сбросить", confirm="Сбросить все настройки приложения?"))
    return rows
