"""Приложение «Настройка»: звук, сеть, питание, ввод, программы по умолчанию и конфиги. Тот же интерфейс, что у «Оформления»."""
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from . import hypr, monitors, screens
from .settings import Setting, clean, option

HOME = Path.home()
SUSPEND_FLAG = HOME / ".local/state/omarchy/toggles/suspend-off"
POWER = ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles", "ActiveProfile")
POWER_PROFILES = [("power-saver", "экономия"), ("balanced", "сбалансированный"), ("performance", "производительность")]
BROWSERS = [("chromium", "chromium.desktop", "Chromium"), ("chrome", "google-chrome.desktop", "Chrome"),
            ("brave", "brave-browser.desktop", "Brave"), ("brave-origin", "brave-origin-beta.desktop", "Brave Origin"),
            ("edge", "microsoft-edge.desktop", "Edge"), ("firefox", "firefox.desktop", "Firefox"), ("zen", "zen.desktop", "Zen")]
TERMINALS = [("alacritty", "Alacritty"), ("foot", "Foot"), ("ghostty", "Ghostty"), ("kitty", "Kitty")]
EDITORS = [("nvim", "nvim", "Neovim"), ("code", "code", "VSCode"), ("cursor", "cursor", "Cursor"), ("zed", "zeditor", "Zed"),
           ("sublime_text", "sublime_text", "Sublime Text"), ("helix", "helix", "Helix"), ("vim", "vim", "Vim"), ("emacs", "emacs", "Emacs")]


def run(*command):
    """Вывод команды или текст ошибки для панели состояния."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as problem:
        return f"{command[0]}: {problem}"
    return (done.stdout or done.stderr).strip()


def launch(command):
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def config_lines(path, prefix):
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(line for line in lines if line.startswith(prefix))


def bindings():
    """Клавиша и описание из строк hl.bind."""
    pattern = re.compile(r'hl\.bind\("([^"]+)".*?description = "([^"]*)"')
    found = [pattern.match(line) for line in config_lines(hypr.MAIN_CONFIG, "hl.bind").splitlines()]
    return "\n".join(f"{match[1]:<32} {match[2]}" for match in found if match)


def power_profile():
    return run("busctl", "get-property", *POWER).removeprefix('s "').removesuffix('"')


def set_power_profile(value):
    done = subprocess.run(["busctl", "set-property", *POWER, "s", value], capture_output=True, text=True, timeout=10)
    return None if done.returncode == 0 else done.stderr.strip()


def set_suspend(enabled):
    if enabled == SUSPEND_FLAG.exists():
        launch(["omarchy-toggle-suspend"])


def open_wifi():
    if not shutil.which("nmtui"):
        return "nmtui не установлен: он появится со следующей сборкой образа"
    launch(["omarchy-launch-wifi"])
    return None


def wifi_status():
    return run("nmcli", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "no") or "Wi-Fi-адаптер не найден или сетей рядом нет"


def bluetooth_status():
    return run("bluetoothctl", "show") + "\n\n" + run("bluetoothctl", "devices", "Connected")


@lru_cache(maxsize=None)
def installed_browsers():
    roots = [HOME / ".local/share/applications", HOME / ".nix-profile/share/applications", Path("/usr/share/applications")]
    return [(name, title) for name, desktop, title in BROWSERS if any((root / desktop).is_file() for root in roots)]


def default_row(label, script, choices):
    return Setting(label, "Новое значение действует для запущенных после выхода из сеанса программ.", "choice", choices=choices,
                   getter=lambda: run(script), setter=lambda value: None if subprocess.run([script, value], capture_output=True).returncode == 0 else f"{script}: не получилось")


def config_row(title, path, restart=""):
    """После закрытия редактора перезапускаем службу, как делало меню."""
    script = 'omarchy-launch-editor "$1" && { [[ -z $2 ]] || "$2"; }'
    return Setting(title, str(path), "action", file=path, verb="открыть",
                   setter=lambda _: launch(["bash", "-c", script, "bash", str(path), restart]))


def tool_row(label, hint, command, details):
    return Setting(label, hint, "action", setter=lambda _: launch(command), details=details, verb="открыть")


def lua_row(label, hint, prefix):
    return Setting(label, hint, "action", setter=lambda _: hypr.open_in_editor(hypr.MAIN_CONFIG), verb="открыть",
                   details=lambda: config_lines(hypr.MAIN_CONFIG, prefix))


def guarded_row(screens_now, name, label, hint, kind, read, change, choices=None):
    """Настройка, после которой экран может погаснуть: остаётся только после подтверждения."""
    before = {}

    def write(value):
        before["specs"] = screens_now.snapshot()
        return screens_now.change(name, **change(value))

    def settle(keep):
        screens_now.save() if keep else screens_now.restore(before["specs"])

    return Setting(label, hint, kind, choices=choices, getter=read, setter=write, guard=settle, preview={"monitor": name})


def monitor_rows(screens_now, name):
    get = lambda: screens_now.find(name)
    monitor = get()
    available = monitors.modes(monitor)
    current = monitors.resolution(monitor)
    scales = sorted({1.0, 1.25, 1.5, 1.75, 2.0, float(monitor["scale"])})

    def best_rate(size):
        rates = available.get(size, [])
        wanted = monitors.rate(get())
        return wanted if wanted in rates else rates[0] if rates else "60.00"

    rows = [guarded_row(screens_now, name, "Включён", "Выключенный экран можно снова включить здесь.", "bool",
                        lambda: not get()["disabled"], lambda value: {"disabled": not value})]
    if monitor["disabled"]:
        return rows
    rows += [
        guarded_row(screens_now, name, "Разрешение", "Если экран погаснет, настройка вернётся через 15 секунд.", "choice",
                    lambda: monitors.resolution(get()), lambda value: {"mode": f"{value}@{best_rate(value)}"},
                    [(size, size.replace("x", " × ")) for size in available]),
        guarded_row(screens_now, name, "Частота обновления", "", "choice", lambda: monitors.rate(get()),
                    lambda value: {"mode": f"{monitors.resolution(get())}@{value}"},
                    [(rate, f"{float(rate):.0f} Гц" if float(rate).is_integer() else f"{float(rate):.2f} Гц") for rate in available.get(current, [])]),
        guarded_row(screens_now, name, "Масштаб", "Hyprland может взять ближайшее значение, которое делится нацело.", "choice",
                    lambda: float(get()["scale"]), lambda value: {"scale": value}, [(scale, f"{scale * 100:.0f} %") for scale in scales]),
        guarded_row(screens_now, name, "Поворот", "", "choice", lambda: get()["transform"], lambda value: {"transform": value},
                    [(0, "нет"), (1, "90°"), (2, "180°"), (3, "270°")]),
        guarded_row(screens_now, name, "Повторять экран", "Показывает то же, что другой экран.", "choice",
                    lambda: screens_now.mirror_of(get()), lambda value: {"mirror": value},
                    [("", "нет")] + [(item["name"], item["name"]) for item in screens_now.items if item["name"] != name and not item["disabled"]]),
        guarded_row(screens_now, name, "10 бит на цвет", "Плавнее градиенты, если экран это поддерживает.", "bool",
                    lambda: "2101010" in get()["currentFormat"], lambda value: {"bitdepth": 10 if value else 8}),
    ]
    if monitor["hardwareDetails"]["vrrCapable"]:
        def set_vrr(value):
            error = screens_now.change(name, vrr=value)
            if not error:
                screens_now.save()
            return error

        rows.append(Setting("Переменная частота (VRR)", "", "choice", choices=[(0, "выключена"), (1, "включена"), (2, "только в полном экране")],
                            getter=lambda: screens_now.spec(name).get("vrr", int(get()["vrr"])), setter=set_vrr, preview={"monitor": name}))
    rows.append(Setting("Вернуть как в конфиге", "Убирает настройки этого экрана, заданные здесь.", "action", verb="вернуть",
                        setter=lambda _: screens_now.reset(name), preview={"monitor": name}))
    return rows


def layout_rows(screens_now):
    active = screens_now.active()
    base = monitors.anchor(active)
    rows = []
    for monitor in active:
        if monitor is base:
            continue
        name = monitor["name"]
        side = lambda name=name: screens_now.relations()[name][0]
        offset = lambda name=name: screens_now.relations()[name][1]
        free = lambda name=name: monitors.span(base, side(name)) - monitors.span(screens_now.find(name), side(name))

        def align_value(name=name):
            return {0: "start", free(name) // 2: "center", free(name): "end"}.get(offset(name), "free")

        def set_align(value, name=name):
            if value != "free":
                return screens_now.move(name, offset={"start": 0, "center": free(name) // 2, "end": free(name)}[value])
            return None

        reach = max(monitors.span(base, "left"), monitors.span(base, "top"), monitors.span(monitor, "left"), monitors.span(monitor, "top"))
        rows += [
            Setting(f"{name}: сторона", f"С какой стороны от {base['name']} стоит экран.", "choice", choices=monitors.SIDES,
                    getter=side, setter=lambda value, name=name: screens_now.move(name, side=value), preview={"monitor": name}),
            Setting(f"{name}: выравнивание", "", "choice", choices=monitors.ALIGNS, getter=align_value, setter=set_align, preview={"monitor": name}),
            Setting(f"{name}: сдвиг вдоль края", "Пиксели. Shift со стрелками двигает в 10 раз быстрее, Enter — ввести число.", "int",
                    lo=-reach, hi=reach, step=10, getter=offset, setter=lambda value, name=name: screens_now.move(name, offset=value),
                    preview={"monitor": name}),
        ]
    if not rows:
        rows.append(Setting("Подключён один экран", "Второй экран появится здесь после подключения.", "action", verb="—", preview={"monitor": base["name"]}))
    return rows


def monitor_pages(store):
    screens_now = monitors.Screens(store)
    if not screens_now.active():
        return [("Мониторы", "files", [Setting("Экраны не найдены", "", "action", details=lambda: run("hyprctl", "monitors"), verb="—")])]
    pages = [("Расположение", "monitors", layout_rows(screens_now))]
    return pages + [(monitors.title(item), "monitors", monitor_rows(screens_now, item["name"])) for item in screens_now.items]


def build_tree(store):
    """Дерево как в «Оформлении»: от того, что нужно чаще, к редкому."""
    o = lambda *args, **kwargs: option(store, *args, **kwargs)
    config = HOME / ".config"
    editors = [(name, title) for name, command, title in EDITORS if shutil.which(command)]

    return [
        ("Звук", "", [("Устройства и громкость", "files", [
            tool_row("Микшер", "Громкость и устройства по программам.", ["omarchy-launch-audio"], lambda: run("wpctl", "status")),
        ])]),
        ("Сеть", "", [
            ("Wi-Fi", "files", [Setting("Подключения Wi-Fi", "Поиск сетей и подключение.", "action", setter=lambda _: open_wifi(),
                                        details=wifi_status, verb="открыть")]),
            ("Bluetooth", "files", [tool_row("Устройства Bluetooth", "Сопряжение и подключение.", ["omarchy-launch-bluetooth"], bluetooth_status)]),
        ]),
        ("Экраны", "\U000f0379", [
            *monitor_pages(store),
            ("Поведение экрана", "info", clean([
                o("misc:vrr", "Переменная частота (VRR)", ""),
                o("misc:mouse_move_enables_dpms", "Будить экран мышью", ""),
                o("misc:key_press_enables_dpms", "Будить экран клавишей", ""),
                o("xwayland:force_zero_scaling", "Не масштабировать X11-приложения", ""),
            ])),
        ]),
        ("Питание", "", [("Профиль и сон", "files", [
            Setting("Профиль питания", "", "choice", choices=POWER_PROFILES, getter=power_profile, setter=set_power_profile,
                    details=lambda: run("tuned-adm", "active")),
            Setting("Сон в меню «Система»", "Показывать ли пункт перехода в сон.", "bool", getter=lambda: not SUSPEND_FLAG.exists(),
                    setter=set_suspend, details=lambda: "Переход в сон доступен" if not SUSPEND_FLAG.exists() else "Пункт сна скрыт"),
        ])]),
        ("Мышь и клавиатура", "", [
            ("Клавиатура", "keys", clean([
                o("input:kb_layout", "Раскладки", "Через запятую: us,ru"),
                o("input:kb_options", "Параметры XKB", "Например: grp:alt_shift_toggle,compose:caps"),
                o("input:repeat_rate", "Скорость повтора", "", 10, 80),
                o("input:repeat_delay", "Задержка повтора, мс", "", 150, 1000, 25),
                o("input:numlock_by_default", "Num Lock при входе", ""),
            ])),
            ("Мышь", "mouse", clean([
                o("input:sensitivity", "Чувствительность", "Shift со стрелками меняет в 10 раз быстрее.", -1, 1, 0.01),
                o("input:accel_profile", "Ускорение", "", choices=[("", "по умолчанию"), ("flat", "выключено"), ("adaptive", "адаптивное")]),
                o("input:natural_scroll", "Естественная прокрутка", ""),
                o("input:left_handed", "Для левой руки", ""),
                o("input:scroll_factor", "Скорость прокрутки", "", 0.1, 4),
                o("input:follow_mouse", "Фокус следует за мышью", ""),
            ])),
            ("Тачпад", "mouse", clean([
                o("input:touchpad:natural_scroll", "Естественная прокрутка", ""),
                o("input:touchpad:tap-to-click", "Нажатие касанием", ""),
                o("input:touchpad:disable_while_typing", "Выключать при наборе", ""),
                o("input:touchpad:scroll_factor", "Скорость прокрутки", "", 0.1, 4),
            ])),
            ("Горячие клавиши", "files", [
                tool_row("Список клавиш", "То же окно, что по Super + K.", ["omarchy-menu-keybindings"],
                         bindings),
                lua_row("Изменить в конфиге", "Строки hl.bind в hyprland.lua.", "hl.bind"),
            ]),
        ]),
        ("Окна и фокус", "", [("Поведение окон", "info", clean([
            o("misc:focus_on_activate", "Фокус на окно, запросившее внимание", ""),
            o("misc:enable_swallow", "Терминал уступает место запущенному окну", ""),
            o("general:allow_tearing", "Разрешить разрыв кадра в играх", "Меньше задержка, но возможны разрывы."),
        ]))]),
        ("Программы по умолчанию", "", [("Браузер, терминал, редактор", "info", [
            default_row("Браузер", "omarchy-default-browser", installed_browsers()),
            default_row("Терминал", "omarchy-default-terminal", [item for item in TERMINALS if shutil.which(item[0])]),
            default_row("Редактор", "omarchy-default-editor", editors),
        ])]),
        ("Конфигурация", "", [("Файлы программ", "files", [
            config_row("Hyprland", hypr.MAIN_CONFIG),
            config_row("Hypridle", screens.HYPRIDLE, "omarchy-restart-hypridle"),
            config_row("Hyprlock", screens.HYPRLOCK),
            config_row("Hyprsunset", hypr.HYPR_DIR / "hyprsunset.conf", "omarchy-restart-hyprsunset"),
            config_row("SwayOSD", config / "swayosd/config.toml", "omarchy-restart-swayosd"),
            config_row("Walker", config / "walker/config.toml", "omarchy-restart-walker"),
            config_row("Waybar", config / "waybar/config.jsonc", "omarchy-restart-waybar"),
            config_row("XCompose", HOME / ".XCompose", "omarchy-restart-xcompose"),
        ])]),
    ]
